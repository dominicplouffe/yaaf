"""The janitor: the automatic state changes the constitution promises.

Everything here is enforcement-by-state-change, not notification:

- stale leases are reaped and their task claims released (lana lost 5.8 hours
  of two agents to one un-reaped claim)
- hypotheses expire AT their kill date; their open tasks are blocked
- pending approvals expire to DENIED, and the requester is woken to re-plan
- budgets: 80% warns, 100% auto-pauses the project and revokes live runs
- any amendment still waiting from before the cooling-off was removed is applied
  and queued for CEO disclosure (new ones land the instant they are saved)
- long-dead claims (no run, no subtasks) are released back to ready
- pending external evidence is verified

Notices produced here are information *about* an enforcement that already
happened — never the mechanism itself.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.constitution import ConstitutionDoc, apply_due_amendments, effective
from yaaf.core.db import db_session, notify
from yaaf.core.models import (
    Agent,
    Approval,
    ApprovalStatus,
    Hypothesis,
    HypothesisStatus,
    LedgerEntry,
    MemoryScope,
    Notice,
    NoticeKind,
    Project,
    ProjectStatus,
    Requirement,
    RequirementStatus,
    Run,
    RunKind,
    RunQueue,
    RunStatus,
    SuccessTestKind,
    Task,
    TaskStatus,
    Team,
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
        check_plan(session, project, cons)
        check_finished_requirements(session, project)
        warn_drought(session, project, cons)
        warn_memory_pressure(session, project, cons)
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


# ── the plan: does it hold yet? ─────────────────────────────────────────────────


def check_plan(session: Session, project: Project, cons: ConstitutionDoc) -> bool:
    """R-00 closes itself the moment the plan holds, and reopens if it stops.

    In the janitor rather than on the accept route because the plan can become
    true or false without anyone touching a requirement: the chairman lowers a
    budget, a month's spend accumulates, an estimate is priced in at the CEO's
    historical ratio. A gate that only re-checked when someone poked it would
    let a project build all month against a plan that stopped adding up on the
    3rd.
    """
    plan = policy.plan_requirement(session, project.id)
    if plan is None:
        return False

    outstanding = policy.evaluate_plan(session, project, cons)
    holds = not outstanding

    if holds and plan.status != RequirementStatus.MET:
        from_status = plan.status
        plan.status = RequirementStatus.MET
        plan.met_at = utcnow()
        plan.check_output = "the plan holds: accepted, staged, estimated and affordable"
        policy.record_requirement_event(session, plan, RequirementStatus.MET, "platform",
                                        note=plan.check_output, from_status=from_status)
        sums = policy.plan_arithmetic(session, project, cons)
        _notice_ceo(
            session, project, NoticeKind.GOAL_CHANGED,
            f"The plan holds, so R-00 is met and the board is open — you may create work "
            f"now.\n\nIt comes to about {sums['adjusted_runs']} runs across "
            f"{sums['requirements']} requirements"
            + (f", roughly {sums['days_at_the_daily_cap']} days at the daily cap"
               if sums["days_at_the_daily_cap"] else "")
            + ". Work stage by stage: the platform refuses work for a requirement whose "
              "stage is not open yet, and for one whose dependencies are unsettled.",
        )
        return True

    if not holds and plan.status == RequirementStatus.MET:
        # It stopped adding up. Reopening is not a punishment — it is the
        # difference between a company that notices and one that spends to the
        # cap and calls it a surprise.
        from_status = plan.status
        plan.status = RequirementStatus.PENDING
        plan.met_at = None
        policy.record_requirement_event(session, plan, RequirementStatus.PENDING, "platform",
                                        note="; ".join(outstanding)[:2000],
                                        from_status=from_status)
        _notice_ceo(
            session, project, NoticeKind.GOAL_CHANGED,
            "The plan no longer holds, so R-00 is open again and no new work may be "
            "created until it does.\n\n" + " ".join(outstanding)
            + "\n\nNothing already on the board is cancelled. Finish or cut what is "
              "there, and re-plan the rest.",
        )
        return True
    return False


# ── requirements: is it true yet? ───────────────────────────────────────────────


def check_finished_requirements(session: Session, project: Project) -> int:
    """A requirement whose work has all closed goes to the platform to be
    judged — never to the agents who did it.

    This lives in the janitor and not the dispatcher on purpose. Deciding that
    a requirement is ready to test costs no tokens and must happen whether or
    not the company has runs left today: a project that spent its allowance is
    still allowed to have finished something, and a `met` that waited for
    budget would make the burn-up lie for a day at a time.

    Command tests queue a REQUIREMENT_CHECK the runner executes. Observable
    ones stop here and wait for the chairman — a script cannot tell him the
    memo reads well, and the agent that wrote it is the last thing that should
    be asked.
    """
    moved = 0
    live = session.execute(
        select(Requirement).where(
            Requirement.project_id == project.id,
            Requirement.status == RequirementStatus.IN_PROGRESS,
        )
    ).scalars().all()
    for requirement in live:
        tasks = session.execute(
            select(Task).where(Task.requirement_id == requirement.id)
        ).scalars().all()
        if not tasks:
            continue
        if any(t.status in policy.OPEN_TASK_STATUSES for t in tasks):
            continue
        # Every task closed. If they were ALL killed, nothing was built and the
        # requirement is not a candidate — it goes back to waiting for a plan
        # rather than being tested against an empty workspace.
        if not any(t.status == TaskStatus.DONE for t in tasks):
            continue

        from_status = requirement.status
        requirement.status = RequirementStatus.CHECKING
        kind = (requirement.acceptance_test or {}).get("type")
        if kind == SuccessTestKind.COMMAND:
            run_id = ids.new_id(ids.RUN)
            session.add(RunQueue(
                id=run_id,
                project_id=project.id,
                agent_id=None,
                kind=RunKind.REQUIREMENT_CHECK,
                trigger={"type": "requirement_check", "requirement_id": requirement.id},
                requirement_id=requirement.id,
            ))
            note = "all work closed; the platform is running the acceptance test"
        else:
            note = "all work closed; waiting on the chairman to confirm the observable"
            _notice_ceo(
                session, project, NoticeKind.GOAL_CHANGED,
                f"{requirement.key} has no work left open and its acceptance test is an "
                f"observable, so it is with the chairman: {requirement.statement[:300]}\n"
                "You cannot mark it met and neither can anyone here. Do not create more "
                "work for it while he is looking.",
            )
        policy.record_requirement_event(session, requirement, RequirementStatus.CHECKING,
                                        "janitor", note=note, from_status=from_status)
        moved += 1
    if moved:
        notify(session, "yaaf_run_queue", project.id)
    return moved


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
        policy.charge_failure(session, run)
        _harvest_reaped_usage(session, project, run)
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


def _harvest_reaped_usage(session: Session, project: Project, run: Run) -> bool:
    """Book what a reaped run actually spent, from the checkpoint it left behind.

    The sandbox writes usage to result-<run>.json after every message precisely
    so that a hard kill is metered rather than free — but only `finalize` ever
    read it, and a reaped run never reaches finalize. The lease expiring means
    the *runner* died or lost the row, not that the model did no work.

    So 244 runs across the fleet — 22% of them, burning ~86 wall-hours — were
    booked at $0.00 and zero tokens. "Non-DONE runs are only 7% of spend" was a
    metering artefact, and every budget decision made on it was wrong by an
    unknown margin. Recovering it here makes failure cost what it costs.
    """
    slug = f"{project.slug}-chat" if run.kind == RunKind.CHAT_SESSION else project.slug
    path = Path(settings().workspaces_root) / slug / ".yaaf" / f"result-{run.id}.json"
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return False  # died before its first checkpoint, or the workspace is gone
    except Exception:
        log.warning("unreadable usage checkpoint for reaped run %s", run.id)
        return False

    run.tokens_in = int(data.get("tokens_in", 0) or 0)
    run.tokens_out = int(data.get("tokens_out", 0) or 0)
    run.cache_creation_tokens = int(data.get("cache_creation_tokens", 0) or 0)
    run.cache_read_tokens = int(data.get("cache_read_tokens", 0) or 0)
    # Subscription projects meter in tokens; booking the SDK's list price there
    # would distort a budget that is not denominated in dollars. Same rule the
    # runner applies at finalize.
    subscription = project.billing_mode == "subscription"
    run.cost_usd = None if subscription else data.get("cost_usd")

    total = (run.tokens_in + run.tokens_out
             + run.cache_creation_tokens + run.cache_read_tokens)
    if not total and not run.cost_usd:
        return False

    session.add(
        LedgerEntry(
            id=ids.new_id(ids.LEDGER),
            project_id=project.id,
            kind="model_usage",
            tokens_in=run.tokens_in,
            tokens_out=run.tokens_out,
            cache_creation_tokens=run.cache_creation_tokens,
            cache_read_tokens=run.cache_read_tokens,
            usd=run.cost_usd,
            run_id=run.id,
            note=f"{run.kind} reaped (usage recovered from checkpoint)",
        )
    )
    log.info("recovered %s tokens from reaped run %s", total, run.id)
    return True


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
    """A claim with no live run behind it and no open subtasks, *untouched* for
    more than 2x the run timeout, goes back to ready.

    Age off ``updated_at``, not ``claimed_at``. ``claimed_at`` records when the
    claim was acquired and is never refreshed, so it measures nothing about
    whether the claim is alive — and the wake throttle guarantees long gaps
    with no run in flight, which this then reads as a crash. A claimant failing
    its success test every twenty minutes had its task released back to READY
    eight times on exactly the one-hour mark, each release re-opening the task
    as fresh team work and zeroing any per-claim state. ``updated_at`` bumps on
    every write to the row, so it is the liveness signal this always wanted."""
    cutoff = utcnow() - timedelta(seconds=2 * cons.run_timeout_s)
    released = 0
    for task in session.execute(
        select(Task).where(
            Task.project_id == project.id,
            Task.status == TaskStatus.CLAIMED,
            Task.updated_at <= cutoff,
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


# ── memory pressure (informational; enforcement is at memory add) ──────────────

# One notice per pressure episode. Long enough that a curator that is choosing
# not to compact is not nagged every wake; short enough that a store which
# fills, gets curated, and fills again is warned about the second time too.
# A *full* scope is an outage, not a nag — every member's writes are being
# refused — so it re-asks far sooner.
MEMORY_PRESSURE_WINDOW_HOURS = 12
MEMORY_FULL_WINDOW_HOURS = 1


def warn_memory_pressure(session: Session, project: Project, cons: ConstitutionDoc) -> None:
    """Tell the *curator* — not whoever hit the wall — before the wall.

    A full store refuses writes from every member of the scope, but only the
    curator can clear it, so the agent that discovers the problem is routinely
    the one agent that cannot fix it. Warning early turns a terminated run into
    a compaction the curator does on a wake it was having anyway.
    """
    scopes: list[tuple[str, str | None, Agent | None, str]] = [
        (MemoryScope.PROJECT, None, policy.ceo_of(session, project.id), project.name)
    ]
    for team in session.execute(
        select(Team).where(Team.project_id == project.id)
    ).scalars():
        scopes.append(
            (MemoryScope.TEAM, team.id, policy.effective_lead(session, team), team.name)
        )

    for scope, team_id, curator, team_name in scopes:
        cap = policy.memory_cap_for(cons, scope)
        live = policy.live_memory_count(session, scope, project.id, team_id)
        if live < cap * cons.memory_warn_ratio:
            continue
        full = live >= cap
        kind = NoticeKind.MEMORY_FULL if full else NoticeKind.MEMORY_PRESSURE
        window = MEMORY_FULL_WINDOW_HOURS if full else MEMORY_PRESSURE_WINDOW_HOURS

        # A team with two or more members and no named lead has no curator at
        # all: its memory fills, every member is refused, and there is nobody
        # the platform could ask to fix it. Only the chairman can name a lead,
        # so this goes to the CEO to escalate — silence here would be a company
        # quietly unable to remember anything.
        target = curator.id if curator is not None else None
        if target is None:
            ceo = policy.ceo_of(session, project.id)
            if ceo is None or not full:
                continue
            target = ceo.id
            body = (
                f"Team \"{team_name}\" has filled its memory ({live}/{cap}) and has no "
                "lead, so no agent on the platform may compact or forget there — every "
                "member's writes are refused. Ask the chairman to name a lead for that "
                "team, or to raise its memory cap."
            )
        else:
            where = "Project memory" if scope == MemoryScope.PROJECT else "Your team's memory"
            body = (
                f"{where} is full ({live}/{cap}). Every member's writes are being refused "
                "and only you can clear it: memory_compact folds several overlapping "
                "entries into one and frees all but one slot. Nothing is lost — folded "
                "entries stay readable with memory_search include_superseded=true."
                if full else
                f"{where} is nearly full ({live}/{cap}). Compact now, on this wake, rather "
                "than discovering the cap mid-write later: memory_compact folds "
                "overlapping entries into one and frees all but one slot."
            )

        window_start = utcnow() - timedelta(hours=window)
        already = session.execute(
            select(func.count(Notice.id)).where(
                Notice.project_id == project.id,
                Notice.agent_id == target,
                Notice.kind == kind,
                Notice.created_at >= window_start,
            )
        ).scalar_one()
        if already:
            continue
        _notice_agent(session, project, target, kind, body)
        log.warning(
            "memory %s: project=%s scope=%s team=%s %d/%d curator=%s",
            "full" if full else "pressure", project.slug, scope, team_id, live, cap,
            curator.name if curator else "NONE (leaderless)",
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
