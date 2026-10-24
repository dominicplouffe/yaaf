"""Invariant selftests: changing who receives a data source, and asking an
address how it signs in before anything is saved.

The access map moves one tick at a time. A tick must change the grant on the
two secrets a sandbox reads — and nothing else: not the address, not the
check verdict, not an OAuth connection. And it answers to the same rule every
other grant does: a role nobody on the project has is refused, because a grant
that reaches no one is a success that lies.

The probe is the add form's first step. It reaches out exactly as a save
would and stores nothing.
"""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import ADMIN, headers_of, make_project, seed_role
from tests.selftest_invariants.test_auth_roles import seat
from yaaf.core.db import db_session
from yaaf.core.models import Project, Secret
from yaaf.server import mcp_oauth
from yaaf.server.api import admin as admin_module
from yaaf.server.api.admin import _check_data_source

URL = "http://data.example.com:3845/mcp"


@pytest.fixture
def checks(monkeypatch):
    """Reachability never leaves the box; `calls` says whether one was made."""
    calls: list[tuple[str, str | None]] = []

    def fake(url, bearer):
        calls.append((url, bearer))
        return {"ok": True, "state": "ok", "tools": 2, "detail": "MCP server ready"}

    monkeypatch.setattr(admin_module, "_check_data_source", fake)
    return calls


def _two_team_project(client):
    seed_role(client, "analyst")
    return make_project(client, start=False, teams=[
        {"role_slug": "worker", "name": "Alpha", "employees": ["Lead"], "lead": "Lead"},
        {"role_slug": "analyst", "name": "Numbers", "employees": ["Kim"], "lead": "Kim"},
    ])


def _source(client, project_id, **overrides):
    saved = client.post(f"/admin/projects/{project_id}/mcp_servers", headers=ADMIN, json={
        "name": "ppc", "url": URL, "roles": ["worker"], "bearer": "tok-not-real-000000",
        **overrides})
    assert saved.status_code == 200, saved.text


def _grants(project_id) -> dict[str, list[str]]:
    with db_session() as session:
        rows = session.query(Secret).filter(Secret.project_id == project_id).all()
        return {r.name: list(r.role_scopes or []) for r in rows}


def test_a_tick_regrants_the_address_and_token_without_a_save(client, checks):
    project = _two_team_project(client)
    _source(client, project["id"])
    checks.clear()

    moved = client.post(f"/admin/projects/{project['id']}/mcp_servers/ppc/roles",
                        json={"roles": ["worker", "analyst"]}, headers=ADMIN)
    assert moved.status_code == 200, moved.text
    assert moved.json()["was"] == ["worker"]

    grants = _grants(project["id"])
    assert grants["mcp-ppc-url"] == ["worker", "analyst"]
    assert grants["mcp-ppc-token"] == ["worker", "analyst"]
    with db_session() as session:
        entry = session.get(Project, project["id"]).integrations["mcp_servers"][0]
        assert entry["roles"] == ["worker", "analyst"]
        assert entry["last_check"]["ok"] is True, "the verdict was not touched"
    assert checks == [], "a regrant does not reach out to the server"


def test_a_regrant_to_a_role_nobody_here_has_is_refused(client, checks):
    project = make_project(client, start=False)
    _source(client, project["id"])
    seed_role(client, "stranger-role")

    refused = client.post(f"/admin/projects/{project['id']}/mcp_servers/ppc/roles",
                          json={"roles": ["stranger-role"]}, headers=ADMIN)
    assert refused.status_code in (403, 422), refused.text
    assert _grants(project["id"])["mcp-ppc-url"] == ["worker"]

    empty = client.post(f"/admin/projects/{project['id']}/mcp_servers/ppc/roles",
                        json={"roles": []}, headers=ADMIN)
    assert empty.status_code == 422, "nobody is a delete, not a grant"


def test_an_observer_cannot_move_a_tick(client, checks):
    project = make_project(client, start=False)
    _source(client, project["id"])
    observer = seat(client, project, "obs@example.com", "observer")

    refused = client.post(f"/admin/projects/{project['id']}/mcp_servers/ppc/roles",
                          json={"roles": ["worker", "ceo"]}, headers=headers_of(observer))
    assert refused.status_code == 403
    assert _grants(project["id"])["mcp-ppc-url"] == ["worker"]


def test_a_regrant_keeps_an_oauth_refresh_token_ungranted(client, checks):
    project = _two_team_project(client)
    _source(client, project["id"], auth="oauth", bearer=None)
    with db_session() as session:
        db_project = session.get(Project, project["id"])
        entry = db_project.integrations["mcp_servers"][0]
        for name in ("mcp-ppc-token", "mcp-ppc-refresh"):
            admin_module._upsert_project_secret(session, project["id"], name, "tok-xyz",
                                                ["worker"] if name.endswith("token") else [])
        entry["bearer_secret"] = "mcp-ppc-token"
        entry["oauth"] = {**entry["oauth"], "status": "connected",
                          "refresh_secret": "mcp-ppc-refresh"}
        mcp_oauth.persist_entry(db_project, entry)
        session.commit()

    client.post(f"/admin/projects/{project['id']}/mcp_servers/ppc/roles",
                json={"roles": ["analyst"]}, headers=ADMIN)
    grants = _grants(project["id"])
    assert grants["mcp-ppc-token"] == ["analyst"]
    assert grants["mcp-ppc-refresh"] == [], "a refresh token is never in a sandbox"
    with db_session() as session:
        entry = session.get(Project, project["id"]).integrations["mcp_servers"][0]
        assert entry["oauth"]["status"] == "connected", "no trip to the consent screen"


def test_the_probe_tells_oauth_from_a_pasted_token_and_stores_nothing(client, monkeypatch):
    project = make_project(client, start=False)
    monkeypatch.setattr(admin_module, "_check_data_source", lambda url, bearer: {
        "ok": False, "code": "unauthorized", "state": "unauthorized",
        "detail": "the server refused the credential (HTTP 401)"})

    monkeypatch.setattr(mcp_oauth, "discover", lambda url: object())
    oauth = client.post(f"/admin/projects/{project['id']}/mcp_servers/probe",
                        json={"url": URL}, headers=ADMIN)
    assert oauth.status_code == 200, oauth.text
    assert oauth.json()["auth"] == "oauth"
    assert oauth.json()["check"]["state"] == "needs_signin"

    def no_metadata(url):
        raise mcp_oauth.OAuthSetupError("no authorization server")

    monkeypatch.setattr(mcp_oauth, "discover", no_metadata)
    token = client.post(f"/admin/projects/{project['id']}/mcp_servers/probe",
                        json={"url": URL}, headers=ADMIN).json()
    assert token["auth"] == "bearer" and "paste its token" in token["check"]["detail"]

    with db_session() as session:
        assert "mcp_servers" not in (session.get(Project, project["id"]).integrations or {})
        assert session.query(Secret).filter(Secret.name.like("mcp-%")).count() == 0


def test_the_probe_refuses_garbage_and_observers(client, checks):
    project = make_project(client, start=False)
    garbage = client.post(f"/admin/projects/{project['id']}/mcp_servers/probe",
                          json={"url": "wrath1q2w!"}, headers=ADMIN)
    assert garbage.status_code == 422 and checks == []

    open_server = client.post(f"/admin/projects/{project['id']}/mcp_servers/probe",
                              json={"url": URL}, headers=ADMIN).json()
    assert open_server["auth"] == "none" and open_server["check"]["state"] == "ok"

    observer = seat(client, project, "obs@example.com", "observer")
    refused = client.post(f"/admin/projects/{project['id']}/mcp_servers/probe",
                          json={"url": URL}, headers=headers_of(observer))
    assert refused.status_code == 403


@pytest.mark.parametrize(("raised", "state"), [
    (httpx.ProxyError, "blocked"),
    (httpx.ConnectTimeout, "timeout"),
    (httpx.ConnectError, "unreachable"),
])
def test_each_way_of_not_connecting_has_its_own_state(raised, state):
    """One red word for "wrong port", "proxy refused" and "timed out" taught
    people to read red as noise. Each now names itself, and says what to do."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise raised("nope", request=request)

    check = _check_data_source(URL, None, transport=httpx.MockTransport(handler))
    assert check["ok"] is False and check["state"] == state
    assert raised.__name__ not in check["detail"], "a sentence, not an exception name"
