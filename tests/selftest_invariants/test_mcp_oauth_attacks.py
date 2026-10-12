"""The OAuth flow, attacked the way it will be once it is on the internet.

M2 is the milestone that puts yaaf's chairman surface on a public hostname, so
these are not hypotheticals: every one of them is a documented way an OAuth
deployment leaks authority. PKCE stripped, a code replayed, a redirect target
swapped, a token minted for somewhere else presented here, scopes widened at
the token endpoint past what the consent screen granted.

The rule each defends is in docs/MCP_SERVER.md under "Full claude.ai support".
"""

from __future__ import annotations

from tests.conftest import ADMIN, CHAIR_EMAIL, bearer, make_project
from tests.oauth_flow import (
    PASSWORD,
    REDIRECT,
    VERIFIER,
    chairman_with_password,
    challenge_for,
    consent,
    exchange,
    register,
)
from tests.selftest_invariants.test_mcp_scopes import READ_TOOLS, STEER_TOOLS


def authorize_params(registered: dict, **overrides) -> dict:
    return {
        "response_type": "code", "client_id": registered["client_id"],
        "redirect_uri": REDIRECT, "scope": "read",
        "code_challenge": challenge_for(VERIFIER), "code_challenge_method": "S256",
        **overrides,
    }


def test_pkce_cannot_be_stripped_or_downgraded(client, public):
    """`plain` is not advertised, and absence is not permission."""
    chairman_with_password(client)
    registered = register(client)

    for params in (
        authorize_params(registered, code_challenge_method="plain",
                         code_challenge=VERIFIER),
        {k: v for k, v in authorize_params(registered).items()
         if k not in ("code_challenge", "code_challenge_method")},
    ):
        page = client.get("/oauth/authorize", params=params)
        assert page.status_code == 400
        assert "S256" in page.text

    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert metadata["code_challenge_methods_supported"] == ["S256"]


def test_a_code_cannot_be_redeemed_without_the_verifier_that_made_it(client, public):
    chairman_with_password(client)
    registered = register(client)
    code = consent(client, registered)

    wrong = exchange(client, registered, code, verifier="not-the-verifier")
    assert wrong.status_code == 400
    assert wrong.json()["error"] == "invalid_grant"
    assert "PKCE" in wrong.json()["error_description"]

    # And the real verifier still works afterwards: a failed attempt must not
    # burn the code, or a network hiccup becomes a lockout.
    assert exchange(client, registered, code).status_code == 200


def test_a_code_is_single_use(client, public):
    """The classic: an authorization code seen in a log, redeemed again."""
    chairman_with_password(client)
    registered = register(client)
    code = consent(client, registered)

    assert exchange(client, registered, code).status_code == 200
    replayed = exchange(client, registered, code)
    assert replayed.status_code == 400
    assert "already redeemed" in replayed.json()["error_description"]


def test_a_code_cannot_be_redeemed_against_a_different_redirect(client, public):
    chairman_with_password(client)
    registered = register(client)
    code = consent(client, registered)

    swapped = exchange(client, registered, code, redirect_uri="https://claude.ai/evil")
    assert swapped.status_code == 400
    assert swapped.json()["error"] == "invalid_grant"


def test_an_unregistered_redirect_is_refused_before_the_browser_moves(client, public):
    """Exact match, not prefix — "starts with" is how a redirect allowlist leaks.

    And the refusal renders instead of redirecting: an error page cannot be
    turned into an open redirect.
    """
    chairman_with_password(client)
    registered = register(client)

    for evil in ("https://claude.ai.attacker.example/cb",
                 REDIRECT + "/../evil",
                 "https://attacker.example/cb"):
        page = client.get("/oauth/authorize",
                          params=authorize_params(registered, redirect_uri=evil))
        assert page.status_code == 400, evil
        assert "redirect_uri does not match" in page.text
        assert "location" not in page.headers


def test_registration_refuses_a_redirect_host_outside_the_allowlist(client, public):
    """Registration is unauthenticated by design, so the wall is on what it may say."""
    chairman_with_password(client)
    refused = client.post("/oauth/register", json={
        "client_name": "not claude", "redirect_uris": ["https://attacker.example/cb"]})
    assert refused.status_code == 400
    assert refused.json()["error"] == "invalid_redirect_uri"

    # http is refused off-localhost too: a consent redirect in clear text hands
    # the code to anyone on the path.
    plain = client.post("/oauth/register", json={
        "client_name": "not claude", "redirect_uris": ["http://claude.ai/cb"]})
    assert plain.status_code == 400


def test_scopes_cannot_be_widened_at_the_token_endpoint(client, public):
    """What the chairman ticked is what the grant holds. Nothing later reads a
    scope parameter, so there is nothing to inflate."""
    chairman_with_password(client)
    registered = register(client)
    code = consent(client, registered)

    issued = exchange(client, registered, code, scope="read steer create configure")
    assert issued.status_code == 200
    assert issued.json()["scope"] == "read steer"

    tools = client.post("/mcp", headers=bearer(issued.json()["access_token"]),
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()
    held = {t["name"] for t in tools["result"]["tools"]}
    # Exactly what "read steer" holds — not the wider set the request asked for.
    assert held == READ_TOOLS | STEER_TOOLS


def test_a_token_minted_for_another_resource_is_refused_here(client, public, monkeypatch):
    """RFC 8707 audience binding, and the reason it exists.

    A token stolen from another MCP server — or handed over by a confused one —
    is a perfectly valid bearer string. What stops it is that it names an
    audience, and this is not it.
    """
    from yaaf.core.config import settings
    from yaaf.core.db import db_session
    from yaaf.core.models import McpToken

    chairman_with_password(client)
    registered = register(client)
    issued = exchange(client, registered, consent(client, registered)).json()

    with db_session() as session:
        row = session.query(McpToken).filter(McpToken.kind == "access").one()
        row.resource = "https://someone-elses-server.example/mcp"

    response = client.post("/mcp", headers=bearer(issued["access_token"]),
                           json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response.status_code == 401
    assert "minted for" in response.json()["detail"]
    assert settings().public_base_url in response.json()["detail"]


def test_a_replayed_refresh_token_takes_the_whole_grant_down(client, public):
    """A rotated refresh token presented again is a replay or a theft, and from
    here they look identical. Assume the worse one."""
    chairman_with_password(client)
    registered = register(client)
    first = exchange(client, registered, consent(client, registered)).json()

    second = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": first["refresh_token"],
        "client_id": registered["client_id"]}).json()

    replayed = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": first["refresh_token"],
        "client_id": registered["client_id"]})
    assert replayed.status_code == 400
    assert "revoked" in replayed.json()["error_description"]

    # Not just that one token: everything the grant issued is dead, because we
    # cannot tell which party is the thief.
    for token in (first["access_token"], second["access_token"]):
        assert client.post("/mcp", headers=bearer(token),
                           json={"jsonrpc": "2.0", "id": 1,
                                 "method": "initialize"}).status_code == 401


def test_an_expired_code_is_refused(client, public):
    from datetime import timedelta

    from yaaf.core.db import db_session
    from yaaf.core.models import OAuthCode, utcnow

    chairman_with_password(client)
    registered = register(client)
    code = consent(client, registered)
    with db_session() as session:
        session.query(OAuthCode).one().expires_at = utcnow() - timedelta(seconds=1)

    stale = exchange(client, registered, code)
    assert stale.status_code == 400
    assert "expired" in stale.json()["error_description"]


def test_consent_requires_the_chairmans_password_not_a_token(client, public):
    """A personal token authenticates a person at a terminal, and a dashboard
    session one in a browser. Consent needs the password again, anyway.

    Accepting either here would put the whole design back where it started: a
    credential that is not the moment's proof of a person, granting authority
    to a machine.
    """
    chairman_with_password(client)
    registered = register(client)
    params = authorize_params(registered)

    # No consent cookie, the personal token offered instead — still the login
    # page, not consent.
    page = client.get("/oauth/authorize", params=params, headers=ADMIN)
    assert page.status_code == 200
    assert "Sign in" in page.text
    assert "Approve" not in page.text

    # And approving without signing in issues nothing.
    forged = client.post("/oauth/authorize",
                         data={**params, "action": "approve", "scope_grant": ["configure"]},
                         headers=ADMIN, follow_redirects=False)
    assert forged.status_code == 401

    # A browser signed in to the dashboard is not signed in to consent either:
    # the session cookie is not the consent cookie, by design.
    signed_in = client.post("/auth/login",
                            json={"email": CHAIR_EMAIL, "password": PASSWORD})
    assert signed_in.status_code == 200, signed_in.text
    page = client.get("/oauth/authorize", params=params)
    assert "Sign in" in page.text and "Approve" not in page.text
    forged = client.post("/oauth/authorize",
                         data={**params, "action": "approve", "scope_grant": ["configure"]},
                         follow_redirects=False)
    assert forged.status_code == 401

    wrong = client.post("/oauth/authorize", data={
        **params, "action": "login", "email": "chair@example.com", "password": "wrong-one"})
    assert wrong.status_code == 401
    assert "did not match" in wrong.text


def test_a_consented_grant_follows_chairmanship_and_stops_there(client, public):
    """The consent screen decides scopes, never projects — so what a grant
    reaches has to be read fresh, and has to stop at the chairman it names.

    Both halves are attacked here: a project founded *after* approval is
    reachable without a second consent, and one another chairman runs is not —
    not even when the client posts a `project_grant` field naming it, which is
    what a client written against the old screen would do.
    """
    chairman_with_password(client)
    client.post("/admin/users", json={"email": "other@example.com", "display_name": "Other"},
                headers=ADMIN)
    gamma = make_project(client, slug="gamma", chairman_email="other@example.com")
    registered = register(client)

    params = authorize_params(registered)
    client.post("/oauth/authorize", data={
        **params, "action": "login", "email": "chair@example.com", "password": PASSWORD})
    approved = client.post("/oauth/authorize", data={
        **params, "action": "approve", "scope_grant": ["read"],
        "project_grant": [gamma["id"]]},
        follow_redirects=False)
    import re

    code = re.search(r"code=([^&]+)", approved.headers["location"]).group(1)
    issued = exchange(client, registered, code).json()

    # Founded after the code was issued, and after the token was minted.
    make_project(client, slug="beta")

    listed = client.post("/mcp", headers=bearer(issued["access_token"]), json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "projects", "arguments": {}}}).json()
    text = listed["result"]["content"][0]["text"]
    assert "acme" in text
    assert "beta" in text
    assert "gamma" not in text
