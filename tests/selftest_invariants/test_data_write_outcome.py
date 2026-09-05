"""Invariant selftests: what a spent grant actually did.

The disclosure says what a call would do and `spent_at` says it was made. The
outcome is the only record of what came of it, so it is refused until the call
has happened, and the chairman's listing carries the newest one.
"""

from __future__ import annotations

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project

SOURCE = "ads"
WRITE = "keyword_bulk_actions"
OUTCOME = {"ok": True,
           "observed": {"label": "ad spend on the 14 terms", "value": "$0",
                        "basis": "7 days since", "as_of": "2026-09-11T06:00:00Z"},
           "note": "All 14 negatives are live."}


def _attach(client, project_id: str) -> None:
    response = client.post(
        f"/admin/projects/{project_id}/mcp_servers",
        json={"name": SOURCE, "url": "https://vendor.example.com/mcp", "bearer": "vendor-token",
              "roles": ["worker"], "access": "bulk", "tools": ["get_stats"]},
        headers=ADMIN)
    assert response.status_code == 200, response.text


def _agent(client, project: dict) -> dict:
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    return bearer(token)


def _filed(client, project: dict, arguments: dict) -> str:
    return client.post("/agent/data_writes", headers=_agent(client, project),
                       json={"source": SOURCE, "tool": WRITE, "arguments": arguments}).json()["id"]


def _spend(client, project: dict, approval_id: str, arguments: dict) -> None:
    decided = client.post(f"/admin/approvals/{approval_id}/decide",
                          json={"approve": True, "note": "go"}, headers=ADMIN)
    assert decided.status_code == 200, decided.text
    spent = client.post(f"/agent/data_writes/{approval_id}/spend", headers=_agent(client, project),
                        json={"source": SOURCE, "tool": WRITE, "arguments": arguments})
    assert spent.status_code == 200, spent.text


def _row(client, project: dict, approval_id: str) -> dict:
    listing = client.get(f"/observe/projects/{project['id']}/approvals", headers=ADMIN).json()
    return next(r for r in listing if r["id"] == approval_id)


def test_an_outcome_is_refused_before_the_call_was_made(client):
    project = make_project(client)
    _attach(client, project["id"])
    approval_id = _filed(client, project, {"a": 1})
    response = client.post(f"/agent/data_writes/{approval_id}/outcome",
                           headers=_agent(client, project), json=OUTCOME)
    assert response.status_code == 409
    assert "has not been spent" in response.text
    assert _row(client, project, approval_id)["outcome"] is None


def test_the_newest_outcome_rides_the_listing(client):
    project = make_project(client)
    _attach(client, project["id"])
    arguments = {"action": "negate"}
    approval_id = _filed(client, project, arguments)
    _spend(client, project, approval_id, arguments)

    first = client.post(f"/agent/data_writes/{approval_id}/outcome",
                        headers=_agent(client, project), json={"ok": True, "note": "landed"})
    assert first.status_code == 200, first.text
    second = client.post(f"/agent/data_writes/{approval_id}/outcome",
                         headers=_agent(client, project), json=OUTCOME)
    assert second.status_code == 200, second.text

    outcome = _row(client, project, approval_id)["outcome"]
    assert outcome["observed"] == OUTCOME["observed"]
    assert outcome["note"] == OUTCOME["note"]
    assert outcome["filed_at"]


def test_another_projects_approval_is_not_reachable(client):
    mine = make_project(client, slug="mine")
    theirs = make_project(client, slug="theirs")
    _attach(client, theirs["id"])
    approval_id = _filed(client, theirs, {"a": 1})
    response = client.post(f"/agent/data_writes/{approval_id}/outcome",
                           headers=_agent(client, mine), json=OUTCOME)
    assert response.status_code == 404


def test_the_observed_effect_is_bounded(client):
    project = make_project(client)
    _attach(client, project["id"])
    arguments = {"action": "negate"}
    approval_id = _filed(client, project, arguments)
    _spend(client, project, approval_id, arguments)
    response = client.post(f"/agent/data_writes/{approval_id}/outcome",
                           headers=_agent(client, project),
                           json={"ok": True, "observed": {"label": "x" * 121, "value": "1"}})
    assert response.status_code == 422
