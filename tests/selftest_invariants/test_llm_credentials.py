"""Attack the managed credential boundary over HTTP, including legacy doors."""
import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core.crypto import decrypt, encrypt
from yaaf.core.db import db_session
from yaaf.core.models import Project, Secret
from yaaf.runner.daemon import _billing_env

URL = "/admin/settings/llm-credentials"


def create(client, name="Production", value="test-credential-value", mode="api_key"):
    response = client.post(URL, headers=ADMIN, json={"name": name, "value": value, "mode": mode})
    assert response.status_code == 201, response.text
    return response.json()


def test_shared_assignment_rotation_and_deletion_guards(client):
    key = create(client)
    projects = [make_project(client, slug=slug, start=False, billing_secret_id=key["id"])
                for slug in ("first", "second")]
    rows = client.get(URL, headers=ADMIN).json()
    assert len(rows[0]["projects"]) == 2
    assert "value" not in rows[0] and "ciphertext" not in rows[0]
    for endpoint in (f"{URL}/{key['id']}", f"/admin/secrets/{key['id']}"):
        assert client.delete(endpoint, headers=ADMIN).status_code == 409
    updated = client.patch(f"{URL}/{key['id']}", headers=ADMIN,
                           json={"name": "Renamed", "value": "replacement-test-value"})
    assert updated.status_code == 200
    with db_session() as session:
        for p in projects:
            project = session.get(Project, p["id"])
            assert _billing_env(session, project) == {"ANTHROPIC_API_KEY": "replacement-test-value"}
        assert session.get(Secret, key["id"]).ciphertext != "replacement-test-value"
    spare = create(client, "Replacement")
    for p in projects:
        assert client.patch(f"/admin/projects/{p['id']}", headers=ADMIN,
                            json={"billing_secret_id": spare["id"]}).status_code == 200
    assert client.delete(f"{URL}/{key['id']}", headers=ADMIN).status_code == 200


def test_subscription_mode_is_derived_and_clone_keeps_assignment(client):
    key = create(client, value="sk-ant-oat-test-placeholder", mode="subscription")
    project = make_project(client, start=False, billing_secret_id=key["id"])
    with db_session() as session:
        row = session.get(Project, project["id"])
        assert row.billing_mode == "subscription"
        assert _billing_env(session, row) == {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-test-placeholder"}
    cloned = client.post(f"/admin/projects/{project['id']}/clone", headers=ADMIN,
                         json={"slug": "cloned"})
    assert cloned.status_code == 200, cloned.text
    with db_session() as session:
        assert session.get(Project, cloned.json()["id"]).billing_secret_id == key["id"]
    assert len(client.get(URL, headers=ADMIN).json()[0]["projects"]) == 2
    assert client.patch(f"{URL}/{key['id']}", headers=ADMIN,
                        json={"name": "Changed", "value": "not-a-subscription"}).status_code == 422


def test_validation_and_legacy_write_refusal(client):
    key = create(client)
    assert client.post(URL, headers=ADMIN, json={"name": " production ", "mode": "api_key",
                                               "value": "test-credential-value"}).status_code == 409
    for name, value in (("   ", "test-credential-value"), ("Blank", "        ")):
        assert client.post(URL, headers=ADMIN, json={"name": name, "mode": "api_key",
                                                   "value": value}).status_code == 422
    project = make_project(client, start=False)
    assert client.post("/admin/secrets", headers=ADMIN, json={"project_id": project["id"],
        "name": "Manual", "kind": "billing", "value": "test-credential-value"}).status_code == 422
    assert client.post("/admin/secrets", headers=ADMIN, json={"name": key["name"],
        "value": "replacement-test-value"}).status_code == 422
    assert client.patch(f"/admin/projects/{project['id']}", headers=ADMIN,
                        json={"billing_secret_id": "missing"}).status_code == 422
    assert client.patch(f"{URL}/{key['id']}", headers=ADMIN,
                        json={"name": "Rename only"}).status_code == 200
    with db_session() as session:
        assert decrypt(session.get(Secret, key["id"]).ciphertext) == "test-credential-value"


def test_only_admin_can_manage_credentials(client):
    key = create(client)
    project = make_project(client)
    token, _ = live_token(project["id"], project["ceo_agent_id"])
    for headers in ({}, bearer(token)):
        assert client.get(URL, headers=headers).status_code == 401
        assert client.post(URL, headers=headers, json={"name": "Forged", "mode": "api_key",
                           "value": "test-credential-value"}).status_code == 401
        assert client.patch(f"{URL}/{key['id']}", headers=headers,
                            json={"name": "Forged"}).status_code == 401
        assert client.delete(f"{URL}/{key['id']}", headers=headers).status_code == 401


@pytest.mark.parametrize("mode,value", [("api_key", "test-credential-value"),
                                       ("subscription", "sk-ant-oat-test-placeholder")])
def test_migration_preserves_ciphertext_and_assignments(client, mode, value):
    project = make_project(client, start=False, billing_mode=mode)
    with db_session() as session:
        ciphertext = encrypt(value)
        session.add(Secret(id="legacy-billing", project_id=project["id"], name="Original",
                           ciphertext=ciphertext, kind="billing", role_scopes=[]))
        session.flush()
        session.get(Project, project["id"]).billing_secret_id = "legacy-billing"
    with db_session() as session:
        migration = importlib.import_module("migrations.versions.0034_managed_llm_credentials")
        with Operations.context(MigrationContext.configure(session.connection())):
            migration.upgrade()
        session.expire_all()
        row = session.get(Secret, "legacy-billing")
        assert row.ciphertext == ciphertext
        assert row.project_id is None and row.llm_mode == mode
        assert session.get(Project, project["id"]).billing_secret_id == row.id
    renamed = client.patch(f"{URL}/legacy-billing", headers=ADMIN, json={"name": "Migrated key"})
    assert renamed.status_code == 200
    assert client.delete(f"{URL}/legacy-billing", headers=ADMIN).status_code == 409


def test_legacy_platform_assignment_is_preserved_separately(client):
    from yaaf.core import platform

    project = make_project(client, start=False)
    with db_session() as session:
        platform.set_credential(session, "founder-test-value")
        session.flush()
        original = session.query(Secret).filter_by(name=platform.PLATFORM_BILLING_SECRET).one()
        original_id, ciphertext = original.id, original.ciphertext
        session.get(Project, project["id"]).billing_secret_id = original_id
    with db_session() as session:
        migration = importlib.import_module("migrations.versions.0034_managed_llm_credentials")
        with Operations.context(MigrationContext.configure(session.connection())):
            migration.upgrade()
        session.expire_all()
        attached = session.get(Project, project["id"]).billing_secret_id
        assert attached != original_id
        row = session.get(Secret, attached)
        assert row.ciphertext == ciphertext and row.llm_mode == "api_key"
        assert platform.credential(session) == "founder-test-value"
    assert client.delete(f"{URL}/{attached}", headers=ADMIN).status_code == 409


def test_credential_type_and_role_grants_cannot_change(client):
    key = create(client)
    for patch in ({"mode": "subscription"}, {"role_scopes": ["worker"]}):
        assert client.patch(f"{URL}/{key['id']}", headers=ADMIN,
                            json={"name": "Production", **patch}).status_code == 422
    for name in ("platform-billing", "github"):
        assert client.post(URL, headers=ADMIN, json={"name": name, "mode": "api_key",
                           "value": "test-credential-value"}).status_code == 422
