"""Invariant selftests: OAuth data sources — the whole ladder, on this side of
the wall.

An OAuth MCP server hands out no pastable credential: connecting one means
discovery (RFC 9728 + 8414), client registration (RFC 7591), the
authorization-code flow with PKCE, and refresh-token renewal forever after.
All of that is control-plane work; the sandbox keeps receiving the same
`MCP_<NAME>_TOKEN` env var it gets for a pasted bearer. These tests hold the
seams: the verifier never appears in config plaintext, the callback state is
single-use and mortal, refresh secrets are never role-granted, clones and
exports never carry tokens, and the lease path injects a *fresh* token.
"""

from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from tests.conftest import ADMIN, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Project, RunKind, RunQueue, Secret, utcnow
from yaaf.server import mcp_oauth
from yaaf.server.api import admin as admin_module

MCP_URL = "https://data.example.com/mcp"
ISSUER = "https://auth.example.com"


class OAuthWorld:
    """One MockTransport playing both the MCP server and its authorization
    server, on shared state: tokens it issued are the tokens it honors, and
    refresh tokens rotate (single-use) the way real providers' do."""

    def __init__(self):
        self.serial = 0
        self.access_token = None      # the one the MCP server currently honors
        self.refresh_token = None     # the one /token currently accepts
        self.codes: set[str] = set()
        self.expires_in = 3600
        self.offer_registration = True
        self.challenge_header = True
        self.issue_client_secret = None
        self.registrations: list[dict] = []
        self.token_requests: list[dict] = []
        self.transport = httpx.MockTransport(self.handler)

    def _issue(self) -> dict:
        self.serial += 1
        self.access_token = f"at-{self.serial}"
        self.refresh_token = f"rt-{self.serial}"
        return {"access_token": self.access_token, "refresh_token": self.refresh_token,
                "token_type": "Bearer", "expires_in": self.expires_in}

    def handler(self, request: httpx.Request) -> httpx.Response:
        url, path = str(request.url), request.url.path
        if url.startswith(MCP_URL):
            if request.headers.get("authorization") != f"Bearer {self.access_token}" \
                    or self.access_token is None:
                headers = {}
                if self.challenge_header:
                    headers["WWW-Authenticate"] = (
                        'Bearer resource_metadata='
                        '"https://data.example.com/.well-known/oauth-protected-resource/mcp"')
                return httpx.Response(401, headers=headers)
            body = json.loads(request.content)
            if body.get("method") == "initialize":
                return httpx.Response(200, json={
                    "jsonrpc": "2.0", "id": 1,
                    "result": {"serverInfo": {"name": "Vault", "version": "2"}}})
            if body.get("method") == "tools/list":
                return httpx.Response(200, json={
                    "jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "search"}]}})
            return httpx.Response(202)
        if path == "/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json={
                "resource": MCP_URL, "authorization_servers": [ISSUER],
                "scopes_supported": ["mcp.read"]})
        if path == "/.well-known/oauth-authorization-server" and url.startswith(ISSUER):
            meta = {"issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token"}
            if self.offer_registration:
                meta["registration_endpoint"] = f"{ISSUER}/register"
            return httpx.Response(200, json=meta)
        if path == "/register":
            body = json.loads(request.content)
            self.registrations.append(body)
            registered = {"client_id": "client-abc", **body}
            if self.issue_client_secret:
                registered["client_secret"] = self.issue_client_secret
            return httpx.Response(201, json=registered)
        if path == "/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            if form.get("grant_type") == "authorization_code":
                if form.get("code") not in self.codes:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                self.codes.discard(form["code"])
                return httpx.Response(200, json=self._issue())
            if form.get("grant_type") == "refresh_token":
                if form.get("refresh_token") != self.refresh_token:
                    return httpx.Response(400, json={
                        "error": "invalid_grant",
                        "error_description": "refresh token is not current"})
                return httpx.Response(200, json=self._issue())
            return httpx.Response(400, json={"error": "unsupported_grant_type"})
        return httpx.Response(404)


@pytest.fixture()
def world(monkeypatch):
    """Route every control-plane OAuth request into the fake world, and keep
    reachability checks off the network (the pattern of the sibling suites)."""
    fake = OAuthWorld()
    monkeypatch.setattr(
        mcp_oauth, "_client",
        lambda transport=None: httpx.Client(timeout=10, follow_redirects=True,
                                            transport=fake.transport))
    fake.checks: list[str | None] = []

    def offline_check(url, bearer, transport=None):
        fake.checks.append(bearer)
        return {"ok": True, "detail": "reachable"}

    monkeypatch.setattr(admin_module, "_check_data_source", offline_check)
    return fake


def _save(client, project_id, **overrides):
    body = {"name": "ppc", "url": MCP_URL, "roles": ["worker"], "auth": "oauth",
            **overrides}
    return client.post(f"/admin/projects/{project_id}/mcp_servers", json=body,
                       headers=ADMIN)


def _entry(project_id: str) -> dict:
    with db_session() as session:
        servers = session.get(Project, project_id).integrations["mcp_servers"]
        return next(s for s in servers if s["name"] == "ppc")


def _connect(client, project_id) -> dict:
    """Save + start + callback: the whole happy path, returning the entry."""
    assert _save(client, project_id).status_code == 200
    started = client.post(f"/admin/projects/{project_id}/mcp_servers/ppc/oauth/start",
                          json={}, headers=ADMIN)
    assert started.status_code == 200, started.text
    query = parse_qs(urlparse(started.json()["authorize_url"]).query)
    return query, client


def test_discovery_walks_the_ladder(world):
    disc = mcp_oauth.discover(MCP_URL)
    assert disc.authorization_endpoint == f"{ISSUER}/authorize"
    assert disc.token_endpoint == f"{ISSUER}/token"
    assert disc.registration_endpoint == f"{ISSUER}/register"
    assert disc.resource == MCP_URL
    assert disc.scopes_supported == ["mcp.read"]


def test_discovery_survives_a_server_that_skips_the_challenge_header(world):
    """RFC 9728's header is a courtesy many servers omit; the well-known
    fallback must find the same ladder."""
    world.challenge_header = False
    disc = mcp_oauth.discover(MCP_URL)
    assert disc.token_endpoint == f"{ISSUER}/token"


def test_no_dcr_and_no_pasted_client_is_a_named_refusal(world, client):
    """A server without dynamic registration cannot be connected hands-free.
    The refusal has to say what to paste and where to find it — not '400'."""
    world.offer_registration = False
    project = make_project(client, start=False)
    assert _save(client, project["id"]).status_code == 200
    refused = client.post(f"/admin/projects/{project['id']}/mcp_servers/ppc/oauth/start",
                          json={}, headers=ADMIN)
    assert refused.status_code == 422
    assert "developer console" in refused.json()["detail"]
    # A pasted client id is the escape hatch: same server now connects.
    assert _save(client, project["id"], client_id="pasted-id").status_code == 200
    started = client.post(f"/admin/projects/{project['id']}/mcp_servers/ppc/oauth/start",
                          json={}, headers=ADMIN)
    assert started.status_code == 200, started.text
    assert "client_id=pasted-id" in started.json()["authorize_url"]


def test_saving_an_oauth_source_asks_to_connect_not_to_paste(world, client):
    project = make_project(client, start=False)
    saved = _save(client, project["id"])
    assert saved.status_code == 200, saved.text
    assert saved.json()["needs_connect"] is True
    assert "press Connect" in saved.json()["check"]["detail"]
    entry = _entry(project["id"])
    assert entry["auth"] == "oauth"
    assert entry["oauth"]["status"] == "needs_auth"
    with db_session() as session:
        assert session.query(Secret).filter_by(name="mcp-ppc-url").count() == 1
        assert session.query(Secret).filter_by(name="mcp-ppc-token").count() == 0


def test_the_authorize_url_carries_pkce_state_and_resource(world, client):
    project = make_project(client, start=False)
    query, _ = _connect(client, project["id"])
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["client-abc"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43  # unpadded SHA-256, always
    assert query["resource"] == [MCP_URL]
    assert query["scope"] == ["mcp.read"]
    assert len(query["state"][0]) >= 43
    # The client registered with the callback this install actually serves.
    assert world.registrations[0]["redirect_uris"][0].endswith("/oauth/mcp/callback")
    assert world.registrations[0]["token_endpoint_auth_method"] == "none"

    entry = _entry(project["id"])
    assert entry["oauth"]["status"] == "pending"
    pending = entry["oauth"]["pending"]
    assert pending["state"] == query["state"][0]
    # The PKCE verifier is a secret: encrypted at rest, never in the JSON.
    from yaaf.core.crypto import decrypt

    verifier = decrypt(pending["verifier_cipher"])
    assert mcp_oauth.pkce_challenge(verifier) == query["code_challenge"][0]
    with db_session() as session:
        blob = json.dumps(session.get(Project, project["id"]).integrations)
        assert verifier not in blob


def test_the_callback_redeems_the_state_once_and_stores_the_tokens(world, client):
    project = make_project(client, start=False)
    query, _ = _connect(client, project["id"])
    world.codes.add("code-1")
    # No Authorization header: the browser redirect cannot carry one.
    landed = client.get("/oauth/mcp/callback",
                        params={"state": query["state"][0], "code": "code-1"})
    assert landed.status_code == 200, landed.text
    assert "Connected" in landed.text

    exchange = world.token_requests[-1]
    assert exchange["grant_type"] == "authorization_code"
    assert mcp_oauth.pkce_challenge(exchange["code_verifier"]) == query["code_challenge"][0]
    assert exchange["redirect_uri"].endswith("/oauth/mcp/callback")
    assert exchange["resource"] == MCP_URL

    entry = _entry(project["id"])
    assert entry["oauth"]["status"] == "connected"
    assert "pending" not in entry["oauth"]
    assert entry["bearer_secret"] == "mcp-ppc-token"
    assert entry["oauth"]["refresh_secret"] == "mcp-ppc-oauth-refresh"
    assert entry["oauth"]["expires_at"] is not None
    assert entry["last_check"]["ok"] is True
    assert world.checks[-1] == world.access_token

    with db_session() as session:
        from yaaf.core.crypto import decrypt

        token = session.query(Secret).filter_by(name="mcp-ppc-token").one()
        assert decrypt(token.ciphertext) == world.access_token
        assert token.role_scopes == ["worker"] and token.kind == "service"
        refresh = session.query(Secret).filter_by(name="mcp-ppc-oauth-refresh").one()
        assert decrypt(refresh.ciphertext) == world.refresh_token
        # Never injected into any sandbox: no grants, ever.
        assert refresh.role_scopes == []

    # The state died with its redemption: a replayed link lands nowhere.
    replay = client.get("/oauth/mcp/callback",
                        params={"state": query["state"][0], "code": "code-1"})
    assert replay.status_code == 400


def test_a_guessed_state_gets_a_shrug(world, client):
    make_project(client, start=False)
    assert client.get("/oauth/mcp/callback",
                      params={"state": "guessed", "code": "x"}).status_code == 400


def test_denied_consent_is_reported_not_swallowed(world, client):
    project = make_project(client, start=False)
    query, _ = _connect(client, project["id"])
    landed = client.get("/oauth/mcp/callback", params={
        "state": query["state"][0], "error": "access_denied",
        "error_description": "the human said no"})
    assert landed.status_code == 400
    assert "the human said no" in landed.text
    entry = _entry(project["id"])
    assert entry["oauth"]["status"] == "needs_auth"
    assert "the human said no" in entry["oauth"]["error"]


def test_an_abandoned_sign_in_expires(world, client):
    project = make_project(client, start=False)
    query, _ = _connect(client, project["id"])
    with db_session() as session:
        row = session.get(Project, project["id"])
        entry = next(s for s in row.integrations["mcp_servers"] if s["name"] == "ppc")
        entry["oauth"]["pending"]["created_at"] = (
            utcnow() - timedelta(seconds=mcp_oauth.PENDING_TTL_S + 60)).isoformat()
        mcp_oauth.persist_entry(row, entry)
    world.codes.add("late-code")
    landed = client.get("/oauth/mcp/callback",
                        params={"state": query["state"][0], "code": "late-code"})
    assert landed.status_code == 400 and "expired" in landed.text
    assert _entry(project["id"])["oauth"]["status"] == "needs_auth"


def _connected_project(world, client):
    project = make_project(client)
    query, _ = _connect(client, project["id"])
    world.codes.add("code-1")
    assert client.get("/oauth/mcp/callback", params={
        "state": query["state"][0], "code": "code-1"}).status_code == 200
    return project


def _age_token(project_id: str, seconds_left: int) -> None:
    with db_session() as session:
        row = session.get(Project, project_id)
        entry = next(s for s in row.integrations["mcp_servers"] if s["name"] == "ppc")
        entry["oauth"]["expires_at"] = (utcnow() + timedelta(seconds=seconds_left)).isoformat()
        mcp_oauth.persist_entry(row, entry)


def test_ensure_fresh_token_refreshes_and_honors_rotation(world, client):
    project = _connected_project(world, client)
    fresh = world.access_token

    # Far from expiry: the stored token is the answer, no network spent.
    with db_session() as session:
        row = session.get(Project, project["id"])
        entry = next(s for s in row.integrations["mcp_servers"] if s["name"] == "ppc")
        assert mcp_oauth.ensure_fresh_token(session, row, entry) == fresh

    # Inside the margin: renewed, re-stored, and the rotated refresh honored.
    _age_token(project["id"], 60)
    with db_session() as session:
        row = session.get(Project, project["id"])
        entry = next(s for s in row.integrations["mcp_servers"] if s["name"] == "ppc")
        renewed = mcp_oauth.ensure_fresh_token(session, row, entry)
    assert renewed == world.access_token != fresh
    with db_session() as session:
        from yaaf.core.crypto import decrypt

        token = session.query(Secret).filter_by(name="mcp-ppc-token").one()
        assert decrypt(token.ciphertext) == renewed
        assert token.role_scopes == ["worker"]
        refresh = session.query(Secret).filter_by(name="mcp-ppc-oauth-refresh").one()
        assert decrypt(refresh.ciphertext) == world.refresh_token
    entry = _entry(project["id"])
    assert entry["oauth"]["status"] == "connected"

    # A dead refresh token cannot be papered over: the stale access token
    # stays mounted (a named auth error beats a silently absent source) and
    # the source says a human sign-in is the only fix.
    _age_token(project["id"], 60)
    world.refresh_token = "revoked-elsewhere"
    with db_session() as session:
        row = session.get(Project, project["id"])
        entry = next(s for s in row.integrations["mcp_servers"] if s["name"] == "ppc")
        stale = mcp_oauth.ensure_fresh_token(session, row, entry)
    assert stale == renewed
    entry = _entry(project["id"])
    assert entry["oauth"]["status"] == "needs_reauth"
    assert "refresh token is not current" in entry["oauth"]["error"]


def test_the_lease_injects_a_token_refreshed_for_the_run(world, client):
    """End to end, the pattern of test_mcp_project: the sandbox spec is
    byte-identical to a bearer source's — same env vars, same shape — but the
    value behind MCP_PPC_TOKEN was renewed at lease time, not at connect time."""
    from yaaf.runner import daemon

    project = _connected_project(world, client)
    stale = world.access_token
    _age_token(project["id"], 60)

    employee = project["teams"][0]["agents"][0]["id"]
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=employee, kind=RunKind.AGENT_RUN,
                             trigger={"reasons": []}, attempt=1))
    job = daemon.lease_and_prepare(limit=1)[0]
    specs = job.tool_permissions["mcp_servers"]
    assert {"name": "ppc", "url_env": "MCP_PPC_URL", "access": "inline",
            "bearer_env": "MCP_PPC_TOKEN"} in specs
    assert job.billing_env["MCP_PPC_TOKEN"] == world.access_token != stale
    # The refresh secret exists for the control plane alone.
    assert "MCP_PPC_OAUTH_REFRESH" not in job.billing_env


def test_the_check_endpoint_renews_before_it_probes(world, client):
    project = _connected_project(world, client)
    _age_token(project["id"], 60)
    checked = client.post(f"/admin/projects/{project['id']}/mcp_servers/ppc/check",
                          json={}, headers=ADMIN)
    assert checked.status_code == 200 and checked.json()["ok"] is True
    assert world.checks[-1] == world.access_token
    assert world.token_requests[-1]["grant_type"] == "refresh_token"


def test_a_clone_carries_the_client_but_never_the_tokens(world, client):
    """Refresh tokens rotate on use: two projects sharing one would invalidate
    each other's copy on the first renewal. The registered client is this
    install's and serves both, so reconnecting the twin is one Connect click."""
    project = _connected_project(world, client)
    cloned = client.post(f"/admin/projects/{project['id']}/clone",
                         json={"slug": "acme-2"}, headers=ADMIN)
    assert cloned.status_code == 200, cloned.text
    twin_id = cloned.json()["id"]
    twin = cloned.json()["copied"]["data_sources"][0]
    assert "press Connect" in twin["check"]["detail"]

    with db_session() as session:
        for gone in ("mcp-ppc-token", "mcp-ppc-oauth-refresh"):
            assert session.query(Secret).filter_by(
                project_id=twin_id, name=gone).count() == 0, gone
        assert session.query(Secret).filter_by(
            project_id=twin_id, name="mcp-ppc-url").count() == 1
        servers = session.get(Project, twin_id).integrations["mcp_servers"]
        entry = next(s for s in servers if s["name"] == "ppc")
    assert entry["oauth"]["status"] == "needs_auth"
    assert entry["oauth"]["client_id"] == "client-abc"
    assert "bearer_secret" not in entry
    assert "pending" not in entry["oauth"] and "expires_at" not in entry["oauth"]


def test_an_export_names_the_mode_but_nothing_install_bound(world, client):
    """The bundle asks a human to re-enter secrets by name; 'paste the refresh
    token' is not a request a human can meet, and the registered client names
    this install's redirect URI. Only the mode marker travels."""
    project = _connected_project(world, client)
    bundle = client.get(f"/admin/projects/{project['id']}/export", headers=ADMIN).json()
    entry = next(s for s in bundle["project"]["integrations"]["mcp_servers"]
                 if s["name"] == "ppc")
    assert entry["auth"] == "oauth"
    assert "oauth" not in entry and "bearer_secret" not in entry and "last_check" not in entry
    names = [s["name"] for s in bundle["secrets"]]
    assert "mcp-ppc-url" in names
    assert not any(n in names for n in
                   ("mcp-ppc-token", "mcp-ppc-oauth-refresh", "mcp-ppc-oauth-client"))


def test_switching_away_from_oauth_leaves_no_token_behind(world, client):
    """An OAuth access token is not a pasted credential; a source edited back
    to bearer (or open) must not keep presenting it, and the refresh secret
    must not linger where nothing will ever renew it."""
    project = _connected_project(world, client)
    switched = _save(client, project["id"], auth="bearer", bearer="pasted-tok")
    assert switched.status_code == 200, switched.text
    entry = _entry(project["id"])
    assert entry["auth"] == "bearer" and "oauth" not in entry
    with db_session() as session:
        from yaaf.core.crypto import decrypt

        assert session.query(Secret).filter_by(name="mcp-ppc-oauth-refresh").count() == 0
        token = session.query(Secret).filter_by(name="mcp-ppc-token").one()
        assert decrypt(token.ciphertext) == "pasted-tok"


def test_a_roles_only_edit_never_forces_a_new_sign_in(world, client):
    """The consent screen is a human's time. Re-ticking roles re-grants the
    stored tokens; it must not tear the connection down."""
    project = _connected_project(world, client)
    edited = _save(client, project["id"], roles=["ceo"])
    assert edited.status_code == 200, edited.text
    assert edited.json()["needs_connect"] is False
    entry = _entry(project["id"])
    assert entry["oauth"]["status"] == "connected"
    with db_session() as session:
        token = session.query(Secret).filter_by(name="mcp-ppc-token").one()
        assert token.role_scopes == ["ceo"]
        refresh = session.query(Secret).filter_by(name="mcp-ppc-oauth-refresh").one()
        assert refresh.role_scopes == []


def test_delete_cleans_up_the_whole_oauth_estate(world, client):
    world.issue_client_secret = "cs-9"
    project = _connected_project(world, client)
    with db_session() as session:
        assert session.query(Secret).filter_by(name="mcp-ppc-oauth-client").count() == 1
    deleted = client.delete(f"/admin/projects/{project['id']}/mcp_servers/ppc",
                            headers=ADMIN)
    assert deleted.status_code == 200
    with db_session() as session:
        assert session.query(Secret).filter(Secret.name.like("mcp-ppc%")).count() == 0
