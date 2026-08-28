"""Velocity: the measured pace of requirements becoming true.

The forecast stage 5 builds is remaining ÷ velocity, so velocity must be
measured on requirements — a metric needs its own log, and `requirement_events`
is it. Tasks closing is activity; a requirement going MET is progress. The
audited install confused the two: 126 tasks completed, 20 side effects, and no
number anywhere that could say whether the project was faster than last week.

Three numbers travel together, because each is misleading alone:

  * the pace — requirements met per week, trailing 3 weeks and trailing 12
  * the waste — the share of the window's runs that died or failed a check,
    with the single worst offender named, so a falling pace has a cause with
    a key on it rather than a mood
  * the attribution — how much of the window's spend was stamped against a
    requirement at all. 62% of runs in the audit carried nothing, which made
    cost per deliverable uncomputable by construction.

Everything here is read-only over rows other code already writes.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.constitution import ConstitutionDoc
from yaaf.core.models import (
    Project,
    Recurrence,
    Requirement,
    RequirementEvent,
    RequirementStatus,
    Run,
    RunKind,
    RunStatus,
    utcnow,
)
from yaaf.server import policy

SHORT_WINDOW_DAYS = 21
LONG_WINDOW_DAYS = 84

DEAD_STATUSES = (RunStatus.FAILED, RunStatus.REAPED, RunStatus.KILLED,
                 RunStatus.TERMINATED_REFUSALS)


def _met_count(session: Session, project_id: str, since) -> int:
    """MET transitions in the window that represent scope actually landing.

    Two exclusions, both about not inflating the denominator of a promise.

    R-00 is the plan gate lifting, not scope landing, and counting it would
    gift every project one free unit of pace in its first week.

    Standing requirements are excluded for a sharper reason. They are met
    again every cycle by design — the memo, the sweep, the routing check — so
    an Operate project owing three of them weekly would post three met-events a
    week forever. Fed to `commitment_projection`, that pace divides the
    remaining scope into an ever-nearer delivery date, and the platform would
    promise the chairman a date its own rhythm had manufactured. Whether a
    standing obligation is being honoured is a real question with a real
    answer — "met in 23 of the last 24 cycles" — and it lives in
    RequirementCycleResult, where it cannot be mistaken for progress.
    """
    plan = policy.plan_requirement(session, project_id)
    standing = [
        r.id for r in session.execute(
            select(Requirement).where(
                Requirement.project_id == project_id,
                Requirement.recurrence == Recurrence.STANDING)
        ).scalars()
    ]
    stmt = (
        select(func.count(RequirementEvent.id))
        .where(
            RequirementEvent.project_id == project_id,
            RequirementEvent.to_status == RequirementStatus.MET,
            RequirementEvent.at >= since,
        )
    )
    if plan is not None:
        stmt = stmt.where(RequirementEvent.requirement_id != plan.id)
    if standing:
        stmt = stmt.where(RequirementEvent.requirement_id.not_in(standing))
    return session.execute(stmt).scalar_one()


def standing_record(session: Session, project_id: str, cycles: int = 12) -> list[dict]:
    """How the standing obligations have actually gone, cycle by cycle.

    What an Operate project has instead of a burndown. A burndown of work that
    recurs is a flat line; this is the shape that carries the information —
    which obligation slipped, and in which turn.
    """
    from yaaf.core.models import Cycle, RequirementCycleResult

    recent = list(session.execute(
        select(Cycle).where(Cycle.project_id == project_id)
        .order_by(Cycle.ordinal.desc()).limit(cycles)
    ).scalars())
    if not recent:
        return []
    ids_in_window = {c.id for c in recent}
    rows: dict[str, dict] = {}
    for result in session.execute(
        select(RequirementCycleResult).where(
            RequirementCycleResult.project_id == project_id)
    ).scalars():
        if result.cycle_id not in ids_in_window:
            continue
        entry = rows.setdefault(result.requirement_id, {"met": 0, "judged": 0})
        entry["judged"] += 1
        if result.status == RequirementStatus.MET:
            entry["met"] += 1
    out = []
    for requirement in session.execute(
        select(Requirement).where(
            Requirement.project_id == project_id,
            Requirement.recurrence == Recurrence.STANDING)
    ).scalars():
        entry = rows.get(requirement.id, {"met": 0, "judged": 0})
        out.append({
            "key": requirement.key,
            "statement": requirement.statement[:200],
            "met": entry["met"],
            "judged": entry["judged"],
            "status_now": requirement.status,
        })
    return sorted(out, key=lambda r: r["key"])


def _runs_in_window(session: Session, project_id: str, since) -> dict:
    """What the window's runs were, and — since the split — what they were FOR.

    Three lanes, because one number hid the finding that started all of this:
    57% of every run an audited project ever spent was an agent waking to read
    a message. Its own broadcasts generated that traffic, and neither the CEO
    nor the chairman could see it, so nobody could price a message before
    sending it. `inbox` is reading; `noop` is a wake that changed nothing and
    was refunded; `work` is the rest.
    """
    total, dead, attributed, usd, attributed_usd = 0, 0, 0, 0.0, 0.0
    inbox, noop, work = 0, 0, 0
    rows = session.execute(
        select(Run.status, Run.requirement_id, Run.cost_usd, Run.kind,
               Run.metered).where(
            Run.project_id == project_id,
            Run.kind != RunKind.CHAT_SESSION,
            Run.created_at >= since,
        )
    ).all()
    for status, requirement_id, cost, kind, metered in rows:
        total += 1
        if status in DEAD_STATUSES:
            dead += 1
        if requirement_id:
            attributed += 1
            attributed_usd += cost or 0.0
        usd += cost or 0.0
        if kind == RunKind.INBOX_RUN:
            inbox += 1
        elif metered is False:
            # `metered` is the ONLY honest signal. Inferring a no-op from
            # produced == 0 reads every run that predates the counter as
            # having done nothing: on the first deploy this reported 26 of 42
            # runs as waste, which was the backfill talking, not the project.
            noop += 1
        else:
            work += 1
    return {
        "total": total,
        "dead": dead,
        "failure_share": round(dead / total, 3) if total else 0.0,
        "attributed": attributed,
        "attributed_share": round(attributed / total, 3) if total else 0.0,
        "usd": round(usd, 2),
        "attributed_usd": round(attributed_usd, 2),
        # The tax, made countable.
        "work": work,
        "inbox": inbox,
        "noop": noop,
        "inbox_share": round(inbox / total, 3) if total else 0.0,
        "noop_share": round(noop / total, 3) if total else 0.0,
    }


def _worst_failure(session: Session, project_id: str) -> dict | None:
    """The open requirement carrying the most accumulated failures. One name,
    because "R-07 has eaten 53 runs" is actionable and a histogram is not."""
    worst = session.execute(
        select(Requirement)
        .where(
            Requirement.project_id == project_id,
            Requirement.status.in_(policy.OPEN_REQUIREMENT_STATUSES),
            Requirement.failure_load > 0,
        )
        .order_by(Requirement.failure_load.desc())
        .limit(1)
    ).scalar_one_or_none()
    if worst is None:
        return None
    return {"key": worst.key, "failure_load": worst.failure_load,
            "statement": worst.statement[:200]}


def project_velocity(session: Session, project_id: str) -> dict:
    """The block the context pack ships and the Plan screen draws. One
    producer, so the CEO and the chairman are always reading the same pace."""
    now = utcnow()
    short_since = now - timedelta(days=SHORT_WINDOW_DAYS)
    long_since = now - timedelta(days=LONG_WINDOW_DAYS)

    met_short = _met_count(session, project_id, short_since)
    met_long = _met_count(session, project_id, long_since)
    runs_short = _runs_in_window(session, project_id, short_since)

    remaining = len([
        r for r in policy.scope_requirements(session, project_id)
        if r.status in policy.OPEN_REQUIREMENT_STATUSES
    ])
    per_week_short = round(met_short / (SHORT_WINDOW_DAYS / 7), 2)

    return {
        "met_3w": met_short,
        "per_week_3w": per_week_short,
        "met_12w": met_long,
        "per_week_12w": round(met_long / (LONG_WINDOW_DAYS / 7), 2),
        "remaining": remaining,
        # The naive read of the pace, here so nobody computes it two ways.
        # None while there is no pace at all: "infinite weeks" helps nobody,
        # and a made-up number would be worse.
        "weeks_left_at_pace": (
            round(remaining / per_week_short, 1) if per_week_short > 0 else None
        ),
        "runs_3w": runs_short,
        "worst_failure": _worst_failure(session, project_id),
    }


def commitment_projection(session: Session, project: Project, cons: ConstitutionDoc) -> dict:
    """Where the remaining scope lands, as a date. The ONLY producer of the
    delivery projection: the propose route quotes it, the janitor stamps it on
    the commitment, the scope refusal shows its arithmetic, and the Plan
    screen draws it — one function, so none of them can ever disagree.

    Measured pace first: remaining ÷ met-per-week, trailing 3 weeks, falling
    back to 12. A project that has met nothing yet has no pace to divide by,
    so it falls back to the plan's own arithmetic — adjusted runs at the daily
    cap, the same sum the feasibility refusals quote. The basis is named in
    the result, because a date that cannot say where it came from is an
    assertion, not a forecast.
    """
    pace = project_velocity(session, project.id)
    remaining = pace["remaining"]
    now = utcnow()
    if remaining == 0:
        return {"known": True, "remaining": 0, "method": "settled",
                "projected_done_at": now,
                "basis": "nothing is open — the scope is settled"}
    per_week = pace["per_week_3w"] or pace["per_week_12w"]
    if per_week:
        window = "3 weeks" if pace["per_week_3w"] else "12 weeks"
        projected = now + timedelta(weeks=remaining / per_week)
        return {"known": True, "remaining": remaining, "method": "velocity",
                "per_week": per_week, "projected_done_at": projected,
                "basis": f"{remaining} open requirement(s) at the measured pace of "
                         f"{per_week}/week (trailing {window})"}
    sums = policy.plan_arithmetic(session, project, cons)
    if sums["adjusted_runs"] > 0 and cons.runs_per_project_per_day:
        days = sums["adjusted_runs"] / cons.runs_per_project_per_day
        return {"known": True, "remaining": remaining, "method": "plan_arithmetic",
                "projected_done_at": now + timedelta(days=days),
                "basis": f"nothing met yet, so this is the plan's own arithmetic: "
                         f"{sums['adjusted_runs']} adjusted runs at "
                         f"{cons.runs_per_project_per_day}/day ≈ {round(days, 1)} days — "
                         "an at-the-cap best case, not a measured pace"}
    return {"known": False, "remaining": remaining,
            "why": "nothing has been met and nothing is estimated — there is no "
                   "arithmetic to project a date from"}
