"""Invariant selftests: a Code Factory wakes a seat only for work it can do.

From the Split app's first night, read back from its runs: 32 of 60 seat runs
were woken by leadership-channel messages alone ("nothing needed me this run"),
a builder was woken five times in twenty minutes for a signed change that only
waited on a merge freeze, and its CI requirement showed "21 of 4 runs" because
the platform's own scenario checks counted as spend.
"""

from __future__ import annotations

from types import SimpleNamespace

from tests.conftest import make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    AgentKind,
    Change,
    ChangeStatus,
    ChannelKind,
    Run,
    RunKind,
    RunStatus,
    utcnow,
)
from yaaf.server import changes, dispatcher, policy


def _change(**over) -> Change:
    base = dict(status=ChangeStatus.OPEN, head_sha="h1", checks={"test": "success"},
                findings={}, scenario_results=None, review_verdict=None, reviewed_sha=None,
                gates={"review": {"ok": False}, "checks": {"ok": True}})
    return Change(**{**base, **over})


def test_a_change_waiting_on_a_reader_or_the_chairman_does_not_wake_its_author():
    assert not changes.author_can_act(_change())
    assert not changes.author_can_act(_change(gates={"ci_config": {"ok": False},
                                                     "review": {"ok": True}}))
    assert not changes.author_can_act(_change(status=ChangeStatus.MERGING))
    assert not changes.author_can_act(_change(checks={"test": "pending"},
                                              gates={"checks": {"ok": False}}))


def test_a_change_with_something_to_fix_wakes_its_author():
    assert changes.author_can_act(_change(review_verdict="returned", reviewed_sha="h1"))
    assert changes.author_can_act(_change(checks={"test": "failure"},
                                          gates={"checks": {"ok": False}}))
    assert changes.author_can_act(_change(gates={"mergeable": {"ok": False}}))
    assert changes.author_can_act(_change(
        scenario_results={"sha": "h1", "results": {"s1": False}},
        gates={"scenarios": {"ok": False}}))


def test_in_a_factory_leadership_lines_wake_only_the_seats_they_name():
    factory = SimpleNamespace(mode="code_factory")
    rhea = SimpleNamespace(id="agnt_rhea", name="Rhea", kind=AgentKind.EMPLOYEE)
    ceo = SimpleNamespace(id="agnt_ceo", name="CEO", kind=AgentKind.CEO)
    assert dispatcher._addressed_only(factory, rhea, ChannelKind.LEADERSHIP)
    assert not dispatcher._addressed_only(factory, ceo, ChannelKind.LEADERSHIP)
    assert not dispatcher._addressed_only(factory, rhea, ChannelKind.TEAM)
    named = SimpleNamespace(body="Rhea, can you read #2 next?", thread_id=None)
    other = SimpleNamespace(body="Theo is revising the record.", thread_id=None)
    assert dispatcher._addressed_to(None, rhea, named)
    assert not dispatcher._addressed_to(None, rhea, other)


def test_the_platforms_own_checks_are_not_spend_on_a_requirement(client):
    project = make_project(client)
    rid = "reqt_" + ids.new_id(ids.RUN)[5:]
    with db_session() as session:
        for kind in (RunKind.AGENT_RUN, RunKind.SCENARIO_CHECK, RunKind.CHANGE_SCAN):
            session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"], kind=kind,
                            trigger={}, status=RunStatus.DONE, requirement_id=rid,
                            started_at=utcnow(), finished_at=utcnow()))
    with db_session() as session:
        assert policy.runs_spent_on(session, rid) == 1
