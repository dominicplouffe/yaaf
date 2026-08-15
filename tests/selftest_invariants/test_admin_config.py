"""Invariant selftests: project reconfiguration touches config, never the
constitution; secrets round-trip encrypted."""

from __future__ import annotations

from tests.conftest import ADMIN, make_project
from yaaf.core.db import db_session


def test_patch_project_config_and_billing(client):
    project = make_project(client, start=False)

    secret = client.post(
        "/admin/secrets",
        json={"project_id": project["id"], "name": "anthropic-api-key",
              "value": "sk-ant-test-not-real", "role_scopes": [], "kind": "billing"},
        headers=ADMIN,
    ).json()

    patched = client.patch(
        f"/admin/projects/{project['id']}",
        json={"billing_secret_id": secret["id"],
              "config": {"runtime": "agent_sdk", "ceo_heartbeat_hours": 4}},
        headers=ADMIN,
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["billing_secret_id"] == secret["id"]
    assert body["config"]["runtime"] == "agent_sdk"

    # Merge, not replace: a later patch keeps earlier keys.
    again = client.patch(
        f"/admin/projects/{project['id']}",
        json={"config": {"max_concurrent_runs": 2}},
        headers=ADMIN,
    ).json()
    assert again["config"]["runtime"] == "agent_sdk"
    assert again["config"]["max_concurrent_runs"] == 2

    # A foreign or unknown secret is refused.
    bad = client.patch(
        f"/admin/projects/{project['id']}",
        json={"billing_secret_id": "secr_doesnotexist"},
        headers=ADMIN,
    )
    assert bad.status_code == 422

    # The stored secret is ciphertext at rest and decrypts to the value.
    from yaaf.core.crypto import decrypt
    from yaaf.core.models import Secret

    with db_session() as session:
        row = session.get(Secret, secret["id"])
        assert row.ciphertext != "sk-ant-test-not-real"
        assert decrypt(row.ciphertext) == "sk-ant-test-not-real"


def test_add_team_to_existing_project(client):
    from tests.conftest import seed_role

    project = make_project(client)
    seed_role(client, "blogger")
    added = client.post(
        f"/admin/projects/{project['id']}/teams",
        json={"role_slug": "blogger", "name": "Content", "employees": ["Blogger"]},
        headers=ADMIN,
    )
    assert added.status_code == 200, added.text
    team = added.json()
    assert [a["name"] for a in team["agents"]] == ["Blogger"]

    org = client.get(f"/observe/projects/{project['id']}/org", headers=ADMIN).json()
    content = next(t for t in org["teams"] if t["name"] == "Content")
    # Sole employee is the de-facto lead (derived, not stored).
    assert content["lead_agent_id"] == team["agents"][0]["id"]

    channels = client.get(f"/observe/projects/{project['id']}/channels", headers=ADMIN).json()
    assert any(c["kind"] == "team" and c["team_id"] == team["id"] for c in channels)

    unknown_role = client.post(
        f"/admin/projects/{project['id']}/teams",
        json={"role_slug": "does-not-exist", "name": "X", "employees": ["Y"]},
        headers=ADMIN,
    )
    assert unknown_role.status_code == 422


def test_patch_cannot_touch_constitution(client):
    """The PATCH surface has no constitution field at all — governance changes
    only through amendments with their cooling-off."""
    project = make_project(client, start=False)
    response = client.patch(
        f"/admin/projects/{project['id']}",
        json={"constitution": {"queue_ceiling": 999}},
        headers=ADMIN,
    )
    # Unknown fields are ignored by the schema; the constitution is unchanged.
    assert response.status_code == 200
    from yaaf.core.constitution import effective

    with db_session() as session:
        assert effective(session, project["id"]).queue_ceiling == 8
