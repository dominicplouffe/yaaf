"""The janitor: the automatic state changes the constitution promises.

Everything here is enforcement-by-state-change, not notification:

- stale leases are reaped and their task claims released (lana lost 5.8 hours
  of two agents to one un-reaped claim)
- hypotheses expire AT their kill date; their open tasks are blocked
- pending approvals expire to DENIED, and the requester is woken to re-plan
- budgets: 80% warns, 100% auto-pauses the project and revokes live runs
- amendments past cooling-off are applied and queued for CEO disclosure
- long-dead claims (no run, no subtasks) are released back to ready
- pending external evidence is verified

Notices produced here are information *about* an enforcement that already
happened — never the mechanism itself.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.constitution import ConstitutionDoc, apply_due_amendments, effective
from yaaf.core.db import db_session
from yaaf.core.models import (
    Approval,
    ApprovalStatus,
    Hypothesis,
    HypothesisStatus,
    LedgerEntry,
    Notice,
    NoticeKind,
    Project,
    ProjectStatus,
    Run,
    RunKind,
    RunQueue,
    RunStatus,
    Task,
    TaskStatus,
    utcnow,
)
from yaaf.server import policy
from yaaf.server.notify import chairman_notify
from yaaf.server.verifier import verify_pending_evidence

log = logging.getLogger("yaaf.janitor")


def janitor_loop() -> None:  # pragma: no cover - thin loop around janitor_once
    while True:
        try:
            with db_session() as session:
                janitor_once(session)
        except Exception:
            log.exception("janitor tick failed")
        time.sleep(settings().janitor_poll_s)


def janitor_once(session: Session) -> None:
    for project in session.execute(select(Project)).scalars():
        applied = apply_due_amendments(session, project.id)
        for amendment in applied:
            _notice_ceo(
                session, project,
                NoticeKind.CONSTITUTION_AMENDED,
                f"The chairman amended the constitution (reason: {amendment.reason}). "
                f"Changes now in force: {amendment.changes}. Plan accordingly.",
            )
        if project.status != ProjectStatus.RUNNING:
            continue
        try:
            cons = effective(session, project.id)
        except LookupError:
            continue
        reap_stale_runs(session, project, cons)
        expire_hypotheses(session, project)
        nag_approvals(session, project, cons)
        expire_approvals(session, project, cons)
        enforce_budget(session, project, cons)
        release_dead_claims(session, project, cons)
        warn_drought(session, project, cons)
    verify_pending_evidence(session)
    # Slack mirroring moved to its own 2s loop (slack.socket.fast_mirror_loop):
    # chat-grade latency, and no double-post race with an enforcement tick.


def _notice_ceo(session: Session, project: Project, kind: str, body: str) -> None:
    ceo = policy.ceo_of(session, project.id)
    if ceo is None:
        return
    session.add(
        Notice(id=ids.new_id(ids.NOTICE), project_id=project.id, agent_id=ceo.id,
               kind=kind, body=body)
    )


def _notice_agent(session: Session, project: Project, agent_id: str, kind: str, body: str) -> None:
    session.add(
        Notice(id=ids.new_id(ids.NOTICE), project_id=project.id, agent_id=agent_id,
               kind=kind, body=body)
    )


# ── reaping ─────────────────────────────────────────────────────────────────────


def reap_stale_runs(session: Session, project: Project, cons: ConstitutionDoc) -> int:
    """A run whose lease heartbeat went stale is dead. Mark it reaped, release
    its task claim, and requeue the work once. Side effects are never erased —
    the retry's context pack carries them."""
    now = utcnow()
    cutoff = now - timedelta(seconds=settings().lease_ttl_s)
    hard_cutoff = now - timedelta(seconds=cons.run_timeout_s)
    stale = list(
        session.execute(
            select(Run).where(
                Run.project_id == project.id,
                Run.status == RunStatus.RUNNING,
                (
                    (Run.heartbeat_at.is_(None) & (Run.created_at <= cutoff))
                    | (Run.heartbeat_at <= cutoff)
                    # Chat sessions are resident by design: a long life is
                    # normal, only a stale heartbeat means death.
                    | ((Run.started_at <= hard_cutoff) & (Run.kind != RunKind.CHAT_SESSION))
                ),
            )
        ).scalars()
    )
    for run in stale:
        run.status = RunStatus.REAPED
        run.token_revoked = True
        run.finished_at = now
        _release_claims_of_run(session, run.id, "run reaped: lease expired")
        if run.agent_id is not None and run.kind != RunKind.CHAT_SESSION:
            # A dead chat session is restarted quietly by its supervisor — a
            # "your run died, re-check your work" notice would only confuse.
            _notice_agent(
                session, project, run.agent_id, NoticeKind.AGENT_RUN_REAPED,
                f"Your previous run {run.id} died mid-flight and was reaped. Any task it "
                "held is ready again. Check the side-effects ledger in your context before "
                "redoing anything external — what fired, stays fired.",
            )
        log.warning("reaped run %s (project %s)", run.id, project.slug)
    return len(stale)


def _release_claims_of_run(session: Session, run_id: str, note: str) -> None:
    for task in session.execute(
        select(Task).where(Task.claim_run_id == run_id, Task.status == TaskStatus.CLAIMED)
    ).scalars():
        open_subtasks = session.execute(
            select(func.count(Task.id)).where(
                Task.parent_task_id == task.id, Task.status.in_(policy.OPEN_TASK_STATUSES)
            )
        ).scalar_one()
        if open_subtasks:
            continue  # a lead's decomposed task legitimately outlives its claiming run
        from_status = task.status
        task.status = TaskStatus.READY
        task.claimed_by_agent_id = None
        task.claim_run_id = None
        policy.record_task_event(session, task, TaskStatus.READY, "janitor", note=note,
                                 from_status=from_status)


def release_dead_claims(session: Session, project: Project, cons: ConstitutionDoc) -> int:
    """A claim with no live run behind it and no open subtasks, held for more
    than 2x the run timeout, goes back to ready."""
    cutoff = utcnow() - timedelta(seconds=2 * cons.run_timeout_s)
    released = 0
    for task in session.execute(
        select(Task).where(
            Task.project_id == project.id,
            Task.status == TaskStatus.CLAIMED,
            Task.claimed_at <= cutoff,
        )
    ).scalars():
        live = session.execute(
            select(func.count(Run.id)).where(
                Run.agent_id == task.claimed_by_agent_id, Run.status == RunStatus.RUNNING
            )
        ).scalar_one()
        open_subtasks = session.execute(
            select(func.count(Task.id)).where(
                Task.parent_task_id == task.id, Task.status.in_(policy.OPEN_TASK_STATUSES)
            )
        ).scalar_one()
        if live or open_subtasks:
            continue
        from_status = task.status
        task.status = TaskStatus.READY
        task.claimed_by_agent_id = None
        task.claim_run_id = None
        policy.record_task_event(session, task, TaskStatus.READY, "janitor",
                                 note="claim expired with no active run",
                                 from_status=from_status)
        released += 1
    return released


# ── expiries ────────────────────────────────────────────────────────────────────


def expire_hypotheses(session: Session, project: Project) -> int:
    """Kill dates are automatic. The CEO does not get to let one slide by; the
    chairman may extend, once, through the admin API."""
    now = utcnow()
    expired = 0
    for hypothesis in session.execute(
        select(Hypothesis).where(
            Hypothesis.project_id == project.id,
            Hypothesis.status == HypothesisStatus.ACTIVE,
            Hypothesis.kill_date <= now,
        )
    ).scalars():
        hypothesis.status = HypothesisStatus.EXPIRED
        blocked = 0
        for task in session.execute(
            select(Task).where(
                Task.hypothesis_id == hypothesis.id,
                Task.status.in_([TaskStatus.READY, TaskStatus.CLAIMED]),
            )
        ).scalars():
            from_status = task.status
            task.status = TaskStatus.BLOCKED
            task.blocked_reason = "hypothesis_expired"
            task.claimed_by_agent_id = None
            policy.record_task_event(session, task, TaskStatus.BLOCKED, "janitor",
                                     note="hypothesis_expired", from_status=from_status)
            blocked += 1
        _notice_ceo(
            session, project, NoticeKind.HYPOTHESIS_EXPIRED,
            f"Hypothesis '{hypothesis.statement[:120]}' hit its kill date and EXPIRED; "
            f"{blocked} task(s) are blocked. Kill it and pivot, or make the case to the "
            "chairman — only the chairman can extend, exactly once. Silence is not an "
            "extension.",
        )
        expired += 1
    return expired


APPROVAL_NAG_HOURS = 48  # how far before expiry the chairman gets chased


def nag_approvals(session: Session, project: Project, cons: ConstitutionDoc) -> int:
    """Chase the chairman once per approval as expiry approaches. The nag is
    transport, not enforcement — expiry itself still lands as the constitution
    says, answered or not."""
    horizon = utcnow() + timedelta(hours=APPROVAL_NAG_HOURS)
    nagged = 0
    for approval in session.execute(
        select(Approval).where(
            Approval.project_id == project.id,
            Approval.status == ApprovalStatus.PENDING,
            Approval.expires_at <= horizon,
            Approval.nag_sent_at.is_(None),
        )
    ).scalars():
        chairman_notify(
            session, project,
            f"approval expiring: {approval.title}",
            f"'{approval.title}' expires at {approval.expires_at:%Y-%m-%d %H:%M} UTC and "
            f"the timeout decision is {cons.approval_timeout_decision.upper()}. "
            f"Decide it in the approvals inbox.\n\n{approval.body[:2000]}",
        )
        approval.nag_sent_at = utcnow()
        nagged += 1
    return nagged


def expire_approvals(session: Session, project: Project, cons: ConstitutionDoc) -> int:
    now = utcnow()
    expired = 0
    approve_on_timeout = cons.approval_timeout_decision == "approve"
    for approval in session.execute(
        select(Approval).where(
            Approval.project_id == project.id,
            Approval.status == ApprovalStatus.PENDING,
            Approval.expires_at <= now,
        )
    ).scalars():
        if approve_on_timeout:
            approval.status = ApprovalStatus.APPROVED
            approval.decision_note = (
                "expired: no chairman decision in time; this constitution auto-APPROVES on timeout"
            )
        else:
            approval.status = ApprovalStatus.EXPIRED
            approval.decision_note = "expired: no chairman decision in time; default is deny"
        approval.decided_at = now
        _notice_agent(
            session, project, approval.requested_by_agent_id, NoticeKind.APPROVAL_EXPIRED,
            f"Approval '{approval.title}' expired unanswered and is "
            f"{'APPROVED per this constitution' if approve_on_timeout else 'DENIED by default'}. "
            + ("Proceed accordingly." if approve_on_timeout
               else "Re-plan without it, or resubmit with a stronger recommendation."),
        )
        expired += 1
    return expired


# ── budgets ─────────────────────────────────────────────────────────────────────


def month_to_date(session: Session, project_id: str) -> tuple[float, int]:
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    usd, tokens = session.execute(
        select(
            func.coalesce(func.sum(LedgerEntry.usd), 0.0),
            # Raw unweighted sum, cache tokens included: subscription mode is a
            # rate limit, not a bill, and rate accounting counts cache reads as
            # input. Weighting them would smuggle price knowledge into a mode
            # that deliberately has none.
            func.coalesce(
                func.sum(LedgerEntry.tokens_in + LedgerEntry.tokens_out
                         + LedgerEntry.cache_creation_tokens
                         + LedgerEntry.cache_read_tokens),
                0,
            ),
        ).where(LedgerEntry.project_id == project_id, LedgerEntry.at >= month_start)
    ).one()
    return float(usd), int(tokens)


def enforce_budget(session: Session, project: Project, cons: ConstitutionDoc) -> None:
    usd, tokens = month_to_date(session, project.id)
    if project.billing_mode == "api_key":
        spent, cap, unit = usd, cons.monthly_usd_cap, "USD"
    else:
        spent, cap, unit = float(tokens), float(cons.monthly_token_cap), "tokens"
    if cap <= 0:
        return
    if spent >= cap:
        project.status = ProjectStatus.PAUSED
        project.status_reason = f"budget exhausted: {spent:,.0f}/{cap:,.0f} {unit} this month"
        for run in session.execute(
            select(Run).where(Run.project_id == project.id, Run.status == RunStatus.RUNNING)
        ).scalars():
            run.token_revoked = True
        for queued in session.execute(
            select(RunQueue).where(RunQueue.project_id == project.id)
        ).scalars():
            session.delete(queued)
        _notice_ceo(
            session, project, NoticeKind.BUDGET_EXHAUSTED,
            f"The monthly budget is exhausted ({spent:,.0f}/{cap:,.0f} {unit}); the platform "
            "paused the project and the chairman was informed. When resumed, cut burn first.",
        )
        chairman_notify(
            session, project, "project auto-paused: budget exhausted",
            f"{spent:,.0f}/{cap:,.0f} {unit} spent this month. Live runs were revoked and "
            "the queue purged. Resume from the project page when you have decided what to cut.",
        )
        log.warning("project %s auto-paused: budget exhausted", project.slug)
    elif spent >= cap * cons.budget_warn_ratio:
        month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        already = session.execute(
            select(func.count(Notice.id)).where(
                Notice.project_id == project.id,
                Notice.kind == NoticeKind.BUDGET_WARNING,
                Notice.created_at >= month_start,
            )
        ).scalar_one()
        if not already:
            _notice_ceo(
                session, project, NoticeKind.BUDGET_WARNING,
                f"{cons.budget_warn_ratio:.0%} of the monthly budget is spent "
                f"({spent:,.0f}/{cap:,.0f} {unit}). Decide what you are cutting and tell "
                "the chairman the number — before the platform pauses the project for you.",
            )


# ── drought (informational; enforcement is at task creation) ───────────────────


def warn_drought(session: Session, project: Project, cons: ConstitutionDoc) -> None:
    if not policy.drought_active(session, project.id, cons):
        return
    window_start = utcnow() - timedelta(hours=cons.drought_hours)
    already = session.execute(
        select(func.count(Notice.id)).where(
            Notice.project_id == project.id,
            Notice.kind == NoticeKind.DROUGHT,
            Notice.created_at >= window_start,
        )
    ).scalar_one()
    if not already:
        _notice_ceo(
            session, project, NoticeKind.DROUGHT,
            f"Evidence drought: no verified external evidence in {cons.drought_hours}h. "
            "Internal task creation is now refused by the platform. Change the channel, "
            "change the offer, change the bet, or escalate to the chairman.",
        )
