"""Velocity: the measured pace of requirements becoming true.

The forecast stage 5 builds is remaining ÷ velocity, so velocity must be
measured on requirements — a metric needs its own log, and `requirement_events`
is it. Tasks closing is activity; a requirement going MET is progress. The
audited install confused the two: 126 tasks completed, 20 side effects, and no
number anywhere that could say whether the company was faster than last week.

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

from yaaf.core.models import (
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
    """MET transitions in the window, excluding R-00 — the plan opening is the
    gate lifting, not scope landing, and counting it would gift every project
    one free unit of pace in its first week."""
    plan = policy.plan_requirement(session, project_id)
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
    return session.execute(stmt).scalar_one()


def _runs_in_window(session: Session, project_id: str, since) -> dict:
    total, dead, attributed, usd, attributed_usd = 0, 0, 0, 0.0, 0.0
    rows = session.execute(
        select(Run.status, Run.requirement_id, Run.cost_usd).where(
            Run.project_id == project_id,
            Run.kind != RunKind.CHAT_SESSION,
            Run.created_at >= since,
        )
    ).all()
    for status, requirement_id, cost in rows:
        total += 1
        if status in DEAD_STATUSES:
            dead += 1
        if requirement_id:
            attributed += 1
            attributed_usd += cost or 0.0
        usd += cost or 0.0
    return {
        "total": total,
        "dead": dead,
        "failure_share": round(dead / total, 3) if total else 0.0,
        "attributed": attributed,
        "attributed_share": round(attributed / total, 3) if total else 0.0,
        "usd": round(usd, 2),
        "attributed_usd": round(attributed_usd, 2),
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
