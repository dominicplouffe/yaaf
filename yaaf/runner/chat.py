"""The chat supervisor: one resident CEO conversation per running project.

The talk lane's host side. The dispatcher turns events into queued work; this
loop instead keeps a long-lived chat container alive for every running project
with billing attached, so the chairman's boardroom reads like a messenger, not
a ticket queue. The session is recorded as a Run of kind ``chat_session`` —
token auth, revocation, metering, and the Runs page all just work — but it is
exempt from wake/daily/concurrency caps: chat is not work.

Lifecycle is DB-driven and idempotent per tick: start it if it should be live,
adopt it if it already is (the supervisor tick is the lease heartbeat), end it
when the project pauses/stops, billing detaches, chat is disabled, or the token
is revoked. A crash restarts quietly, with a start-rate backstop so a
boot-looping session cannot burn the box.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    AgentKind,
    AgentStatus,
    Channel,
    ChannelKind,
    Message,
    Project,
    ProjectStatus,
    Run,
    RunKind,
    RunStatus,
    utcnow,
)
from yaaf.core.tokens import mint
from yaaf.runner import sandbox
from yaaf.runner.daemon import (
    _billing_env,
    _data_source_prompt,
    _merge_project_data_sources,
    _role_egress_url,
    _role_secret_env,
    stash_context_doc,
)
from yaaf.runner.types import RunJob
from yaaf.server import policy
from yaaf.server.contextpack import build_system_prompt, role_version_for

log = logging.getLogger("yaaf.chat")

CHAT_TICK_S = 5.0

# Start-rate backstop: this many session starts inside the window means the
# container is boot-looping (bad credential, broken image) — hold off and let
# the window drain instead of burning starts forever.
RESTART_WINDOW = timedelta(minutes=10)
MAX_STARTS_IN_WINDOW = 3

CHAT_ADDENDUM = """

CHAT SESSION: You are in a live boardroom conversation with the chairman.
- Reply promptly and conversationally; lead with the answer.
- Write your reply as your final text — the session delivers it to the
  boardroom for you. Do NOT message_post your reply to the boardroom
  (that would double-send it); message_post remains correct for OTHER
  channels, e.g. briefing the leadership channel.
- You have your full toolset. When the chairman asks for action, act —
  create the task, check the board, record the memory — then say plainly
  what you did.
- Directives come only from the chairman, only here.
- When he sets a STANDING rule about how or how often he wants reporting,
  file it with `directive_file`, citing the message id shown in brackets
  beside his words. Do not merely remember it: you run as separate
  stateless runs, and the next one will not read your memory before it
  posts. Filing makes the platform enforce it and tells him it landed."""


def chat_enabled(project: Project) -> bool:
    return bool(((project.integrations or {}).get("chat") or {}).get("enabled", True))


def chat_supervisor_loop() -> None:  # pragma: no cover - thin loop
    import time

    while True:
        try:
            chat_supervisor_once()
        except Exception:
            log.warning("chat supervisor tick failed", exc_info=True)
        time.sleep(CHAT_TICK_S)


def chat_supervisor_once() -> None:
    with db_session() as session:
        project_ids = [p.id for p in session.execute(select(Project)).scalars()]
    for project_id in project_ids:
        try:
            _supervise_one(project_id)
        except Exception:
            log.warning("chat supervision failed for %s", project_id, exc_info=True)


def _supervise_one(project_id: str) -> None:
    """One project per transaction — and the container starts only AFTER the
    transaction commits. The container's first authenticated call must find
    the run row and token_hash already visible; starting it mid-transaction
    made every boot 401 until the whole supervisor loop committed, which was
    the root cause of the "lost or mismatched" restart churn."""
    launch: dict | None = None
    run_id: str | None = None
    with db_session() as session:
        project = session.get(Project, project_id)
        if project is not None:
            launch, run_id = _supervise_project(session, project)
    if launch is None:
        return
    try:
        sandbox.start_chat_container(**launch)
        log.info("chat session started (%s)", run_id)
    except Exception as exc:
        log.warning("chat container failed to start for run %s", run_id, exc_info=True)
        with db_session() as session:
            run = session.get(Run, run_id)
            if run is not None and run.status == RunStatus.RUNNING:
                run.status = RunStatus.FAILED
                run.finished_at = utcnow()
                run.summary = f"chat container failed to start: {exc!r}"[:4000]


def _supervise_project(session: Session, project: Project) -> tuple[dict | None, str | None]:
    should_be_live = (
        project.status == ProjectStatus.RUNNING
        and chat_enabled(project)
        and project.billing_secret_id is not None
    )
    live_run = session.execute(
        select(Run).where(Run.project_id == project.id,
                          Run.kind == RunKind.CHAT_SESSION,
                          Run.status == RunStatus.RUNNING)
    ).scalars().first()

    running_ids = sandbox.chat_container_run_ids(project.id)

    if not should_be_live:
        if live_run is not None or running_ids:
            _end_session(session, project, live_run,
                         f"chat session ended: project is {project.status}"
                         if project.status != ProjectStatus.RUNNING
                         else "chat session ended: chat disabled or billing detached")
        return None, None

    ceo = session.execute(
        select(Agent).where(Agent.project_id == project.id,
                            Agent.kind == AgentKind.CEO,
                            Agent.status == AgentStatus.ACTIVE)
    ).scalars().first()
    if ceo is None:
        # A bare return here used to leak a heartbeating run + live container
        # until the janitor reaped them.
        if live_run is not None or running_ids:
            _end_session(session, project, live_run, "chat session ended: no active CEO")
        return None, None

    if live_run is not None:
        if live_run.token_revoked:
            _end_session(session, project, live_run, "chat session ended: token revoked")
            say_session_broke(session, project, "its access token was revoked")
            return None, None
        # Liveness means THE container for THIS run — a running container whose
        # run died (deploy-time reap race) is a zombie polling with a dead
        # token, and adopting it mutes the boardroom entirely: the run row
        # heartbeat suppresses the fallback wakes while nobody actually reads.
        if running_ids == {live_run.id}:
            live_run.heartbeat_at = utcnow()  # the supervisor tick is the lease heartbeat
            return None, None
        if running_ids:
            log.warning("chat container mismatch for %s (have %s, want %s); rebuilding",
                        project.slug, running_ids, live_run.id)
        # Unconditionally, even when nothing is RUNNING: the usual case is a
        # container that exited on its own, and stop_chat_container lists with
        # all=True — so this is the one chance to read *why* before Docker's
        # record of it goes away. Discarding those logs is why a chat session
        # that quietly dies every few minutes has been undiagnosable: the run
        # is marked failed with a summary that only restates the symptom, and
        # each gap silently converts the chairman's own boardroom messages into
        # full-price CEO work runs (see dispatcher.chat_session_live).
        logs = sandbox.stop_chat_container(project.id)
        why = ("chat session container lost or mismatched; supervisor restarts it"
               + ("\n" + logs[-2000:] if logs else "\n(no container logs available)"))
        live_run.status = RunStatus.FAILED
        live_run.finished_at = utcnow()
        live_run.summary = why[:4000]
        say_session_broke(session, project, "its container stopped")
        log.warning("chat session for %s lost its container; restarting. Last logs:\n%s",
                    project.slug, logs[-2000:] or "(none)")
    elif running_ids:
        # Containers with no live run at all: zombies from a reaped session.
        log.warning("clearing orphaned chat container(s) for %s: %s",
                    project.slug, running_ids)
        sandbox.stop_chat_container(project.id)

    window_start = utcnow() - RESTART_WINDOW
    # Only this worker's involuntary starts count: KILLED runs are deliberate
    # ends (pause/resume, billing detach) and other workers own their own
    # windows — neither should be able to lock this box out of the chat lane.
    recent_starts = session.execute(
        select(func.count(Run.id)).where(Run.project_id == project.id,
                                         Run.kind == RunKind.CHAT_SESSION,
                                         Run.worker == settings().worker_name,
                                         Run.status != RunStatus.KILLED,
                                         Run.created_at >= window_start)
    ).scalar_one()
    if recent_starts >= MAX_STARTS_IN_WINDOW:
        return None, None  # boot-looping; hold until the window drains
    return _prepare_session(session, project, ceo)


def _end_session(session: Session, project: Project, live_run: Run | None,
                 why: str) -> None:
    logs = sandbox.stop_chat_container(project.id)
    if live_run is not None:
        live_run.status = RunStatus.KILLED
        live_run.token_revoked = True
        live_run.finished_at = utcnow()
        live_run.summary = (why + ("\n" + logs[-1000:] if logs else ""))[:4000]
    log.info("chat session for %s ended: %s", project.slug, why)


def say_session_broke(session: Session, project: Project, why: str) -> None:
    """Tell the boardroom when a session dies with the chairman mid-question.

    Every abnormal end wrote its forensics to `run.summary` and stopped there,
    so a chairman who had just asked something got silence — indistinguishable
    from a CEO still thinking, for as long as it took him to give up. Only when
    he is actually waiting: a routine restart between conversations is nobody's
    business and a room that announces its own plumbing stops being read.
    """
    ceo = policy.ceo_of(session, project.id)
    boardroom = session.execute(
        select(Channel).where(Channel.project_id == project.id,
                              Channel.kind == ChannelKind.BOARDROOM)
    ).scalars().first()
    if ceo is None or boardroom is None:
        return
    last_ceo, last_chairman = policy.boardroom_turns(session, boardroom.id, ceo.id)
    if last_chairman is None or (last_ceo is not None and last_ceo >= last_chairman):
        return  # nobody is waiting on an answer
    session.add(Message(
        id=ids.new_id(ids.MESSAGE), project_id=project.id, channel_id=boardroom.id,
        sender_agent_id=None, sender_user_id=None,
        body=(f"_The CEO's session ended before it could answer you ({why}). "
              "A new session is starting and will pick your message up — you do not "
              "need to repeat it._"),
        created_at=utcnow(),
    ))
    log.warning("boardroom left unanswered in %s: %s", project.slug, why)


def _prepare_session(session: Session, project: Project, ceo: Agent) -> tuple[dict, str]:
    """Everything a session start needs EXCEPT the container start: the run
    row, token, staged prompt, and launch kwargs. The caller starts the
    container after this transaction commits."""
    role_version = role_version_for(session, ceo)
    run = Run(
        id=ids.new_id(ids.RUN),
        project_id=project.id,
        agent_id=ceo.id,
        kind=RunKind.CHAT_SESSION,
        trigger={"type": "chat_session"},
        status=RunStatus.RUNNING,
        worker=settings().worker_name,
        started_at=utcnow(),
        heartbeat_at=utcnow(),
    )
    session.add(run)
    session.flush()
    token_plain, token_hash = mint()
    run.token_hash = token_hash

    job = RunJob(
        run_id=run.id,
        project_id=project.id,
        agent_id=ceo.id,
        kind=RunKind.CHAT_SESSION,
        trigger=run.trigger,
        task_id=None,
        attempt=1,
        api_base_url=settings().api_base_url,
        api_token=token_plain,
    )
    job.system_prompt = build_system_prompt(session, ceo, project, lane="chat") + CHAT_ADDENDUM
    job.model = ceo.model_override or (role_version.model if role_version else "claude-sonnet-5")
    job.effort = role_version.effort if role_version else "high"
    run.model = job.model
    run.effort = job.effort
    job.tool_permissions = dict(role_version.tool_permissions) if role_version else {}
    # Same sources, same cost lesson: the chat lane mounts them exactly as a run
    # does, so it gets the same paragraph describing them.
    job.system_prompt += _data_source_prompt(
        _merge_project_data_sources(session, project, role_version, job))
    job.egress_proxy_url = _role_egress_url(session, role_version, job.tool_permissions)
    job.billing_env = {**_billing_env(session, project),
                       **_role_secret_env(session, project, role_version)}

    # Chat gets its own scratch at /workspace: a run's staging must never race
    # a resident session's. It also gets the company's working tree read-only
    # at /company — without that the boardroom CEO could see nothing the
    # company had made, and reasoned from an empty directory out loud.
    workspace = Path(settings().workspaces_root) / f"{project.slug}-chat"
    staging = workspace / ".yaaf"
    staging.mkdir(parents=True, exist_ok=True)
    workspace.chmod(0o777)
    staging.chmod(0o777)
    company = Path(settings().workspaces_root) / project.slug
    company.mkdir(parents=True, exist_ok=True)
    (staging / "system_prompt.txt").write_text(job.system_prompt)
    # A resident session has no context.json — stash the prompt at start so the
    # chairman can see what the session was primed with. Best-effort.
    run.context_path = stash_context_doc(run.id, job.system_prompt, {
        "note": "resident chat session — the context is the boardroom transcript, "
                "bootstrapped from the last 30 messages (1k chars each)",
    })

    env = {
        "YAAF_RUN_ID": run.id,
        "YAAF_API_BASE_URL": job.api_base_url,
        "YAAF_API_TOKEN": token_plain,
        "YAAF_MODEL": job.model,
        "YAAF_EFFORT": job.effort,
        "YAAF_TOOL_PERMISSIONS": json.dumps(job.tool_permissions),
        **job.billing_env,
    }
    launch = dict(
        project_id=project.id,
        run_id=run.id,
        name=sandbox.chat_container_name(project.slug),
        workspace_dir=str(workspace),
        company_dir=str(company),
        env=env,
        proxy_url=job.egress_proxy_url,
    )
    log.info("chat session prepared for %s (%s)", project.slug, run.id)
    return launch, run.id
