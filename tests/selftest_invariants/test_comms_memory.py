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


def _fill_team_memory(client, token, n):
    """n team-scope entries, returning their ids."""
    ids = []
    for i in range(n):
        ok = client.post(
            "/agent/memory",
            json={"scope": "team", "kind": "lesson", "subject": f"lesson {i}",
                  "body": f"something durable we learned the hard way, number {i}"},
            headers=bearer(token),
        )
        assert ok.status_code == 200, ok.json()
        ids.append(ok.json()["id"])
    return ids


def test_compaction_frees_slots_at_the_cap(client):
    """The deadlock regression: compaction has to work *at* the cap, which is
    the only moment anyone needs it. Gating it on the cap would make the one
    verb that creates room refuse for lack of room."""
    project = make_project(client, constitution={"memory_cap_team": 3})
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))

    ids = _fill_team_memory(client, lead_token, 3)
    assert client.post(
        "/agent/memory",
        json={"scope": "team", "kind": "lesson", "subject": "one too many",
              "body": "the cap should refuse this one"},
        headers=bearer(lead_token),
    ).status_code == 409

    folded = client.post(
        "/agent/memory/compact",
        json={"replaces": ids, "kind": "lesson", "subject": "what we learned the hard way",
              "body": "three overlapping lessons, folded into the one that matters"},
        headers=bearer(lead_token),
    )
    assert folded.status_code == 200, folded.json()
    assert folded.json()["freed"] == 2
    assert folded.json()["live"] == 1
    assert folded.json()["cap"] == 3

    # Room again, in the same run that hit the wall.
    assert client.post(
        "/agent/memory",
        json={"scope": "team", "kind": "lesson", "subject": "now there is room",
              "body": "the write that was refused a moment ago"},
        headers=bearer(lead_token),
    ).status_code == 200


def test_compaction_is_reversible(client):
    """Folding is only safe because nothing is destroyed — the tool tells the
    model exactly that, so the read path has to back the claim up."""
    project = make_project(client, constitution={"memory_cap_team": 5})
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    ids = _fill_team_memory(client, lead_token, 3)

    new_id = client.post(
        "/agent/memory/compact",
        json={"replaces": ids, "kind": "lesson", "subject": "folded",
              "body": "the three lessons above, said once"},
        headers=bearer(lead_token),
    ).json()["id"]

    live = client.get("/agent/memory/search?q=lesson", headers=bearer(lead_token)).json()
    assert [m["id"] for m in live] == [new_id]

    archived = client.get(
        "/agent/memory/search?q=lesson&include_superseded=true", headers=bearer(lead_token)
    ).json()
    by_id = {m["id"]: m for m in archived}
    for old in ids:
        assert old in by_id, "a folded memory must still be findable"
        assert by_id[old]["superseded_by"] == new_id


def test_only_the_curator_compacts(client):
    project = make_project(client, constitution={"memory_cap_team": 5})
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    _, emp_token = live_token(project["id"], agent_by_name(project, "Emp2"))
    ids = _fill_team_memory(client, lead_token, 2)

    denied = client.post(
        "/agent/memory/compact",
        json={"replaces": ids, "kind": "lesson", "subject": "employee tidying up",
              "body": "an employee folding its team's memory"},
        headers=bearer(emp_token),
    )
    assert denied.status_code == 403


def test_compact_refuses_degenerate_and_cross_scope_folds(client):
    project = make_project(client, constitution={"memory_cap_team": 5})
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    team_ids = _fill_team_memory(client, lead_token, 2)

    # One entry (even spelled twice) is a supersede, not a compaction.
    single = client.post(
        "/agent/memory/compact",
        json={"replaces": [team_ids[0], team_ids[0]], "kind": "lesson",
              "subject": "folding one thing", "body": "this is just a supersede"},
        headers=bearer(lead_token),
    )
    assert single.status_code == 422
    assert "memory_supersede" in single.json()["error"]

    project_id = client.post(
        "/agent/memory",
        json={"scope": "project", "kind": "fact", "subject": "a project fact",
              "body": "curated by the CEO, capped separately"},
        headers=bearer(ceo_token),
    ).json()["id"]
    mixed = client.post(
        "/agent/memory/compact",
        json={"replaces": [team_ids[0], project_id], "kind": "fact",
              "subject": "across scopes", "body": "folding a team fact into a project one"},
        headers=bearer(ceo_token),
    )
    assert mixed.status_code == 422
    assert "scope" in mixed.json()["error"]

    # An id from another project is "unknown", never "not yours" — the refusal
    # must not confirm that a row exists somewhere the agent cannot see.
    other = make_project(client, slug="other-co", constitution={"memory_cap_team": 5})
    _, other_lead = live_token(other["id"], agent_by_name(other, "Lead"))
    stranger = _fill_team_memory(client, other_lead, 2)
    leaked = client.post(
        "/agent/memory/compact",
        json={"replaces": stranger, "kind": "lesson", "subject": "someone else's memory",
              "body": "folding another project's entries"},
        headers=bearer(lead_token),
    )
    assert leaked.status_code == 404
    assert "Unknown memory" in leaked.json()["error"]


def test_cap_refusal_is_addressed_to_whoever_can_act(client):
    """The message that started all this. An employee was told to 'supersede or
    forget' — a verb that frees nothing, and a call it would be refused anyway."""
    project = make_project(client, constitution={"memory_cap_team": 2})
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    _, emp_token = live_token(project["id"], agent_by_name(project, "Emp2"))
    ids = _fill_team_memory(client, lead_token, 2)

    lead_wall = client.post(
        "/agent/memory",
        json={"scope": "team", "kind": "lesson", "subject": "one too many",
              "body": "the lead hits its own cap"},
        headers=bearer(lead_token),
    ).json()["error"]
    assert "Memory cap" in lead_wall  # the operator-greppable prefix
    assert "memory_compact" in lead_wall
    assert any(mid in lead_wall for mid in ids), "the curator gets candidates to act on"

    emp_wall = client.post(
        "/agent/memory",
        json={"scope": "team", "kind": "lesson", "subject": "one too many",
              "body": "an employee hits a cap only its lead can clear"},
        headers=bearer(emp_token),
    ).json()["error"]
    assert "Memory cap" in emp_wall
    assert "Lead" in emp_wall, "name the agent that can actually free a slot"
    assert "do not retry" in emp_wall
    assert "memory_supersede" not in emp_wall, "never offer a verb that frees no slot"


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


def test_a_solo_team_room_refuses_posts_because_nobody_reads_it(client):
    """A one-person team's room has exactly one member, and an agent never
    reads its own messages back — so posting there reaches nobody at all.

    This is not pedantry. A sole team member reported a stuck task into her own
    team room five times over 22 hours; every word went nowhere, while the same
    agent had used leadership successfully the day before and been answered in
    minutes. Silence was indistinguishable from having escalated."""
    project = make_project(client, teams=[
        {"role_slug": "worker", "name": "Solo", "employees": ["Onlyone"], "lead": "Onlyone"},
        {"role_slug": "worker", "name": "Pair", "employees": ["Ann", "Bo"], "lead": "Ann"},
    ])
    solo_team, pair_team = project["teams"][0]["id"], project["teams"][1]["id"]
    solo_room = channel_of(client, project["id"], "team", solo_team)
    pair_room = channel_of(client, project["id"], "team", pair_team)

    _, solo_token = live_token(project["id"], agent_by_name(project, "Onlyone"))
    refused = client.post(
        "/agent/messages",
        json={"channel_id": solo_room, "body": "my task is stuck and I need help"},
        headers=bearer(solo_token),
    )
    assert refused.status_code == 409, refused.text
    assert "no other members" in refused.json()["error"]
    assert "leadership" in refused.json()["error"], "the refusal must name the way out"

    # A room with someone in it is untouched: this guards the dead letter, not
    # team rooms in general.
    _, ann_token = live_token(project["id"], agent_by_name(project, "Ann"))
    assert client.post(
        "/agent/messages",
        json={"channel_id": pair_room, "body": "status: the overlay is rebuilt"},
        headers=bearer(ann_token),
    ).status_code == 200

    # And the solo member can still reach leadership, which is the point.
    leadership = channel_of(client, project["id"], "leadership")
    assert client.post(
        "/agent/messages",
        json={"channel_id": leadership, "body": "my task is stuck and I need help"},
        headers=bearer(solo_token),
    ).status_code == 200
