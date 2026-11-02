"""Daily obligations: judged at each deadline, not when the work queue empties.

A standing requirement is owed every cycle. The only judge it ever had was the
one every requirement has — when all the work under it closes, the platform
runs the acceptance test. That is the right trigger for a thing that becomes
true once. It is never the trigger for a report owed every morning: a daily
pipeline always has tomorrow's run queued, so its work never all closes, so the
test never runs. One project shipped its report before the deadline fifty days
running while seven cycles in a row closed "0 of 4 met, 1 carried forward" —
the platform had checked it exactly once, on day one.

So a standing command test may name its deadline: `due_daily_at: "08:00"`, in
the constitution's timezone. After each deadline (plus `GRACE`) the janitor
queues the acceptance test whatever the work looks like, the runner records a
`StandingCheck`, and the cycle's verdict is a count of days. The requirement's
own status is left alone: a daily obligation is never "already true" — marking
it MET would refuse tomorrow's work, which is exactly why the CEO reopened it by
hand the one time it passed.

What the check grades is the project's own business: the command runs at the
deadline, so a script that grades "the day whose report was owed" grades the
right day.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import (
    Cycle,
    Recurrence,
    Requirement,
    RequirementStatus,
    RunKind,
    RunQueue,
    StandingCheck,
    SuccessTestKind,
    utcnow,
)

#: How long after a deadline the check runs. Long enough that a delivery
#: landing in the last minute is on the record; short enough that the verdict
#: is about the deadline that just passed and not the next one.
GRACE = timedelta(minutes=10)
#: How many times a check that died before reporting is re-run for the same
#: deadline. After that the day is recorded as not checked — a platform that
#: cannot run its checks says so rather than guessing.
MAX_ATTEMPTS = 3
#: How long a queued check may go unanswered before it counts as lost.
LOST_AFTER = timedelta(minutes=10)

PENDING, MET, MISSED = "pending", "met", "missed"

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def validate(acceptance_test: dict | None, recurrence: str) -> None:
    """`due_daily_at` is a standing command test's deadline. Raises Refusal."""
    from yaaf.server.policy import Refusal

    at = (acceptance_test or {}).get("due_daily_at")
    if at is None:
        return
    if recurrence != Recurrence.STANDING:
        raise Refusal(
            "due_daily_at belongs on a standing requirement: it is the deadline of "
            "something owed every day. A thing that becomes true once is judged when "
            "its work closes.", status_code=422)
    if (acceptance_test or {}).get("type") != SuccessTestKind.COMMAND:
        raise Refusal(
            "due_daily_at needs a command test: the platform runs the check itself "
            "after every deadline, and only a command is something it can run.",
            status_code=422)
    if not isinstance(at, str) or not _HHMM.match(at):
        raise Refusal(
            'due_daily_at is the daily deadline as "HH:MM" on a 24-hour clock, in the '
            'project\'s timezone — e.g. "08:00".', status_code=422)


def deadline_of(requirement: Requirement) -> time | None:
    """The daily deadline, or None for a requirement judged the ordinary way."""
    if requirement.recurrence != Recurrence.STANDING:
        return None
    at = (requirement.acceptance_test or {}).get("due_daily_at")
    if not isinstance(at, str) or not _HHMM.match(at):
        return None
    hour, minute = at.split(":")
    return time(int(hour), int(minute))


def is_daily(requirement: Requirement) -> bool:
    return deadline_of(requirement) is not None


def deadlines(requirement: Requirement, tz: str, start: datetime,
              end: datetime) -> list[datetime]:
    """Every deadline D with start < D and D + GRACE <= end, in UTC.

    The local wall clock is the authority: "08:00" stays 08:00 across a clock
    change, so the UTC instants move with it.
    """
    at = deadline_of(requirement)
    if at is None or end <= start:
        return []
    zone = ZoneInfo(tz)
    day = start.astimezone(zone).date()
    last = end.astimezone(zone).date()
    out = []
    while day <= last:
        due = datetime.combine(day, at, tzinfo=zone).astimezone(start.tzinfo)
        if start < due and due + GRACE <= end:
            out.append(due)
        day += timedelta(days=1)
    return out


def _counting_from(requirement: Requirement, cycle: Cycle) -> datetime:
    """Deadlines before the obligation was accepted are not owed."""
    accepted = requirement.accepted_at or requirement.created_at
    return max(filter(None, (cycle.opened_at, accepted)))


def live(session: Session, project_id: str, cycle: Cycle) -> list[Requirement]:
    """The open cycle's daily obligations that are in force."""
    rows = session.execute(
        select(Requirement).where(
            Requirement.project_id == project_id,
            Requirement.cycle_id == cycle.id,
            Requirement.recurrence == Recurrence.STANDING,
            Requirement.status.notin_((RequirementStatus.PROPOSED, RequirementStatus.CUT)),
        )
    ).scalars()
    return [r for r in rows if is_daily(r)]


def _queue(session: Session, project_id: str, requirement: Requirement,
           check: StandingCheck) -> None:
    run_id = ids.new_id(ids.RUN)
    check.run_id = run_id
    session.add(RunQueue(
        id=run_id, project_id=project_id, agent_id=None,
        kind=RunKind.REQUIREMENT_CHECK,
        trigger={"type": "standing_check", "requirement_id": requirement.id,
                 "standing_check_id": check.id, "due_at": check.due_at.isoformat()},
        requirement_id=requirement.id,
    ))


def judge_due(session: Session, project_id: str, cycle: Cycle | None, tz: str,
              now: datetime | None = None) -> int:
    """Queue the check for each daily obligation whose deadline just passed.

    Only the most recent deadline: the check grades "the day owed now", so a
    check run for an older deadline would grade the wrong day. A deadline the
    platform slept through stays on the record as not checked.
    """
    if cycle is None:
        return 0
    now = now or utcnow()
    queued = 0
    for requirement in live(session, project_id, cycle):
        due = deadlines(requirement, tz, _counting_from(requirement, cycle) - timedelta(
            microseconds=1), now)
        if not due:
            continue
        latest = due[-1]
        check = session.execute(
            select(StandingCheck).where(StandingCheck.requirement_id == requirement.id,
                                        StandingCheck.due_at == latest)
        ).scalar_one_or_none()
        if check is None:
            check = StandingCheck(
                id=ids.new_id(ids.STANDING_CHECK), project_id=project_id,
                requirement_id=requirement.id, cycle_id=cycle.id, due_at=latest,
                status=PENDING, attempts=1, created_at=now, updated_at=now)
            session.add(check)
            session.flush()
            _queue(session, project_id, requirement, check)
            queued += 1
            continue
        if check.status != PENDING or check.attempts >= MAX_ATTEMPTS:
            continue
        if (check.updated_at or check.created_at) + LOST_AFTER > now:
            continue
        if _in_flight(session, check):
            continue
        check.attempts += 1
        check.updated_at = now
        _queue(session, project_id, requirement, check)
        queued += 1
    return queued


def _in_flight(session: Session, check: StandingCheck) -> bool:
    from yaaf.core.models import Run, RunStatus

    if check.run_id is None:
        return False
    if session.get(RunQueue, check.run_id) is not None:
        return True
    run = session.get(Run, check.run_id)
    return run is not None and run.status == RunStatus.RUNNING


def record(session: Session, check_id: str, exit_code: int | None, output: str,
           run_id: str) -> StandingCheck | None:
    """The runner's verdict on one deadline. Returns the row when it changed."""
    check = session.get(StandingCheck, check_id)
    if check is None or check.status != PENDING:
        return None
    check.exit_code = exit_code
    check.check_output = (output or "")[:4000] or None
    check.run_id = run_id
    check.status = MET if exit_code == 0 else MISSED
    check.judged_at = utcnow()
    return check


def tally(session: Session, requirement: Requirement, cycle: Cycle, tz: str,
          until: datetime) -> dict:
    """How the cycle went for one daily obligation, in days."""
    due = deadlines(requirement, tz, _counting_from(requirement, cycle) - timedelta(
        microseconds=1), until)
    verdicts = {}
    if due:
        verdicts = {
            c.due_at: c.status for c in session.execute(
                select(StandingCheck).where(StandingCheck.requirement_id == requirement.id,
                                            StandingCheck.due_at.in_(due))
            ).scalars()
        }
    met = [d for d in due if verdicts.get(d) == MET]
    missed = [d for d in due if verdicts.get(d) == MISSED]
    zone = ZoneInfo(tz)
    return {
        "days_due": len(due),
        "days_met": len(met),
        "days_missed": len(missed),
        "days_unchecked": len(due) - len(met) - len(missed),
        "missed_days": [d.astimezone(zone).date().isoformat() for d in missed],
    }
