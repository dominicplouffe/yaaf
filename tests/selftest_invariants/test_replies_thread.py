"""Invariant selftests: a reply is filed under what it answers.

27 November 2026: every conversation on the Messages page read "no replies".
The agents answered each other as new lines in the room ("Sasha: all five of
your ADR points are in…") and 7 of 526 lines that week carried a thread, so
the page, which groups by thread, drew each line as a conversation nobody
answered. The chairman's answers in the boardroom sat under nothing too.
"""

from __future__ import annotations

from tests.conftest import ADMIN, agent_by_name, bearer, channel_of, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Message


def _team(client):
    project = make_project(client)
    return project, channel_of(client, project["id"], "team")


def _say(client, project, who, channel, body, **extra):
    _, token = live_token(project["id"], agent_by_name(project, who))
    posted = client.post("/agent/messages", headers=bearer(token),
                         json={"channel_id": channel, "body": body, **extra})
    assert posted.status_code == 200, posted.text
    return posted.json()


def _thread(message_id: str) -> str | None:
    with db_session() as session:
        return session.get(Message, message_id).thread_id


def test_an_answer_to_a_line_addressed_to_it_is_filed_under_that_line(client):
    project, team = _team(client)
    asked = _say(client, project, "Lead", team, "Emp2: can you take the pricing page today?")
    said = _say(client, project, "Emp2", team, "Yes, it is up by four.")
    assert said["thread_id"] == asked["id"]
    assert "Filed as your reply to Lead's line" in said["note"]


def test_a_line_to_everyone_or_to_someone_else_starts_its_own(client):
    project, team = _team(client)
    _say(client, project, "Lead", team, "Emp2: can you take the pricing page today?")
    to_all = _say(client, project, "Emp2", team, "To all of you: the build is red until noon.")
    assert to_all["thread_id"] is None
    _say(client, project, "Lead", team, "Emp2: and the footer?")
    to_ceo = _say(client, project, "Emp2", team, "CEO, the footer copy is missing.")
    assert to_ceo["thread_id"] is None


def test_a_line_to_the_leads_is_new_even_after_a_reply_to_its_last(client):
    """Snip, 12:57: the CEO's "CEO to leads." went under Quinn's reply to the
    CEO's previous "CEO to leads." line."""
    project = make_project(client)
    leadership = channel_of(client, project["id"], "leadership")
    first = _say(client, project, "CEO", leadership, "CEO to leads. Lead: ci.yml changes once.")
    _say(client, project, "Lead", leadership, "Understood, the scenarios follow it.",
         thread_id=first["id"])
    again = _say(client, project, "CEO", leadership, "CEO to leads. R-01 is on main.")
    assert again["thread_id"] is None


def test_once_it_has_answered_its_next_line_is_new(client):
    project, team = _team(client)
    _say(client, project, "Lead", team, "Emp2: can you take the pricing page today?")
    _say(client, project, "Emp2", team, "Yes.")
    again = _say(client, project, "Emp2", team, "Separately, the logo file is corrupt.")
    assert again["thread_id"] is None


def test_a_named_thread_stands_for_its_root_and_stays_in_its_room(client):
    project, team = _team(client)
    root = _say(client, project, "Lead", team, "Emp2: the pricing page?")
    reply = _say(client, project, "Emp2", team, "On it.", thread_id=root["id"])
    deeper = _say(client, project, "Lead", team, "Thanks.", thread_id=reply["id"])
    assert _thread(deeper["id"]) == root["id"]

    leadership = channel_of(client, project["id"], "leadership")
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    refused = client.post("/agent/messages", headers=bearer(ceo), json={
        "channel_id": leadership, "body": "Noted.", "thread_id": root["id"]})
    assert refused.status_code == 422 and "another room" in refused.json()["error"]


def test_his_answer_to_a_question_goes_under_the_question(client):
    project = make_project(client)
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    asked = client.post("/agent/asks", headers=bearer(ceo), json={
        "title": "Where should the live app run?",
        "body": "Fly.io or Render. My pick is Fly.io.",
        "why": "The plan ends with the app live at a public address.",
        "answer_by_hours": 6,
        "default_action": "Go with Fly.io and tell you I did."})
    assert asked.status_code == 200, asked.text
    answer = client.post(f"/admin/projects/{project['id']}/boardroom", headers=ADMIN,
                         json={"body": "Re: “Where should the live app run?” — Fly.io."})
    assert answer.status_code == 200, answer.text
    with db_session() as session:
        from yaaf.core.models import Ask

        ask = session.query(Ask).filter_by(project_id=project["id"]).one()
        assert _thread(answer.json()["id"]) == ask.asked_in_message_id


def test_his_note_on_a_need_goes_under_the_line_that_asked_it(client):
    project = make_project(client)
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    need = client.post("/agent/needs", headers=bearer(ceo), json={
        "title": "Fly.io access token", "why": "So the app can go live.",
        "est_usd_per_month": 4})
    assert need.status_code == 200, need.text
    decided = client.post(f"/admin/approvals/{need.json()['need_id']}/decide", headers=ADMIN,
                          json={"approve": False, "note": "Not yet, after the beta."})
    assert decided.status_code == 200, decided.text
    with db_session() as session:
        from yaaf.core.models import Approval, ChairmanNote

        approval = session.get(Approval, need.json()["need_id"])
        note = session.query(ChairmanNote).filter_by(project_id=project["id"]).one()
        assert approval.announced_in_message_id
        assert _thread(note.message_id) == approval.announced_in_message_id


def test_the_ceos_answer_to_his_line_goes_under_it(client):
    project = make_project(client)
    boardroom = channel_of(client, project["id"], "boardroom")
    his = client.post(f"/admin/projects/{project['id']}/boardroom", headers=ADMIN,
                      json={"body": "How is the launch going?"}).json()
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    said = client.post("/agent/messages", headers=bearer(ceo), json={
        "channel_id": boardroom, "body": "On track for Friday.", "in_reply_to": [his["id"]]})
    assert said.status_code == 200, said.text
    assert _thread(said.json()["id"]) == his["id"]


def test_the_messages_page_counts_the_reply(client):
    project, team = _team(client)
    asked = _say(client, project, "Lead", team, "Emp2: can you take the pricing page today?")
    _say(client, project, "Emp2", team, "Yes, it is up by four.")
    view = client.get(f"/observe/projects/{project['id']}/conversations", headers=ADMIN).json()
    conv = next(c for c in view["conversations"] if c["id"] == asked["id"])
    assert conv["replies"] == 1 and conv["outcome"] == "answered"
