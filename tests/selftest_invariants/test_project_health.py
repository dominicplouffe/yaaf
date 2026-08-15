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
    secret = client.post(
        "/admin/secrets",
        json={"project_id": project_id, "name": "anthropic-api-key",
              "value": "sk-ant-api03-fake", "kind": "billing"},
        headers=ADMIN,
    ).json()
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


def test_legacy_role_source_failures_are_named(client):
    """The exact failure that stopped the live PPC project: a role-declared
    data source whose credential is missing, ungranted, or not a URL — each
    gets its own plain-language finding instead of a generic dead end."""
    project = make_project(client)
    _attach_billing(client, project["id"])
    team = project["teams"][0]
    # Publish v2 declaring a data source, adopt it.
    seed_role(client, "worker", tool_permissions={
        "mcp_servers": [{"name": "ppc", "url_env": "MCP_SERVER"}]})
    assert client.post(f"/admin/teams/{team['id']}/repin",
                       headers=ADMIN).json()["changed"] is True
    key = f"role-source:{team['name']}:ppc"

    # 1. No credential at all.
    finding = _by_key(_health(client, project["id"]), key)
    assert finding["status"] == "fail" and "no credential produces" in finding["detail"]

    # 2. Credential exists but isn't granted to the role.
    client.post("/admin/secrets",
                json={"project_id": project["id"], "name": "MCP_SERVER",
                      "value": "wrath1q2w!", "role_scopes": []}, headers=ADMIN)
    finding = _by_key(_health(client, project["id"]), key)
    assert finding["status"] == "fail" and "not granted" in finding["detail"]

    # 3. Granted, but the value is not a URL — today's live failure, named.
    client.post("/admin/secrets",
                json={"project_id": project["id"], "name": "MCP_SERVER",
                      "role_scopes": ["worker"]}, headers=ADMIN)
    finding = _by_key(_health(client, project["id"]), key)
    assert finding["status"] == "fail" and "should be a URL" in finding["detail"]
    assert "wrath1" in finding["detail"]  # shows just enough to recognize it

    # 4. Fixed value: green.
    client.post("/admin/secrets",
                json={"project_id": project["id"], "name": "MCP_SERVER",
                      "value": "http://data.example.com:3845/mcp",
                      "role_scopes": ["worker"]}, headers=ADMIN)
    assert _by_key(_health(client, project["id"]), key)["status"] == "ok"


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
