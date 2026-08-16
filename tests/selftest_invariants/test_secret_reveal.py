"""Invariant selftests: the chairman reads their own credentials, and nobody
else does.

There is one human on this system and they hold the master admin token, so a
value they could read straight out of Postgres is masked in the UI, never
withheld. What must stay true is the *boundary*: reveal is an `/admin` act with
an audit row behind it, and no agent principal can reach it or anything like it.
"""

from __future__ import annotations

from tests.conftest import ADMIN, make_project
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog, Secret


def _store(client, project_id, name, value="s3cret-value", **extra):
    return client.post(
        "/admin/secrets",
        json={"project_id": project_id, "name": name, "value": value, **extra},
        headers=ADMIN,
    )


def test_chairman_reads_service_and_billing_values(client):
    project = make_project(client, start=False)
    wp = _store(client, project["id"], "wp-pass", "hunter2", role_scopes=["worker"]).json()
    billing = _store(client, project["id"], "anthropic-api-key",
                     "sk-ant-api03-not-real", kind="billing").json()

    for secret_id, expected in ((wp["id"], "hunter2"),
                                (billing["id"], "sk-ant-api03-not-real")):
        shown = client.get(f"/admin/secrets/{secret_id}/value", headers=ADMIN)
        assert shown.status_code == 200, shown.text
        assert shown.json()["value"] == expected

    # The polled list still carries no values — reading one is a separate act.
    listed = client.get(f"/admin/secrets?project_id={project['id']}", headers=ADMIN)
    assert all("value" not in row for row in listed.json())


def test_every_reveal_is_audited(client):
    project = make_project(client, start=False)
    wp = _store(client, project["id"], "wp-pass").json()

    client.get(f"/admin/secrets/{wp['id']}/value", headers=ADMIN)
    client.get(f"/admin/secrets/{wp['id']}/value", headers=ADMIN)

    with db_session() as session:
        reveals = session.query(AuditLog).filter_by(action="secret.reveal").all()
        assert len(reveals) == 2, "one press, one row"
        assert reveals[0].detail["name"] == "wp-pass"
        assert reveals[0].entity_id == wp["id"]


def test_reveal_needs_the_admin_token(client):
    """The whole safety argument is that this route sits behind admin auth."""
    project = make_project(client, start=False)
    wp = _store(client, project["id"], "wp-pass").json()

    assert client.get(f"/admin/secrets/{wp['id']}/value").status_code in (401, 403)
    assert client.get(
        f"/admin/secrets/{wp['id']}/value",
        headers={"Authorization": "Bearer not-the-admin-token"},
    ).status_code in (401, 403)
    assert client.get(f"/admin/secrets/{wp['id']}/value", headers=ADMIN).status_code == 200


def test_no_agent_route_serves_a_secret(client):
    """Agents authenticate against /agent/* with per-run tokens. Reveal must
    never acquire a sibling there — the run principal is not the chairman."""
    from yaaf.server.app import create_app

    paths = create_app().openapi()["paths"]
    assert "/admin/secrets/{secret_id}/value" in paths, "sanity: reveal is mounted"

    agent_paths = [path for path in paths if path.startswith("/agent")]
    assert agent_paths, "sanity: the agent router is mounted"
    for path in agent_paths:
        assert "secret" not in path
        assert "credential" not in path


def test_delete_removes_a_credential(client):
    project = make_project(client, start=False)
    wp = _store(client, project["id"], "wp-pass", role_scopes=["worker"]).json()

    removed = client.delete(f"/admin/secrets/{wp['id']}", headers=ADMIN)
    assert removed.status_code == 200
    assert removed.json()["deleted"] == "wp-pass"
    with db_session() as session:
        assert session.get(Secret, wp["id"]) is None
        assert session.query(AuditLog).filter_by(action="secret.delete").count() == 1


def test_delete_refuses_the_credential_the_project_pays_with(client):
    project = make_project(client, start=False)
    billing = _store(client, project["id"], "anthropic-api-key", kind="billing").json()
    client.patch(f"/admin/projects/{project['id']}",
                 json={"billing_secret_id": billing["id"]}, headers=ADMIN)

    refused = client.delete(f"/admin/secrets/{billing['id']}", headers=ADMIN)
    assert refused.status_code == 422
    assert "pays for Claude" in refused.json()["detail"]
    with db_session() as session:
        assert session.get(Secret, billing["id"]) is not None

    # Detached, it deletes fine — the guard is about the live attachment.
    spare = _store(client, project["id"], "anthropic-api-key-2", kind="billing").json()
    client.patch(f"/admin/projects/{project['id']}",
                 json={"billing_secret_id": spare["id"]}, headers=ADMIN)
    assert client.delete(f"/admin/secrets/{billing['id']}", headers=ADMIN).status_code == 200
