"""Invariant selftests: decide, acknowledge, or neither — and his note is answered.

What reached the chairman from the CEO was all one shape: Approve, Deny, a text
box. On most kinds the click changed nothing, and the note went into the CEO's
wake notice once and was gone. A trading desk sent seventy-seven "assists",
many reading "No trade today. Place nothing.", each with an Approve and a Deny;
and a note on a credential request — "yes, please go ahead, every day" — was a
standing order that no prompt ever carried again.

So: a heads-up is what he asked to be told, and it must name the standing order
that asked; it gets Noted, not Approve. And every note he writes is held until
the CEO answers it, and one the CEO calls standing must be in a brief first.
"""

from __future__ import annotations

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import ChairmanNote, HeadsUp, Message, Project

ORDER = "Send me tomorrow's ticket every trading evening, even when it is no trade"


def ceo(project: dict) -> str:
    return live_token(project["id"], project["ceo_agent_id"])[1]


def with_order(project: dict) -> None:
    with db_session() as session:
        session.get(Project, project["id"]).standing_orders = [ORDER]


def heads_up(client, project: dict, order: str = ORDER):
    return client.post("/agent/heads_up", json={
        "title": "No trade tomorrow",
        "body": "Nothing to place on Friday. The book is unchanged at $95,806.",
        "standing_order": order}, headers=bearer(ceo(project)))


def pack(client, project: dict) -> dict:
    return client.get("/agent/context", headers=bearer(ceo(project))).json()


# ── a heads-up answers a standing order, or it is information ───────────────────


def test_a_heads_up_without_a_standing_order_is_information(client):
    project = make_project(client)
    response = heads_up(client, project)
    assert response.status_code == 422
    assert "information" in response.text


def test_a_heads_up_must_quote_an_order_in_the_brief(client):
    project = make_project(client)
    with_order(project)
    response = heads_up(client, project, order="Tell me the weather")
    assert response.status_code == 422
    assert ORDER in response.text, "the refusal should list the orders it could cite"


def test_a_heads_up_lands_on_his_desk_to_note(client):
    project = make_project(client)
    with_order(project)
    # Quoting the part that says "tell me" is enough.
    response = heads_up(client, project, order="tomorrow's ticket every trading evening")
    assert response.status_code == 200, response.text
    assert response.json()["standing_order"] == ORDER

    listed = client.get(f"/observe/projects/{project['id']}/heads_ups", headers=ADMIN).json()
    assert [h["title"] for h in listed] == ["No trade tomorrow"]
    assert listed[0]["acknowledged_at"] is None
    with db_session() as session:
        posted = [m.body for m in session.query(Message).filter_by(project_id=project["id"])]
    assert any("No trade tomorrow" in b and ORDER in b for b in posted)


def test_only_the_ceo_sends_a_heads_up(client):
    project = make_project(client)
    with_order(project)
    _, token = live_token(project["id"], agent_by_name(project, "Emp2"))
    response = client.post("/agent/heads_up", json={
        "title": "x" * 5, "body": "y" * 12, "standing_order": ORDER}, headers=bearer(token))
    assert response.status_code == 403


def test_noting_without_a_word_asks_nothing_of_the_ceo(client):
    project = make_project(client)
    with_order(project)
    heads_id = heads_up(client, project).json()["heads_up_id"]
    noted = client.post(f"/admin/heads_up/{heads_id}/ack", json={}, headers=ADMIN)
    assert noted.status_code == 200, noted.text
    with db_session() as session:
        assert session.get(HeadsUp, heads_id).acknowledged_at is not None
        assert session.query(ChairmanNote).count() == 0
    assert "chairman_notes" not in pack(client, project)
    assert client.post(f"/admin/heads_up/{heads_id}/ack", json={},
                       headers=ADMIN).status_code == 409


# ── his note is held until the CEO answers it ───────────────────────────────────


def test_a_note_on_a_decision_stays_in_the_pack_until_answered(client):
    project = make_project(client)
    with_order(project)
    heads_id = heads_up(client, project).json()["heads_up_id"]
    client.post(f"/admin/heads_up/{heads_id}/ack",
                json={"note": "Send it by 8pm, not 10pm."}, headers=ADMIN)

    # Every run, not once.
    for _ in range(2):
        held = pack(client, project)["chairman_notes"]["notes"]
        assert [n["he_wrote"] for n in held] == ["Send it by 8pm, not 10pm."]
    note_id = held[0]["id"]

    answered = client.post(f"/agent/notes/{note_id}/answer",
                           json={"reply": "Moved tonight's send to 8pm.", "standing": False},
                           headers=bearer(ceo(project)))
    assert answered.status_code == 200, answered.text
    assert "chairman_notes" not in pack(client, project)
    listed = client.get(f"/observe/projects/{project['id']}/heads_ups", headers=ADMIN).json()
    assert listed[0]["note"]["reply"] == "Moved tonight's send to 8pm."


def test_a_note_on_an_approval_is_held_too(client):
    project = make_project(client)
    filed = client.post("/agent/approvals", json={
        "kind": "spend", "title": "Buy the $199 export tier", "body": "Export is paywalled.",
        "if_declined": "We cannot export the plan.", "amount_usd": 199},
        headers=bearer(ceo(project)))
    assert filed.status_code == 200, filed.text
    approval_id = filed.json()["id"]
    client.post(f"/admin/approvals/{approval_id}/decide",
                json={"approve": True, "note": "yes, and go ahead every month"},
                headers=ADMIN)
    held = pack(client, project)["chairman_notes"]["notes"]
    assert held[0]["on"].startswith("Approved: Buy the $199")
    rows = client.get(f"/observe/projects/{project['id']}/approvals", headers=ADMIN).json()
    assert rows[0]["held_note"]["note"] == "yes, and go ahead every month"


def test_a_standing_note_must_be_in_a_brief_first(client):
    project = make_project(client)
    with_order(project)
    heads_id = heads_up(client, project).json()["heads_up_id"]
    client.post(f"/admin/heads_up/{heads_id}/ack",
                json={"note": "From now on, always by 8pm."}, headers=ADMIN)
    note_id = pack(client, project)["chairman_notes"]["notes"][0]["id"]

    refused = client.post(f"/agent/notes/{note_id}/answer",
                          json={"reply": "Saved to memory.", "standing": True},
                          headers=bearer(ceo(project)))
    assert refused.status_code == 422
    assert "brief_propose" in refused.text

    drafted = client.post("/agent/brief", json={
        "goal": "Run the desk and send the chairman tomorrow's ticket each evening.",
        "standing_orders": [ORDER + ", by 8pm"],
        "what_changed": "Oct 22, on a heads-up: 'From now on, always by 8pm.'"},
        headers=bearer(ceo(project)))
    assert drafted.status_code == 200, drafted.text
    answered = client.post(f"/agent/notes/{note_id}/answer",
                           json={"reply": "Put in the brief for your confirm.",
                                 "standing": True},
                           headers=bearer(ceo(project)))
    assert answered.status_code == 200, answered.text
