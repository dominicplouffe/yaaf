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
        workspace_dir=str(Path(settings().workspaces_root) / project.slug),
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
    job.billing_env = _billing_env(session, project)
    return job


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
            result = backend.execute(job)
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
        run.trace_path = result.trace_path
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
