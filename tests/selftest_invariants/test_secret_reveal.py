"""Invariant selftests: the chairman reads their own credentials, and nobody
else does.

There is one human on this system and they hold the master admin token, so a
project value they could read straight out of Postgres is masked in the UI,
never withheld. What must stay true is the *boundary*: reveal is an `/admin`
act with an audit row behind it, and no agent principal can reach it or
anything like it.

The credentials that live *above* projects are the exception, and always were
for the Slack tokens: an LLM credential pays for every project assigned to it,
is rotated rather than read, and reveal refuses it — the same answer the
settings screen gives for a bot token.
"""

from __future__ import annotations

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog, Secret


def _store(client, project_id, name, value="s3cret-value", **extra):
    if extra.get("kind") == "billing" and not extra.get("role_scopes"):
        return client.post("/admin/settings/llm-credentials", headers=ADMIN,
                           json={"name": name, "mode": "api_key", "value": value})
    return client.post(
        "/admin/secrets",
        json={"project_id": project_id, "name": name, "value": value, **extra},
        headers=ADMIN,
    )


def test_chairman_reads_service_values_and_the_billing_credential_refuses(client):
    project = make_project(client, start=False)
    wp = _store(client, project["id"], "wp-pass", "hunter2", role_scopes=["worker"]).json()
    billing = _store(client, project["id"], "anthropic-api-key",
                     "sk-ant-api03-not-real", kind="billing").json()

    shown = client.get(f"/admin/secrets/{wp['id']}/value", headers=ADMIN)
    assert shown.status_code == 200, shown.text
    assert shown.json()["value"] == "hunter2"
    # The credential every project pays with is rotated, never read back.
    refused = client.get(f"/admin/secrets/{billing['id']}/value", headers=ADMIN)
    assert refused.status_code == 422
    assert "sk-ant-api03-not-real" not in refused.text

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


def test_no_agent_route_serves_a_secret_value(client):
    """Agents authenticate against /agent/* with per-run tokens. Reveal must
    never acquire a sibling there — the run principal is not the chairman.

    Stage D gave agents /agent/credentials (the roster: names and scopes, the
    CEO's to read and — dial permitting — re-scope), so the invariant is now
    stated as what it always meant: no agent path reaches a VALUE. The words
    'secret', 'value' and 'credential' are banned in /agent paths except the
    two roster routes named below — a new credential-flavored route must be
    added here deliberately, with a value-free proof beside it — and the
    roster route is proven value-free by calling it."""
    paths = client.app.openapi()["paths"]
    assert "/admin/secrets/{secret_id}/value" in paths, "sanity: reveal is mounted"

    # The only agent routes allowed to say 'credential' — both value-free.
    roster_routes = {"/agent/credentials", "/agent/credentials/{name}/scope"}
    agent_paths = [path for path in paths if path.startswith("/agent")]
    assert agent_paths, "sanity: the agent router is mounted"
    assert roster_routes <= set(agent_paths), "sanity: the roster is mounted"
    for path in agent_paths:
        assert "secret" not in path
        assert "value" not in path
        if path not in roster_routes:
            assert "credential" not in path, f"unvetted credential route: {path}"

    project = make_project(client, slug="roster")
    _store(client, project["id"], "wp-pass", role_scopes=["worker"])
    _, token = live_token(project["id"], project["ceo_agent_id"])
    roster = client.get("/agent/credentials", headers=bearer(token))
    assert roster.status_code == 200
    for row in roster.json():
        # Still exact, so a field added here has to be argued for. `file` says
        # the variable will hold a path rather than the credential; the file's
        # own name is NOT here, because this roster lists credentials the CEO
        # may not hold and a key file is often named for the account it opens.
        assert set(row) == {"name", "role_scopes", "file"}, "the roster must stay value-free"
        assert "s3cret-value" not in str(row)


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
    assert refused.status_code == 409
    assert "assigned to a project" in refused.json()["detail"]
    with db_session() as session:
        assert session.get(Secret, billing["id"]) is not None

    # Detached, it deletes fine — the guard is about the live attachment.
    spare = _store(client, project["id"], "anthropic-api-key-2", kind="billing").json()
    client.patch(f"/admin/projects/{project['id']}",
                 json={"billing_secret_id": spare["id"]}, headers=ADMIN)
    assert client.delete(f"/admin/secrets/{billing['id']}", headers=ADMIN).status_code == 200
