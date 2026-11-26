"""Invariant selftests: agents' mail is never held until midnight.

Reading had a daily allowance of its own, and when it was spent every message
between agents waited for the 00:00 UTC refill. A message can be a handoff
("my part is done, over to you"), and a Deliver project stalled all afternoon
that way with 136 of its 150 runs unused. The chairman asked for no ceiling on
reading. What must hold:

  * past the reading allowance, mail is read on the daily runs instead
  * those runs always count, so the daily runs still bound reading
  * reading never takes the runs kept for reviews, or a day that is spent
  * an allowance of 0 is still a choice (agents read on their next work wake),
    not a ceiling, and it is still honoured
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import agent_by_name, channel_of, make_project
from yaaf.core import ids
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import Message, Project, Run, RunKind, RunQueue, RunStatus, utcnow
from yaaf.server import dispatcher


def _drain(max_rounds: int = 6) -> None:
    for _ in range(max_rounds):
        with db_session() as session:
            if dispatcher.dispatch_once(session) == 0:
                return
            session.query(RunQueue).delete()
    raise AssertionError("dispatcher would not settle")


def _spend(project: dict, kind: str, n: int) -> None:
    """n runs of `kind` today that count: what a used-up allowance looks like."""
    with db_session() as session:
        for _ in range(n):
            session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"],
                            agent_id=project["ceo_agent_id"], kind=kind, trigger={"type": "test"},
                            status=RunStatus.DONE, produced=1, metered=True, created_at=utcnow()))


def _mail(client, project: dict) -> None:
    team_room = channel_of(client, project["id"], "team")
    with db_session() as session:
        session.add(Message(
            id=ids.new_id(ids.MESSAGE), project_id=project["id"], channel_id=team_room,
            sender_agent_id=agent_by_name(project, "Lead"),
            body="section four is done, the totals are yours now",
            created_at=utcnow() - timedelta(minutes=30)))


def _activity(project: dict) -> dict:
    with db_session() as session:
        row = session.get(Project, project["id"])
        return dispatcher.activity_state(session, row, effective(session, row.id))


def test_mail_past_the_reading_allowance_is_read_on_the_daily_runs(client):
    project = make_project(client, constitution={"inbox_runs_per_project_per_day": 2})
    _drain()
    _spend(project, RunKind.INBOX_RUN, 2)
    _mail(client, project)

    assert _activity(project)["blocked_by"] != "inbox_runs"
    with db_session() as session:
        assert dispatcher.dispatch_once(session) >= 1
        queued = session.query(RunQueue).filter(RunQueue.project_id == project["id"]).all()
    assert queued and all(q.kind == RunKind.AGENT_RUN for q in queued)
    assert all(q.trigger.get(dispatcher.READ_ON_WORK) for q in queued)


def test_a_read_on_the_daily_runs_always_counts():
    """Reading and finding nothing is the ordinary outcome of a read. Refunded
    like an empty work run, reading would have no ceiling at all."""
    reads = [{"kind": "messages", "count": 1, "urgent": False}]
    work = [{"kind": "team_task_ready", "task_id": "t1"}]
    assert dispatcher.refundable(RunKind.AGENT_RUN, 0, {"reasons": work}) is True
    assert dispatcher.refundable(RunKind.AGENT_RUN, 0,
                                 {"reasons": reads, dispatcher.READ_ON_WORK: True}) is False
    assert dispatcher.refundable(RunKind.INBOX_RUN, 0, {"reasons": reads}) is False
    assert dispatcher.refundable(RunKind.AGENT_RUN, 2, {"reasons": work}) is False


def test_reading_never_spends_a_day_that_is_used_up(client):
    project = make_project(client, constitution={"inbox_runs_per_project_per_day": 1,
                                                 "runs_per_project_per_day": 4,
                                                 "reserved_governance_runs": 0})
    _drain()
    _spend(project, RunKind.INBOX_RUN, 1)
    _spend(project, RunKind.AGENT_RUN, 4)
    _mail(client, project)

    assert _activity(project)["blocked_by"] == "daily_runs"
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0


def test_reading_never_takes_the_runs_kept_for_reviews(client):
    project = make_project(client, constitution={"inbox_runs_per_project_per_day": 1,
                                                 "runs_per_project_per_day": 6,
                                                 "reserved_governance_runs": 2})
    _drain()
    _spend(project, RunKind.INBOX_RUN, 1)
    _spend(project, RunKind.AGENT_RUN, 4)
    _mail(client, project)

    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0


def test_reading_switched_off_is_still_a_choice(client):
    """0 is what the few-times-daily pace ships: agents read their mail on the
    next wake they were having anyway. Not a ceiling, so it is not lifted."""
    project = make_project(client, constitution={"inbox_runs_per_project_per_day": 0})
    _drain()
    _mail(client, project)

    assert _activity(project)["blocked_by"] == "inbox_runs"
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0
