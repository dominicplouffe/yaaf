"""Invariant selftests: solo mode (the M2 single-agent arm) — one CEO-kind
agent that is also the sole member and derived lead of its only team, under
the same policy engine as everyone else."""

from __future__ import annotations

from tests.conftest import ADMIN, bearer, live_token, seed_role

OBS_TEST = {"type": "observable", "target": "https://example.org/thing exists and loads"}


def _make_solo(client):
    client.post("/admin/users",
                json={"email": "chair@example.com", "display_name": "Chair"}, headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")
    body = {
        "slug": "solo-arm", "name": "Solo Arm", "goal": "One brain, same rules.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "ceo", "ceo_name": "CEO",
        "constitution": {}, "solo": True,
        "teams": [{"role_slug": "worker", "name": "Solo", "employees": []}],
    }
    response = client.post("/admin/projects", json=body, headers=ADMIN)
    assert response.status_code == 200, response.text
    project = response.json()
    assert client.post(f"/admin/projects/{project['id']}/start",
                       headers=ADMIN).status_code == 200
    return project


def test_solo_ceo_plans_and_executes_under_the_same_rules(client):
    project = _make_solo(client)
    team_id = project["teams"][0]["id"]
    # The CEO is listed as the team's (only) member.
    assert [a["name"] for a in project["teams"][0]["agents"]] == ["CEO"]
    _, token = live_token(project["id"], project["ceo_agent_id"])

    # Plans: creates the task (CEO power)...
    task = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "do the thing", "goal": "and prove it",
              "success_test": OBS_TEST},
        headers=bearer(token),
    )
    assert task.status_code == 200, task.text
    task = task.json()
    # ...and executes: claims as derived lead, completes with evidence.
    assert client.post(f"/agent/tasks/{task['id']}/claim",
                       headers=bearer(token)).status_code == 200
    done = client.post(
        f"/agent/tasks/{task['id']}/complete",
        json={"evidence": "https://example.org/thing loads; screenshot in workspace"},
        headers=bearer(token),
    )
    assert done.status_code == 200, done.text

    # Same governance: completing without evidence would have been refused —
    # prove the refusal surface is identical by trying a second task bare.
    task2 = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "cut corners", "goal": "skip evidence",
              "success_test": OBS_TEST},
        headers=bearer(token),
    ).json()
    assert client.post(f"/agent/tasks/{task2['id']}/claim",
                       headers=bearer(token)).status_code == 200
    refused = client.post(f"/agent/tasks/{task2['id']}/complete",
                          json={"evidence": "done"}, headers=bearer(token))
    assert refused.status_code in (400, 422)


def test_solo_requires_exactly_one_empty_team(client):
    client.post("/admin/users",
                json={"email": "chair@example.com", "display_name": "Chair"}, headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")
    body = {
        "slug": "bad-solo", "name": "Bad", "goal": "Solo with employees is not solo.",
        "chairman_email": "chair@example.com", "solo": True,
        "teams": [{"role_slug": "worker", "name": "Solo", "employees": ["Emp"]}],
    }
    assert client.post("/admin/projects", json=body, headers=ADMIN).status_code == 422
