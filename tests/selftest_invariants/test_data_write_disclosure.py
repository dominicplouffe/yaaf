"""Invariant selftests: a data write's disclosure — what the chairman decides on.

A request filed as a title and a paragraph is a paragraph with buttons. The
disclosure is the structure that makes a confident yes and a confident no
possible: the change set before → after, the rows it rests on, the estimated
effect, whether it can be undone, and the case against. These pin three things:
it round-trips to the chairman's listing whole; it is bounded, so an agent cannot
store a data dump on an approval row; and it is never part of the grant — the
digest binds the arguments, not the argument for them.
"""

from __future__ import annotations

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Approval

SOURCE = "ads"
WRITE = "keyword_bulk_actions"

DISCLOSURE = {
    "category": "negative_keywords",
    "change_set": [
        {"entity": "Ridgeline — Broad", "field": "negative_keywords", "before": 0, "after": 14},
    ],
    "scope_counts": {"campaigns": 3, "keywords": 14},
    "evidence": {
        "columns": ["search term", "spend", "clicks", "orders"],
        "rows": [["insulated dog jacket xl", 212.0, 148, 0], ["cheap camping tent 2 person", 147.0, 206, 0]],
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "currency": "USD",
        "as_of": "2026-09-04T06:45:00Z",
    },
    "estimate": {"label": "ad spend freed", "value": "$1,240 / mo", "basis": "last 30 days, zero orders"},
    "reversible": True,
    "counter_case": "Two of the terms had a sale 47 days ago, outside this window.",
    "spend_affected_usd_month": 1240.0,
}


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


def _file(client, project: dict, arguments: dict, disclosure: dict | None):
    body = {"source": SOURCE, "tool": WRITE, "arguments": arguments, "why": "Stop the waste."}
    if disclosure is not None:
        body["disclosure"] = disclosure
    return client.post("/agent/data_writes", headers=_agent(client, project), json=body)


def test_a_disclosure_round_trips_to_the_chairmans_listing_whole(client):
    project = make_project(client)
    _attach(client, project["id"])

    response = _file(client, project, {"action": "negate", "keywords": ["insulated dog jacket xl"]}, DISCLOSURE)
    assert response.status_code == 200, response.text

    listing = client.get(f"/observe/projects/{project['id']}/approvals", headers=ADMIN).json()
    row = next(r for r in listing if r["id"] == response.json()["id"])
    assert row["disclosure"] == DISCLOSURE


def test_a_request_without_a_disclosure_lists_none(client):
    project = make_project(client)
    _attach(client, project["id"])
    response = _file(client, project, {"action": "negate"}, None)
    assert response.status_code == 200, response.text

    listing = client.get(f"/observe/projects/{project['id']}/approvals", headers=ADMIN).json()
    assert next(r for r in listing if r["id"] == response.json()["id"])["disclosure"] is None


def test_the_evidence_is_bounded(client):
    """An approval row is rendered to a person; it is not a place to park a data pull."""
    project = make_project(client)
    _attach(client, project["id"])
    oversized = {**DISCLOSURE, "evidence": {**DISCLOSURE["evidence"], "rows": [["x", 1]] * 201}}
    assert _file(client, project, {"action": "negate"}, oversized).status_code == 422
    too_many_changes = {**DISCLOSURE, "change_set": [DISCLOSURE["change_set"][0]] * 501}
    assert _file(client, project, {"action": "negate"}, too_many_changes).status_code == 422


def test_the_digest_binds_the_arguments_not_the_disclosure(client):
    """Two requests for the same call with different prose carry the same grant; the
    disclosure is for the chairman, never something an approval is spent on."""
    project = make_project(client)
    _attach(client, project["id"])
    arguments = {"action": "negate", "keywords": ["a"]}

    with_it = _file(client, project, arguments, DISCLOSURE).json()["id"]
    without = _file(client, project, arguments, None).json()["id"]
    with db_session() as session:
        grants = [session.get(Approval, i).grant for i in (with_it, without)]
    assert grants[0] == grants[1]

    decided = client.post(f"/admin/approvals/{with_it}/decide", json={"approve": True, "note": "go"}, headers=ADMIN)
    assert decided.status_code == 200, decided.text
    spent = client.post(f"/agent/data_writes/{with_it}/spend", headers=_agent(client, project),
                        json={"source": SOURCE, "tool": WRITE, "arguments": arguments})
    assert spent.status_code == 200, spent.text
