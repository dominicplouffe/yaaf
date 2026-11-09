"""Invariant selftests: what the approvals listing tells the chairman's client.

A chairman deciding on a request needs what it would change, structured: when
it was asked (a deadline without an asked-at is decoration) and, for a request
to move a data source tool, which tool and which way — so a client can say
"allow create_campaigns" rather than parse the title. These pin the listing so
a client built on it cannot silently lose them.
"""
from __future__ import annotations

from tests import fake_mcp
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


def test_a_tool_permission_request_lists_when_asked_and_what_it_moves(client, monkeypatch):
    # The source answers, so its write tool is recorded and denied.
    fake_mcp.install(monkeypatch)
    project = make_project(client)
    _attach(client, project["id"])
    _, token = live_token(project["id"], project["ceo_agent_id"])
    approval_id = client.post("/agent/data_sources/tool_permission", headers=bearer(token), json={
        "source": SOURCE, "tool": WRITE, "permission": "allow",
        "why": "Launching the Q4 campaigns needs it; the budgets are set.",
        "if_declined": "Campaigns are created by hand."}).json()["approval_id"]

    row = _row(client, project["id"], approval_id)
    assert row["created_at"] and row["grant"] is None
    assert row["tool_permission"] == {"source": SOURCE, "tool": WRITE, "from": "deny",
                                      "to": "allow", "kind": "write"}

    decided = client.post(f"/admin/approvals/{approval_id}/decide",
                          json={"approve": True}, headers=ADMIN)
    assert decided.status_code == 200, decided.text
    row = _row(client, project["id"], approval_id)
    assert row["decided_at"] and row["status"] == "approved"


def test_an_approval_that_is_not_a_grant_says_so(client):
    """A spend or owner decision carries no grant; a client must be able to tell."""
    project = make_project(client)
    headers = _agent(client, project)
    response = client.post("/agent/approvals", headers=headers,
                           json={"kind": "spend", "title": "Buy a data pull",
                                 "body": "Two months of category data.",
                                 "recommendation": "Approve it; the case says why.", "if_declined": "We forecast from last year's data.",
                                 "amount_usd": 40})
    assert response.status_code == 200, response.text

    row = _row(client, project["id"], response.json()["id"])
    assert row["grant"] is None
    assert row["spent_at"] is None
    assert row["created_at"]
