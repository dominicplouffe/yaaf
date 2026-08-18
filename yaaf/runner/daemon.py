"""The runner daemon: leases jobs, executes them, survives their deaths.

Lease semantics: a queue row is claimed with FOR UPDATE SKIP LOCKED, the runs
row is created, and the queue row deleted — one transaction, so work is never
both queued and running, and two workers can never hold the same job. While a
job executes, a heartbeat thread keeps the lease fresh and watches for
revocation (kill switches, refusal caps); the janitor reaps anything whose
heartbeat goes stale.

Cost is booked even for killed and failed runs — a cost report that omits the
cost of producing it is wrong.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    LedgerEntry,
    Notice,
    NoticeKind,
    Project,
    ProjectStatus,
    Run,
    RunKind,
    RunQueue,
    RunStatus,
    Secret,
    Task,
    TaskStatus,
    utcnow,
)
from yaaf.core.tokens import mint
from yaaf.runner.backends import get_backend
from yaaf.runner.types import RunJob, RunResult
from yaaf.server import policy
from yaaf.server.contextpack import build_context_pack, build_system_prompt, role_version_for

log = logging.getLogger("yaaf.runner")

MAX_PARALLEL = 4


def main() -> None:  # pragma: no cover - process entrypoint
    # Imported here, not at module top: chat imports this module's helpers.
    from yaaf.runner.chat import chat_supervisor_loop

    logging.basicConfig(level=logging.INFO)
    pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL)
    log.info("runner %s starting", settings().worker_name)
    threading.Thread(target=_egress_watch_loop, daemon=True).start()
    threading.Thread(target=chat_supervisor_loop, daemon=True).start()
    while True:
        leased = lease_and_prepare(limit=MAX_PARALLEL)
        for job in leased:
            pool.submit(execute_job, job)
        if not leased:
            time.sleep(settings().runner_poll_s)


def egress_watch_once(session: Session, out_dir: Path, last_hash: str | None) -> str:
    """One tick of egress auto-apply: regenerate, and only on change write the
    files and reload the proxy. A config edit in the UI reaches squid within
    one tick — no CLI, no manual reload."""
    import hashlib
    import json

    from yaaf.server.egress import build

    _, files = build(session)
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    if digest == last_hash:
        return digest
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (out_dir / name).write_text(content)
    _reload_proxy()
    log.info("egress config applied; proxy reload requested")
    return digest


def _reload_proxy() -> None:
    container = settings().proxy_container
    if not container:
        return
    try:
        import docker

        proxy = docker.from_env().containers.get(container)
        # `squid -k reconfigure` signals the squid master directly. A container
        # HUP goes to PID 1, which in the stock ubuntu/squid image is a bash
        # entrypoint that never forwards signals — the reload silently dies
        # there (found live: role ports stayed closed for hours).
        code, output = proxy.exec_run(["squid", "-k", "reconfigure"])
        if code != 0:
            raise RuntimeError(f"squid -k reconfigure exited {code}: {output[:200]!r}")
    except Exception:
        log.warning("could not reload proxy container %r", container, exc_info=True)


def _egress_watch_loop() -> None:  # pragma: no cover - thin loop
    if not settings().proxy_generated_dir:
        return  # manual `yaaf egress-sync` mode (multi-box setups)
    out_dir = Path(settings().proxy_generated_dir)
    last: str | None = None
    while True:
        try:
            with db_session() as session:
                last = egress_watch_once(session, out_dir, last)
        except Exception:
            log.exception("egress watch tick failed")
        time.sleep(15)


# ── leasing ─────────────────────────────────────────────────────────────────────


def lease_and_prepare(limit: int = 1) -> list[RunJob]:
    """Claim up to `limit` queue rows and assemble everything their execution
    needs, in one transaction per row."""
    jobs: list[RunJob] = []
    with db_session() as session:
        now = utcnow()
        rows = list(
            session.execute(
                select(RunQueue)
                .where((RunQueue.not_before.is_(None)) | (RunQueue.not_before <= now))
                .order_by(RunQueue.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).scalars()
        )
        for queued in rows:
            project = session.get(Project, queued.project_id)
            if project is None or project.status != ProjectStatus.RUNNING:
                session.delete(queued)  # paused/stopped mid-queue: drop silently
                continue
            job = _materialize(session, queued, project)
            if job is not None:
                jobs.append(job)
            session.delete(queued)
    return jobs


def _materialize(session: Session, queued: RunQueue, project: Project) -> RunJob | None:
    cons = effective(session, project.id)
    run = Run(
        id=queued.id,
        project_id=project.id,
        agent_id=queued.agent_id,
        kind=queued.kind,
        trigger=queued.trigger,
        task_id=queued.task_id,
        attempt=queued.attempt,
        prior_run_id=queued.prior_run_id,
        status=RunStatus.RUNNING,
        worker=settings().worker_name,
        started_at=utcnow(),
        heartbeat_at=utcnow(),
    )
    session.add(run)
    session.flush()

    job = RunJob(
        run_id=run.id,
        project_id=project.id,
        agent_id=queued.agent_id,
        kind=queued.kind,
        trigger=queued.trigger or {},
        task_id=queued.task_id,
        attempt=queued.attempt,
        timeout_s=cons.run_timeout_s,
        api_base_url=settings().api_base_url,
        workspace_dir=_prepare_workspace(session, project),
    )

    if queued.kind == RunKind.SUCCESS_CHECK:
        task = session.get(Task, queued.task_id)
        if task is None or task.status != TaskStatus.CHECKING:
            run.status = RunStatus.FAILED
            run.summary = "success check job with no task in checking state"
            run.finished_at = utcnow()
            return None
        job.check_command = (task.success_test or {}).get("command", "")
        job.timeout_s = min(cons.run_timeout_s, 300)
        return job

    agent = session.get(Agent, queued.agent_id)
    if agent is None or agent.status != "active":
        run.status = RunStatus.FAILED
        run.summary = "agent missing or paused at lease time"
        run.finished_at = utcnow()
        return None

    token_plain, token_hash = mint()
    run.token_hash = token_hash
    job.api_token = token_plain
    job.system_prompt = build_system_prompt(session, agent, project)
    job.context_pack = build_context_pack(session, run, agent, project)

    role_version = role_version_for(session, agent)
    job.max_usd = cons.max_usd_per_run
    job.model = agent.model_override or (role_version.model if role_version else "claude-sonnet-5")
    job.effort = role_version.effort if role_version else "high"
    # Pinned on the run row at lease time, not finalize: reaped and killed runs
    # stay attributed to the model that actually ran.
    run.model = job.model
    run.effort = job.effort
    job.tool_permissions = dict(role_version.tool_permissions) if role_version else {}
    source_names = _merge_project_data_sources(session, project, role_version, job)
    job.egress_proxy_url = _role_egress_url(session, role_version, job.tool_permissions)
    role_secrets = _role_secret_env(session, project, role_version)
    job.billing_env = {**_billing_env(session, project), **role_secrets}
    if role_secrets:
        job.system_prompt += (
            "\n\nCREDENTIALS: these environment variables are set in your sandbox for your "
            f"role: {', '.join(sorted(role_secrets))}. Use them for their purpose; never "
            "print, log, or write them to files."
        )
    if source_names:
        job.system_prompt += (
            "\n\nDATA SOURCES: " + ", ".join(f"{n} (tools mcp__{n}__*)" for n in source_names)
            + " — MCP servers mounted for your role. If a listed source's tools are absent "
            "from your toolset, report that plainly instead of guessing at causes."
        )
    return job


def _merge_project_data_sources(session: Session, project: Project, role_version,
                                job: RunJob) -> list[str]:
    """Data sources are project-owned (config.mcp_servers), mounted for the
    roles they are assigned to — roles never declare their own servers. The
    mapping lives on the project and is applied at lease time, which is why
    connecting a source needs no role-library edit and no team repin. Returns
    the mounted source names."""
    # Pinned versions minted before data sources became project-owned may still
    # freeze a legacy mcp_servers key; it is dead weight, never mounted.
    job.tool_permissions.pop("mcp_servers", None)
    specs: list[dict] = []
    if role_version is not None:
        from yaaf.core.models import Role

        role = session.get(Role, role_version.role_id)
        hosts = list(job.tool_permissions.get("egress_allow", []))
        for source in (project.config or {}).get("mcp_servers", []):
            if role is None or role.slug not in source.get("roles", []):
                continue
            spec = {"name": source["name"],
                    "url_env": _env_var_name(source["url_secret"])}
            if source.get("bearer_secret"):
                spec["bearer_env"] = _env_var_name(source["bearer_secret"])
            specs.append(spec)
            host = source.get("url_host")
            if host and host not in hosts:
                hosts.append(host)  # routes this role via its scoped proxy port
        if specs:
            # New lists, never in-place: job.tool_permissions shallow-copies the
            # role version's dict and must not mutate its nested lists.
            job.tool_permissions = {**job.tool_permissions, "mcp_servers": specs,
                                    **({"egress_allow": hosts} if hosts else {})}
    return [s.get("name", "?") for s in specs]


def _prepare_workspace(session: Session, project: Project) -> str:
    """The job's workspace path. GitHub-backed projects get their repo cloned
    on first touch; after that (or on any failure) the workspace is exactly
    the plain local dir it always was — agents own the working tree, and a
    workspace must never hard-block a run."""
    workspace = Path(settings().workspaces_root) / project.slug
    if project.github_repo and not (workspace / ".git").exists():
        _clone_workspace(session, project.github_repo, workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    return str(workspace)


def _clone_workspace(session: Session, full_name: str, workspace: Path) -> None:
    """Clone with the platform "github" token, then scrub the credential from
    the remote so nothing under .git ever holds it — agents push with their
    own role-scoped secret. Failures log and degrade to the plain dir; log
    lines and exceptions are kept token-free (a TimeoutExpired repr would
    happily print the whole authenticated URL)."""
    import subprocess

    from yaaf.server import github

    token = github.platform_token(session)
    if token is None:
        log.warning("project repo %s configured but the platform 'github' secret is "
                    "missing; using a plain local dir", full_name)
        return
    workspace.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["git", "clone", github.clone_url(full_name, token), str(workspace)],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:
        _scrub_remote(workspace, full_name)  # a killed clone may have written .git
        log.warning("clone of %s failed (%s); using a plain local dir",
                    full_name, type(exc).__name__)
        return
    if proc.returncode != 0:
        _scrub_remote(workspace, full_name)
        log.warning("clone of %s failed; using a plain local dir: %s", full_name,
                    (proc.stderr or "").replace(token, "<redacted>").strip()[:500])
        return
    _scrub_remote(workspace, full_name)
    log.info("cloned %s into %s", full_name, workspace)


def _scrub_remote(workspace: Path, full_name: str) -> None:
    """Point origin back at the credential-free URL. If even that fails, the
    .git dir is removed outright — leaving the token behind is the one
    unacceptable outcome."""
    import shutil
    import subprocess

    from yaaf.server import github

    if not (workspace / ".git").exists():
        return
    try:
        subprocess.run(
            ["git", "-C", str(workspace), "remote", "set-url", "origin",
             github.public_url(full_name)],
            capture_output=True, timeout=30, check=True,
        )
    except Exception:
        shutil.rmtree(workspace / ".git", ignore_errors=True)
        log.warning("could not scrub the clone credential for %s; dropped .git", full_name)


def _role_egress_url(session: Session, role_version, tool_permissions: dict) -> str | None:
    """A role with its own egress_allow talks to its own squid port (a pure
    function of the role slug — see yaaf/server/egress.py). Everyone else uses
    the default port and the global allowlist."""
    if role_version is None or not tool_permissions.get("egress_allow"):
        return None
    from yaaf.core.models import Role
    from yaaf.server.egress import role_port

    role = session.get(Role, role_version.role_id)
    base = settings().egress_proxy_url
    if role is None or not base:
        return None
    host, _, port = base.rpartition(":")
    if not port.isdigit():
        host = base  # no explicit port in the configured URL
    return f"{host}:{role_port(role.slug)}"


def _env_var_name(secret_name: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9]+", "_", secret_name).strip("_").upper()


def _role_secret_env(session: Session, project: Project, role_version) -> dict:
    """Secrets whose role_scopes name this agent's role, decrypted and named as
    env vars. The grant lives on the SECRET (chairman-editable, effective on
    the next run) — never on the role version, which teams pin at creation.
    Empty role_scopes means injected nowhere: billing credentials and
    not-yet-granted secrets stay out of every sandbox."""
    if role_version is None:
        return {}
    from sqlalchemy import or_

    from yaaf.core.crypto import decrypt
    from yaaf.core.models import Role

    role = session.get(Role, role_version.role_id)
    if role is None:
        return {}
    candidates = session.execute(
        select(Secret)
        .where(or_(Secret.project_id == project.id, Secret.project_id.is_(None)))
        .order_by(Secret.project_id.is_(None))  # project-scoped wins over platform
    ).scalars()
    env: dict = {}
    for secret in candidates:
        if secret.kind == "billing":
            continue  # belt and suspenders: the API refuses grants on billing
            # secrets, but even a hand-edited DB row must not leak the model
            # credential into a role's env.
        if not secret.role_scopes or role.slug not in secret.role_scopes:
            continue
        var = _env_var_name(secret.name)
        if var in env:
            continue  # project-scoped already claimed this name
        try:
            env[var] = decrypt(secret.ciphertext)
        except Exception:
            log.warning("could not decrypt secret %s for role scope", secret.id)
    return env


def _billing_env(session: Session, project: Project) -> dict:
    if not project.billing_secret_id:
        return {}
    secret = session.get(Secret, project.billing_secret_id)
    if secret is None:
        return {}
    from yaaf.core.crypto import decrypt

    value = decrypt(secret.ciphertext)
    if project.billing_mode == "subscription":
        return {"CLAUDE_CODE_OAUTH_TOKEN": value}
    return {"ANTHROPIC_API_KEY": value}


# ── execution ───────────────────────────────────────────────────────────────────


def execute_job(job: RunJob, backend=None) -> RunResult:
    stop_heartbeat = threading.Event()
    revoked = threading.Event()
    beat = threading.Thread(
        target=_heartbeat, args=(job.run_id, stop_heartbeat, revoked), daemon=True
    )
    beat.start()
    try:
        if job.kind == RunKind.SUCCESS_CHECK:
            result = _run_success_check(job)
        else:
            backend = backend or _backend_for(job)
            # The revoked event reaches the sandbox layer so a kill switch,
            # pause, or budget trip stops the container, not just its token.
            result = backend.execute(job, stop_event=revoked)
    except Exception as exc:
        log.exception("job %s crashed in backend", job.run_id)
        result = RunResult(ok=False, summary=f"backend crashed: {exc!r}")
    finally:
        stop_heartbeat.set()
    finalize(job, result, was_revoked=revoked.is_set())
    return result


def _backend_for(job: RunJob):
    with db_session() as session:
        project = session.get(Project, job.project_id)
        runtime = (project.config or {}).get("runtime", "agent_sdk")
    return get_backend(runtime)


def _heartbeat(run_id: str, stop: threading.Event, revoked: threading.Event) -> None:
    interval = settings().lease_heartbeat_s
    while not stop.wait(interval):
        try:
            with db_session() as session:
                run = session.get(Run, run_id)
                if run is None:
                    return
                run.heartbeat_at = utcnow()
                if run.token_revoked:
                    revoked.set()
        except Exception:
            log.warning("heartbeat failed for %s", run_id, exc_info=True)


def finalize(job: RunJob, result: RunResult, was_revoked: bool = False) -> None:
    with db_session() as session:
        run = session.get(Run, job.run_id)
        if run is None or run.status not in (RunStatus.RUNNING,):
            return  # reaped or killed while we worked; the janitor won
        if was_revoked or run.token_revoked:
            run.status = (
                RunStatus.TERMINATED_REFUSALS if run.refusal_count else RunStatus.KILLED
            )
        else:
            run.status = RunStatus.DONE if result.ok else RunStatus.FAILED
        run.finished_at = utcnow()
        run.tokens_in = result.tokens_in
        run.tokens_out = result.tokens_out
        run.cache_creation_tokens = result.cache_creation_tokens
        run.cache_read_tokens = result.cache_read_tokens
        # Tokens always; dollars only when dollars move. Subscription runs are
        # a rate limit, not a bill — booking the SDK's list-price figure would
        # distort every budget decision (the budget gate for subscription
        # projects runs on monthly_token_cap instead).
        project = session.get(Project, job.project_id)
        subscription = project is not None and project.billing_mode == "subscription"
        run.cost_usd = None if subscription else result.cost_usd
        run.trace_path = _stash_trace(job) or result.trace_path
        run.context_path = _stash_context(job)
        _clear_staged_inputs(job)
        run.summary = (result.summary or "")[:4000]

        if (result.tokens_in or result.tokens_out or result.cache_creation_tokens
                or result.cache_read_tokens or run.cost_usd):
            session.add(
                LedgerEntry(
                    id=ids.new_id(ids.LEDGER),
                    project_id=job.project_id,
                    kind="model_usage",
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    cache_creation_tokens=result.cache_creation_tokens,
                    cache_read_tokens=result.cache_read_tokens,
                    usd=run.cost_usd,
                    run_id=run.id,
                    note=f"{run.kind} {run.status}"
                    + (" (subscription: tokens only)" if subscription else ""),
                )
            )

        if job.kind == RunKind.SUCCESS_CHECK:
            _finalize_success_check(session, job, result)
        elif run.status == RunStatus.FAILED:
            if project is not None and _looks_like_credential_failure(run.summary):
                _flag_credential_failure(session, project, run)
            _retry_failed_run(session, run)


CREDENTIAL_FAILURE_MARKERS = (
    "not logged in", "please run /login", "invalid x-api-key",
    "authentication_error", "invalid bearer token", "oauth token has expired",
    "credit balance is too low",
)


def _looks_like_credential_failure(summary: str | None) -> bool:
    lowered = (summary or "").lower()
    return any(marker in lowered for marker in CREDENTIAL_FAILURE_MARKERS)


def _flag_credential_failure(session: Session, project: Project, run: Run) -> None:
    """A dead billing credential fails every run it touches, and the scheduler
    would happily wake agents into it forever. So: audit every occurrence,
    chase the chairman at most once a day — and after three consecutive
    credential-failed runs, pause the project outright, exactly like budget
    exhaustion. A notification is not an enforcement mechanism; the pause is."""
    from datetime import timedelta

    from yaaf.core.models import AuditLog
    from yaaf.server.notify import chairman_notify

    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="runner",
        action="billing.credential_failure", entity="run", entity_id=run.id,
        detail={"summary": (run.summary or "")[:300]},
    ))
    recent = session.execute(
        select(Run)
        .where(Run.project_id == project.id, Run.kind == RunKind.AGENT_RUN,
               Run.finished_at.is_not(None))
        .order_by(Run.finished_at.desc())
        .limit(3)
    ).scalars().all()
    dead = len(recent) == 3 and all(
        r.status == RunStatus.FAILED and _looks_like_credential_failure(r.summary)
        for r in recent
    )
    if dead and project.status == ProjectStatus.RUNNING:
        project.status = ProjectStatus.PAUSED
        project.status_reason = ("billing credential invalid: 3 consecutive runs failed "
                                 "to authenticate")
        for queued in session.execute(
            select(RunQueue).where(RunQueue.project_id == project.id)
        ).scalars():
            session.delete(queued)
        chairman_notify(
            session, project, "project auto-paused: billing credential invalid",
            "Three consecutive runs failed to authenticate ('Not logged in' / invalid "
            "key). No run can succeed until the credential is fixed. Reconnect billing "
            "in Settings (the 'test' button verifies it), then resume the project.",
        )
        log.warning("project %s auto-paused: credential failures", project.slug)
        return
    from sqlalchemy import func

    day_ago = utcnow() - timedelta(hours=24)
    already = session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.project_id == project.id,
            AuditLog.action == "billing.credential_notify",
            AuditLog.at >= day_ago,
        )
    ).scalar_one()
    if not already:
        session.add(AuditLog(
            id=ids.new_id(ids.AUDIT), project_id=project.id, actor="runner",
            action="billing.credential_notify", entity="project", entity_id=project.id,
            detail={},
        ))
        chairman_notify(
            session, project, "a run failed to authenticate",
            "A run failed with a credential error. If this repeats twice more, the "
            "project will pause itself. Check Settings → billing (the 'test' button "
            "verifies the credential without burning a run).",
        )


def _stash_trace(job: RunJob) -> str | None:
    """Copy the run's trace out of the (ephemeral, runner-only) workspace into
    the artifact store, where the API can serve it. Best-effort: a missing
    trace never fails a finalize."""
    from yaaf.core.artifacts import store

    try:
        src = Path(job.workspace_dir) / ".yaaf" / f"trace-{job.run_id}.jsonl"
        if not src.exists():
            return None
        return store().put_bytes(f"traces/{job.run_id}.jsonl", src.read_bytes())
    except Exception:
        log.warning("could not stash trace for %s", job.run_id, exc_info=True)
        return None


def _clear_staged_inputs(job: RunJob) -> None:
    """Drop this run's staged prompt and pack once they are stashed. Run-keyed
    names mean they no longer overwrite each other — so without this they
    accumulate one pair per run in a workspace that lives as long as the
    project. Best-effort: never fails a finalize."""
    staging = Path(job.workspace_dir) / ".yaaf"
    for name in (f"system_prompt-{job.run_id}.txt", f"context-{job.run_id}.json"):
        try:
            (staging / name).unlink(missing_ok=True)
        except Exception:
            log.warning("could not clear staged input %s for %s", name, job.run_id)


def _stash_context(job: RunJob) -> str | None:
    """Stash what the run actually sent to the model — the staged system prompt
    and context pack — so the chairman can audit a run's inputs, not just its
    outputs. Best-effort, like _stash_trace: never fails a finalize."""
    import json

    try:
        staging = Path(job.workspace_dir) / ".yaaf"
        # Run-keyed, and deliberately with NO fallback to the old shared names.
        # A run that staged nothing must stash nothing: success checks never
        # stage a pack at all, so any fallback hands them whichever file was
        # lying in a workspace every agent on the project shares — which is how
        # a check run came to be recorded against another agent's context from
        # the day before. An empty audit record is honest; a confident wrong
        # one is worse than none.
        prompt_file = staging / f"system_prompt-{job.run_id}.txt"
        context_file = staging / f"context-{job.run_id}.json"
        system_prompt = prompt_file.read_text() if prompt_file.exists() else None
        context_pack = (json.loads(context_file.read_text())
                        if context_file.exists() else None)
        if system_prompt is None and context_pack is None:
            return None
        return stash_context_doc(job.run_id, system_prompt, context_pack)
    except Exception:
        log.warning("could not stash context for %s", job.run_id, exc_info=True)
        return None


def stash_context_doc(run_id: str, system_prompt: str | None,
                      context_pack: dict | None) -> str | None:
    """Write the {"system_prompt", "context_pack"} document to the artifact
    store as ``contexts/{run_id}.json``. Shared with the chat supervisor, whose
    context is not a staged file. Returns the store uri, or None on failure."""
    import json

    from yaaf.core.artifacts import store

    try:
        doc = json.dumps({"system_prompt": system_prompt, "context_pack": context_pack})
        return store().put_bytes(f"contexts/{run_id}.json", doc.encode())
    except Exception:
        log.warning("could not stash context for %s", run_id, exc_info=True)
        return None


MAX_RUN_ATTEMPTS = 3


def _retry_failed_run(session: Session, run: Run) -> None:
    """A failed run consumed its trigger (notices marked read, suggestions
    marked read, message cursors advanced) without a model ever acting on it.
    Give them back and requeue the wake with backoff; the retry's context pack
    re-includes recent messages (see contextpack) so nothing said gets silently
    eaten."""
    from datetime import timedelta

    from yaaf.core.models import Notice, Suggestion

    for notice in session.execute(
        select(Notice).where(Notice.consumed_by_run_id == run.id)
    ).scalars():
        notice.consumed_at = None
        notice.consumed_by_run_id = None

    # Suggestions are consumed by pack construction the same way notices are,
    # and are an employee's ONLY authority over the backlog. Losing one to a
    # crash silently deletes the single thing a non-CEO agent may say about
    # what should be worked on.
    for suggestion in session.execute(
        select(Suggestion).where(Suggestion.consumed_by_run_id == run.id)
    ).scalars():
        suggestion.consumed_at = None
        suggestion.consumed_by_run_id = None

    if run.attempt >= MAX_RUN_ATTEMPTS:
        log.warning("run %s failed at attempt %s; giving up", run.id, run.attempt)
        return
    session.add(
        RunQueue(
            id=ids.new_id(ids.RUN),
            project_id=run.project_id,
            agent_id=run.agent_id,
            kind=run.kind,
            trigger=run.trigger,
            task_id=run.task_id,
            attempt=run.attempt + 1,
            prior_run_id=run.id,
            not_before=utcnow() + timedelta(seconds=60 * run.attempt),
        )
    )


# How many prior attempts an agent is shown, and how much of each. Enough to
# see a pattern in what it has already tried; small enough that a task row
# stays a task row.
CHECK_HISTORY_KEEP = 5
CHECK_HISTORY_EVIDENCE_CHARS = 300
CHECK_HISTORY_OUTPUT_CHARS = 500


def _record_check_attempt(task: Task, output: str) -> None:
    """Append this failed attempt to the task's rolling history.

    The *evidence* half is the point: it is the claimant's own account of what
    it changed, and it is the only thing that tells a later run "you already
    tried that". Without it an agent sees one failure at a time and can cycle
    between two rejected fixes forever."""
    history = list(task.check_history or [])
    history.append({
        "at": utcnow().isoformat(),
        "evidence": (task.completion_evidence or "")[:CHECK_HISTORY_EVIDENCE_CHARS],
        "output": output[:CHECK_HISTORY_OUTPUT_CHARS],
    })
    task.check_history = history[-CHECK_HISTORY_KEEP:]


def _finalize_success_check(session: Session, job: RunJob, result: RunResult) -> None:
    task = session.get(Task, job.task_id)
    if task is None or task.status != TaskStatus.CHECKING:
        return
    output = (result.check_output or "")[:4000]
    task.check_output = output
    if result.check_exit_code == 0:
        from_status = task.status
        task.status = TaskStatus.DONE
        task.check_failures = 0
        task.check_history = None
        policy.record_task_event(session, task, TaskStatus.DONE, "checker",
                                 note="success test passed", from_status=from_status)
        return

    task.check_failures = (task.check_failures or 0) + 1
    _record_check_attempt(task, output)
    try:
        cap = effective(session, task.project_id).task_check_failures_before_block
    except LookupError:  # no constitution: never worse than the old behaviour
        cap = 0
    from_status = task.status

    if cap and task.check_failures >= cap:
        _block_on_repeated_check_failure(session, task, result, output, from_status, cap)
        return

    task.status = TaskStatus.CLAIMED  # back to the claimant with the output
    policy.record_task_event(
        session, task, TaskStatus.CLAIMED, "checker",
        note=f"success test FAILED (exit {result.check_exit_code}), "
             f"attempt {task.check_failures}",
        from_status=from_status,
    )
    if task.claimed_by_agent_id:
        remaining = cap - task.check_failures if cap else None
        session.add(
            Notice(
                id=ids.new_id(ids.NOTICE),
                project_id=task.project_id,
                agent_id=task.claimed_by_agent_id,
                kind=NoticeKind.TASK_CHECK_FAILED,
                body=(
                    f"ATTEMPT {task.check_failures}"
                    + (f" OF {cap}" if cap else "")
                    + f": the platform ran your task's success test and it FAILED "
                      f"(exit {result.check_exit_code}). The task is claimed by you again."
                    + (f" {remaining} attempt(s) left before it blocks and goes back to "
                       "whoever planned it." if remaining else "")
                    + " Read `task.previous_attempts` in your context before changing "
                      "anything: if you have already tried a fix and it failed, trying it "
                      "again will fail again. If the same rule keeps rejecting materially "
                      "different submissions, the check itself is the likely fault — block "
                      "the task and say so rather than guessing another edit."
                    + f"\nOutput:\n{output[:1500]}"
                ),
            )
        )


def _block_on_repeated_check_failure(
    session: Session, task: Task, result: RunResult, output: str,
    from_status: str, cap: int,
) -> None:
    """The loop breaker. A task that cannot pass its own success test is not a
    task the claimant can finish by trying harder — it is a question for
    whoever planned it, and on a repeat, for the chairman."""
    from yaaf.server.notify import chairman_notify

    reason = (
        f"Success test failed {task.check_failures} times running (exit "
        f"{result.check_exit_code}); the platform stopped re-waking the claimant. "
        f"Last output:\n{output[:1500]}"
    )
    task.status = TaskStatus.BLOCKED
    task.blocked_reason = reason
    claimant_id = task.claimed_by_agent_id
    task.claimed_by_agent_id = None
    task.claim_run_id = None
    policy.record_task_event(
        session, task, TaskStatus.BLOCKED, "checker",
        note=f"blocked after {task.check_failures} failed checks (cap {cap})",
        from_status=from_status,
    )

    # Has this exact check killed a task before? If so the checker is the
    # suspect, and the planner is not the right person to hear it twice: its
    # only lever is kill-and-re-issue, which mints a task with a fresh counter.
    prior = policy.tasks_dead_on_check(session, task.project_id, task.success_test,
                                       exclude_task_id=task.id)
    planner = policy.planner_of(session, task)
    if planner is not None and planner.id != claimant_id:
        session.add(
            Notice(
                id=ids.new_id(ids.NOTICE),
                project_id=task.project_id,
                agent_id=planner.id,
                kind=NoticeKind.TASK_BLOCKED,
                body=(
                    f"Task '{task.title}' ({task.id}) was BLOCKED by the platform: its "
                    f"success test failed {task.check_failures} times running. Nobody is "
                    "working it now. Re-plan it: change it, kill it, or fix the check. "
                    + (f"NOTE: {len(prior)} earlier task(s) also died on this exact "
                       "success test and none ever passed it — re-issuing it unchanged "
                       "will produce this same message again. The check is the suspect, "
                       "not the deliverable. The chairman has been told.\n"
                       if prior else "")
                    + f"\n{reason}"
                ),
            )
        )
    project = session.get(Project, task.project_id)
    if prior and project is not None:
        chairman_notify(
            session, project,
            "a success test has now killed two tasks",
            f"Task {task.id} blocked after {task.check_failures} failed checks, and "
            f"{len(prior)} earlier task(s) died on the same success test without ever "
            f"passing it ({', '.join(t.id for t in prior[:5])}). Further tasks carrying "
            f"this check are refused until you intervene.\n\n"
            f"Check: {(task.success_test or {}).get('command', '')[:500]}\n\n{reason}",
        )


def _run_success_check(job: RunJob) -> RunResult:
    """Execute a command-type success test. The agent never self-reports this;
    the platform runs it and reads the exit code."""
    from yaaf.runner.sandbox import run_check_command

    exit_code, output = run_check_command(
        command=job.check_command or "",
        workspace_dir=job.workspace_dir,
        timeout_s=job.timeout_s,
    )
    return RunResult(
        ok=True,
        summary=f"success check exit={exit_code}",
        check_exit_code=exit_code,
        check_output=output,
    )
