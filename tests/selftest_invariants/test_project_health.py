"""Invariant selftests: the project health checklist — every silent
misconfiguration class gets a named, plain-language finding."""

from __future__ import annotations

from tests.conftest import ADMIN, make_project, seed_role


def _health(client, project_id) -> dict:
    response = client.get(f"/observe/projects/{project_id}/health", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def _by_key(health: dict, key: str) -> dict | None:
    return next((c for c in health["checks"] if c["key"] == key), None)


def _attach_billing(client, project_id) -> None:
    secret = client.post('/admin/settings/llm-credentials', json={'name': 'anthropic-api-key', 'value': 'sk-ant-api03-fake', 'mode': 'api_key'}, headers=ADMIN).json()
    assert client.patch(f"/admin/projects/{project_id}",
                        json={"billing_mode": "api_key",
                              "billing_secret_id": secret["id"]},
                        headers=ADMIN).status_code == 200


def test_billing_check_goes_green_when_attached(client):
    project = make_project(client, start=False)
    health = _health(client, project["id"])
    billing = _by_key(health, "billing")
    assert billing["status"] == "fail" and "no billing credential" in billing["detail"]
    assert health["ok"] is False

    _attach_billing(client, project["id"])
    health = _health(client, project["id"])
    assert _by_key(health, "billing")["status"] == "ok"
    assert health["all_ok"] is True  # nothing else configured => nothing else checked


def test_roles_cannot_declare_their_own_data_sources(client):
    """Data sources are project-owned. The ghost this kills: a server line
    frozen into a role template follows every project that pins the role —
    the live failure where a fresh project inherited a source named 'ppc' it
    had never heard of. The API refuses the shape, with directions to the
    right place."""
    from tests.conftest import ROLE_MIN

    refused = client.post("/admin/roles", json={
        "slug": "worker", "name": "Worker", **ROLE_MIN,
        "tool_permissions": {
            "mcp_servers": [{"name": "ppc", "url_env": "MCP_SERVER"}]},
    }, headers=ADMIN)
    assert refused.status_code == 422
    assert "Data Source" in refused.json()["detail"]
    assert "Settings" in refused.json()["detail"]

    # No orphan role or version slipped in alongside the refusal.
    assert client.get("/admin/roles/worker", headers=ADMIN).status_code == 404


def test_role_drift_warns_with_one_click_action(client):
    project = make_project(client, start=False)
    _attach_billing(client, project["id"])
    team = project["teams"][0]
    seed_role(client, "worker", tool_permissions={"bash": True})  # v2, unadopted

    health = _health(client, project["id"])
    drift = _by_key(health, f"role-drift:{team['name']}")
    assert drift["status"] == "warn"
    assert drift["action"] == {"kind": "repin", "team_id": team["id"]}
    assert health["ok"] is True  # a warn degrades all_ok, not ok
    assert health["all_ok"] is False

    client.post(f"/admin/teams/{team['id']}/repin", headers=ADMIN)
    assert _by_key(_health(client, project["id"]), f"role-drift:{team['name']}") is None
