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
    logging.basicConfig(level=logging.INFO)
    pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL)
    log.info("runner %s starting", settings().worker_name)
    while True:
        leased = lease_and_prepare(limit=MAX_PARALLEL)
        for job in leased:
            pool.submit(execute_job, job)
        if not leased:
            time.sleep(settings().runner_poll_s)


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
    job.model = agent.model_override or (role_version.model if role_version else "claude-sonnet-5")
    job.effort = role_version.effort if role_version else "high"
    job.tool_permissions = dict(role_version.tool_permissions) if role_version else {}
    job.egress_proxy_url = _role_egress_url(session, role_version, job.tool_permissions)
    role_secrets = _role_secret_env(session, project, role_version)
    job.billing_env = {**_billing_env(session, project), **role_secrets}
    if role_secrets:
        job.system_prompt += (
            "\n\nCREDENTIALS: these environment variables are set in your sandbox for your "
            f"role: {', '.join(sorted(role_secrets))}. Use them for their purpose; never "
            "print, log, or write them to files."
        )
    return job


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
        # Tokens always; dollars only when dollars move. Subscription runs are
        # a rate limit, not a bill — booking the SDK's list-price figure would
        # distort every budget decision (the budget gate for subscription
        # projects runs on monthly_token_cap instead).
        project = session.get(Project, job.project_id)
        subscription = project is not None and project.billing_mode == "subscription"
        run.cost_usd = None if subscription else result.cost_usd
        run.trace_path = _stash_trace(job) or result.trace_path
        run.summary = (result.summary or "")[:4000]

        if result.tokens_in or result.tokens_out or run.cost_usd:
            session.add(
                LedgerEntry(
                    id=ids.new_id(ids.LEDGER),
                    project_id=job.project_id,
                    kind="model_usage",
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    usd=run.cost_usd,
                    run_id=run.id,
                    note=f"{run.kind} {run.status}"
                    + (" (subscription: tokens only)" if subscription else ""),
                )
            )

        if job.kind == RunKind.SUCCESS_CHECK:
            _finalize_success_check(session, job, result)
        elif run.status == RunStatus.FAILED:
            _retry_failed_run(session, run)


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


MAX_RUN_ATTEMPTS = 3


def _retry_failed_run(session: Session, run: Run) -> None:
    """A failed run consumed its trigger (notices marked read, message cursors
    advanced) without a model ever acting on it. Give the notices back and
    requeue the wake with backoff; the retry's context pack re-includes recent
    messages (see contextpack) so nothing said gets silently eaten."""
    from datetime import timedelta

    from yaaf.core.models import Notice

    for notice in session.execute(
        select(Notice).where(Notice.consumed_by_run_id == run.id)
    ).scalars():
        notice.consumed_at = None
        notice.consumed_by_run_id = None

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


def _finalize_success_check(session: Session, job: RunJob, result: RunResult) -> None:
    task = session.get(Task, job.task_id)
    if task is None or task.status != TaskStatus.CHECKING:
        return
    output = (result.check_output or "")[:4000]
    task.check_output = output
    if result.check_exit_code == 0:
        from_status = task.status
        task.status = TaskStatus.DONE
        policy.record_task_event(session, task, TaskStatus.DONE, "checker",
                                 note="success test passed", from_status=from_status)
    else:
        from_status = task.status
        task.status = TaskStatus.CLAIMED  # back to the claimant with the output
        policy.record_task_event(
            session, task, TaskStatus.CLAIMED, "checker",
            note=f"success test FAILED (exit {result.check_exit_code})",
            from_status=from_status,
        )
        if task.claimed_by_agent_id:
            session.add(
                Notice(
                    id=ids.new_id(ids.NOTICE),
                    project_id=task.project_id,
                    agent_id=task.claimed_by_agent_id,
                    kind=NoticeKind.TASK_CHECK_FAILED,
                    body=(
                        f"The platform ran your task's success test and it FAILED "
                        f"(exit {result.check_exit_code}). The task is claimed by you again. "
                        f"Output:\n{output[:1500]}"
                    ),
                )
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
