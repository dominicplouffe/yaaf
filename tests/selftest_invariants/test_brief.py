"""Invariant selftests: the chairman can change his mind, and it holds.

Two desks showed the same thing. A blog told to stop being an HN editorial went
on ranking topics by HN points; a trading desk had a stop the chairman deleted
cut an order five weeks later, "from the original project settings". In both,
every change of direction held for a day or two and then reverted.

Not stubbornness. The founding goal was pushed into every agent's prompt on every
run and never changed, while each later instruction went into a memory, a file or
a handoff note that an agent had to think to look up. And the work runs that plan
the day never saw the chairman's boardroom messages at all: the resident chat
session reads as the same agent and moved the one "last read" mark both lanes
share every two seconds.

So: the CEO writes his current direction back to him as a brief — the goal,
standing orders with their numbers, what no longer applies — and his Confirm is
what replaces the goal every agent reads. And the work run is told what he said
in chat since it last ran.
"""

from __future__ import annotations

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from tests.selftest_invariants.test_chat_lane import _attach_billing, _fake_sandbox
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Message,
    Notice,
    NoticeKind,
    Project,
    Run,
    RunStatus,
)

BRIEF = {
    "goal": "Grow the book 10% after tax by year end, owning a few names with conviction.",
    "standing_orders": ["Max 15% of the book in one name",
                        "No stop-loss on core holdings"],
    "retired": ["25% trailing stop on every position"],
    "what_changed": "Oct 13: 'only 1 rule — grow it 10% after tax'. Sep 8: drop the 25% stop.",
}


def propose(client, project: dict, **over):
    _, token = live_token(project["id"], project["ceo_agent_id"])
    return client.post("/agent/brief", json={**BRIEF, **over}, headers=bearer(token))


def pending_brief(project: dict) -> Approval:
    with db_session() as session:
        row = session.query(Approval).filter_by(project_id=project["id"],
                                                kind=ApprovalKind.BRIEF,
                                                status=ApprovalStatus.PENDING).one()
        session.expunge(row)
        return row


def system_prompt(project: dict, agent_id: str | None = None) -> str:
    from yaaf.server.contextpack import build_system_prompt

    with db_session() as session:
        row = session.get(Project, project["id"])
        agent = session.get(Agent, agent_id or project["ceo_agent_id"])
        return build_system_prompt(session, agent, row)


def boardroom(client, project: dict) -> str:
    channels = client.get(f"/observe/projects/{project['id']}/channels", headers=ADMIN).json()
    return next(c["id"] for c in channels if c["kind"] == "boardroom")


# ── the CEO writes it back; nothing moves until he confirms ─────────────────────


def test_a_proposed_brief_changes_nothing_until_he_confirms(client):
    project = make_project(client)
    founded = system_prompt(project)

    response = propose(client, project)
    assert response.status_code == 200, response.text
    assert "Nothing has changed yet" in response.json()["note"]

    with db_session() as session:
        assert session.get(Project, project["id"]).goal != BRIEF["goal"]
    assert system_prompt(project) == founded
    assert pending_brief(project).brief["standing_orders"] == BRIEF["standing_orders"]


def test_the_boardroom_shows_him_the_brief_and_where_to_confirm_it(client):
    project = make_project(client)
    propose(client, project)
    with db_session() as session:
        posted = [m.body for m in session.query(Message)
                  .filter_by(channel_id=boardroom(client, project))]
    line = next(b for b in posted if "Confirm the brief" in b)
    assert "Max 15% of the book in one name" in line
    assert "25% trailing stop" in line
    assert f"#/projects/{project['id']}/activity/decisions" in line


def test_a_redraft_is_shown_as_what_it_changes_not_the_whole_brief_again(client):
    """Asked to move one sentence, the CEO writes back all of them; the chairman
    read ten orders to find the one that moved. The post leads with the change,
    against the brief in force, and only counts what stayed."""
    project = make_project(client)
    propose(client, project, standing_orders=[
        "Max 15% of the book in one name", "No stop-loss on core holdings",
        "Give the orders as prose", "Every figure carries its source URL"])
    client.post(f"/admin/approvals/{pending_brief(project).id}/decide",
                json={"approve": True}, headers=ADMIN)
    propose(client, project, standing_orders=[
        "Max 12% of the book in one name", "No stop-loss on core holdings",
        "The orders lead the ticket as ONE table", "Every figure carries its source URL"],
        retired=["25% trailing stop on every position", "Orders given as prose"])
    with db_session() as session:
        posted = [m.body for m in session.query(Message)
                  .filter_by(channel_id=boardroom(client, project))
                  .order_by(Message.created_at)]
    line = [b for b in posted if "Confirm the brief" in b][-1]
    assert "**Order, edited:** Max ~~15%~~ **12%** of the book in one name" in line
    assert "**New order:** The orders lead the ticket as ONE table" in line
    assert "**Order dropped:** ~~Give the orders as prose~~" in line
    assert "**Now retired:** Orders given as prose" in line
    assert "_Unchanged: the goal, 2 standing orders, 1 retired._" in line
    # What did not move is counted, not re-printed.
    assert "No stop-loss on core holdings" not in line
    assert "25% trailing stop" not in line

    mail = pending_brief(project).body
    assert "- Order edited. Was: Max 15% of the book in one name" in mail
    assert "  Now: Max 12% of the book in one name" in mail


def test_confirming_replaces_the_goal_every_agent_reads(client):
    project = make_project(client)
    with db_session() as session:
        founding = session.get(Project, project["id"]).goal
    propose(client, project)
    decided = client.post(f"/admin/approvals/{pending_brief(project).id}/decide",
                          json={"approve": True}, headers=ADMIN)
    assert decided.status_code == 200, decided.text

    employee = agent_by_name(project, "Emp2")
    for prompt in (system_prompt(project), system_prompt(project, employee)):
        assert f"PROJECT GOAL: {BRIEF['goal']}" in prompt
        assert "- Max 15% of the book in one name" in prompt
        assert "RETIRED" in prompt and "- 25% trailing stop on every position" in prompt
        # Replaced, not appended: the founding goal is gone from the prompt.
        assert founding not in prompt


def test_the_ceo_is_told_to_bring_its_files_into_line(client):
    project = make_project(client)
    propose(client, project)
    client.post(f"/admin/approvals/{pending_brief(project).id}/decide",
                json={"approve": True}, headers=ADMIN)
    with db_session() as session:
        notice = (session.query(Notice)
                  .filter_by(agent_id=project["ceo_agent_id"], kind=NoticeKind.GOAL_CHANGED)
                  .order_by(Notice.created_at.desc()).first())
    assert notice is not None
    assert "IT WAS:" in notice.body
    assert "replace, do not append" in notice.body


def test_he_can_edit_the_brief_as_he_confirms_it(client):
    project = make_project(client)
    propose(client, project)
    edited = {**BRIEF, "standing_orders": ["Max 12% of the book in one name"]}
    client.post(f"/admin/approvals/{pending_brief(project).id}/decide",
                json={"approve": True, "brief": edited}, headers=ADMIN)
    with db_session() as session:
        row = session.get(Project, project["id"])
        assert row.standing_orders == ["Max 12% of the book in one name"]
        notice = (session.query(Notice)
                  .filter_by(agent_id=project["ceo_agent_id"], kind=NoticeKind.GOAL_CHANGED)
                  .order_by(Notice.created_at.desc()).first())
    assert "after editing it" in notice.body


def test_declining_keeps_the_brief_in_force(client):
    project = make_project(client)
    propose(client, project)
    with db_session() as session:
        goal = session.get(Project, project["id"]).goal
    client.post(f"/admin/approvals/{pending_brief(project).id}/decide",
                json={"approve": False, "note": "Keep the 25% stop."}, headers=ADMIN)
    with db_session() as session:
        assert session.get(Project, project["id"]).goal == goal
        assert not session.get(Project, project["id"]).standing_orders


def test_a_new_draft_replaces_the_unconfirmed_one(client):
    project = make_project(client)
    propose(client, project)
    first = pending_brief(project).id
    propose(client, project, standing_orders=["Max 10% of the book in one name"])
    second = pending_brief(project)
    assert second.id != first
    assert second.brief["standing_orders"] == ["Max 10% of the book in one name"]


def test_only_the_ceo_proposes_the_brief(client):
    project = make_project(client)
    _, token = live_token(project["id"], agent_by_name(project, "Emp2"))
    response = client.post("/agent/brief", json=BRIEF, headers=bearer(token))
    assert response.status_code == 403


def test_the_brief_in_force_is_not_filed_again(client):
    project = make_project(client)
    propose(client, project)
    client.post(f"/admin/approvals/{pending_brief(project).id}/decide",
                json={"approve": True}, headers=ADMIN)
    assert propose(client, project).status_code == 409


def test_an_order_cannot_be_both_standing_and_retired(client):
    project = make_project(client)
    response = propose(client, project, retired=["Max 15% of the book in one name"])
    assert response.status_code == 422


def test_retired_orders_carry_forward_until_revived(client):
    """A rule taken back in October still sits in the files it was written into
    in November; the list is how the agent that finds it knows it is dead."""
    project = make_project(client)
    propose(client, project)
    client.post(f"/admin/approvals/{pending_brief(project).id}/decide",
                json={"approve": True}, headers=ADMIN)
    propose(client, project, retired=["No shorting"],
            standing_orders=["Max 15% of the book in one name"])
    client.post(f"/admin/approvals/{pending_brief(project).id}/decide",
                json={"approve": True}, headers=ADMIN)
    with db_session() as session:
        retired = session.get(Project, project["id"]).retired_orders
    assert "No shorting" in retired
    assert "25% trailing stop on every position" in retired

    propose(client, project, retired=[],
            standing_orders=["25% trailing stop on every position"])
    client.post(f"/admin/approvals/{pending_brief(project).id}/decide",
                json={"approve": True}, headers=ADMIN)
    with db_session() as session:
        assert "25% trailing stop on every position" not in (
            session.get(Project, project["id"]).retired_orders)


# ── Settings edits the same brief ───────────────────────────────────────────────


def test_settings_saves_the_whole_brief(client):
    project = make_project(client)
    response = client.patch(f"/admin/projects/{project['id']}", json={
        "goal": BRIEF["goal"], "standing_orders": BRIEF["standing_orders"],
        "retired_orders": ["Old rule"]}, headers=ADMIN)
    assert response.status_code == 200, response.text
    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert dash["standing_orders"] == BRIEF["standing_orders"]
    assert dash["retired_orders"] == ["Old rule"]
    assert dash["brief_confirmed_at"]
    # In Settings he edits the whole retired list, so clearing it clears it.
    client.patch(f"/admin/projects/{project['id']}", json={"retired_orders": []},
                 headers=ADMIN)
    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert dash["retired_orders"] == []


# ── the chat session restarts onto it ───────────────────────────────────────────


def test_the_chat_session_restarts_when_the_brief_changes(client, monkeypatch):
    from yaaf.runner import chat

    state = _fake_sandbox(monkeypatch)
    project = make_project(client)
    _attach_billing(client, project["id"])
    chat.chat_supervisor_once()
    with db_session() as session:
        first = session.query(Run).filter_by(kind="chat_session",
                                             status=RunStatus.RUNNING).one().id

    chat.chat_supervisor_once()  # nothing changed: adopted, not restarted
    assert len(state["started"]) == 1

    client.patch(f"/admin/projects/{project['id']}",
                 json={"standing_orders": ["Max 15% of the book in one name"]},
                 headers=ADMIN)
    chat.chat_supervisor_once()
    with db_session() as session:
        ended = session.get(Run, first)
        assert ended.status == RunStatus.KILLED
        assert "brief changed" in ended.summary
        assert session.query(Run).filter_by(kind="chat_session",
                                            status=RunStatus.RUNNING).one().id != first


# ── the work run hears what he said in chat ─────────────────────────────────────


def test_a_work_run_is_told_what_he_said_in_chat(client):
    project = make_project(client)
    client.post(f"/admin/projects/{project['id']}/boardroom",
                json={"body": "Forget the stop. Only one rule now."}, headers=ADMIN)
    # The resident session reads the room, which used to move the shared mark.
    _, chat_token = live_token(project["id"], project["ceo_agent_id"], kind="chat_session")
    client.get("/agent/messages", params={"channel_id": boardroom(client, project)},
               headers=bearer(chat_token))

    _, work = live_token(project["id"], project["ceo_agent_id"])
    pack = client.get("/agent/context", headers=bearer(work)).json()
    heard = pack["chairman_in_chat"]
    assert [m["body"] for m in heard["messages"]] == ["Forget the stop. Only one rule now."]
    assert "do not reply" in heard["note"]
    assert "brief_propose" in heard["note"]

    # The chat lane itself is not told twice: it holds the transcript.
    chat_pack = client.get("/agent/context", headers=bearer(chat_token)).json()
    assert "chairman_in_chat" not in chat_pack
