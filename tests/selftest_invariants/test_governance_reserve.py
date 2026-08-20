"""Invariant selftests: the reserved governance slice.

The audit's sharpest single finding was a company that spent a whole day's runs
before lunch and then sat dead until midnight. On such a day the brake is
working — ordinary work *should* stop. What must not stop is the run whose only
job is to tell the chairman what happened, because the day the company burns
its allowance is precisely the day he needs the report.

So the daily ceiling is split: ordinary wakes stop at `cap - reserved`, and
governance wakes — a status review, a new cycle's plan, a commitment being
re-forecast — may spend up to the full `cap`.

Two things are pinned here. That the reservation actually holds runs back, and
that it can never eat the whole day, which is the bug this rule shipped with:
a reservation of 3 against a ceiling of 1 made *every* run a governance run,
so a small project did no work at all.
"""

from __future__ import annotations

from sqlalchemy import select

from tests.conftest import amend, make_project
from yaaf.core import ids
from yaaf.core.constitution import ConstitutionDoc, effective
from yaaf.core.db import db_session
from yaaf.core.models import (
    Heartbeat,
    Notice,
    NoticeKind,
    Project,
    Run,
    RunQueue,
    RunStatus,
    utcnow,
)
from yaaf.server import dispatcher


def _run_until_settled(project: dict, max_rounds: int = 400) -> int:
    """Drive the dispatcher until it stops enqueuing, keeping every agent
    hungry so the only thing that can stop it is a cap.

    Queued rows are *promoted* to finished runs rather than deleted, because
    the day's spend counts both tables: deleting the queue would hand the
    budget back and the ceiling would never arrive.
    """
    total = 0
    for _ in range(max_rounds):
        _make_everyone_hungry(project)
        with db_session() as session:
            enqueued = dispatcher.dispatch_once(session)
        if enqueued == 0:
            return total
        total += enqueued
        _promote_queue_to_finished_runs()
    raise AssertionError("dispatcher would not settle")


def _promote_queue_to_finished_runs() -> None:
    """What the daemon does: lease the queued row into a real run, then let it
    end. The agent stops being busy; the run still counts against the day."""
    with db_session() as session:
        for queued in session.execute(select(RunQueue)).scalars():
            session.add(Run(
                id=queued.id,
                project_id=queued.project_id,
                agent_id=queued.agent_id,
                kind=queued.kind,
                trigger=queued.trigger,
                task_id=queued.task_id,
                attempt=1,
                status=RunStatus.DONE,
                created_at=queued.created_at,
                started_at=queued.created_at,
                finished_at=utcnow(),
            ))
            session.delete(queued)


def _make_everyone_hungry(project: dict) -> None:
    """A fresh notice per agent, and no wake-rate history — so every tick has a
    reason to run and the daily ceiling is the only thing in the way."""
    with db_session() as session:
        rows = session.execute(
            select(Heartbeat)).scalars().all()
        for beat in rows:
            beat.last_fired_at = utcnow() - dispatcher.timedelta(days=1)
        ceo = project["ceo_agent_id"]
        session.add(Notice(id=ids.new_id(ids.NOTICE), project_id=project["id"],
                           agent_id=ceo, kind=NoticeKind.BUDGET_WARNING,
                           body="keep the CEO hungry"))


def _used_today(project: dict) -> int:
    with db_session() as session:
        row = session.get(Project, project["id"])
        return dispatcher.daily_runs_used(session, row,
                                          dispatcher.day_start(utcnow()))


def test_ordinary_work_stops_short_of_the_ceiling_by_the_reservation(client):
    """Ordinary wakes must leave the reserved slice alone, so a review always
    has somewhere to run."""
    project = make_project(
        client,
        constitution={"runs_per_project_per_day": 10, "reserved_governance_runs": 3,
                      "runs_per_project_per_hour": 1000, "wakes_per_agent_per_hour": 1000,
                      "max_concurrent_runs": 1},
    )
    _run_until_settled(project)

    used = _used_today(project)
    assert used == 7, f"ordinary work should stop at cap - reserved = 7, used {used}"

    # The remaining three are not lost — they are unavailable to ordinary work
    # only, and the dispatcher still reports the day as not yet spent.
    with db_session() as session:
        row = session.get(Project, project["id"])
        cons = effective(session, project["id"])
        state = dispatcher.activity_state(session, row, cons)
    assert state["daily_runs"]["cap"] == 10
    assert state["daily_runs"]["used"] == 7


def test_a_reservation_can_never_eat_the_whole_day(client):
    """The bug this rule shipped with. With a ceiling of 1 and the default
    reservation of 3, `cap - reserved` is negative, so every wake looked like
    it needed governance clearance and a small project did nothing at all."""
    project = make_project(
        client,
        constitution={"runs_per_project_per_day": 1, "runs_per_project_per_hour": 1},
    )
    with db_session() as session:
        cons = effective(session, project["id"])
    assert cons.reserved_governance_runs == ConstitutionDoc().reserved_governance_runs > 1

    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1, \
            "a one-run day must still buy one run of ordinary work"


def test_the_reservation_never_takes_more_than_half_a_day(client):
    """Between those two extremes the clamp is proportional, so a small
    ceiling degrades smoothly instead of falling off a cliff."""
    project = make_project(
        client,
        constitution={"runs_per_project_per_day": 4, "reserved_governance_runs": 3,
                      "runs_per_project_per_hour": 1000, "wakes_per_agent_per_hour": 1000,
                      "max_concurrent_runs": 1},
    )
    _run_until_settled(project)
    used = _used_today(project)
    assert used == 2, f"4-run day, reservation clamped to 2, so work gets 2 — used {used}"


def test_turning_the_reservation_off_gives_the_whole_day_to_work(client):
    """0 is a real setting: a project with no reviews should not hold runs back
    for a meeting that never happens."""
    project = make_project(
        client,
        constitution={"runs_per_project_per_day": 5, "reserved_governance_runs": 0,
                      "runs_per_project_per_hour": 1000, "wakes_per_agent_per_hour": 1000,
                      "max_concurrent_runs": 1},
    )
    _run_until_settled(project)
    assert _used_today(project) == 5


def test_the_reservation_is_a_rule_the_chairman_can_move(client):
    """It is a limit, so it lives in the constitution — amendable, audited, and
    disclosed to the CEO like every other limit."""
    project = make_project(client, start=False)
    amend(client, project["id"], reason="this company reports weekly, not daily",
          reserved_governance_runs=1)
    with db_session() as session:
        assert effective(session, project["id"]).reserved_governance_runs == 1

    assert "reserved_governance_runs" in ConstitutionDoc.model_fields
    field = ConstitutionDoc.model_fields["reserved_governance_runs"]
    assert field.json_schema_extra["group"], "a rule without a group never renders in settings"
    assert field.description, "a rule without help text is a rule nobody can set"


def test_governance_reasons_are_recognised_as_governance():
    """The classifier the reservation turns on. These names are the contract
    between the wake producers (reviews, cycles, commitments) and the gate."""
    assert dispatcher._is_governance([{"kind": "review"}])
    assert dispatcher._is_governance([{"kind": "cycle_plan"}])
    assert dispatcher._is_governance([{"kind": "commitment"}])
    # A mixed wake counts as governance: the review still has to happen, and
    # coalescing must not strip it of its clearance.
    assert dispatcher._is_governance([{"kind": "messages"}, {"kind": "review"}])
    # Ordinary work never is.
    assert not dispatcher._is_governance([{"kind": "heartbeat", "name": "planning"}])
    assert not dispatcher._is_governance([{"kind": "team_task_ready"}])
    assert not dispatcher._is_governance([])
