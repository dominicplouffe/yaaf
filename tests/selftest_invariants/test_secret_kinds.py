"""Invariant selftests: the service/billing secret split — a WordPress password
can never become the model credential, and the model credential can never be
granted into a role's sandbox."""

from __future__ import annotations

from tests.conftest import ADMIN, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Project, Secret
from yaaf.runner.daemon import _role_secret_env
from yaaf.server.contextpack import role_version_for


def _store(client, project_id, name, **extra):
    if extra.get("kind") == "billing" and not extra.get("role_scopes"):
        return client.post("/admin/settings/llm-credentials", headers=ADMIN,
                           json={"name": name, "mode": "api_key", "value": "s3cret-value"})
    response = client.post(
        "/admin/secrets",
        json={"project_id": project_id, "name": name, "value": "s3cret-value", **extra},
        headers=ADMIN,
    )
    return response


def test_billing_credentials_refuse_role_grants(client):
    project = make_project(client, start=False)

    # At creation — a billing kind cannot even be stored here now, granted or
    # not; the refusal points at the door that replaced this one.
    refused = _store(client, project["id"], "anthropic-api-key",
                     kind="billing", role_scopes=["worker"])
    assert refused.status_code == 422
    assert "Manage LLM credentials in Settings" in refused.json()["detail"]

    # And later, on an existing billing secret:
    billing = _store(client, project["id"], "anthropic-api-key", kind="billing")
    assert billing.status_code == 201
    rescope = client.post(
        "/admin/secrets",
        json={"project_id": None, "name": "anthropic-api-key",
              "role_scopes": ["worker"]},
        headers=ADMIN,
    )
    assert rescope.status_code == 422


def test_kind_is_fixed_at_creation(client):
    project = make_project(client, start=False)
    assert _store(client, project["id"], "wp-pass").status_code == 200
    relabel = client.post(
        "/admin/secrets",
        json={"project_id": project["id"], "name": "wp-pass", "kind": "billing"},
        headers=ADMIN,
    )
    assert relabel.status_code == 422
    assert "fixed at creation" in relabel.json()["detail"]


def test_service_credentials_refuse_billing_attachment(client):
    project = make_project(client, start=False)
    wp = _store(client, project["id"], "wp-pass",
                role_scopes=["worker"]).json()

    refused = client.patch(
        f"/admin/projects/{project['id']}",
        json={"billing_secret_id": wp["id"]},
        headers=ADMIN,
    )
    assert refused.status_code == 422
    assert "service credential" in refused.json()["detail"]

    billing = _store(client, project["id"], "anthropic-api-key", kind="billing").json()
    attached = client.patch(
        f"/admin/projects/{project['id']}",
        json={"billing_secret_id": billing["id"]},
        headers=ADMIN,
    )
    assert attached.status_code == 200
    assert attached.json()["billing_secret_id"] == billing["id"]


def test_runner_never_injects_billing_kind_as_role_env(client):
    """Belt and suspenders: even a hand-edited DB row granting a billing secret
    to a role must not reach any sandbox env."""
    project = make_project(client)
    billing = _store(client, project["id"], "anthropic-api-key", kind="billing").json()
    worker_id = project["teams"][0]["agents"][0]["id"]

    with db_session() as session:
        session.get(Secret, billing["id"]).role_scopes = ["worker"]  # bypass the API
    with db_session() as session:
        agent = session.get(Agent, worker_id)
        env = _role_secret_env(session, session.get(Project, project["id"]),
                               role_version_for(session, agent))
    assert "ANTHROPIC_API_KEY" not in env
    assert not env, "a billing credential leaked into a role's env"
