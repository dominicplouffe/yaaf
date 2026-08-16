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
    _merge_project_data_sources,
    _role_egress_url,
    _role_secret_env,
)
from yaaf.runner.types import RunJob
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
    return bool(((project.config or {}).get("chat") or {}).get("enabled", True))


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
        for project in session.execute(select(Project)).scalars():
            try:
                _supervise_project(session, project)
            except Exception:
                log.warning("chat supervision failed for %s", project.slug, exc_info=True)


def _supervise_project(session: Session, project: Project) -> None:
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
        return

    ceo = session.execute(
        select(Agent).where(Agent.project_id == project.id,
                            Agent.kind == AgentKind.CEO,
                            Agent.status == AgentStatus.ACTIVE)
    ).scalars().first()
    if ceo is None:
        return

    if live_run is not None:
        if live_run.token_revoked:
            _end_session(session, project, live_run, "chat session ended: token revoked")
            return
        # Liveness means THE container for THIS run — a running container whose
        # run died (deploy-time reap race) is a zombie polling with a dead
        # token, and adopting it mutes the boardroom entirely: the run row
        # heartbeat suppresses the fallback wakes while nobody actually reads.
        if running_ids == {live_run.id}:
            live_run.heartbeat_at = utcnow()  # the supervisor tick is the lease heartbeat
            return
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
        log.warning("chat session for %s lost its container; restarting. Last logs:\n%s",
                    project.slug, logs[-2000:] or "(none)")
    elif running_ids:
        # Containers with no live run at all: zombies from a reaped session.
        log.warning("clearing orphaned chat container(s) for %s: %s",
                    project.slug, running_ids)
        sandbox.stop_chat_container(project.id)

    window_start = utcnow() - RESTART_WINDOW
    recent_starts = session.execute(
        select(func.count(Run.id)).where(Run.project_id == project.id,
                                         Run.kind == RunKind.CHAT_SESSION,
                                         Run.created_at >= window_start)
    ).scalar_one()
    if recent_starts >= MAX_STARTS_IN_WINDOW:
        return  # boot-looping; hold until the window drains
    _start_session(session, project, ceo)


def _end_session(session: Session, project: Project, live_run: Run | None,
                 why: str) -> None:
    logs = sandbox.stop_chat_container(project.id)
    if live_run is not None:
        live_run.status = RunStatus.KILLED
        live_run.token_revoked = True
        live_run.finished_at = utcnow()
        live_run.summary = (why + ("\n" + logs[-1000:] if logs else ""))[:4000]
    log.info("chat session for %s ended: %s", project.slug, why)


def _start_session(session: Session, project: Project, ceo: Agent) -> None:
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
    job.system_prompt = build_system_prompt(session, ceo, project) + CHAT_ADDENDUM
    job.model = ceo.model_override or (role_version.model if role_version else "claude-sonnet-5")
    job.effort = role_version.effort if role_version else "high"
    run.model = job.model
    run.effort = job.effort
    job.tool_permissions = dict(role_version.tool_permissions) if role_version else {}
    _merge_project_data_sources(session, project, role_version, job)
    job.egress_proxy_url = _role_egress_url(session, role_version, job.tool_permissions)
    job.billing_env = {**_billing_env(session, project),
                       **_role_secret_env(session, project, role_version)}

    # Chat gets its own workspace: a run's staging must never race a resident
    # session's, and the session never needs the project's working tree.
    workspace = Path(settings().workspaces_root) / f"{project.slug}-chat"
    staging = workspace / ".yaaf"
    staging.mkdir(parents=True, exist_ok=True)
    workspace.chmod(0o777)
    staging.chmod(0o777)
    (staging / "system_prompt.txt").write_text(job.system_prompt)

    env = {
        "YAAF_RUN_ID": run.id,
        "YAAF_API_BASE_URL": job.api_base_url,
        "YAAF_API_TOKEN": token_plain,
        "YAAF_MODEL": job.model,
        "YAAF_EFFORT": job.effort,
        "YAAF_TOOL_PERMISSIONS": json.dumps(job.tool_permissions),
        **job.billing_env,
    }
    sandbox.start_chat_container(
        project_id=project.id,
        run_id=run.id,
        name=f"yaaf-chat-{project.slug}",
        workspace_dir=str(workspace),
        env=env,
        proxy_url=job.egress_proxy_url,
    )
    log.info("chat session started for %s (%s)", project.slug, run.id)
