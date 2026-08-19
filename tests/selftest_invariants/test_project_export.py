"""Invariant selftests: the project setup export is portable and secret-free.

A setup file must recreate the project's design on any install — and must
never carry a secret value, a webhook signing key, or anything bound to this
deployment (Slack wiring, data-source check results)."""

from __future__ import annotations

import json

from tests.conftest import ADMIN, amend, make_project


def _export(client, project_id: str) -> dict:
    response = client.get(f"/admin/projects/{project_id}/export", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def test_bundle_carries_design_not_plumbing(client):
    project = make_project(client, constitution={"queue_ceiling": 5}, start=False)

    # A service credential (its value must not travel), a billing credential
    # (must not appear at all), webhook signing keys and Slack wiring in
    # config (install plumbing — must be stripped).
    client.post("/admin/secrets", json={
        "project_id": project["id"], "name": "wordpress",
        "value": "wp-secret-value", "role_scopes": ["worker"], "kind": "service",
    }, headers=ADMIN)
    client.post("/admin/secrets", json={
        "project_id": project["id"], "name": "anthropic-api-key",
        "value": "sk-ant-api-not-real", "role_scopes": [], "kind": "billing",
    }, headers=ADMIN)
    client.post(f"/admin/projects/{project['id']}/webhook_secrets", headers=ADMIN)
    client.patch(f"/admin/projects/{project['id']}", json={
        "integrations": {"slack": {"chairman_slack_user": "U123"},
                         "runtime": "mock"},
    }, headers=ADMIN)
    # The chairman's timezone is a constitution rule, not wiring — it travels
    # in the constitution half of the bundle.
    amend(client, project["id"], timezone="America/Toronto")

    bundle = _export(client, project["id"])
    spec = bundle["project"]

    assert bundle["yaaf_bundle"] == 1
    assert spec["teams"] == [{"role_slug": "worker", "name": "Alpha",
                              "employees": ["Lead", "Emp2"], "lead": "Lead"}]
    assert spec["constitution"]["queue_ceiling"] == 5
    assert spec["ceo_role_slug"] == "ceo"
    assert spec["solo"] is False
    # Portable wiring survives; this install's plumbing does not.
    assert spec["integrations"]["runtime"] == "mock"
    for key in ("slack", "github_webhook_secret", "webhook_secret"):
        assert key not in spec["integrations"]
    # And a rule travels as a rule, so an imported twin runs under the same
    # limits rather than silently reverting to the defaults.
    assert spec["constitution"]["timezone"] == "America/Toronto"
    # Role definitions travel with their prompts; credential names travel bare.
    assert {r["slug"] for r in bundle["roles"]} == {"ceo", "worker"}
    assert all(r["system_prompt"] for r in bundle["roles"])
    assert bundle["secrets"] == [{"name": "wordpress", "role_scopes": ["worker"],
                                  "kind": "service"}]
    # No secret value anywhere in the file, under any key.
    dumped = json.dumps(bundle)
    assert "wp-secret-value" not in dumped
    assert "sk-ant-api-not-real" not in dumped


def test_data_sources_travel_without_check_results(client):
    project = make_project(client, start=False)
    added = client.post(f"/admin/projects/{project['id']}/mcp_servers", json={
        "name": "kb", "url": "http://127.0.0.1:9/mcp", "roles": ["worker"],
        "bearer": "kb-token-value",
    }, headers=ADMIN)
    assert added.status_code == 200, added.text

    bundle = _export(client, project["id"])
    servers = bundle["project"]["integrations"]["mcp_servers"]
    assert [s["name"] for s in servers] == ["kb"]
    assert "last_check" not in servers[0]
    # The data source's secrets ride along by name so an import can ask for
    # their values again.
    assert {"mcp-kb-url", "mcp-kb-token"} <= {s["name"] for s in bundle["secrets"]}
    assert "kb-token-value" not in json.dumps(bundle)


def test_bundle_recreates_the_project(client):
    original = make_project(client, constitution={"drought_hours": 12}, start=False)
    bundle = _export(client, original["id"])

    # The `project` section is a ready create body: add a slug and a chairman
    # and it must go straight through POST /admin/projects.
    created = client.post("/admin/projects", json={
        **bundle["project"], "slug": "acme-twin", "chairman_email": "chair@example.com",
    }, headers=ADMIN)
    assert created.status_code == 200, created.text

    twin = _export(client, created.json()["id"])
    # Same design, new identity: everything except the slug matches.
    assert {**bundle["project"], "slug": None} == {**twin["project"], "slug": None}


def test_solo_bundle_round_trips(client):
    original = make_project(
        client, slug="solo-src", solo=True,
        teams=[{"role_slug": "worker", "name": "Solo", "employees": []}],
        start=False,
    )
    bundle = _export(client, original["id"])
    assert bundle["project"]["solo"] is True
    assert bundle["project"]["teams"] == [{"role_slug": "worker", "name": "Solo",
                                           "employees": [], "lead": None}]

    created = client.post("/admin/projects", json={
        **bundle["project"], "slug": "solo-twin", "chairman_email": "chair@example.com",
    }, headers=ADMIN)
    assert created.status_code == 200, created.text
