"""Invariant selftests: the Messages page says how each conversation ended.

It was a When / From / Message table, where "the report is yours now" with no
reply looked exactly like "On it." Each conversation (a thread root and its
replies) is now labelled from the record, never from an agent's own claim:

  * a handoff nobody replied to, with nothing made, is "stuck" once it is old
  * a reply from somebody else is "answered"
  * a task made by someone in it while it went on is "handoff"
  * a frozen root is "frozen"
  * a recent question with no reply is "waiting", naming who has not read it
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, agent_by_name, channel_of, make_project, person
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Message, Task, utcnow


def _say(project: dict, channel: str, who: str, body: str, *, ago: timedelta,
         thread: str | None = None, frozen: bool = False) -> str:
    mid = ids.new_id(ids.MESSAGE)
    with db_session() as session:
        session.add(Message(id=mid, project_id=project["id"], channel_id=channel,
                            sender_agent_id=agent_by_name(project, who), body=body, thread_id=thread,
                            created_at=utcnow() - ago, frozen_at=utcnow() if frozen else None))
    return mid


def _view(client, project: dict) -> dict:
    response = client.get(f"/observe/projects/{project['id']}/conversations", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def _conv(view: dict, root: str) -> dict:
    return next(c for c in view["conversations"] if c["id"] == root)


def test_a_handoff_nobody_picked_up_is_stuck_and_says_who_never_read_it(client):
    project = make_project(client)
    team = channel_of(client, project["id"], "team")
    root = _say(project, team, "Lead", "Section four is done. @Emp2 the report is yours now.",
                ago=timedelta(hours=5))

    view = _view(client, project)
    c = _conv(view, root)
    assert c["outcome"] == "stuck"
    assert c["to"] == "Emp2"
    assert "Emp2" in c["unread"]
    assert c["title"] == "Section four is done."
    room = next(r for r in view["rooms"] if r["id"] == team)
    assert room["open"] == 1


def test_a_reply_answers_it(client):
    project = make_project(client)
    team = channel_of(client, project["id"], "team")
    root = _say(project, team, "Emp2", "Which months count as last year?", ago=timedelta(hours=3))
    _say(project, team, "Lead", "Nov to Oct, so every month is complete.", ago=timedelta(hours=2), thread=root)

    c = _conv(_view(client, project), root)
    assert c["outcome"] == "answered"
    assert c["replies"] == 1 and c["people"] == [agent_by_name(project, "Emp2"), agent_by_name(project, "Lead")]


def test_a_task_made_while_it_went_on_is_a_handoff(client):
    project = make_project(client)
    team = channel_of(client, project["id"], "team")
    root = _say(project, team, "Emp2", "Direct traffic from Singapore looks like a bot.", ago=timedelta(hours=3))
    with db_session() as session:
        session.add(Task(id=ids.new_id(ids.TASK), project_id=project["id"], team_id=project["teams"][0]["id"],
                         created_by_agent_id=agent_by_name(project, "Lead"),
                         title="Exclude scripted traffic from the counts", goal="g",
                         success_test={"type": "observable", "target": "x"},
                         created_at=utcnow() - timedelta(hours=2)))
    _say(project, team, "Lead", "Made it a task.", ago=timedelta(hours=2), thread=root)

    c = _conv(_view(client, project), root)
    assert c["outcome"] == "handoff"
    assert [r["label"] for r in c["refs"] if r["kind"] == "task"] == ["Exclude scripted traffic from the counts"]


def test_a_frozen_conversation_says_so(client):
    project = make_project(client)
    team = channel_of(client, project["id"], "team")
    root = _say(project, team, "Lead", "Second person throughout?", ago=timedelta(hours=1), frozen=True)
    assert _conv(_view(client, project), root)["outcome"] == "frozen"


def test_a_fresh_question_is_waiting_not_stuck(client):
    project = make_project(client)
    team = channel_of(client, project["id"], "team")
    root = _say(project, team, "Lead", "Over to you for the totals, when you can.", ago=timedelta(minutes=20))
    c = _conv(_view(client, project), root)
    assert c["outcome"] == "waiting"
    assert c["unread"] == ["Emp2"]


def test_a_stranger_reads_none_of_it(client):
    project = make_project(client)
    stranger = person("nosy@example.com")
    response = client.get(f"/observe/projects/{project['id']}/conversations", headers=stranger)
    assert response.status_code in (403, 404)


def test_nothing_in_the_boardroom_needs_attention_here(client):
    """His own room, read as a chat where the CEO answers in the open. A line
    yaaf posted to him is yaaf's, not "Someone" waiting for a reply."""
    project = make_project(client)
    board = channel_of(client, project["id"], "boardroom")
    with db_session() as session:
        mid = ids.new_id(ids.MESSAGE)
        session.add(Message(id=mid, project_id=project["id"], channel_id=board, body="The team published a file.",
                            created_at=utcnow() - timedelta(hours=5)))
    view = _view(client, project)
    c = _conv(view, mid)
    assert c["outcome"] == "note" and c["people"] == ["yaaf"]
    assert next(r for r in view["rooms"] if r["id"] == board)["open"] == 0


def test_each_agent_says_why_it_has_not_read_its_mail(client):
    """"Why is nothing ever read?" answered per agent, from the same gates the
    dispatcher wakes by: a setting is named as a setting, and mail that nothing
    is holding back is called what it is, a fault."""
    off = make_project(client, slug="readoff", constitution={"inbox_runs_per_project_per_day": 0})
    team = channel_of(client, off["id"], "team")
    _say(off, team, "Lead", "Can you check the totals?", ago=timedelta(hours=3))
    (emp2,) = [a for a in _view(client, off)["agents"] if a["name"] == "Emp2"]
    assert emp2["unread"] == 1 and emp2["fault"] is False
    assert "doesn't wake agents just to read" in emp2["why"]

    on = make_project(client, slug="readon")
    team = channel_of(client, on["id"], "team")
    _say(on, team, "Lead", "Can you check the totals?", ago=timedelta(hours=3))
    (emp2,) = [a for a in _view(client, on)["agents"] if a["name"] == "Emp2"]
    assert emp2["fault"] is True and "should have woken up" in emp2["why"]
