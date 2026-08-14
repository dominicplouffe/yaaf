"""Invariant selftests: the hierarchy is the message bus; memory is capped and
curated; the circuit breaker freezes ping-pong threads."""

from __future__ import annotations

from tests.conftest import (
    ADMIN,
    agent_by_name,
    bearer,
    channel_of,
    live_token,
    make_project,
)


def test_channel_membership_is_the_hierarchy(client):
    project = make_project(client)
    team_id = project["teams"][0]["id"]
    boardroom = channel_of(client, project["id"], "boardroom")
    leadership = channel_of(client, project["id"], "leadership")
    team_room = channel_of(client, project["id"], "team", team_id)

    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    _, emp_token = live_token(project["id"], agent_by_name(project, "Emp2"))

    # CEO: boardroom + leadership, never team rooms.
    assert client.post("/agent/messages", json={"channel_id": boardroom, "body": "hi board"},
                       headers=bearer(ceo_token)).status_code == 200
    assert client.post("/agent/messages", json={"channel_id": team_room, "body": "micromgmt"},
                       headers=bearer(ceo_token)).status_code == 403

    # Lead: leadership + own team room, not the boardroom.
    assert client.post("/agent/messages", json={"channel_id": leadership, "body": "report"},
                       headers=bearer(lead_token)).status_code == 200
    assert client.post("/agent/messages", json={"channel_id": boardroom, "body": "hi chair"},
                       headers=bearer(lead_token)).status_code == 403

    # Employee: team room only.
    assert client.post("/agent/messages", json={"channel_id": team_room, "body": "on it"},
                       headers=bearer(emp_token)).status_code == 200
    assert client.post("/agent/messages", json={"channel_id": leadership, "body": "skip level"},
                       headers=bearer(emp_token)).status_code == 403


def test_circuit_breaker_freezes_ping_pong(client):
    """N messages between the same two agents with no task transition -> the
    dispatcher freezes the thread instead of waking anyone again."""
    from yaaf.core.db import db_session
    from yaaf.server.dispatcher import dispatch_once

    project = make_project(client, constitution={"pair_breaker_messages": 4})
    team_id = project["teams"][0]["id"]
    team_room = channel_of(client, project["id"], "team", team_id)
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    _, emp_token = live_token(project["id"], agent_by_name(project, "Emp2"))

    root = client.post("/agent/messages", json={"channel_id": team_room, "body": "ping 0"},
                       headers=bearer(lead_token)).json()
    tokens = [emp_token, lead_token]
    for i in range(1, 4):
        response = client.post(
            "/agent/messages",
            json={"channel_id": team_room, "body": f"ping {i}", "thread_id": root["id"]},
            headers=bearer(tokens[i % 2]),
        )
        assert response.status_code == 200

    from tests.conftest import finish_runs

    finish_runs(project["id"])  # busy agents are never re-woken; end their runs
    with db_session() as session:
        dispatch_once(session)

    # The thread is frozen; further posts are refused (fresh run, fresh token —
    # the freeze outlives the runs that caused it).
    _, emp_token = live_token(project["id"], agent_by_name(project, "Emp2"))
    frozen = client.post(
        "/agent/messages",
        json={"channel_id": team_room, "body": "one more ping", "thread_id": root["id"]},
        headers=bearer(emp_token),
    )
    assert frozen.status_code == 409
    assert "circuit breaker" in frozen.json()["error"]

    # The chairman can unfreeze it.
    client.post(f"/admin/threads/{root['id']}/unfreeze", headers=ADMIN)
    thawed = client.post(
        "/agent/messages",
        json={"channel_id": team_room, "body": "we moved a task, honest", "thread_id": root["id"]},
        headers=bearer(emp_token),
    )
    assert thawed.status_code == 200


def test_memory_caps_and_curation(client):
    project = make_project(client, constitution={"memory_cap_team": 2,
                                                 "memory_cap_project": 2})
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    _, emp_token = live_token(project["id"], agent_by_name(project, "Emp2"))

    # Employees cannot write project-scope memory.
    denied = client.post(
        "/agent/memory",
        json={"scope": "project", "kind": "fact", "subject": "sneaky",
              "body": "an employee writing project memory"},
        headers=bearer(emp_token),
    )
    assert denied.status_code == 403

    # Team memory caps at 2, then refuses.
    ids = []
    for i in range(2):
        ok = client.post(
            "/agent/memory",
            json={"scope": "team", "kind": "lesson", "subject": f"lesson {i}",
                  "body": "something durable we learned the hard way"},
            headers=bearer(emp_token),
        )
        assert ok.status_code == 200
        ids.append(ok.json()["id"])
    full = client.post(
        "/agent/memory",
        json={"scope": "team", "kind": "lesson", "subject": "one too many",
              "body": "the cap should refuse this one"},
        headers=bearer(emp_token),
    )
    assert full.status_code == 409
    assert "Memory cap" in full.json()["error"]

    # Only the curator (the lead, for team scope) supersedes; the employee can't.
    not_curator = client.post(
        f"/agent/memory/{ids[0]}/supersede",
        json={"kind": "lesson", "subject": "rewritten", "body": "employee rewriting history"},
        headers=bearer(emp_token),
    )
    assert not_curator.status_code == 403
    curator = client.post(
        f"/agent/memory/{ids[0]}/supersede",
        json={"kind": "lesson", "subject": "rewritten", "body": "the lead curates the store"},
        headers=bearer(lead_token),
    )
    assert curator.status_code == 200

    # CEO owns project scope.
    assert client.post(
        "/agent/memory",
        json={"scope": "project", "kind": "fact", "subject": "durable fact",
              "body": "true next month as well"},
        headers=bearer(ceo_token),
    ).status_code == 200


def test_spend_needs_approval_above_threshold(client):
    project = make_project(client, constitution={"approval_threshold_usd": 100})
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])

    small = client.post("/agent/ledger/spend",
                        json={"usd": 20, "note": "a small tool subscription"},
                        headers=bearer(ceo_token))
    assert small.status_code == 200

    big = client.post("/agent/ledger/spend",
                      json={"usd": 250, "note": "a big ad buy with no approval"},
                      headers=bearer(ceo_token))
    assert big.status_code == 403

    request = client.post(
        "/agent/approvals",
        json={"kind": "spend", "title": "Ad buy on ExampleAds",
              "body": "I recommend spending $250 on X because Y; say no and I will work "
                      "around it.", "amount_usd": 250},
        headers=bearer(ceo_token),
    ).json()
    client.post(f"/admin/approvals/{request['id']}/decide",
                json={"approve": True, "note": "go"}, headers=ADMIN)
    approved = client.post(
        "/agent/ledger/spend",
        json={"usd": 250, "note": "the approved ad buy", "approval_id": request["id"]},
        headers=bearer(ceo_token),
    )
    assert approved.status_code == 200


def test_side_effect_idempotency(client):
    project = make_project(client)
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))

    first = client.post(
        "/agent/side_effects",
        json={"kind": "email", "idempotency_key": "outreach:alice@example.com:v1",
              "payload": {"to": "alice@example.com"}},
        headers=bearer(lead_token),
    ).json()
    assert first["duplicate"] is False
    client.post(f"/agent/side_effects/{first['id']}/finish",
                json={"ok": True, "result": {"message_id": "m-1"}},
                headers=bearer(lead_token))

    # A retry (same key, e.g. after a reaped run) sees the completed record.
    again = client.post(
        "/agent/side_effects",
        json={"kind": "email", "idempotency_key": "outreach:alice@example.com:v1",
              "payload": {"to": "alice@example.com"}},
        headers=bearer(lead_token),
    ).json()
    assert again["duplicate"] is True
    assert again["status"] == "completed"
    assert "DO NOT" in again["note"]
