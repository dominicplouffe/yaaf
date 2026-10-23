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
import random
import time
from datetime import UTC, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.constitution import (
    ConstitutionDoc,
    apply_due_amendments,
    effective,
    effective_version,
    parse_hhmm,
)
from yaaf.core.db import db_session, notify
from yaaf.core.models import (
    Agent,
    AgentStatus,
    Approval,
    ApprovalStatus,
    Ask,
    AskStatus,
    AuditLog,
    AuditSample,
    AuditSampleStatus,
    Commitment,
    CommitmentStatus,
    Cycle,
    CycleStatus,
    Heartbeat,
    Hypothesis,
    HypothesisStatus,
    LedgerEntry,
    LedgerKind,
    MemoryScope,
    Notice,
    NoticeKind,
    Project,
    ProjectMode,
    ProjectStatus,
    Recurrence,
    Requirement,
    RequirementCycleResult,
    RequirementEvent,
    RequirementStatus,
    Review,
    ReviewStatus,
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
from yaaf.server import asks as ask_lib
from yaaf.server import briefing, metrics, policy, staffing
from yaaf.server.notify import (
    Attention,
    boardroom_line,
    chairman_notify,
    inbox_line,
    project_link,
)
from yaaf.server.policy import Refusal
from yaaf.server.rows import approval_row, chairman_time, requirement_name
from yaaf.server.verifier import verify_pending_evidence

log = logging.getLogger("yaaf.janitor")


def janitor_loop() -> None:  # pragma: no cover - thin loop around janitor_all
    while True:
        janitor_all()
        time.sleep(settings().janitor_poll_s)


def janitor_all() -> None:
    """One sweep, with each project in its own transaction.

    The tick used to share one across every project on the box, so a single bad
    row rolled back the enforcement pass for all of them — no reaping, no budget
    check, no approval expiry anywhere, reported by one line on stderr. See the
    twin of this in `dispatcher.dispatch_all`.
    """
    with db_session() as session:
        project_ids = [row for (row,) in session.execute(select(Project.id))]
    for project_id in project_ids:
        try:
            with db_session() as session:
                project = session.get(Project, project_id)
                if project is not None:
                    _janitor_project(session, project)
        except Exception:
            log.exception("janitor failed for project %s", project_id)
    try:
        with db_session() as session:
            verify_pending_evidence(session)
    except Exception:
        log.exception("evidence verification failed")


def janitor_once(session: Session) -> None:
    """One sweep inside the caller's transaction — the test harness's entry
    point. Production uses `janitor_all`, which isolates each project."""
    for project in session.execute(select(Project)).scalars():
        _janitor_project(session, project)
    verify_pending_evidence(session)
    # Slack mirroring moved to its own 2s loop (slack.socket.fast_mirror_loop):
    # chat-grade latency, and no double-post race with an enforcement tick.


def _janitor_project(session: Session, project: Project) -> None:
    """Every enforcement rule this project is due, in order."""
    applied = apply_due_amendments(session, project.id)
    for amendment in applied:
        _notice_ceo(
            session, project,
            NoticeKind.CONSTITUTION_AMENDED,
            f"The chairman amended the constitution (reason: {amendment.reason}). "
            f"Changes now in force: {amendment.changes}. Plan accordingly.",
        )
    # Before the running-only gate on purpose. The briefing is composed from
    # rows at zero tokens, so it can — and must — reach a project the platform
    # paused for an exhausted budget: that is precisely the morning its
    # chairman needs to hear from it, and the morning a model-written status
    # could not be afforded.
    send_daily_brief(session, project)
    if project.status != ProjectStatus.RUNNING:
        return
    try:
        cons = effective(session, project.id)
    except LookupError:
        return
    # Beside evidence verification in spirit: both are the platform going and
    # looking at the outside world on its own schedule, and neither can be
    # asked for or skipped by an agent. Per-project and here rather than in
    # either sweep function, so it reaches production (`janitor_all`) as well
    # as the tests (`janitor_once`) and so one project's slow endpoint is
    # isolated by janitor_all's own per-project transaction and except.
    metrics.measure_due(session, project.id)
    metrics.settle_metrics(session, project.id)
    reap_stale_runs(session, project, cons)
    expire_hypotheses(session, project)
    nag_approvals(session, project, cons)
    expire_approvals(session, project, cons)
    answer_asks(session, project)
    nag_asks(session, project, cons)
    expire_asks(session, project)
    enforce_budget(session, project, cons)
    release_dead_claims(session, project, cons)
    release_unreadable_reviews(session, project)
    requeue_lost_checks(session, project)
    elect_missing_leads(session, project)
    stand_down_due_teams(session, project)
    check_plan(session, project, cons)
    check_finished_requirements(session, project, cons)
    check_stalled_requirements(session, project)
    nag_observable_requirements(session, project, cons)
    nag_awaiting_chairman(session, project, cons)
    check_commitments(session, project, cons)
    draw_audit_samples(session, project, cons)
    expire_audit_samples(session, project)
    open_reviews(session, project, cons)
    nag_reviews(session, project)
    roll_cycles(session, project, cons)
    check_explore_deadline(session, project)
    check_delivery_ready(session, project)
    warn_drought(session, project, cons)
    warn_stall(session, project, cons)
    warn_memory_pressure(session, project, cons)
    sweep_spend_window(session, project)
    evaluate_flags(session, project, cons)


def sweep_spend_window(session: Session, project: Project) -> None:
    """Close a measurement window that is due and write its findings; make the
    CEO report at each checkpoint. See :mod:`yaaf.server.spendwindow`."""
    from yaaf.server import spendwindow

    spendwindow.sweep(session, project)


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


# ── the briefing ────────────────────────────────────────────────────────────────

#: The dedup heartbeat for the daily push, hung off the CEO agent like every
#: other wall-clock cadence. Distinct from the chairman's READ mark
#: (`project_members.last_briefed_at`): the email's window runs from the last
#: email, the panel's from the last time he actually looked. Sharing one mark
#: would mean the mail silently ate the window the panel was going to show.
BRIEF_BEAT = "chairman_brief"


def send_daily_brief(session: Session, project: Project) -> bool:
    """The project writes to its chairman once a day, whatever the news.

    Every other outbound mail in this system is an alarm — a budget gone, a
    meeting unattended, an approval about to expire — and a correspondent who
    only ever brings bad news is one you stop reading. This is the letter that
    arrives when nothing is wrong, which is what makes the alarms legible when
    they come.

    Zero tokens by construction: composed from rows, no run, no wake, no model.
    So it survives an exhausted budget and an empty board, the same reasoning
    that has the janitor open a review with the budget gone.

    Terminal projects are excluded. A stopped or completed one has nothing
    left to report and a daily mail about it forever is how a chairman learns
    to filter this address.
    """
    if project.status not in (ProjectStatus.RUNNING, ProjectStatus.PAUSED):
        return False
    try:
        cons = effective(session, project.id)
    except LookupError:
        return False
    raw = cons.brief_daily_at
    when = parse_hhmm(raw) if raw else None
    if when is None:
        return False
    try:
        tz = ZoneInfo(cons.timezone)
    except Exception:  # pragma: no cover - the rule validates this on write
        log.warning("project %s has an unreadable timezone; briefing skipped", project.slug)
        return False

    now = utcnow()
    local = now.astimezone(tz)
    target = local.replace(hour=when.hour, minute=when.minute, second=0, microsecond=0)
    if local < target:
        return False
    ceo = policy.ceo_of(session, project.id)
    if ceo is None:
        return False
    beat = session.execute(
        select(Heartbeat).where(Heartbeat.agent_id == ceo.id,
                                Heartbeat.name == BRIEF_BEAT)
    ).scalar_one_or_none()
    if beat is not None and beat.last_fired_at.astimezone(tz).date() >= local.date():
        return False

    since = beat.last_fired_at if beat is not None else project.created_at
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    # Stamped BEFORE the send, and deliberately: chairman_notify is
    # fire-and-forget over a thread, so there is no delivery result to wait
    # for, and a stamp that only landed on success would re-send the same
    # briefing every tick against a dead SMTP host.
    if beat is None:
        session.add(Heartbeat(agent_id=ceo.id, name=BRIEF_BEAT, last_fired_at=now))
    else:
        beat.last_fired_at = now

    brief = briefing.project_briefing(
        session, project, cons, since=since, until=now,
        # The morning letter's edges, said the way a person would; it used to
        # call its own schedule "the window you asked for".
        source="since yesterday's briefing" if beat is not None else "since this project started")
    chairman_notify(
        session, project,
        f"briefing — {brief['headline'][:100]}",
        briefing.render_text(brief),
        attention=Attention.BRIEF,
    )
    log.info("sent the daily briefing for %s", project.slug)
    return True


# ── the plan: does it hold yet? ─────────────────────────────────────────────────


def stand_down_due_teams(session: Session, project: Project) -> list[str]:
    """A team whose declared condition came true stops, and the platform does it.

    Not a nag and not a reminder. The decision was made when the CEO wrote the
    condition while planning; this is the platform keeping it. A prompt here
    would be the same thing the platform already does badly everywhere else —
    an idle seat costs quietly, nothing raises the question, and the reminder
    loses to whatever else is in the run.

    A team holding work is skipped rather than refused: the condition is still
    true and will fire on the tick after the board clears, so a stand-down can
    never orphan a claimed task and can never be lost either.
    """
    stood_down = []
    for team in staffing.running_teams(session, project.id):
        if not staffing.condition_met(session, project, team.stand_down_when):
            continue
        try:
            staffing.check_stand_down(session, project, team, by_chairman=True)
        except Refusal:
            continue
        outcome = staffing.stand_down(
            session, project, team,
            f"Declared stand-down condition met: {_condition_words(team.stand_down_when)}.",
            actor="janitor")
        staffing.notify_ceo(
            session, project,
            f"{team.name} stood down: the condition you set — "
            f"{_condition_words(team.stand_down_when)} — is now true. Those seats no "
            "longer wake and no longer cost. Plan around a roster without them, or "
            "recall the team if you were wrong about when its job ended.")
        boardroom_line(
            session, project.id,
            f"🚪 STOOD DOWN: {team.name} — the condition your CEO set when it planned "
            f"({_condition_words(team.stand_down_when)}) came true, so the platform "
            "stood the role down. Recall it on the Team page if you disagree.")
        stood_down.append(outcome["team"])
    if stood_down:
        notify(session, "yaaf_wake", project.id)
    return stood_down


def _condition_words(condition: dict | None) -> str:
    if not condition:
        return "no condition"
    if condition.get("kind") == "requirement_met":
        return " and ".join(condition.get("keys") or []) + " are met"
    return f"every requirement at or below stage {condition.get('stage')} has settled"


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
        # difference between a project that notices and one that spends to the
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


# ── the commitment: does the date still hold? ───────────────────────────────────


# Only re-stamp the row when the projection has genuinely moved; the naive
# projection slides forward every second and stamping it every tick would turn
# an idle project into a write-per-15s heartbeat on its own promise.
PROJECTION_STAMP_TOLERANCE = timedelta(hours=1)


def _slip_moves_for_chairman(cons: ConstitutionDoc) -> str:
    """The same fact from the other side: whether this needs the chairman at
    all. Telling a chairman "your levers" about a project he has already
    delegated is how a dashboard teaches him to ignore it."""
    holds_scope = policy.ceo_holds(cons, "scope_authority")
    holds_date = policy.ceo_holds(cons, "commitment_authority")
    if holds_scope and holds_date:
        return (
            "Nothing is waiting on you: the constitution gives the CEO both levers "
            "here, so it will finish, cut, or reset the date to the platform's "
            "projection, and tell you which it chose and why. Every one of those is "
            "reversible by you, and the dial is yours to turn back."
        )
    if holds_scope:
        return (
            "The CEO can cut scope itself and will. The date is yours: move it on the "
            "Plan screen, or leave it and let the freeze hold the project to finishing "
            "what is open."
        )
    if holds_date:
        return (
            "The CEO can reset the date to the platform's projection itself and will "
            "tell you when it does. Cuts are yours, on the Plan screen."
        )
    return (
        "Your levers: cut scope on the Plan screen, move the date, or wait — the "
        "freeze lifts by itself if the pace recovers."
    )


def void_operate_commitments(session: Session, project: Project) -> None:
    """Retire any promise an Operate project is holding.

    Operate refuses a project date everywhere a date can be written, but the
    CEO's proposal path used to let one through — and under an autonomous dial
    it signed itself, so a daily rhythm ended up "breached" against a finish
    line it never had, with scope frozen and a red banner saying the chairman
    signed a date he never saw. The proposal is refused now; this clears the
    rows that got in before it was. State-based like everything here, so it is
    idempotent and needs no migration to reach an install.
    """
    rows = session.execute(
        select(Commitment).where(
            Commitment.project_id == project.id,
            Commitment.status.in_((CommitmentStatus.PROPOSED,
                                   *policy.LIVE_COMMITMENT_STATUSES)))
    ).scalars().all()
    for row in rows:
        was = row.status
        row.status = CommitmentStatus.VOID
        row.status_reason = (
            "Voided by the platform: an Operate project runs on cycles and has no "
            "single delivery date. Each cycle carries its own due-by.")
        session.add(AuditLog(
            id=ids.new_id(ids.AUDIT), project_id=project.id, actor="janitor",
            action="commitment.void", entity="commitment", entity_id=row.id,
            detail={"was": was, "deliver_by": row.deliver_by.isoformat()},
        ))
        _notice_ceo(
            session, project, NoticeKind.COMMITMENT_REVALIDATED,
            f"The commitment to {row.deliver_by:%Y-%m-%d} has been voided by the "
            "platform. This is an Operate project: it runs on cycles, and a project-"
            "wide delivery date has nothing to finish. Any scope freeze it caused is "
            "lifted. Plan each cycle; the rhythm is the promise.",
        )
        if was != CommitmentStatus.PROPOSED:
            chairman_notify(
                session, project,
                f"Delivery date {row.deliver_by:%Y-%m-%d} withdrawn",
                "This project is Operate, which runs on a rhythm and never finishes, so "
                "a single delivery date for it meant nothing — and it should never have "
                "been accepted. The platform has withdrawn it. The project is judged "
                "cycle by cycle instead: each turn's results, and what carried over, "
                "are on its Team page. Nothing about the work itself changed.",
                attention=Attention.FYI,
            )


def check_commitments(session: Session, project: Project, cons: ConstitutionDoc) -> None:
    """The forecast, recomputed; the promise, re-validated; every transition,
    disclosed.

    In the janitor for the same reason check_plan is: the promise can stop
    holding without anyone touching a commitment — the trailing pace slides, a
    run dies, the chairman amends a rule. Re-validation is STATE-BASED, a diff
    of {constitution version, scope keys, failure load} against what this
    function last recorded on the row. Never event hooks: amendments apply
    inline from three different callers, and a hook misses every one it was
    not wired to.
    """
    if project.mode == ProjectMode.OPERATE:
        void_operate_commitments(session, project)
        return
    commitment = policy.live_commitment(session, project.id)
    if commitment is None:
        return
    from yaaf.server.velocity import commitment_projection

    now = utcnow()
    scope = [
        r for r in policy.scope_requirements(session, project.id)
        if r.status not in (RequirementStatus.PROPOSED, RequirementStatus.CUT)
    ]
    scope_keys = sorted(r.key for r in scope)
    failure_load = sum(r.failure_load for r in scope)
    version = effective_version(session, project.id)
    projection = commitment_projection(session, project, cons)
    projected = projection["projected_done_at"] if projection["known"] else None

    # The three re-validation triggers, as diffs against the recorded basis.
    basis = dict(commitment.projection or {})
    moved: list[str] = []
    if basis.get("constitution_version") != version:
        if basis.get("constitution_version") is not None:
            moved.append(
                f"the rules moved (constitution v{basis['constitution_version']} → "
                f"v{version}; the promise was accepted under "
                f"v{commitment.constitution_version})"
            )
        basis["constitution_version"] = version
    if basis.get("scope_keys") != scope_keys:
        if basis.get("scope_keys") is not None:
            old, new = set(basis["scope_keys"]), set(scope_keys)
            delta = [f"+{k}" for k in sorted(new - old)] + \
                    [f"−{k}" for k in sorted(old - new)]
            moved.append("the scope changed (" + ", ".join(delta) + ")")
        basis["scope_keys"] = scope_keys
    prior_load = int(basis.get("failure_load") or 0)
    if failure_load < prior_load:
        basis["failure_load"] = failure_load  # scope was cut out from under it
    elif cons.commitment_failure_reforecast and \
            failure_load - prior_load >= cons.commitment_failure_reforecast:
        moved.append(
            f"{failure_load - prior_load} run(s) have died against the plan since the "
            "promise was last validated"
        )
        basis["failure_load"] = failure_load
    basis["method"] = projection.get("method")
    basis["note"] = projection.get("basis") or projection.get("why")

    # Where the promise stands now. Breach outranks risk; met outranks both.
    met = bool(scope) and all(r.status == RequirementStatus.MET for r in scope)
    if met:
        status = CommitmentStatus.MET
    elif now > commitment.deliver_by:
        status = CommitmentStatus.BREACHED
    elif projected is not None and projection["remaining"] > 0 \
            and projected > commitment.deliver_by:
        status = CommitmentStatus.AT_RISK
    else:
        status = CommitmentStatus.ACCEPTED

    stamp = (
        status != commitment.status
        or bool(moved)
        or (projected is None) != (commitment.projected_done_at is None)
        or (projected is not None and commitment.projected_done_at is not None
            and abs(projected - commitment.projected_done_at) > PROJECTION_STAMP_TOLERANCE)
    )
    if not stamp:
        return
    commitment.projected_done_at = projected
    if projected is not None:
        basis["projected_done_at"] = projected.isoformat()
    commitment.projection = basis

    if status == commitment.status:
        if moved:
            _notice_ceo(
                session, project, NoticeKind.COMMITMENT_REVALIDATED,
                "The ground under the commitment moved: " + "; ".join(moved) + ". "
                "The platform re-projected the date"
                + (f": the work now lands about {projected:%Y-%m-%d} against the "
                   f"promised {commitment.deliver_by:%Y-%m-%d}." if projected
                   else f", and there is currently no arithmetic to project from "
                        f"({basis['note']}).")
                + " Nothing is frozen — this is the promise being re-checked, "
                  "which is what a promise is for.",
            )
        return

    landing = (f"the projection lands about {projected:%Y-%m-%d}" if projected
               else f"there is no arithmetic to project from ({basis['note']})")
    ground = (" The ground moved first: " + "; ".join(moved) + ".") if moved else ""
    commitment.status, from_status = status, commitment.status

    if status == CommitmentStatus.MET:
        commitment.met_at = now
        commitment.status_reason = (
            f"every requirement in scope is true ({len(scope_keys)} of them)"
        )
        _notice_ceo(
            session, project, NoticeKind.COMMITMENT_MET,
            f"The commitment is MET: all {len(scope_keys)} requirement(s) in scope are "
            f"true, against a promise of {commitment.deliver_by:%Y-%m-%d}. The chairman "
            "has been told. Do not invent new work to fill the quiet — if something "
            "more is worth doing, it is a proposal for him, not a task.",
        )
        chairman_notify(
            session, project, "commitment met",
            f"Every requirement in scope is true — {len(scope_keys)} requirement(s), "
            f"promised by {commitment.deliver_by.day} {commitment.deliver_by:%b}, finished "
            f"{chairman_time(now, cons.timezone)}."
            + project_link(project.id, "deliverables", "See what was delivered"),
            attention=Attention.FYI,
        )
    elif status == CommitmentStatus.BREACHED:
        commitment.status_reason = (
            f"{commitment.deliver_by:%Y-%m-%d} passed with "
            f"{projection['remaining']} requirement(s) still open"
        )
        _notice_ceo(
            session, project, NoticeKind.COMMITMENT_BREACHED,
            f"The commitment is BREACHED: {commitment.deliver_by:%Y-%m-%d} has passed "
            f"and {projection['remaining']} requirement(s) are still open.{ground} "
            "New scope is frozen — requirement proposals will be refused. Existing "
            "work continues; finish it. " + policy.slip_moves(cons),
        )
        chairman_notify(
            session, project, "commitment BREACHED",
            f"The promised date {commitment.deliver_by:%Y-%m-%d} has passed with "
            f"{projection['remaining']} requirement(s) still open; {landing}. "
            "New scope is frozen. " + _slip_moves_for_chairman(cons)
            + project_link(project.id, "roadmap", "Cut scope or move the date"),
            attention=Attention.ALARM,
        )
    elif status == CommitmentStatus.AT_RISK:
        days = max(1, (projected - commitment.deliver_by).days)
        commitment.status_reason = f"{landing}, ~{days} day(s) past the promise"
        _notice_ceo(
            session, project, NoticeKind.COMMITMENT_AT_RISK,
            f"The commitment is AT RISK: you promised {commitment.deliver_by:%Y-%m-%d} "
            f"and {landing} — about {days} day(s) late ({basis['note']}).{ground} "
            "New scope is frozen while this holds: proposals will be refused. "
            + policy.slip_moves(cons),
        )
        chairman_notify(
            session, project, "commitment at risk",
            f"The projection now lands about {projected:%Y-%m-%d} against the promised "
            f"{commitment.deliver_by:%Y-%m-%d} ({basis['note']}).{ground}\n\n"
            "New scope is frozen for the CEO. " + _slip_moves_for_chairman(cons)
            + project_link(project.id, "roadmap", "Cut scope or move the date"),
            attention=Attention.ACTION,
        )
    else:  # recovered to ACCEPTED
        commitment.status_reason = None
        _notice_ceo(
            session, project, NoticeKind.COMMITMENT_RECOVERED,
            f"The commitment holds again: {landing}, inside the promised "
            f"{commitment.deliver_by:%Y-%m-%d}.{ground} New scope is no longer frozen. "
            "It unfroze because the arithmetic changed, not because anyone relented — "
            "keep it that way.",
        )
    log.info("commitment %s: %s → %s (project %s)",
             commitment.id, from_status, status, project.slug)


# ── requirements: is it true yet? ───────────────────────────────────────────────


def _work_closed_since_last_check(session: Session, requirement, tasks) -> bool:
    """Has any task under this requirement moved since it was last ruled on?

    The marker is the ruling itself, whoever made it: any event that took this
    requirement OUT of `checking`. That is the checker's failing command test
    and the chairman's verdict on an observable, under one rule. Never ruled on
    before → True: the first ruling is always owed.

    An earlier version counted only the *checker*, on the reasoning that the
    chairman's loop has a human in it and paces itself. It does not. An
    observable acceptance test never reaches the checker, so it never wrote the
    event this reads, so the guard was dead for exactly the requirements a human
    rules on: the chairman pressed "not met", the next sweep fifteen seconds
    later put it straight back in front of him, and the red button did nothing
    at all. A rejection means the artifact is wrong. The test is a function of
    work that has not changed since, so re-asking cannot produce a new answer —
    it can only ask the same person the same question until he stops reading.

    Deliberately keyed on task movement rather than a timer or a retry budget.
    A retry budget still spends N containers proving the same thing; a clock
    still re-tests a workspace nobody touched. What makes a re-test worth
    running is that the work under it changed.
    """
    last = session.execute(
        select(func.max(RequirementEvent.at)).where(
            RequirementEvent.requirement_id == requirement.id,
            RequirementEvent.from_status == RequirementStatus.CHECKING,
        )
    ).scalar()
    if last is None:
        return True
    return any(t.updated_at and t.updated_at > last for t in tasks)


def check_finished_requirements(session: Session, project: Project,
                                cons: ConstitutionDoc) -> int:
    """A requirement whose work has all closed goes to the platform to be
    judged — never to the agents who did it.

    This lives in the janitor and not the dispatcher on purpose. Deciding that
    a requirement is ready to test costs no tokens and must happen whether or
    not the project has runs left today: a project that spent its allowance is
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
        # A metric requirement does not settle on work closing. Its verdict is
        # a reading, and the reading arrives on its own schedule whether or not
        # anybody has finished anything — which is exactly what lets it be
        # accepted in week one by a project that does not yet know how it will
        # get there. Left in this loop it would reach the `else` branch below
        # and park on the chairman to "confirm the observable", which is the
        # one thing a measured number never needs. See `settle_metrics`.
        if metrics.is_metric(requirement.acceptance_test):
            continue
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
        # ...and the work must have MOVED since the platform last ruled. A
        # failing test returns the requirement to exactly the state that got it
        # tested, so without this the two halves make a loop: one project ran
        # the same acceptance test 297 times in 80 minutes against a workspace
        # nobody had touched since the first. The test is a pure function of
        # that workspace — re-running it unchanged cannot produce a new answer,
        # it can only burn a container every fifteen seconds and bury the CEO
        # in identical notices.
        if not _work_closed_since_last_check(session, requirement, tasks):
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
        elif cons.verdict_policy == "ceo_judgment":
            # The dial hands unverifiable closes to the CEO's judgment: tell it
            # to judge instead of parking on a desk nobody is required to
            # check. Every self-judgment is disclosed to the chairman in full.
            note = "all work closed; awaiting the CEO's judgment on the observable"
            _notice_ceo(
                session, project, NoticeKind.GOAL_CHANGED,
                f"{requirement.key} has no work left open and its acceptance test is an "
                f"observable the platform cannot run: {requirement.statement[:300]}\n"
                "The constitution grants you the verdict (verdict_policy: "
                "ceo_judgment). Look at what was actually produced and judge it "
                "honestly with scope_act — kind 'verdict', ok true or false, and say "
                "what you saw. Your judgment is disclosed to the chairman in full, "
                "and the spot-audit exists for exactly this close.",
            )
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


# ── the Operate rhythm: cycles roll here, and the learning loop rides them ──────


def next_cycle_roll(opened_at, cons: ConstitutionDoc, cycle: Cycle | None = None):
    """When the cycle that opened at `opened_at` rolls, in UTC.

    All local arithmetic happens in the constitution's one timezone. Monthly
    and quarterly days are 1-28 by validation — the 29th-31st were refused at
    the rule, not clamped here, so this never has to guess what the chairman
    meant by "the 30th" in February.

    A cycle carrying its own `due_at` rolls on THAT, and the constitution's
    rhythm is the fallback. This is the difference between a project whose work
    is paced by the calendar and one paced by the world: a gameweek deadline
    lands on a different day every week, a warehouse cutoff moves with the
    carrier, and a rhythm that cannot follow them is a rhythm the work ignores.
    """
    from zoneinfo import ZoneInfo

    # The world's date beats the calendar's, when the world has given us one.
    if cycle is not None and cycle.due_at is not None:
        from yaaf.server.dispatcher import _utc

        return _utc(cycle.due_at)

    try:
        tz = ZoneInfo(cons.timezone)
    except Exception:
        from datetime import UTC as tz  # noqa: N813 - a body written before validation

    local = opened_at.astimezone(tz)
    midnight = {"hour": 0, "minute": 0, "second": 0, "microsecond": 0}
    if cons.cycle_frequency == "hourly":
        # The inbox sweep, the routing check: rhythms shorter than a day, where
        # snapping to midnight would mean one cycle a day. The boundary is the
        # top of the next hour, so cycles line up with the wake ceilings that
        # bound them.
        boundary = (local + timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0)
    elif cons.cycle_frequency == "daily":
        boundary = (local + timedelta(days=1)).replace(**midnight)
    elif cons.cycle_frequency == "weekly":
        ahead = (cons.cycle_day - local.weekday()) % 7
        boundary = (local + timedelta(days=ahead)).replace(**midnight)
        if boundary <= local:
            boundary += timedelta(days=7)
    else:
        anchors = (list(range(1, 13)) if cons.cycle_frequency == "monthly"
                   else [1, 4, 7, 10])
        boundary = None
        for months_ahead in range(0, 16):
            month = (local.month - 1 + months_ahead) % 12 + 1
            year = local.year + (local.month - 1 + months_ahead) // 12
            if month not in anchors:
                continue
            candidate = local.replace(year=year, month=month,
                                      day=cons.cycle_day, **midnight)
            if candidate > local:
                boundary = candidate
                break
    from datetime import UTC

    return boundary.astimezone(UTC)


def _open_cycle(session: Session, project: Project, ordinal: int) -> Cycle:
    cycle = Cycle(id=ids.new_id(ids.CYCLE), project_id=project.id, ordinal=ordinal)
    session.add(cycle)
    session.flush()
    return cycle


def roll_cycles(session: Session, project: Project, cons: ConstitutionDoc) -> None:
    """Close the cycle whose time came, carry the unfinished scope, reopen the
    plan gate, and hand the CEO the last three narratives.

    In the janitor because a rhythm firing while the project is busy or broke
    must still be recorded, and opening a cycle costs nothing. At most one
    cycle is ever open: close-and-open is one transition, and a project
    paused across many boundaries resumes into ONE new cycle — the janitor
    never touches paused projects, so no boundaries "queue up".
    """
    if project.mode != ProjectMode.OPERATE:
        return
    cycle = policy.current_cycle(session, project.id)
    now = utcnow()
    if cycle is None:
        last = session.execute(
            select(func.max(Cycle.ordinal)).where(Cycle.project_id == project.id)
        ).scalar_one() or 0
        opened = _open_cycle(session, project, last + 1)
        log.info("opened cycle #%s for %s", opened.ordinal, project.slug)
        return
    if now < next_cycle_roll(cycle.opened_at, cons, cycle):
        return

    scope = policy.scope_requirements(session, project.id)  # the open cycle's
    accepted = [r for r in scope if r.status != RequirementStatus.PROPOSED]
    unsettled = [r for r in accepted
                 if r.status not in policy.CLOSED_REQUIREMENT_STATUSES]
    met = [r for r in accepted if r.status == RequirementStatus.MET]

    if unsettled:
        cycle.status = CycleStatus.MISSED
    elif met:
        cycle.status = CycleStatus.COMPLETED
    else:
        cycle.status = CycleStatus.SKIPPED  # nothing accepted, or nothing made true
    # The verdict is one of three words and the difference between a cycle that
    # made nine of ten things true and one that made none is invisible in all
    # three. Kept here so the flag, the brief and the chairman all read the same
    # numbers, and so nobody has to reconstruct them by hand a week later.
    cycle.scorecard = {
        "accepted": len(accepted),
        "met": len(met),
        "carried": len(unsettled),
        "met_keys": [r.key for r in met],
        "carried_keys": [r.key for r in unsettled],
    }
    cycle.closed_at = now
    if not cycle.narrative:
        # No review wrote one this cycle; the platform leaves an honest stub
        # so the next cycle's pack never carries a silent gap.
        cycle.narrative = (
            f"Cycle {cycle.ordinal} closed {cycle.status}: "
            f"{len(met)} met" + (f" ({', '.join(r.key for r in met)})" if met else "")
            + f", {len(unsettled)} carried forward. No review narrative was "
              "written this cycle."
        )

    chairman_notify(
        session, project, f"cycle {cycle.ordinal} closed {cycle.status}", cycle.narrative or "",
        kind="cycle_closed",
        data={"cycle": {"id": cycle.id, "ordinal": cycle.ordinal, "status": cycle.status,
                        "goal": cycle.goal,
                        "opened_at": cycle.opened_at.isoformat() if cycle.opened_at else None,
                        "closed_at": now.isoformat()}},
        attention=Attention.FYI,
    )

    # Two consecutive cycles with nothing made true pause the project: a
    # rhythm nobody dances to is a bill, not a project.
    if cycle.status == CycleStatus.SKIPPED:
        previous = session.execute(
            select(Cycle).where(Cycle.project_id == project.id,
                                Cycle.ordinal == cycle.ordinal - 1)
        ).scalar_one_or_none()
        if previous is not None and previous.status == CycleStatus.SKIPPED:
            project.status = ProjectStatus.PAUSED
            project.status_reason = (
                f"two consecutive cycles ({previous.ordinal} and {cycle.ordinal}) "
                "closed with nothing made true"
            )
            for run in session.execute(
                select(Run).where(Run.project_id == project.id,
                                  Run.status == RunStatus.RUNNING)
            ).scalars():
                run.token_revoked = True
            chairman_notify(
                session, project, "project paused: two skipped cycles",
                f"Cycles {previous.ordinal} and {cycle.ordinal} both closed with "
                "nothing made true. The platform paused the project rather than "
                "billing you for a third. Resume from the project page when you have "
                "decided what changes — the next cycle opens on resume."
                + project_link(project.id, "", "Resume it"),
                attention=Attention.ALARM,
            )
            log.warning("project %s paused: two skipped cycles", project.slug)
            return

    # A standing requirement is judged for the cycle that just closed and then
    # starts again. Without this an Operate project that owes the same thing
    # every week had one row, met once, and looked finished after the first —
    # while the platform had no way to check the other thirty-seven.
    standing = [r for r in accepted if r.recurrence == Recurrence.STANDING]
    for requirement in standing:
        session.add(RequirementCycleResult(
            id=ids.new_id(ids.REQUIREMENT_RESULT),
            project_id=project.id,
            requirement_id=requirement.id,
            cycle_id=cycle.id,
            status=requirement.status,
            met_at=requirement.met_at,
            met_by_run_id=requirement.met_by_run_id,
            check_output=(requirement.check_output or "")[:4000] or None,
        ))

    new = _open_cycle(session, project, cycle.ordinal + 1)
    carried = unsettled + [r for r in scope if r.status == RequirementStatus.PROPOSED]
    for requirement in carried:
        requirement.cycle_id = new.id

    # Reopened, not re-proposed: the chairman accepted this obligation once and
    # it stays accepted. What resets is whether it has been honoured THIS turn.
    for requirement in standing:
        requirement.cycle_id = new.id
        if requirement.status == RequirementStatus.MET:
            requirement.status = RequirementStatus.PENDING
            requirement.met_at = None
            requirement.met_by_run_id = None
            policy.record_requirement_event(
                session, requirement, RequirementStatus.PENDING, "janitor",
                note=f"standing requirement reopened for cycle {new.ordinal}",
                from_status=RequirementStatus.MET)

    plan = policy.plan_requirement(session, project.id)
    if plan is not None and plan.status == RequirementStatus.MET:
        from_status = plan.status
        plan.status = RequirementStatus.PENDING
        plan.met_at = None
        policy.record_requirement_event(
            session, plan, RequirementStatus.PENDING, "janitor",
            note=f"cycle {new.ordinal} opened; each cycle plans fresh",
            from_status=from_status)

    _notice_ceo(
        session, project, NoticeKind.CYCLE_ROLLED,
        f"Cycle {cycle.ordinal} closed {cycle.status.upper()} "
        f"({len(met)} met, {len(unsettled)} carried forward"
        + (f": {', '.join(r.key for r in unsettled)}" if unsettled else "") + "). "
        f"Cycle {new.ordinal} is open. Your FIRST act: read the last three cycles' "
        "narratives in your pack, then plan — R-00 reopened, and no work may be "
        "created until the new cycle's plan holds. A lesson still true several "
        "cycles out goes to memory; everything else lives in the narratives.",
    )
    log.info("rolled cycle #%s → #%s for %s (%s)",
             cycle.ordinal, new.ordinal, project.slug, cycle.status)


# ── flags: the obligation to speak ──────────────────────────────────────────────


def evaluate_flags(session: Session, project: Project, cons: ConstitutionDoc) -> None:
    """Recompute the trip conditions, and wake the CEO when one fires.

    In the janitor for the same reason cycles roll here: news the chairman
    needs must not depend on the project having runs left to notice it. The
    notice is what turns an open flag into a wake — a flag nobody is told about
    until the next scheduled status is a flag that arrives after the deadline
    it was warning about.
    """
    from yaaf.server import flags as flag_lib

    try:
        opened = flag_lib.evaluate(session, project, cons)
    except Exception:
        log.warning("flag evaluation failed for %s", project.slug, exc_info=True)
        return
    if not opened:
        return
    _notice_ceo(
        session, project, NoticeKind.FLAG_OPENED,
        f"{opened} flag(s) opened: the platform's own arithmetic says something is "
        "wrong that the chairman has not been told. Read `situation.flags`, say it in "
        "the boardroom with the numbers and what you propose cutting or raising, then "
        "close each with flag_raise. The reporting cadence does not hold you here, and "
        "he can see how many are unraised — so saying nothing is not a quiet option.",
    )


# ── explore: a window, not a scope ──────────────────────────────────────────────


def check_explore_deadline(session: Session, project: Project) -> None:
    """The chairman's own example: "one sale in the next 60 days" — open
    scope, bounded time. When the window closes, both parties are told ONCE
    and the judgment is his; nothing pauses by itself, because an Explore
    project's worth was always his call, not a date's."""
    if project.mode != ProjectMode.EXPLORE or project.deadline is None:
        return
    if utcnow() < project.deadline:
        return
    already = session.execute(
        select(func.count(Notice.id)).where(
            Notice.project_id == project.id,
            Notice.kind == NoticeKind.EXPLORE_DEADLINE)
    ).scalar_one()
    if already:
        return
    usd, tokens = month_to_date(session, project.id)
    _notice_ceo(
        session, project, NoticeKind.EXPLORE_DEADLINE,
        f"The window closed: {project.deadline:%Y-%m-%d} has passed. Say plainly, "
        "in the boardroom, what was earned and what was learned — the chairman is "
        "deciding whether this project earned its keep, and spending on as if the "
        "date had not existed is the one answer that decides it against you.",
    )
    chairman_notify(
        session, project, "explore window closed",
        f"The deadline you set ({project.deadline:%Y-%m-%d}) has passed. "
        f"This month's spend so far: ${usd:,.2f} / {tokens:,} tokens.\n\n"
        "Your call, as it always was: stop it, extend the window (set a new date in "
        "Settings), or convert what worked into a Deliver project with real "
        "requirements. The project keeps working until you decide."
        + project_link(project.id, "settings", "Decide"),
        attention=Attention.ALARM,
    )


# ── deliver: completion is detected here, confirmed by the chairman ─────────────


def delivery_outstanding(session: Session, project: Project) -> list[str]:
    """What still stands between this Deliver project and COMPLETED. Kept here
    as the name every caller already knows; the body moved to `policy` so the
    dispatcher can gate on it without importing this module."""
    return policy.delivery_outstanding(session, project)


#: A delivery the chairman has not confirmed is chased on this rhythm, in the
#: boardroom, where he actually is. The line is platform-authored, so repeating
#: it costs no run and spends no reporting cadence — the only thing it can waste
#: is his attention, and a finished project that keeps waking wastes his money.
DELIVERY_NAG_HOURS = 12.0
DELIVERY_BEAT = "delivery_ready"


def check_delivery_ready(session: Session, project: Project) -> None:
    """Tell the chairman everything is true, and keep telling him until he
    decides. Completion itself is his gate — one of the three moments he chose
    to be asked at — so the janitor detects and the button on the page decides.

    It used to fire ONCE per project, ever, deduped on the audit row alone. That
    is wrong in the exact case it matters: one project went ready, was announced,
    then had its last requirement reopened forty minutes later for real defects.
    It became genuinely ready again two hours after that and no notification ever
    went out, because the single shot had been spent on a state that no longer
    held. So the dedup is now per readiness EPISODE — any requirement moving
    after the last announcement re-arms it.

    Three audiences, because the failure was that only one was told:
    the chairman by email, the chairman in the boardroom (where he was actually
    sitting, asking why the project was still running), and the CEO — which had
    no way at all to learn that its own project had finished.
    """
    if project.mode != ProjectMode.DELIVER:
        return
    if delivery_outstanding(session, project):
        return

    last = session.execute(
        select(AuditLog.at).where(
            AuditLog.project_id == project.id,
            AuditLog.action == "project.completion_ready")
        .order_by(AuditLog.at.desc()).limit(1)
    ).scalar_one_or_none()

    if last is not None:
        # Already announced. Re-announce only if the scope actually moved since
        # — a reopen-and-re-meet is a new delivery, not the same one twice.
        moved = session.execute(
            select(func.count(RequirementEvent.id)).where(
                RequirementEvent.project_id == project.id,
                RequirementEvent.at > last)
        ).scalar_one()
        if not moved:
            if _complete_itself(session, project, last):
                return
            _nag_delivery_ready(session, project)
            return

    met = [r for r in policy.scope_requirements(session, project.id)
           if r.status == RequirementStatus.MET]
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:janitor",
        action="project.completion_ready", entity="project", entity_id=project.id,
        detail={"met": [r.key for r in met]},
    ))
    cons = effective(session, project.id)
    at = chairman_time(utcnow() + timedelta(hours=cons.complete_after_hours), cons.timezone)
    closes = ("Confirm completion on the project page — it writes the delivery receipt "
              "and closes the project down cleanly"
              + (f" — or it completes itself {at} unless scope moves first. Either way "
                 "you can reopen it for 14 days. "
                 if cons.complete_after_hours > 0 else ". "))
    body = (
        f"Every requirement is settled and {len(met)} of them are true: "
        f"{', '.join(r.key for r in met)}. {closes}"
        "Nothing runs in the meantime: a delivered project stops waking for its "
        "own agents' traffic, so it is not spending while it waits."
        + project_link(project.id, "", "Open it")
    )
    chairman_notify(session, project, "ready to complete", body, attention=Attention.ACTION)
    boardroom_line(session, project.id, body)
    _notice_ceo(
        session, project, NoticeKind.DELIVERY_READY,
        f"Delivery is complete: {len(met)} requirement(s) in scope are true and "
        "nothing is open. The chairman has been told; the project now waits for his "
        "confirmation"
        + (f", and closes itself in {cons.complete_after_hours:g}h if scope does not move"
           if cons.complete_after_hours > 0 else "")
        + " — it will not wake for agent-to-agent messages meanwhile, and new scope "
        "is refused. Do not invent work to fill the "
        "quiet. If you believe something is genuinely missing, say so in the "
        "boardroom; reopening the plan is his call, not yours.",
    )
    # The beat starts now, so the first chase lands a full window from the
    # announcement rather than on the janitor's next tick.
    _touch_delivery_beat(session, project)


def _complete_itself(session: Session, project: Project, announced_at) -> bool:
    """Close a delivery that has stood finished for `complete_after_hours`
    since he was told, with the receipt his own Complete writes. Not when the
    rule is 0, not on a project he paused, and not after he reopened it: a
    reopened delivery closes again only once its scope has moved, which starts
    a new announcement and a new clock."""
    from yaaf.server.api.admin import REOPEN_DAYS, complete_delivery

    cons = effective(session, project.id)
    if cons.complete_after_hours <= 0 or project.status != ProjectStatus.RUNNING:
        return False
    if announced_at.tzinfo is None:
        announced_at = announced_at.replace(tzinfo=UTC)
    if utcnow() - announced_at < timedelta(hours=cons.complete_after_hours):
        return False
    reopened = session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.project_id == project.id, AuditLog.action == "project.reopen",
            AuditLog.at > announced_at)
    ).scalar_one()
    if reopened:
        return False
    told = chairman_time(announced_at, cons.timezone)
    done = complete_delivery(
        session, project, by_user_id=None,
        how=(f"Completed by the platform: every requirement had stood settled for "
             f"{cons.complete_after_hours:g}h since you were told ({told}), and the "
             "rules close a finished delivery then."))
    receipt = done["receipt"]
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:janitor",
        action="project.auto_complete", entity="project", entity_id=project.id,
        detail={**receipt, "voided_approvals": done["voided_approvals"]},
    ))
    money = f"${receipt['cost_usd']:,.2f}"
    body = (f"Delivered: {receipt['met']} requirement(s) true, {receipt['cut']} cut, "
            f"{receipt['runs_total']} runs, {money}, {receipt['duration_days']} days. "
            f"It had stood finished since {told}, so it closed itself and wrote the "
            f"receipt. If something is missing, reopen it on the project page within "
            f"{REOPEN_DAYS} days." + project_link(project.id, "deliverables", "See what was delivered"))
    chairman_notify(session, project, "delivered and closed", body, attention=Attention.ACTION)
    boardroom_line(session, project.id, body)
    log.info("delivery %s completed itself", project.slug)
    return True


def _delivery_beat(session: Session, project: Project) -> Heartbeat | None:
    ceo = policy.ceo_of(session, project.id)
    if ceo is None:
        return None
    return session.execute(
        select(Heartbeat).where(Heartbeat.agent_id == ceo.id,
                                Heartbeat.name == DELIVERY_BEAT)
    ).scalar_one_or_none()


def _touch_delivery_beat(session: Session, project: Project) -> None:
    ceo = policy.ceo_of(session, project.id)
    if ceo is None:
        return
    beat = _delivery_beat(session, project)
    if beat is None:
        session.add(Heartbeat(agent_id=ceo.id, name=DELIVERY_BEAT, last_fired_at=utcnow()))
    else:
        beat.last_fired_at = utcnow()


def _nag_delivery_ready(session: Session, project: Project) -> None:
    """Chase an unconfirmed delivery in the boardroom, on DELIVERY_NAG_HOURS.

    Boardroom only: the email was already sent and a second one changes nothing,
    but the boardroom is mirrored to Slack and is where he asks the CEO what is
    going on. It costs no run — the dispatcher never wakes an agent for a
    sender-less line.
    """
    beat = _delivery_beat(session, project)
    now = utcnow()
    if beat is not None and beat.last_fired_at > now - timedelta(hours=DELIVERY_NAG_HOURS):
        return
    met = len([r for r in policy.scope_requirements(session, project.id)
               if r.status == RequirementStatus.MET])
    _touch_delivery_beat(session, project)
    boardroom_line(
        session, project.id,
        f"Still waiting on you: all {met} requirement(s) are true and this delivery "
        "has not been confirmed. Complete it on the project page to write the receipt "
        "and close it, or reopen scope if something is missing"
        + (" — otherwise it closes itself when your rules say"
           if effective(session, project.id).complete_after_hours > 0 else "")
        + ". The project is not spending while it waits.",
    )


# ── the review: opened, numbered and emailed with zero tokens ───────────────────

REVIEW_NAG_HOURS = 24       # an open review the chairman has not closed is chased once
REVIEW_ESCALATE_HOURS = 72  # and chased harder here — but the work never stops for it


def review_numbers(session: Session, project: Project, cons: ConstitutionDoc,
                   prior: Review | None) -> dict:
    """The platform's half of the meeting, from the same producers every
    screen reads — velocity, the commitment projection, month-to-date spend,
    audit coverage — frozen into the review row so the record cannot drift as
    the week moves on. The prior review's decisions ride along beside what
    they bought, which is what makes a lever accountable next time."""
    from yaaf.server.velocity import commitment_projection, project_velocity

    now = utcnow()
    velocity = project_velocity(session, project.id)
    usd, tokens = month_to_date(session, project.id)
    scope = policy.scope_requirements(session, project.id)
    since = prior.opened_at if prior is not None else project.created_at

    commitment = policy.live_commitment(session, project.id)
    commitment_block = None
    if commitment is not None:
        projection = commitment_projection(session, project, cons)
        commitment_block = {
            "status": commitment.status,
            "deliver_by": commitment.deliver_by.isoformat(),
            "projected_done_at": (projection["projected_done_at"].isoformat()
                                  if projection["known"] else None),
            "basis": projection.get("basis") or projection.get("why"),
        }

    samples = session.execute(
        select(AuditSample).where(AuditSample.project_id == project.id,
                                  AuditSample.drawn_at >= since)
    ).scalars().all()
    met_since = session.execute(
        select(func.count(RequirementEvent.id)).where(
            RequirementEvent.project_id == project.id,
            RequirementEvent.to_status == RequirementStatus.MET,
            RequirementEvent.at >= since,
        )
    ).scalar_one()

    numbers = {
        "as_of": now.isoformat(),
        "goal": project.goal[:2000],
        "velocity": velocity,
        "commitment": commitment_block,
        "budget": {
            "unit": "USD" if project.billing_mode == "api_key" else "tokens",
            "spent_mtd": usd if project.billing_mode == "api_key" else tokens,
            "cap": (cons.monthly_usd_cap if project.billing_mode == "api_key"
                    else cons.monthly_token_cap),
        },
        "scope": {
            "open": len([r for r in scope if r.status in policy.OPEN_REQUIREMENT_STATUSES]),
            "proposed": len([r for r in scope if r.status == RequirementStatus.PROPOSED]),
            "met": len([r for r in scope if r.status == RequirementStatus.MET]),
            "cut": len([r for r in scope if r.status == RequirementStatus.CUT]),
            "met_since_last_review": int(met_since),
        },
        "hypotheses": [
            {"statement": h.statement[:300], "kill_date": h.kill_date.isoformat(),
             "status": h.status}
            for h in session.execute(
                select(Hypothesis).where(
                    Hypothesis.project_id == project.id,
                    Hypothesis.status.in_([HypothesisStatus.ACTIVE,
                                           HypothesisStatus.EXPIRED]))
            ).scalars()
        ],
        "audit_coverage": {
            "considered": len(samples),
            "sampled": len([s for s in samples if s.status != AuditSampleStatus.SKIPPED]),
            "failed": len([s for s in samples if s.status == AuditSampleStatus.FAILED]),
            "expired_unreviewed": len(
                [s for s in samples if s.status == AuditSampleStatus.EXPIRED]),
        },
    }
    if prior is not None:
        numbers["prior"] = {
            "number": prior.number,
            "opened_at": prior.opened_at.isoformat(),
            "decisions": prior.decisions or [],
            # What the week's levers bought, in the only currencies that
            # count here: things made true, and the pace they happened at.
            "bought": {
                "met_since": int(met_since),
                "pace_then": (prior.numbers or {}).get("velocity", {}).get("per_week_3w"),
                "pace_now": velocity["per_week_3w"],
            },
        }
    return numbers


def open_reviews(session: Session, project: Project, cons: ConstitutionDoc) -> Review | None:
    """Open the standing meeting when it is due — with zero tokens, regardless
    of budgets, board state, or anything else. A review with an empty board
    still fires: that is the one the chairman most needs."""
    latest = session.execute(
        select(Review).where(Review.project_id == project.id)
        .order_by(Review.number.desc()).limit(1)
    ).scalar_one_or_none()
    if latest is not None and latest.status == ReviewStatus.OPEN:
        return None  # one meeting at a time; an unattended one nags instead
    interval = timedelta(days=cons.review_interval_days)
    if latest is None:
        due = project.created_at + interval
        number = 1
    else:
        due = max(latest.scheduled_for + interval,
                  (latest.closed_at or latest.opened_at))
        number = latest.number + 1
    now = utcnow()
    if now < due:
        return None

    numbers = review_numbers(session, project, cons, latest)
    review = Review(
        id=ids.new_id(ids.REVIEW),
        project_id=project.id,
        number=number,
        scheduled_for=due,
        numbers=numbers,
    )
    session.add(review)
    session.flush()

    velocity = numbers["velocity"]
    commitment = numbers["commitment"]
    prior_lines = ""
    if numbers.get("prior", {}).get("decisions"):
        bought = numbers["prior"]["bought"]
        prior_lines = (
            f"\nLast review's decisions ({len(numbers['prior']['decisions'])}): "
            + "; ".join(f"{d['kind']} — {d['reason'][:80]}"
                        for d in numbers["prior"]["decisions"])
            + f"\nWhat they bought: {bought['met_since']} requirement(s) met since, "
              f"pace {bought['pace_then']} → {bought['pace_now']}/wk.\n"
        )
    chairman_notify(
        session, project, f"review #{number} is open",
        f"The standing review for {project.name} is open. The numbers:\n\n"
        f"  goal: {project.goal[:300]}\n"
        f"  pace: {velocity['per_week_3w']}/wk (3w), {velocity['per_week_12w']}/wk (12w); "
        f"{velocity['remaining']} requirement(s) open\n"
        f"  met since last review: {numbers['scope']['met_since_last_review']}\n"
        + (f"  commitment: {commitment['status']} — promised "
           f"{commitment['deliver_by'][:10]}, projected "
           f"{(commitment['projected_done_at'] or 'unknown')[:10]}\n" if commitment else "")
        + f"  budget: {numbers['budget']['spent_mtd']:,.0f} of "
          f"{numbers['budget']['cap']:,.0f} {numbers['budget']['unit']} this month\n"
        f"  audit coverage since last review: "
        f"{numbers['audit_coverage']['sampled']} sampled, "
        f"{numbers['audit_coverage']['failed']} failed, "
        f"{numbers['audit_coverage']['expired_unreviewed']} expired unreviewed\n"
        f"{prior_lines}\n"
        "The CEO is filing its variance narrative on a reserved governance run. "
        "Take the meeting on the Review tab — your levers there are cut, add, "
        "reword, move the date, raise the budget, and accept the slip; each is "
        "recorded with your reason and shown at the next review beside what it "
        "bought.",
        attention=Attention.ACTION,
    )
    _notice_ceo(
        session, project, NoticeKind.REVIEW_OPENED,
        f"Review #{number} is open. File your variance narrative with "
        "review_narrative: what moved against the plan since last review, the "
        "SINGLE largest cause by name, and what you recommend he cut or move. "
        "This wake may spend the governance reserve — the narrative is why the "
        "reserve exists. The platform's numbers are in your pack; do not restate "
        "them, explain them.",
    )
    log.info("opened review #%s for %s", number, project.slug)
    return review


def nag_reviews(session: Session, project: Project) -> None:
    """An unattended meeting chases the chairman — once politely, once loudly —
    and never stops the work. The escalation is a louder email and an audit
    row, not a brake: a governance meeting that could halt the project would
    be a new failure mode, not a fix."""
    review = session.execute(
        select(Review).where(Review.project_id == project.id,
                             Review.status == ReviewStatus.OPEN)
    ).scalar_one_or_none()
    if review is None:
        return
    now = utcnow()
    age_hours = (now - review.opened_at).total_seconds() / 3600
    if (review.nag_sent_at is None and age_hours >= REVIEW_NAG_HOURS
            and _review_needs_nobody(review)):
        _close_green_review(session, project, review)
        return
    if review.nag_sent_at is None and age_hours >= REVIEW_NAG_HOURS:
        chairman_notify(
            session, project, f"review #{review.number} is waiting on you",
            f"It opened {age_hours:.0f}h ago"
            + (" and the CEO's narrative is in" if review.narrative else
               " (the CEO has not filed its narrative yet)")
            + ". The project keeps working either way — but decisions deferred "
              "are decisions the arithmetic makes for you.",
            attention=Attention.ACTION,
        )
        review.nag_sent_at = now
    elif review.escalated_at is None and age_hours >= REVIEW_ESCALATE_HOURS:
        chairman_notify(
            session, project, f"review #{review.number} has been open {age_hours / 24:.0f} days",
            "This is the second reminder; there will not be a third. Close it with "
            "no decisions if that is the decision — an explicit 'no change' is a "
            "record, an open meeting is a hole.",
            attention=Attention.ACTION,
        )
        review.escalated_at = now
        session.add(AuditLog(
            id=ids.new_id(ids.AUDIT),
            project_id=project.id,
            actor="system:janitor",
            action="review.escalated",
            entity="review",
            entity_id=review.id,
            detail={"number": review.number, "open_hours": round(age_hours)},
        ))


def _review_needs_nobody(review: Review) -> str | None:
    """Why this meeting has nothing for him, or None if it might. All four,
    from the numbers frozen when it opened and the CEO's own filing: the CEO
    explained the period and recommended no change; the promised date (if
    any) still holds; no spot-check failed; the month is inside its plan.
    Anything else — no narrative yet, a recommendation, a date at risk, a
    failed audit, an overrun — is a meeting worth having, and it chases him."""
    numbers = review.numbers or {}
    if not review.narrative or review.recommended:
        return None
    commitment = numbers.get("commitment") or {}
    if commitment.get("status") in (CommitmentStatus.AT_RISK, CommitmentStatus.BREACHED):
        return None
    if (numbers.get("audit_coverage") or {}).get("failed"):
        return None
    budget = numbers.get("budget") or {}
    if budget.get("cap") and (budget.get("spent_mtd") or 0) > budget["cap"]:
        return None
    return ("every number was inside its plan and the CEO recommended no change")


def _close_green_review(session: Session, project: Project, review: Review) -> None:
    """Close a review nobody needed, as "no changes", and say so — with the
    CEO's narrative, so what he would have read in the meeting still reaches
    him. Every lever the meeting offered stays on the Roadmap and in Settings,
    where it always was."""
    from yaaf.server.api.admin import finish_review

    why = _review_needs_nobody(review)
    finish_review(session, project, review, by_user_id=None,
                  note=f"Closed by the platform: {why}.")
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:janitor",
        action="review.auto_close", entity="review", entity_id=review.id,
        detail={"number": review.number, "why": why}))
    chairman_notify(
        session, project, f"review #{review.number} closed itself — nothing needed you",
        f"Review #{review.number} closed with no changes after {REVIEW_NAG_HOURS}h: {why}. "
        f"The CEO's account of the period:\n\n{(review.narrative or '')[:3000]}\n\n"
        "Nothing was decided for you. Every lever the meeting offered — cut, add, move "
        "the date, raise the budget — is still on the Roadmap and in Settings."
        + project_link(project.id, "reviews", "Read the review"),
        attention=Attention.FYI,
    )


# ── the sampled audit: drawn after the fact, never blocking ─────────────────────


def draw_audit_samples(session: Session, project: Project, cons: ConstitutionDoc) -> int:
    """Roll the dice on every observable close that has not been considered.

    Observable closes are the ones taken on the claimant's word — a command
    test the platform already ran needs no audit. The draw happens HERE,
    server-side, after the close, at the constitution's rate: no run token can
    know which claim will be checked, and that unpredictability is the entire
    mechanism. Every considered close gets a row — drawn or skipped — so
    nothing is ever re-rolled and the review's coverage line has a
    denominator.
    """
    if cons.audit_sample_rate <= 0:
        return 0
    considered = {
        task_id for (task_id,) in session.execute(
            select(AuditSample.task_id).where(AuditSample.project_id == project.id))
    }
    drawn = 0
    for task in session.execute(
        select(Task).where(Task.project_id == project.id, Task.status == TaskStatus.DONE)
    ).scalars():
        if task.id in considered:
            continue
        if (task.success_test or {}).get("type") != SuccessTestKind.OBSERVABLE:
            continue
        sampled = random.random() < cons.audit_sample_rate
        session.add(AuditSample(
            id=ids.new_id(ids.AUDIT_SAMPLE),
            project_id=project.id,
            task_id=task.id,
            status=AuditSampleStatus.PENDING if sampled else AuditSampleStatus.SKIPPED,
            expires_at=(utcnow() + timedelta(hours=cons.audit_response_hours)
                        if sampled else None),
        ))
        if sampled:
            drawn += 1
            chairman_notify(
                session, project,
                f"audit sample: {task.title[:80]}",
                f"The platform drew this observable close for your look "
                f"({cons.audit_sample_rate:.0%} sample rate):\n\n"
                f"  task: {task.title}\n"
                f"  test: {(task.success_test or {}).get('target', '')[:300]}\n"
                f"  claimed evidence: {(task.completion_evidence or '')[:500]}\n\n"
                f"Confirm or fail it on the Work screen within "
                f"{cons.audit_response_hours}h; after that it is recorded as "
                "'not reviewed' and nothing blocks either way.",
                attention=Attention.FYI,
            )
    return drawn


def expire_audit_samples(session: Session, project: Project) -> int:
    """A sample the chairman never answered records "not reviewed" and moves
    on. It NEVER blocks — the whole design is gates at the moments he chose
    plus a thermometer in between, and a thermometer that stops the line is a
    gate he did not choose."""
    now = utcnow()
    expired = 0
    for sample in session.execute(
        select(AuditSample).where(
            AuditSample.project_id == project.id,
            AuditSample.status == AuditSampleStatus.PENDING,
            AuditSample.expires_at <= now,
        )
    ).scalars():
        sample.status = AuditSampleStatus.EXPIRED
        sample.decided_at = now
        sample.note = "not reviewed in time"
        expired += 1
    return expired


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
    run.priced_usd = None if subscription else data.get("priced_usd")

    total = (run.tokens_in + run.tokens_out
             + run.cache_creation_tokens + run.cache_read_tokens)
    if not total and not run.cost_usd:
        return False

    session.add(
        LedgerEntry(
            id=ids.new_id(ids.LEDGER),
            project_id=project.id,
            kind=LedgerKind.MODEL_USAGE,
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


def release_unreadable_reviews(session: Session, project: Project) -> int:
    """A task waiting on a read that nobody left alive can give.

    `review_is_owed` asks the same question before the fork, so a project with
    no reviewer never sends work to REVIEW in the first place. This is the
    other order: the task went in while a reader existed, and then the reader
    was paused, deleted or refiled. Nothing else clears it — the janitor's
    claim reaper only looks at CLAIMED, `lost_checks` only at CHECKING — and
    REVIEW counts against its author's one-task-at-a-time slot, so the wedge is
    not one task but the whole seat, permanently.

    It closes rather than returning to the author, for the reason the roster
    rule already states: a task stuck in REVIEW forever is worse than the
    self-report it replaced. The event says the read never happened, so the
    audit trail does not claim a second pair of eyes that nobody had.
    """
    waiting = list(session.execute(
        select(Task).where(Task.project_id == project.id,
                           Task.status == TaskStatus.REVIEW)
    ).scalars())
    if not waiting or policy.review_seats(session, project.id):
        return 0
    for task in waiting:
        from_status = task.status
        task.status = TaskStatus.DONE
        policy.record_task_event(
            session, task, TaskStatus.DONE, "janitor",
            note="closed unread: the project has no reviewing seat left",
            from_status=from_status)
    return len(waiting)


# ── lost checks: a wedge nothing else will ever clear ───────────────────────────

# After this many requeues for the same stay in CHECKING, the CEO is told once:
# a check that keeps dying is the checker infrastructure failing, and the one
# agent that can re-plan around it deserves to know before the chairman asks.
LOST_CHECKS_BEFORE_ESCALATION = 3


def requeue_lost_checks(session: Session, project: Project) -> int:
    """A task or requirement in CHECKING whose check run no longer exists gets
    its check queued again — enforcement by state change, like everything here.

    CHECKING is a promise that the platform will move the row itself, so
    nothing else watches it: it wakes nobody, counts as no one's work, and a
    board holding only CHECKING rows reads as idle. When the check run behind
    it is lost — reaped mid-flight, or its queue row purged by a pause — the
    promise is broken silently and permanently: the task never closes, its
    requirement stays OPEN, and every stage behind it is gated. One project
    sat wedged exactly this way for 80 minutes with no alarm, until the
    chairman asked why nothing was happening. Requeuing is idempotent (a
    queued check makes the row no longer lost) and each requeue is audited.
    """
    tasks, requirements = policy.lost_checks(session, project.id)
    requeued = 0
    for task in tasks:
        session.add(RunQueue(
            id=ids.new_id(ids.RUN),
            project_id=project.id,
            agent_id=None,
            kind=RunKind.SUCCESS_CHECK,
            trigger={"type": "success_check", "task_id": task.id},
            task_id=task.id,
        ))
        # No CHECKING→CHECKING task event: the row's status has not moved, and
        # the audit row below is the record. (It would also reset the counter
        # `task_checking_since` feeds.)
        prior = _audit_requeue(session, project, "task", task.id,
                               policy.task_checking_since(session, task.id))
        if prior + 1 == LOST_CHECKS_BEFORE_ESCALATION:
            _notice_ceo(
                session, project, NoticeKind.TASK_CHECK_FAILED,
                f"The platform has now lost {prior + 1} runs of the success test for "
                f"task '{task.title}' ({task.id}) — each check died before reporting a "
                "result, which is the checker infrastructure failing, not the work. "
                "The test is queued again and the task stays in checking; the "
                "deliverable may well be fine. Do not plan around this task as if it "
                "had closed, and if it stays wedged, tell the chairman plainly that "
                "the platform cannot run its checks.",
            )
        requeued += 1
        log.warning("requeued lost success check for task %s (%s), attempt %s",
                    task.id, project.slug, prior + 1)
    for requirement in requirements:
        session.add(RunQueue(
            id=ids.new_id(ids.RUN),
            project_id=project.id,
            agent_id=None,
            kind=RunKind.REQUIREMENT_CHECK,
            trigger={"type": "requirement_check", "requirement_id": requirement.id},
            requirement_id=requirement.id,
        ))
        # No CHECKING→CHECKING requirement event: `_work_closed_since_last_check`
        # reads events with from_status=CHECKING as rulings, and a requeue is
        # not a ruling. The audit row below is the record.
        prior = _audit_requeue(session, project, "requirement", requirement.id,
                               policy.requirement_checking_since(session, requirement.id))
        if prior + 1 == LOST_CHECKS_BEFORE_ESCALATION:
            _notice_ceo(
                session, project, NoticeKind.GOAL_CHANGED,
                f"The platform has now lost {prior + 1} runs of the acceptance test for "
                f"{requirement.key} — each check died before reporting a result. The "
                "requirement stays in checking (it still counts as open, so it gates "
                "the stages behind it) and the test is queued again. This is the "
                "checker failing, not the work: if it stays wedged, tell the chairman "
                "plainly that the platform cannot run its checks.",
            )
        requeued += 1
        log.warning("requeued lost acceptance check for %s (%s), attempt %s",
                    requirement.key, project.slug, prior + 1)
    if requeued:
        notify(session, "yaaf_run_queue", project.id)
    return requeued


def _audit_requeue(session: Session, project: Project, entity: str, entity_id: str,
                   since) -> int:
    """Record this requeue and return how many came before it in the same stay
    in CHECKING. The audit trail is the counter, exactly as the verdict nag's
    cadence is: a lost check across two different completions of the same task
    starts a fresh count, because `since` moves."""
    stmt = select(func.count(AuditLog.id)).where(
        AuditLog.project_id == project.id,
        AuditLog.action == f"{entity}.check_requeued",
        AuditLog.entity_id == entity_id,
    )
    if since is not None:
        stmt = stmt.where(AuditLog.at >= since)
    prior = session.execute(stmt).scalar_one()
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:janitor",
        action=f"{entity}.check_requeued", entity=entity, entity_id=entity_id,
        detail={"attempt": int(prior) + 1},
    ))
    return int(prior)


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
        # The case first and the clock last, in his timezone and in words:
        # "expires at 2026-10-16 19:00 UTC, the timeout decision is DENY" was
        # true and made him do a sum and a translation before he could tell
        # whether it was tonight, and what silence would mean.
        silence = "yes" if cons.approval_timeout_decision == "approve" else "no"
        chairman_notify(
            session, project,
            f"approval expiring: {approval.title}",
            f"{approval.body[:4000]}\n\n"
            f"If you have not decided by {chairman_time(approval.expires_at, cons.timezone)}, "
            f"the answer is {silence} — that is what this project's rules say.\n"
            + inbox_line(approval.id),
            kind="approval_expiring", data={"approval": approval_row(approval)},
            attention=Attention.ACTION,
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
        # The answer silence gave, in the words he would have used, and what
        # to do if it was the wrong one — "DENIED per this constitution" told
        # him a rule fired, not what happens now.
        chairman_notify(
            session, project,
            f"no decision, so {'yes' if approve_on_timeout else 'no'}: {approval.title}",
            f"'{approval.title}' ran out of time without your decision. Your rules answer "
            + ("yes on silence, so it is approved and the CEO is going ahead."
               if approve_on_timeout else
               "no on silence, so it is declined and the CEO will plan without it. "
               "If you wanted it, tell the CEO in chat and it will ask again.")
            + project_link(project.id, "activity/decisions", "See the decision"),
            kind="approval_expired", data={"approval": approval_row(approval)},
            attention=Attention.FYI,
        )
        _notice_agent(
            session, project, approval.requested_by_agent_id, NoticeKind.APPROVAL_EXPIRED,
            f"Approval '{approval.title}' expired unanswered and is "
            f"{'APPROVED per this constitution' if approve_on_timeout else 'DENIED by default'}. "
            + ("Proceed accordingly." if approve_on_timeout
               else "Re-plan without it, or resubmit with a stronger recommendation."),
        )
        expired += 1
    return expired


# ── asks: his half of a question, on a clock ────────────────────────────────────

#: How far into the window the chase goes out. An approval nags at a fixed 48h
#: because its window is measured in days; an ask's window can be six hours, so
#: a fixed horizon would either fire instantly or never. Proportional is the
#: only thing that means the same at both scales.
ASK_NAG_FRACTION = 0.5


def answer_asks(session: Session, project: Project) -> int:
    """Anything he said in the boardroom after the question answers it.

    Mechanical on purpose. "Has he answered" is a question about whether a
    message exists; leaving it to the CEO to judge would mean a CEO could
    conclude it had not been answered and keep the row open, or conclude it had
    and close one he invented an answer to.
    """
    from yaaf.core.models import Channel, ChannelKind

    boardroom = session.execute(
        select(Channel).where(Channel.project_id == project.id,
                              Channel.kind == ChannelKind.BOARDROOM)
    ).scalars().first()
    if boardroom is None:
        return 0
    return ask_lib.mark_answered(session, project.id, boardroom.id)


def nag_asks(session: Session, project: Project, cons: ConstitutionDoc) -> int:
    """Chase him once, partway through the window he was given."""
    now = utcnow()
    nagged = 0
    for ask in ask_lib.open_asks(session, project.id):
        if ask.nag_sent_at is not None:
            continue
        span = (ask.answer_by - ask.created_at).total_seconds()
        if span <= 0 or now < ask.created_at + timedelta(seconds=span * ASK_NAG_FRACTION):
            continue
        chairman_notify(
            session, project,
            f"your CEO is waiting on you: {ask.title}",
            f"Your CEO asked you this on {chairman_time(ask.created_at, cons.timezone)}:"
            f"\n\n{ask.body[:4000]}\n\n"
            f"Answer in the boardroom by {chairman_time(ask.answer_by, cons.timezone)}. "
            f"If you say nothing, your CEO will: {ask.default_action}",
            attention=Attention.ACTION,
        )
        ask.nag_sent_at = now
        nagged += 1
    return nagged


def expire_asks(session: Session, project: Project) -> int:
    """The window closes and the CEO's own stated default becomes the plan.

    This notice is the load-bearing part of the whole mechanism. A project that
    is waiting on an answer has, by construction, closed the work that raised
    the question — which empties the board, which suppresses the planning wake.
    There is no next run to fold this into. So ASK_LAPSED is urgent, and it is
    what makes the default actually happen rather than merely be on file.
    """
    now = utcnow()
    lapsed = 0
    for ask in session.execute(
        select(Ask).where(Ask.project_id == project.id,
                          Ask.status == AskStatus.OPEN,
                          Ask.answer_by <= now)
    ).scalars():
        ask.status = AskStatus.LAPSED
        ask.lapsed_at = now
        _notice_agent(
            session, project, ask.asked_by_agent_id, NoticeKind.ASK_LAPSED,
            f"Your question '{ask.title}' reached {ask.answer_by:%Y-%m-%d %H:%M} UTC with "
            f"no answer from the chairman. What you said you would do: {ask.default_action}\n\n"
            "Do that now — it is what he was told would happen, so doing something else "
            "without telling him is worse than never having asked. Then post what you did "
            "in the boardroom (the cadence does not hold this) and close the ask citing "
            "that message.",
        )
        lapsed += 1
    return lapsed


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
        ).where(LedgerEntry.project_id == project_id,
                LedgerEntry.at >= month_start,
                # Revenue is money coming IN — summing it here once auto-paused
                # a project for earning. Spend-by-default: any future kind
                # counts as spend unless it is revenue.
                LedgerEntry.kind != LedgerKind.REVENUE)
    ).one()
    return float(usd), int(tokens)


#: How far back the rate looks. Long enough that a single build day cannot be
#: the whole sample, short enough that a project which changed shape last week
#: is not judged on what it used to be.
BURN_WINDOW_DAYS = 14

#: Below this many spending days there is no rate, only a couple of numbers.
#: Saying so is the honest output; inventing a slope from two points is how a
#: desk told its chairman 3M/day, then 5M, then 35M, inside two hours.
MIN_DAYS_FOR_RATE = 3

#: A build day and a quiet day are different animals. When the busiest day in
#: the window is this many times the median, the days are not alike and no
#: single number describes them — the mean in particular is a fiction.
SPREAD_IS_LUMPY = 3.0


def daily_spend(session: Session, project_id: str, days: int = BURN_WINDOW_DAYS
                ) -> list[tuple[str, float, int]]:
    """Spend per calendar day over the trailing window, in the project's own
    unit. One row per day that actually spent — a project does not get a rate
    for days before it existed."""
    since = utcnow() - timedelta(days=days)
    usd_mode = _is_usd(session, project_id)
    amount = (func.sum(LedgerEntry.usd) if usd_mode else
              func.sum(LedgerEntry.tokens_in + LedgerEntry.tokens_out
                       + LedgerEntry.cache_creation_tokens
                       + LedgerEntry.cache_read_tokens))
    day = func.date(LedgerEntry.at)
    rows = session.execute(
        select(day, amount, func.count(LedgerEntry.id))
        .where(LedgerEntry.project_id == project_id,
               LedgerEntry.at >= since,
               LedgerEntry.kind != LedgerKind.REVENUE)
        .group_by(day).order_by(day)
    ).all()
    return [(str(d), float(total or 0), int(n)) for d, total, n in rows if total]


def _is_usd(session: Session, project_id: str) -> bool:
    project = session.get(Project, project_id)
    return bool(project and project.billing_mode == "api_key")


def burn_rate(session: Session, project_id: str, cap: float, spent: float) -> dict:
    """What this project actually burns per day — measured, not divided.

    The first version of this divided month-to-date by the day of the month,
    which is what the dashboard has always done and is wrong in two directions
    at once. A project that started on the 27th had its four days of spend
    divided by thirty, reporting 4.7M/day against a real 18M. And a CEO doing
    the same sum the other way — 140M over four days — got 35M/day, when one
    of those four days was the 90M build day and the two most recent were 21M
    and 16M and falling. Both numbers were means over a window that did not
    match the question, and the chairman was handed each of them in turn.

    So: a series, a median, and an explicit refusal to project when the days
    are not alike. The median is the headline because a build day is real
    spend that says nothing about tomorrow, and the mean lets one of them
    speak for the whole window.

    When there is not enough to project, `projectable` is false and the note
    says so in the words the CEO should use. "I do not know yet, and here is
    what I need to find out" is a better answer than a confident number, and
    the platform has never once told an agent that.
    """
    series = daily_spend(session, project_id)
    days_observed = len(series)
    amounts = sorted(total for _, total, _ in series)
    median = amounts[len(amounts) // 2] if amounts else 0.0
    mean = (sum(amounts) / len(amounts)) if amounts else 0.0
    busiest = amounts[-1] if amounts else 0.0
    lumpy = bool(median and busiest / median >= SPREAD_IS_LUMPY)
    projectable = days_observed >= MIN_DAYS_FOR_RATE and not lumpy

    lands_on = None
    if projectable and median > 0 and 0 < spent < cap:
        landing = utcnow() + timedelta(days=(cap - spent) / median)
        next_month = (utcnow().replace(day=1) + timedelta(days=32)).replace(day=1)
        if landing < next_month:
            lands_on = landing

    if days_observed == 0:
        note = "Nothing has been spent yet, so there is no rate to report."
    elif days_observed < MIN_DAYS_FOR_RATE:
        note = (
            f"Only {days_observed} day(s) of spend exist. That is not a rate and you "
            "must not present it as one. If the chairman asks what this will cost, "
            "say plainly that you do not know yet, show him the days you have, and "
            "propose a measurement window with an allowance to fund it — then bring "
            "him the measured number. A confident estimate you cannot support costs "
            "more than an honest 'not yet'."
        )
    elif lumpy:
        note = (
            f"These days are not alike: the busiest was {busiest:,.0f} against a "
            f"median of {median:,.0f}. One build day in the sample means no single "
            "number describes this project yet — do NOT average them and quote the "
            "result. Say which days were exceptional and why, give the median as the "
            "ordinary day, and say plainly that the steady state is still unproven."
        )
    elif lands_on is not None:
        note = (
            f"At the median day of {median:,.0f} this reaches the cap on "
            f"{lands_on:%-d %B}, before the month resets. Say so now, with the "
            "number, rather than after the platform pauses the project."
        )
    else:
        note = (
            f"The median day is {median:,.0f} and does not reach the cap before the "
            "month resets."
        )
    return {
        "unit_per_day_median": round(median, 2),
        "unit_per_day_mean": round(mean, 2),
        "busiest_day": round(busiest, 2),
        "days_observed": days_observed,
        "window_days": BURN_WINDOW_DAYS,
        "days_are_alike": not lumpy,
        "projectable": projectable,
        "series": [{"day": d, "spent": round(total, 2), "runs": n}
                   for d, total, n in series],
        "lands_on_cap_at": lands_on.isoformat() if lands_on else None,
        "note": note,
    }


def run_cost_profile(session: Session, project_id: str,
                     days: int = BURN_WINDOW_DAYS) -> dict:
    """What each KIND of run actually costs here, measured.

    Without this the CEO has one blended average and reasons from it, which is
    how one of them told its chairman that a message-reading wake "costs nearly
    the same as a run that produces something" and priced forty of them at 22M.
    They cost a third of a work run and came to 8.9M. The blended figure is
    real and is the wrong number to multiply by, because the kinds differ by
    4x and the mix changes every day.

    A CEO asked what a stripped day would cost can now build the answer out of
    parts it has measured, instead of out of one average and an intention.
    """
    since = utcnow() - timedelta(days=days)
    total = (Run.tokens_in + Run.tokens_out
             + Run.cache_creation_tokens + Run.cache_read_tokens)
    rows = session.execute(
        select(Run.kind, func.count(Run.id), func.avg(total), func.sum(total))
        .where(Run.project_id == project_id, Run.created_at >= since)
        .group_by(Run.kind).order_by(func.sum(total).desc())
    ).all()
    kinds = [
        {"kind": kind, "runs": int(n),
         "avg_tokens": int(avg or 0), "total_tokens": int(summed or 0)}
        for kind, n, avg, summed in rows
    ]
    return {
        "window_days": days,
        "by_kind": kinds,
        "note": (
            "Measured over this project's own runs. Build a forecast from these "
            "parts — runs of each kind times its own cost — never from one blended "
            "average across kinds that differ several-fold. If a kind has few runs "
            "here, say the estimate is thin rather than smoothing over it."
            if kinds else
            "No runs in the window, so there is nothing measured to build an "
            "estimate from. Say that rather than reaching for a number."
        ),
    }



def enforce_budget(session: Session, project: Project, cons: ConstitutionDoc) -> None:
    """Three thresholds, and only the last one stops anything.

    This used to be one number that both planned the month and killed the
    project, so the plan was never a plan — it was a cliff, and every CEO on
    this platform stood well back from it. Three of them refused money their
    chairman offered. That reads as timidity and is really arithmetic: when
    crossing your own budget is fatal, promising less is always the winning
    move, and the chairman ends up unable to tell a safe number from an honest
    one. He said so: "the confidence in the entire harness of yaaf is lost."

    So: WARN approaching the plan, RECORD AN OVERRUN on crossing it — an
    obligation to say what the money bought, not a stop — and PAUSE only at
    the hard stop, which exists so a runaway cannot drain the account and is
    set to a multiple of the plan rather than equal to it.
    """
    from yaaf.server import budget as budget_lib
    from yaaf.server import flags as flag_lib

    usd, tokens = month_to_date(session, project.id)
    budget, unit = budget_lib.budget_of(project, cons)
    spent = usd if project.billing_mode == "api_key" else float(tokens)
    if budget <= 0:
        return
    hard_stop = budget_lib.hard_stop_of(project, cons)

    if spent >= hard_stop:
        project.status = ProjectStatus.PAUSED
        project.status_reason = (
            f"hard stop: {spent:,.0f} {unit} this month against a budget of "
            f"{budget:,.0f} and a wall at {hard_stop:,.0f}"
        )
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
            f"The HARD STOP tripped: {spent:,.0f} of a {budget:,.0f} {unit} budget, "
            f"against a wall at {hard_stop:,.0f}. This is not the budget — going over "
            "the budget is allowed and you report it. This is the runaway wall, and "
            "reaching it means something went wrong that no amount of reporting "
            "covers. The project is paused and the chairman was told. When he "
            "resumes it, lead with what happened, not with an apology.",
        )
        chairman_notify(
            session, project, "project auto-paused: hard stop reached",
            f"{spent:,.0f} {unit} this month, against a budget of {budget:,.0f} and a "
            f"hard stop at {hard_stop:,.0f}. Live runs were revoked and the queue "
            "purged. This is the runaway wall rather than the plan — the CEO is "
            "allowed to go over the plan and report, so reaching this means "
            "something else is wrong. Resume from the project page."
            + project_link(project.id, "spend", "See where it went"),
            attention=Attention.ALARM,
        )
        log.warning("project %s auto-paused: hard stop at %.0f", project.slug, hard_stop)

    elif spent >= budget:
        # Over the plan. This is a legitimate place for a project to be, and
        # the platform's job here is to make it visible and answerable rather
        # than to stop the work.
        overrun = budget_lib.record_overrun(session, project, cons, spent)
        if overrun is not None:
            over_by = spent - budget
            flag_lib.open_flag(
                session, project, "budget_overrun",
                summary=(
                    f"The month is {over_by:,.0f} {unit} past its {budget:,.0f} "
                    f"budget ({spent:,.0f} spent). Nothing has stopped and nothing "
                    "will until the hard stop at "
                    f"{hard_stop:,.0f}. What is owed is an account: what the extra "
                    "bought, whether you would spend it again, and what the budget "
                    "should be. The platform has already written where the money "
                    "went — do not restate it, explain it."
                ),
                arithmetic={"budget": budget, "spent": spent, "over_by": over_by,
                            "unit": unit, "hard_stop": hard_stop,
                            "breakdown": overrun.breakdown},
                subject_id=overrun.id,
            )
            _notice_ceo(
                session, project, NoticeKind.BUDGET_WARNING,
                f"The month has gone past its budget: {spent:,.0f} of {budget:,.0f} "
                f"{unit}. THE WORK CONTINUES — a budget on this platform is an "
                "educated guess, not a wall, and the wall is at "
                f"{hard_stop:,.0f}. What you owe is an account of what the extra "
                "bought, filed with overrun_account. The chairman then decides "
                "whether the number was wrong or the work was; both are ordinary "
                "answers. Do not cut work you believe in to get back under a guess.",
            )

    elif spent >= budget * cons.budget_warn_ratio:
        month_edge = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        already = session.execute(
            select(func.count(Notice.id)).where(
                Notice.project_id == project.id,
                Notice.kind == NoticeKind.BUDGET_WARNING,
                Notice.created_at >= month_edge,
            )
        ).scalar_one()
        if not already:
            _notice_ceo(
                session, project, NoticeKind.BUDGET_WARNING,
                f"{cons.budget_warn_ratio:.0%} of the monthly budget is spent "
                f"({spent:,.0f}/{budget:,.0f} {unit}). "
                + _stakes_advice(cons, hard_stop, unit),
            )
    _warn_external_envelope(session, project, cons)


def _stakes_advice(cons: ConstitutionDoc, hard_stop: float, unit: str) -> str:
    """What approaching the plan means HERE, which depends entirely on what is
    riding on the project.

    A LinkedIn post and a real trading account ran under identical caution
    because the platform had no way to be told which was which. Now it does,
    and the difference shows up at exactly the moment it matters.
    """
    if cons.stakes == "high":
        return (
            "This is a high-stakes project: running out of room is worse than going "
            "over, so do NOT thin the work to fit. If the job needs more, spend it "
            f"and account for it — nothing stops before the hard stop at "
            f"{hard_stop:,.0f} {unit}. Tell the chairman now what you expect the "
            "month to come to and why, so he is deciding rather than discovering."
        )
    if cons.stakes == "low":
        return (
            "This is a low-stakes project, so the budget is close to the real limit: "
            "going over is not worth it here. Say what is left that you believe in, "
            "and ask the chairman before spending past the plan."
        )
    return (
        "Say where the month is heading and what you would change, if anything. "
        "Going over is allowed and is reported, not refused — but say it before it "
        "happens rather than after, so he is deciding rather than discovering."
    )


def _warn_external_envelope(session: Session, project: Project,
                            cons: ConstitutionDoc) -> None:
    """The real-world spend envelope, warned like the model budget — in every
    billing mode, because a token-billed project still spends dollars in the
    world. Never pauses anything: the wall itself is enforced where a purchase
    is cleared and a spend is booked."""
    cap = cons.external_monthly_usd_cap
    if cap <= 0:
        return
    # Cheapest check first: once the month's warning exists, every subsequent
    # 15s tick skips the ledger SUM entirely for the rest of the month.
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    already = session.execute(
        select(func.count(Notice.id)).where(
            Notice.project_id == project.id,
            Notice.kind == NoticeKind.EXTERNAL_SPEND_WARNING,
            Notice.created_at >= month_start,
        )
    ).scalar_one()
    if already:
        return
    spent = policy.external_spent_this_month(session, project.id)
    if spent < cap * cons.budget_warn_ratio:
        return
    _notice_ceo(
        session, project, NoticeKind.EXTERNAL_SPEND_WARNING,
        f"${spent:,.2f} of the ${cap:,.0f} real-world spend envelope is used this "
        "month. Purchases that would cross the wall will be refused with the "
        "arithmetic — plan the remaining spend, or recommend an amendment to the "
        "chairman with the numbers.",
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
        # so this goes to the CEO to escalate — silence here would be a project
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


# ── the stall watchdog and the three silences it used to miss ─────────────────


def elect_missing_leads(session: Session, project: Project) -> None:
    """A team of two or more with nobody named as lead cannot claim its own
    queue: only the lead may claim a team task, and only the lead is woken for
    one. Nothing reported it, so such a team's board sat READY forever while
    every screen showed a healthy project with work in hand.

    The earliest-created member is the safe answer — it is the same agent
    `effective_lead` already picks when a team has exactly one — and the
    election is disclosed rather than done quietly.
    """
    for team in session.execute(
        select(Team).where(Team.project_id == project.id)
    ).scalars():
        if policy.effective_lead(session, team) is not None:
            continue
        members = policy.team_agents(session, team.id)
        active = [a for a in members if a.status == AgentStatus.ACTIVE]
        if len(active) < 2:
            continue  # no members, or a sole member who is already the de-facto lead
        lead = active[0]  # team_agents orders by created_at
        team.lead_agent_id = lead.id
        session.add(AuditLog(
            id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:janitor",
            action="team.lead_elected", entity="team", entity_id=team.id,
            detail={"agent_id": lead.id, "name": lead.name},
        ))
        _notice_ceo(
            session, project, NoticeKind.LEAD_ELECTED,
            f"Team {team.name} had ready work and no lead, so nobody could claim it and "
            f"nobody was woken for it. The platform named {lead.name} — its earliest "
            "member — so the queue can move. Change it in Settings if you meant someone "
            "else.",
        )
        log.warning("elected lead %s for leaderless team %s (%s)",
                    lead.name, team.name, project.slug)


def check_stalled_requirements(session: Session, project: Project) -> None:
    """Scope whose every task is closed and none of them succeeded.

    `check_finished_requirements` returns early here — correctly, since there is
    nothing to test — so the requirement stays IN_PROGRESS forever with an empty
    board. And an empty board is exactly what suppresses the CEO's planning
    heartbeat, so the project goes completely silent over work that is still
    formally open. Told once per requirement.
    """
    for req in session.execute(
        select(Requirement).where(Requirement.project_id == project.id,
                                  Requirement.status == RequirementStatus.IN_PROGRESS)
    ).scalars():
        total = session.execute(
            select(func.count(Task.id)).where(Task.requirement_id == req.id)
        ).scalar_one()
        if not total:
            continue
        open_now = session.execute(
            select(func.count(Task.id)).where(
                Task.requirement_id == req.id,
                Task.status.in_(policy.OPEN_TASK_STATUSES))
        ).scalar_one()
        done = session.execute(
            select(func.count(Task.id)).where(Task.requirement_id == req.id,
                                              Task.status == TaskStatus.DONE)
        ).scalar_one()
        if open_now or done:
            continue
        already = session.execute(
            select(func.count(Notice.id)).where(
                Notice.project_id == project.id,
                Notice.kind == NoticeKind.REQUIREMENT_STALLED,
                Notice.body.like(f"%{req.key}%"))
        ).scalar_one()
        if already:
            continue
        _notice_ceo(
            session, project, NoticeKind.REQUIREMENT_STALLED,
            f"{req.key} has stalled: every task it ever had is closed and none of them "
            "succeeded, so its acceptance test will never run and it can never be met. "
            "It needs new work or it needs proposing for a cut — it will not resolve "
            "itself.",
        )
        log.warning("requirement %s stalled with no surviving work (%s)",
                    req.key, project.slug)


def nag_observable_requirements(
    session: Session, project: Project, cons: ConstitutionDoc
) -> None:
    """A requirement whose acceptance test only the chairman can judge parks in
    CHECKING and waits. There was no expiry, no nag and no email — one notice to
    the CEO at the moment it parked, and then silence for as long as it took.
    Nagged on the approval cadence, because it is the same kind of wait.

    Only where he actually holds the verdict. This function predates the dial
    and took `cons` without reading it, which was survivable while the desk
    showed no verdicts either — both channels were consistently silent under
    `ceo_judgment`. It stopped being survivable when the desk started reading
    policy.awaiting_chairman: the page and the badge said "nothing waiting"
    while this mailed "nothing downstream can start until you rule", and a
    chairman who catches the two disagreeing once stops believing the page.
    """
    if policy.ceo_holds(cons, "verdict_policy"):
        return
    cutoff = utcnow() - timedelta(days=1)
    due, first_for = [], {}
    for req in session.execute(
        select(Requirement).where(Requirement.project_id == project.id,
                                  Requirement.status == RequirementStatus.CHECKING)
    ).scalars():
        if (req.acceptance_test or {}).get("type") != "observable":
            continue
        # The nag's own cadence lives in the audit trail rather than a column:
        # it is the same "told once, then once a day" shape as the completion
        # receipt, and it wants to be readable as a record either way. Kept per
        # requirement even though the mail is now one: each requirement enters
        # checking on its own day, and a shared stamp would mean the first one
        # to be chased silenced the next week's arrivals for a day.
        sent = session.execute(
            select(func.count(AuditLog.id)).where(
                AuditLog.project_id == project.id,
                AuditLog.action == "requirement.verdict_nag",
                AuditLog.entity_id == req.id,
                AuditLog.at >= cutoff)
        ).scalar_one()
        if sent:
            continue
        first_for[req.id] = not session.execute(
            select(func.count(AuditLog.id)).where(
                AuditLog.project_id == project.id,
                AuditLog.action == "requirement.verdict_nag",
                AuditLog.entity_id == req.id)
        ).scalar_one()
        session.add(AuditLog(
            id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:janitor",
            action="requirement.verdict_nag", entity="requirement", entity_id=req.id,
            detail={"key": req.key, "first": first_for[req.id]},
        ))
        due.append(req)
    if not due:
        return

    # One mail for the batch, for the reason `nag_proposals` already gives a
    # few hundred lines down: a planning run puts requirements into checking in
    # bulk, and eight emails for eight requirements is how a chairman learns to
    # filter the sender. The verdict nag was the last per-item one left, and it
    # was the worst of them — it fires once a day, forever, until he rules.
    # Named the way he would name them, with the checker's sentence underneath
    # as what he is actually ruling on — the key told him nothing, and was the
    # only name the subject line had.
    detail = "\n\n".join(
        f"  {requirement_name(req)}\n"
        + (f"  What has to be true: {req.statement}\n"
           if requirement_name(req) != req.statement else "")
        + f"  How to check: "
          f"{(req.acceptance_test or {}).get('target', 'look at what was produced')}"
        + ("" if first_for[req.id] else "\n  (a repeat — waiting since it entered checking)")
        for req in due
    )
    subject = (f"waiting on your verdict: {_clip(requirement_name(due[0]), 80)}"
               if len(due) == 1
               else f"{len(due)} requirements are waiting on your verdict")
    chairman_notify(
        session, project, subject,
        f"{_plural_reqs(len(due))} reached an acceptance test the platform cannot "
        f"run, so the verdict is yours:\n\n{detail}\n\n"
        "Nothing downstream of them can start until you rule. Open the Roadmap and "
        "record met or not met.",
        attention=Attention.ACTION,
    )
    log.info("nagged chairman on %d observable requirement(s) (%s)",
             len(due), project.slug)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _plural_reqs(n: int) -> str:
    return "One requirement has" if n == 1 else f"{n} requirements have"


# Long enough for a planning run to finish proposing. The CEO proposes scope in
# a batch, so nagging on the first tick would mail the chairman mid-run and then
# again as the rest arrived; long enough that a chairman already at his desk
# accepts it before hearing from us at all.
PROPOSAL_SETTLE_HOURS = 3
PROPOSAL_NAG_HOURS = 24  # then chased once a day, like every other open wait


def _nagged_within(session: Session, project: Project, action: str,
                   entity_id: str, hours: int) -> tuple[bool, bool]:
    """(recently nagged, ever nagged) for one thing.

    The cadence lives in the audit trail rather than a column, the same shape
    nag_observable_requirements uses: it wants to be readable as a record, and
    "we told him, on this date" is a fact about the project either way.
    """
    ever = int(session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.project_id == project.id,
            AuditLog.action == action,
            AuditLog.entity_id == entity_id)
    ).scalar_one())
    if not ever:
        return False, False
    recent = int(session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.project_id == project.id,
            AuditLog.action == action,
            AuditLog.entity_id == entity_id,
            AuditLog.at >= utcnow() - timedelta(hours=hours))
    ).scalar_one())
    return bool(recent), True


def nag_awaiting_chairman(
    session: Session, project: Project, cons: ConstitutionDoc
) -> None:
    """Chase the two waits that had no voice: proposed scope, and a proposed
    delivery date.

    Both were silent in a way that ran exactly backwards. When the dial gave the
    CEO the signature, the chairman was emailed the moment it signed ("CEO
    committed to 2026-09-10"). When the chairman held it — the default — the
    commitment landed as PROPOSED and nothing was sent at all. Proposed scope
    was never announced under any setting. So yaaf interrupted him when a
    decision had been taken *for* him, and went quiet when one was waiting *on*
    him. Both halves are now fixed: these chases exist, and the signing they
    were the mirror of is an `fyi` that reads in the briefing rather than mail.

    Batched per project, because a planning run proposes scope in bulk and
    eight emails for eight requirements is how a chairman learns to filter the
    sender. Chased daily until acted on rather than announced once, because a
    one-shot notice that arrives while he is away is indistinguishable from
    never having been sent.

    The definition of "waiting" is policy.awaiting_chairman — the same one the
    desk renders, so the page and the inbox cannot disagree.
    """
    waiting = policy.awaiting_chairman(session, project, cons)
    settled = utcnow() - timedelta(hours=PROPOSAL_SETTLE_HOURS)

    proposed = [r for r in waiting["plan"] if r.created_at.replace(tzinfo=UTC) <= settled]
    if proposed:
        # Keyed on the project: the wait is "there is scope to accept", not one
        # per requirement, and the whole point is to send one mail rather than
        # one each.
        recent, ever = _nagged_within(session, project, "scope.proposal_nag",
                                      project.id, PROPOSAL_NAG_HOURS)
        if not recent:
            more = ("" if len(proposed) <= 10
                    else f"\n  … and {len(proposed) - 10} more on the Roadmap")
            session.add(AuditLog(
                id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:janitor",
                action="scope.proposal_nag", entity="project", entity_id=project.id,
                detail={"count": len(proposed), "keys": [r.key for r in proposed]},
            ))
            chairman_notify(
                session, project,
                f"{len(proposed)} requirement(s) waiting on you to accept",
                f"The CEO proposed {len(proposed)} requirement(s):\n\n"
                + "\n".join(f"  - {_clip(requirement_name(r), 200)}" for r in proposed[:10])
                + more
                + "\n\nProposed scope is not scope. No work can be created against any "
                  "of these until you accept them, so the plan they belong to cannot "
                  "start. Open the Roadmap to accept, reword, or decline."
                + ("" if not ever else "\n\nThis is a repeat — they are still waiting."),
                attention=Attention.ACTION,
            )
            log.info("nagged chairman on %d proposed requirement(s) (%s)",
                     len(proposed), project.slug)

    commitment = waiting["commitment"]
    if commitment is not None and commitment.created_at.replace(tzinfo=UTC) <= settled:
        recent, ever = _nagged_within(session, project, "commitment.proposal_nag",
                                      commitment.id, PROPOSAL_NAG_HOURS)
        if not recent:
            session.add(AuditLog(
                id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:janitor",
                action="commitment.proposal_nag", entity="commitment",
                entity_id=commitment.id,
                detail={"deliver_by": commitment.deliver_by.isoformat()},
            ))
            chairman_notify(
                session, project,
                f"a delivery date is waiting on your signature: "
                f"{commitment.deliver_by:%Y-%m-%d}",
                f"The CEO proposes delivering {len(commitment.scope_snapshot or [])} "
                f"requirement(s) by {commitment.deliver_by:%Y-%m-%d}. The date is the "
                "platform's own projection of measured pace — the CEO did not write it "
                "and cannot.\n\nNothing binds until you sign: until then the project "
                "is working without a promise, so no slip can freeze new scope and "
                "nothing tells you when the date stops holding. Open the Roadmap to "
                "sign it, or set your own."
                + ("" if not ever else "\n\nThis is a repeat — it is still unsigned."),
                attention=Attention.ACTION,
            )
            log.info("nagged chairman on an unsigned commitment (%s)", project.slug)


def warn_stall(session: Session, project: Project, cons: ConstitutionDoc) -> None:
    """A running project with work in hand that has started nothing for hours.

    Every gate below this is individually correct and individually invisible;
    what nobody had was the fact that they add up to a project doing nothing.
    The notice carries the dispatcher's own diagnosis verbatim, so the CEO reads
    the same sentence the chairman does instead of guessing at a cause.
    """
    if cons.stall_hours <= 0:
        return
    from yaaf.server.dispatcher import activity_state

    state = activity_state(session, project, cons)
    waiting = state["waiting"]
    if not (waiting["ready_tasks"] or waiting["claimed_tasks"]
            or waiting["blocked_tasks"] or waiting["checking_tasks"]
            or waiting["reviewing_tasks"] or waiting["notices"]
            # Open scope with a closed board is the one shape of "nothing on
            # the board" that is not quiet at all. Every other term here is a
            # task row or a notice, so a project waiting on the chairman with
            # its work finished under it read as zero across the board and
            # bought silence from the one watchdog written to notice silence.
            or waiting["open_scope_no_board"]):
        return  # nothing on the board and nothing open: quiet is the right answer
    # A gate that names its own clearing time is a wait, not a stall — the
    # watchdog exists for silence nobody can explain, not for one the product
    # already explains with a moment attached.
    brief = state["brief"]
    if brief["clears_at"]:
        return

    since = utcnow() - timedelta(hours=float(cons.stall_hours))
    started = session.execute(
        select(func.count(Run.id)).where(Run.project_id == project.id,
                                         Run.kind != RunKind.CHAT_SESSION,
                                         Run.created_at >= since)
    ).scalar_one()
    if started:
        return

    already = session.execute(
        select(func.count(Notice.id)).where(
            Notice.project_id == project.id,
            Notice.kind == NoticeKind.STALLED,
            Notice.created_at >= since,
        )
    ).scalar_one()
    if already:
        return

    _notice_ceo(
        session, project, NoticeKind.STALLED,
        f"This project has started no run in {cons.stall_hours:g}h and still has work "
        f"open. The platform's own reading: {brief['headline']} {brief['detail']} "
        "Do not guess at a different cause — that sentence is the dispatcher's, not a "
        "summary. Act on it or tell the chairman what you need.",
    )
    chairman_notify(
        session, project, "the project has gone quiet",
        f"No run has started in {cons.stall_hours:g}h and there is still work open.\n\n"
        f"{brief['headline']} {brief['detail']}"
        + project_link(project.id, "activity/runs", "See its runs"),
        attention=Attention.ALARM,
    )
    log.warning("stall in %s after %sh: %s", project.slug, cons.stall_hours,
                state["blocked_by"])


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
