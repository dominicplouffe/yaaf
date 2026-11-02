"""Invariant selftests: an obligation owed every day is judged every day.

The incident: a weekly Operate project owed one thing — a traffic report by
08:00 every morning — and delivered it before the deadline fifty days running.
Seven cycles in a row closed "0 of 4 met, 1 carried forward", and the review
headlined it. Three of the four were cut versions of the same report that the
roll kept carrying and counting. The fourth was judged only when all its work
closed, which a daily pipeline never does, so the platform had run its test
once, on day one.
"""

from __future__ import annotations

from sqlalchemy import select

from tests.conftest import ADMIN, bearer
from tests.selftest_invariants.test_modes import backdate_cycle, operate_project, run_mode_tick
from tests.selftest_invariants.test_requirements import accept, ceo_token, make_task, propose
from yaaf.core import ids
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import (
    Cycle,
    CycleStatus,
    FlagKind,
    Project,
    Requirement,
    RunKind,
    RunQueue,
    StandingCheck,
    utcnow,
)
from yaaf.server import flags, review_record, scorecard, standing

DAILY = {"type": "command", "command": "python3 verify_day.py --due --strict",
         "due_daily_at": "08:00"}


def _daily(client, project, statement="Every day, yesterday's report lands by 08:00"):
    token = ceo_token(project)
    proposed = propose(client, token, statement=statement, test=DAILY,
                       recurrence="standing",
                       meta={"title": "Daily traffic report", "cadence": "daily"})
    accept(client, proposed["requirement_id"], project)
    return proposed["requirement_id"], token


def _open_cycle(session, project_id) -> Cycle:
    return session.execute(select(Cycle).where(
        Cycle.project_id == project_id, Cycle.status == CycleStatus.OPEN)).scalar_one()


def _verdicts(project_id: str, rid: str, statuses: list[str]) -> list:
    """One recorded verdict per deadline of the open cycle, oldest first."""
    with db_session() as session:
        cycle = _open_cycle(session, project_id)
        requirement = session.get(Requirement, rid)
        tz = effective(session, project_id).timezone
        requirement.accepted_at = cycle.opened_at
        due = standing.deadlines(requirement, tz, cycle.opened_at, utcnow())
        for at, status in zip(due, statuses, strict=False):
            session.add(StandingCheck(
                id=ids.new_id(ids.STANDING_CHECK), project_id=project_id,
                requirement_id=rid, cycle_id=cycle.id, due_at=at, status=status,
                attempts=1, exit_code=0 if status == standing.MET else 1))
        return due


def test_a_daily_obligation_is_checked_after_its_deadline_with_work_still_open(client):
    """The trigger every other requirement uses — all work closed — never fires
    for a pipeline that always has tomorrow's run queued."""
    from yaaf.runner import daemon
    from yaaf.runner.types import RunJob, RunResult

    project = operate_project(client, slug="daily-judged")
    rid, token = _daily(client, project)
    assert make_task(client, token, project, rid).status_code == 200, "tomorrow's run, open"
    backdate_cycle(project["id"], days=2)

    with db_session() as session:
        cycle = _open_cycle(session, project["id"])
        session.get(Requirement, rid).accepted_at = cycle.opened_at
        tz = effective(session, project["id"]).timezone
        assert standing.judge_due(session, project["id"], cycle, tz) == 1
        assert standing.judge_due(session, project["id"], cycle, tz) == 0, \
            "one check per deadline"
        queued = session.execute(select(RunQueue).where(
            RunQueue.project_id == project["id"],
            RunQueue.kind == RunKind.REQUIREMENT_CHECK)).scalar_one()
        assert queued.trigger["type"] == "standing_check"
        status_before = session.get(Requirement, rid).status

    with db_session() as session:
        daemon._finalize_requirement_check(
            session,
            RunJob(run_id=queued.id, project_id=project["id"], agent_id=None,
                   kind=RunKind.REQUIREMENT_CHECK, trigger=queued.trigger, task_id=None,
                   requirement_id=rid),
            RunResult(ok=True, summary="exit=0", check_exit_code=0, check_output="PASS"))
    with db_session() as session:
        check = session.execute(select(StandingCheck).where(
            StandingCheck.requirement_id == rid)).scalar_one()
        assert check.status == standing.MET
        assert session.get(Requirement, rid).status == status_before, (
            "a daily obligation is never 'already true': MET would refuse tomorrow's work")


def test_the_cycle_reports_a_daily_obligation_in_days(client):
    project = operate_project(client, slug="daily-days")
    rid, _ = _daily(client, project)
    backdate_cycle(project["id"], days=8)
    due = _verdicts(project["id"], rid, [standing.MET] * 20)
    assert len(due) >= 7

    run_mode_tick(project)
    with db_session() as session:
        closed = session.execute(select(Cycle).where(
            Cycle.project_id == project["id"], Cycle.status != CycleStatus.OPEN)).scalar_one()
        card = closed.scorecard
        assert closed.status == CycleStatus.COMPLETED
        row = card["standing"][0]
        assert (row["days_met"], row["days_due"]) == (len(due), len(due))
        assert card["met"] == card["accepted"] == 1
        assert card["carried_keys"] == [], "an obligation is owed again, not carried"
    said = scorecard.sentence(card)
    assert f"Daily traffic report: on time {len(due)} of {len(due)} days" in said, said


def test_one_missed_day_misses_the_cycle_and_names_the_day(client):
    project = operate_project(client, slug="daily-miss")
    rid, _ = _daily(client, project)
    backdate_cycle(project["id"], days=8)
    due = _verdicts(project["id"], rid, [standing.MET, standing.MISSED] + [standing.MET] * 20)

    with db_session() as session:
        row = session.get(Project, project["id"])
        flags.evaluate(session, row, effective(session, row.id))
        kinds = [f.kind for f in flags.open_flags(session, row.id)]
    assert FlagKind.STANDING_MISSED in kinds, "a missed morning speaks the day it happens"

    run_mode_tick(project)
    with db_session() as session:
        closed = session.execute(select(Cycle).where(
            Cycle.project_id == project["id"], Cycle.status != CycleStatus.OPEN)).scalar_one()
        assert closed.status == CycleStatus.MISSED
        row = closed.scorecard["standing"][0]
        assert (row["days_met"], row["days_missed"]) == (len(due) - 1, 1)
        assert len(row["missed_days"]) == 1


def test_a_cut_obligation_is_neither_carried_nor_counted(client):
    """Three cut versions of one report rode along in the denominator for seven
    cycles: "0 of 4" was one obligation."""
    project = operate_project(client, slug="cut-standing")
    rid, _ = _daily(client, project)
    cut_rid, _ = _daily(client, project, statement="The first version of the daily report")
    cut = client.post(f"/admin/requirements/{cut_rid}/cut", json={"reason": "replaced"},
                      headers=ADMIN)
    assert cut.status_code == 200, cut.text
    backdate_cycle(project["id"], days=8)
    _verdicts(project["id"], rid, [standing.MET] * 20)

    run_mode_tick(project)
    with db_session() as session:
        first = session.execute(select(Cycle).where(
            Cycle.project_id == project["id"], Cycle.ordinal == 1)).scalar_one()
        assert first.scorecard["accepted"] == 1
        assert first.scorecard["cut_keys"] == [session.get(Requirement, cut_rid).key]
        assert session.get(Requirement, cut_rid).cycle_id == first.id, "left where it was cut"
        assert session.get(Requirement, rid).cycle_id != first.id, "the live one moves on"

    backdate_cycle(project["id"], days=8)
    run_mode_tick(project)
    with db_session() as session:
        second = session.execute(select(Cycle).where(
            Cycle.project_id == project["id"], Cycle.ordinal == 2)).scalar_one()
        assert second.scorecard["accepted"] == 1
        assert second.scorecard["cut_keys"] == [], "cut once, not every week after"


def test_the_headline_names_what_it_counts():
    card = {"accepted": 2, "met": 1, "carried": 1, "missed": 0, "cut": 0,
            "met_keys": ["R-17"], "carried_keys": ["R-18"], "missed_keys": [],
            "cut_keys": [], "standing": [{
                "key": "R-17", "name": "Daily traffic report", "title": "Daily traffic report",
                "verdict": "honoured", "days_due": 7, "days_met": 7, "days_missed": 0,
                "days_unchecked": 0, "missed_days": []}]}
    line = review_record.headline("operate", {}, {"cycle": {"ordinal": 8, **card}})
    assert line.startswith("Cycle 8: Daily traffic report: on time 7 of 7 days;"), line
    assert "0 of 1 one-off requirement met, R-18 carried forward." in line
    assert "of 2" not in line, "a bare count with nothing to look up is what this replaced"


def test_a_never_checked_obligation_says_so():
    card = {"accepted": 1, "met": 0, "carried": 0, "missed": 1, "cut": 0, "met_keys": [],
            "carried_keys": [], "missed_keys": ["R-17"], "cut_keys": [],
            "standing": [{"key": "R-17", "name": "x", "title": None, "verdict": "unchecked"}]}
    assert scorecard.sentence(card) == "R-17: owed this cycle and never checked."


def test_due_daily_at_belongs_on_a_standing_command_test(client):
    project = operate_project(client, slug="daily-refusals")
    token = ceo_token(project)

    def _try(test, recurrence="standing"):
        return client.post("/agent/requirements", headers=bearer(token), json={
            "statement": "Every day, the report lands", "acceptance_test": test,
            "est_runs": 3, "recurrence": recurrence})

    assert _try(DAILY, recurrence="terminal").status_code == 422
    assert _try({**DAILY, "due_daily_at": "8am"}).status_code == 422
    assert _try({"type": "observable", "target": "the report is in the channel",
                 "due_daily_at": "08:00"}).status_code == 422
    ok = _try(DAILY)
    assert ok.status_code == 200, ok.text
    assert "after every 08:00 deadline" in ok.json()["note"]


def test_deadlines_follow_the_local_clock_across_a_clock_change():
    """08:00 Eastern is 12:00Z in October and 13:00Z in November."""
    from datetime import UTC, datetime

    row = Requirement(recurrence="standing", acceptance_test=DAILY)
    due = standing.deadlines(row, "America/New_York",
                             datetime(2026, 10, 31, 0, tzinfo=UTC),
                             datetime(2026, 11, 2, 23, tzinfo=UTC))
    assert [d.hour for d in due] == [12, 13, 13]


def test_a_meeting_from_before_the_record_does_not_say_no_cycle_closed():
    """Three meetings opened before records existed headlined "No cycle closed
    this period" while seven cycles closed under them."""
    line = review_record.headline("operate", {}, None)
    assert "No cycle closed" not in line, line
    assert "No cycle closed" in review_record.headline("operate", {}, {"cycle": None})


def test_met_since_last_counts_requirements_and_never_the_plan(client):
    """R-00 re-closes itself at every Operate roll; it put "Met since last: 1"
    over "0 met" on a week where nothing was met."""
    from yaaf.core.models import RequirementEvent, RequirementStatus
    from yaaf.server import janitor

    project = operate_project(client, slug="met-since-plan")
    rid, _ = _daily(client, project)
    with db_session() as session:
        plan = session.execute(select(Requirement).where(
            Requirement.project_id == project["id"], Requirement.key == "R-00")).scalar_one()
        for requirement_id in (plan.id, plan.id, rid, rid):
            session.add(RequirementEvent(
                id=ids.new_id(ids.REQUIREMENT_EVENT), project_id=project["id"],
                requirement_id=requirement_id, from_status=RequirementStatus.CHECKING,
                to_status=RequirementStatus.MET, actor="platform"))
    with db_session() as session:
        row = session.get(Project, project["id"])
        numbers = janitor.review_numbers(session, row, effective(session, row.id), None)
    assert numbers["scope"]["met_since_last_review"] == 1
