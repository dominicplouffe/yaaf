"""Invariant selftests: the chairman is answered, whatever the day's runs did.

Chat is talk and runs are work (627ad99). A Docker install keeps that promise
with a resident CEO session that counts against none of the run allowances.
Subprocess and host mode have no resident session, and their boardroom fell
back to an ordinary work run, billed to the same daily allowance as the work.
On the desktop install that found it, the twelve runs of a few-times-a-day
tempo were gone by 04:00, and the chairman's "hello ava, how are you?" was
held until midnight UTC behind a banner saying nothing would start.

The suite runs in subprocess mode, so every test here is on the queued path.
"""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN, agent_by_name, bearer, channel_of, live_token, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Notice,
    NoticeKind,
    Project,
    Run,
    RunKind,
    RunQueue,
    RunStatus,
    utcnow,
)
from yaaf.server import dispatcher

# A few-times-a-day shape: a small work allowance, a reserve for reporting, and
# no reading lane at all — the tempo the incident ran on.
TIGHT = {"runs_per_project_per_day": 4, "reserved_governance_runs": 1,
         "inbox_runs_per_project_per_day": 0, "inbox_runs_per_project_per_hour": 0,
         "max_concurrent_runs": 5}


def _drain() -> None:
    for _ in range(5):
        with db_session() as session:
            if dispatcher.dispatch_once(session) == 0:
                return
            session.query(RunQueue).delete()
    raise AssertionError("dispatcher would not settle")


def _spend(project: dict, runs: int) -> None:
    """Finished work runs today, on a seat that is not the CEO's, so the CEO's
    own wake brake is untouched and only the project allowance is spent."""
    lead = agent_by_name(project, "Lead")
    with db_session() as session:
        for _ in range(runs):
            at = utcnow()
            session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"], agent_id=lead,
                            kind=RunKind.AGENT_RUN, status=RunStatus.DONE,
                            trigger={"type": "wake"}, created_at=at, started_at=at,
                            finished_at=at))


def _used_today(project: dict) -> int:
    with db_session() as session:
        row = session.get(Project, project["id"])
        return dispatcher.daily_runs_used(session, row, dispatcher.day_start(utcnow()))


def _chairman_says(client, project: dict, body: str = "hello, how are you?") -> None:
    response = client.post(f"/admin/projects/{project['id']}/boardroom",
                           json={"body": body}, headers=ADMIN)
    assert response.status_code == 200, response.text


def _queued_one() -> RunQueue:
    with db_session() as session:
        row = session.query(RunQueue).one()
        session.expunge(row)
        return row


@pytest.mark.parametrize("spent", [4, 3], ids=["allowance-gone", "reserve-only"])
def test_the_ceo_answers_the_chairman_on_a_spent_day(client, spent):
    """At the wall, and in the reserved slice where only reporting may spend,
    his message still buys the CEO a run — in the talk lane, which neither
    allowance pays for."""
    project = make_project(client, constitution=TIGHT)
    _drain()
    _spend(project, spent)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0, "nothing else may run today"

    _chairman_says(client, project)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1, "the chairman waited for midnight"
    queued = _queued_one()
    assert queued.agent_id == project["ceo_agent_id"]
    assert queued.kind == RunKind.BOARDROOM_RUN
    assert _used_today(project) == spent, "answering him must not spend the allowance"


def test_the_work_behind_his_message_still_waits_for_its_purse(client):
    """The talk lane carries the chairman's message and nothing else. A notice
    that arrived on the same wake is work, and on a spent day work waits."""
    project = make_project(client, constitution=TIGHT)
    _drain()
    _spend(project, 4)
    with db_session() as session:
        session.add(Notice(id=ids.new_id(ids.NOTICE), project_id=project["id"],
                           agent_id=project["ceo_agent_id"], kind=NoticeKind.BUDGET_WARNING,
                           body="x"))
    _chairman_says(client, project)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1
    reasons = _queued_one().trigger["reasons"]
    boardroom = channel_of(client, project["id"], "boardroom")
    assert reasons == [{"kind": "messages", "channel_id": boardroom, "count": 1,
                        "urgent": True}], reasons


def test_answering_the_chairman_never_spends_the_work_allowance(client):
    """Not only on a spent day: a reply is talk on every day, exactly as the
    resident chat session is. Queued or finished, it is not counted."""
    project = make_project(client, constitution=TIGHT)
    _drain()
    before = _used_today(project)
    _chairman_says(client, project)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1
    queued = _queued_one()
    assert queued.kind == RunKind.BOARDROOM_RUN
    assert _used_today(project) == before, "a queued reply spent the allowance"

    with db_session() as session:
        session.query(RunQueue).delete()
        at = utcnow()
        session.add(Run(id=queued.id, project_id=project["id"], agent_id=queued.agent_id,
                        kind=queued.kind, trigger=queued.trigger, status=RunStatus.DONE,
                        created_at=at, started_at=at, finished_at=at))
    assert _used_today(project) == before, "a finished reply spent the allowance"


def test_a_spent_day_does_not_spend_the_ceos_planning_beat(client):
    """With both allowances gone the dispatcher asks the CEO about the
    boardroom only. Asking her full set of reasons would advance her planning
    heartbeat and her daily wake on a tick that cannot run them — and they
    would not be due again when the allowance came back."""
    from datetime import timedelta

    from yaaf.core.models import Heartbeat

    project = make_project(client, constitution=TIGHT)
    _drain()
    _spend(project, 4)
    stale = utcnow() - timedelta(hours=999)
    with db_session() as session:
        beat = session.query(Heartbeat).filter_by(agent_id=project["ceo_agent_id"],
                                                  name="planning").one()
        beat.last_fired_at = stale
    _chairman_says(client, project)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1
    with db_session() as session:
        beat = session.query(Heartbeat).filter_by(agent_id=project["ceo_agent_id"],
                                                  name="planning").one()
        fired = beat.last_fired_at.replace(tzinfo=None)
        assert fired == stale.replace(tzinfo=None), "the spent tick consumed the beat"


def test_a_reply_shows_the_ceo_her_notices_and_leaves_them_for_the_work(client):
    """The dispatcher held the work back; the run must not do it anyway. A
    reply run that consumed the CEO's notices would either act on them
    unbilled or swallow them, so the planner never saw them — the failure the
    chat lane already learned. It is shown them, and they stay owed."""
    project = make_project(client, constitution=TIGHT)
    _drain()
    notice_id = ids.new_id(ids.NOTICE)
    with db_session() as session:
        session.add(Notice(id=notice_id, project_id=project["id"],
                           agent_id=project["ceo_agent_id"],
                           kind=NoticeKind.COMMITMENT_AT_RISK, body="the date slipped"))
    _, token = live_token(project["id"], project["ceo_agent_id"], kind=RunKind.BOARDROOM_RUN)
    pack = client.get("/agent/context", headers=bearer(token))
    assert pack.status_code == 200, pack.text
    assert [n["body"] for n in pack.json()["notices"]] == ["the date slipped"]
    with db_session() as session:
        assert session.get(Notice, notice_id).consumed_at is None, "the reply ate the work"

    _, work = live_token(project["id"], project["ceo_agent_id"])
    assert client.get("/agent/context", headers=bearer(work)).status_code == 200
    with db_session() as session:
        assert session.get(Notice, notice_id).consumed_at is not None, \
            "the lane that acts on a notice is the one that marks it done"


def test_an_agent_cannot_buy_itself_a_free_run_through_the_boardroom(client):
    """The lane is free because only the chairman can open it. The CEO's own
    words in the boardroom never wake her, and nobody else may speak there,
    so a spent project stays spent for every agent that tries."""
    project = make_project(client, constitution=TIGHT)
    _drain()
    _spend(project, 4)
    boardroom = channel_of(client, project["id"], "boardroom")

    run_id, token = live_token(project["id"], project["ceo_agent_id"])
    posted = client.post("/agent/messages", headers=bearer(token),
                         json={"channel_id": boardroom, "body": "status: all quiet"})
    assert posted.status_code == 200, posted.text
    with db_session() as session:
        session.get(Run, run_id).status = RunStatus.DONE

    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    refused = client.post("/agent/messages", headers=bearer(lead_token),
                          json={"channel_id": boardroom, "body": "hello?"})
    assert refused.status_code == 403, refused.text

    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0


def test_no_work_brake_stands_between_the_chairman_and_his_answer(client):
    """The chat session passes the agent's wake ceiling, the project's
    concurrency cap and a run the CEO is in the middle of. So does its queued
    vehicle: a CEO at its ceiling, in a work run, on a full project still
    answers him."""
    project = make_project(client, constitution={**TIGHT, "wakes_per_agent_per_hour": 1,
                                                 "max_concurrent_runs": 1})
    _drain()
    ceo = project["ceo_agent_id"]
    with db_session() as session:
        at = utcnow()
        session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"], agent_id=ceo,
                        kind=RunKind.AGENT_RUN, status=RunStatus.RUNNING,
                        trigger={"type": "wake"}, created_at=at, started_at=at))
    _chairman_says(client, project)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1
    assert _queued_one().kind == RunKind.BOARDROOM_RUN
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0, "one reply per message, not one per tick"


def test_a_ceo_whose_replies_keep_failing_is_held_by_the_lanes_backstop(client):
    """Free of the work brakes is not free of every brake: a seat whose
    replies die is a loop, held as the chat supervisor holds a boot-looping
    session — by failures in a window, never by a chairman talking quickly."""
    project = make_project(client, constitution=TIGHT)
    _drain()
    ceo = project["ceo_agent_id"]
    with db_session() as session:
        for attempt in (1, 2, 3):
            at = utcnow()
            session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"], agent_id=ceo,
                            kind=RunKind.BOARDROOM_RUN, status=RunStatus.FAILED,
                            trigger={"type": "wake"}, attempt=attempt, created_at=at,
                            started_at=at, finished_at=at))
    _chairman_says(client, project)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0


def test_a_reply_run_is_given_the_chat_lanes_rules():
    """Same conversation, same rules, one text: only the delivery differs."""
    from yaaf.runner.talk_prompt import BOARDROOM_ADDENDUM, CHAT_ADDENDUM

    for rule in ("THREE SENTENCES", "NEVER PASTE A DOCUMENT", "brief_propose",
                 "directive_file", "note_answer"):
        assert rule in BOARDROOM_ADDENDUM and rule in CHAT_ADDENDUM, rule
    assert "Post your reply to the boardroom with message_post" in BOARDROOM_ADDENDUM
    assert "Do NOT message_post your reply" not in BOARDROOM_ADDENDUM


def test_a_reply_runs_second_draft_lands_as_the_sessions_does(client):
    """Refused once for unclear writing, the rewrite goes through — recorded —
    so a live conversation never leaves him unanswered over style. A work run
    has no such way: it rewrites until it passes."""
    from yaaf.server import plain

    project = make_project(client, constitution=TIGHT)
    boardroom = channel_of(client, project["id"], "boardroom")
    long = "This " + "very " * 40 + "long sentence is hard to read."
    for kind, lands in ((RunKind.BOARDROOM_RUN, True), (RunKind.AGENT_RUN, False)):
        run_id, token = live_token(project["id"], project["ceo_agent_id"], kind=kind)
        with db_session() as session:
            session.get(Run, run_id).last_refusal = plain.refusal_text(["x"])
        posted = client.post("/agent/messages", headers=bearer(token),
                             json={"channel_id": boardroom, "body": long})
        assert (posted.status_code == 200) is lands, (kind, posted.text)
        with db_session() as session:
            session.get(Run, run_id).status = RunStatus.DONE


def test_the_spent_day_banner_says_he_is_still_answered(client):
    """The banner the chairman reads, and the CEO is told to quote, said
    nothing would start. Work stops; his answer does not."""
    project = make_project(client, constitution=TIGHT)
    _drain()
    _spend(project, 4)
    from yaaf.core.constitution import effective

    with db_session() as session:
        row = session.get(Project, project["id"])
        state = dispatcher.activity_state(session, row, effective(session, row.id))
    assert state["blocked_by"] == "daily_runs"
    detail = state["brief"]["detail"]
    assert "Nothing starts" not in detail, detail
    assert "still answered" in detail, detail
    assert "still spent the allowance" not in detail, "dead runs that did nothing are given back"
