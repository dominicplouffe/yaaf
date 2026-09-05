"""Invariant selftests: what the approvals listing tells the chairman's client.

A chairman deciding on a data write needs three things the listing did not carry:
when it was asked (a deadline without an asked-at is decoration), what call it
grants (the tool, so a client can say "negative keywords" rather than parse the
title), and when the granted call was made (an approval is a change only once it
is spent). These pin the listing so a client built on it cannot silently lose them.
"""

from __future__ import annotations

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project

SOURCE = "ads"
WRITE = "create_campaigns"


def _attach(client, project_id: str) -> None:
    response = client.post(
        f"/admin/projects/{project_id}/mcp_servers",
        json={"name": SOURCE, "url": "https://vendor.example.com/mcp",
              "bearer": "vendor-token", "roles": ["worker"], "access": "bulk",
              "tools": ["get_stats"]},
        headers=ADMIN)
    assert response.status_code == 200, response.text


def _agent(client, project: dict) -> dict:
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    return bearer(token)


def _row(client, project_id: str, approval_id: str) -> dict:
    listing = client.get(f"/observe/projects/{project_id}/approvals", headers=ADMIN)
    assert listing.status_code == 200, listing.text
    return next(row for row in listing.json() if row["id"] == approval_id)


def test_a_data_write_lists_when_asked_what_it_grants_and_when_spent(client):
    project = make_project(client)
    _attach(client, project["id"])
    headers = _agent(client, project)
    arguments = {"campaigns": [{"name": "Q4", "budget": 50}]}
    call = {"source": SOURCE, "tool": WRITE, "arguments": arguments}

    approval_id = client.post("/agent/data_writes", headers=headers,
                              json={**call, "why": "Launch Q4."}).json()["id"]

    row = _row(client, project["id"], approval_id)
    assert row["created_at"]
    assert row["spent_at"] is None
    assert row["grant"]["source"] == SOURCE
    assert row["grant"]["tool"] == WRITE
    assert row["grant"]["arguments_sha256"]

    decided = client.post(f"/admin/approvals/{approval_id}/decide",
                          json={"approve": True, "note": "go"}, headers=ADMIN)
    assert decided.status_code == 200, decided.text
    spent = client.post(f"/agent/data_writes/{approval_id}/spend", json=call, headers=headers)
    assert spent.status_code == 200, spent.text

    row = _row(client, project["id"], approval_id)
    assert row["spent_at"]
    assert row["decided_at"]
    assert row["spent_at"] >= row["created_at"]


def test_an_approval_that_is_not_a_grant_says_so(client):
    """A spend or owner decision carries no grant; a client must be able to tell."""
    project = make_project(client)
    headers = _agent(client, project)
    response = client.post("/agent/approvals", headers=headers,
                           json={"kind": "spend", "title": "Buy a data pull",
                                 "body": "Two months of category data.", "amount_usd": 40})
    assert response.status_code == 200, response.text

    row = _row(client, project["id"], response.json()["id"])
    assert row["grant"] is None
    assert row["spent_at"] is None
    assert row["created_at"]
