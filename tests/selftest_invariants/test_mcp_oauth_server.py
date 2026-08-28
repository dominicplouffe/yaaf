"""The OAuth ladder, walked end to end by yaaf's own OAuth client.

claude.ai's connector offers no field for a bearer header, so OAuth is the only
door into the browser — and the browser is a requirement. This is that door,
tested without claude.ai in the loop.

The harness is the nice part: `yaaf/server/mcp_oauth.py` already walks this
exact ladder as a *client*, to reach hosted MCP data sources, and it carries a
`transport=` seam for tests. So yaaf's OAuth client is pointed at yaaf's OAuth
server, in-process and offline, and every rung it needs has to be there.

What this proves is conformance. It does not prove claude.ai accepts it — that
needs one real connection against a public deployment, and it is the last
unticked line of M2 for exactly that reason.
"""

from __future__ import annotations

from tests.conftest import ADMIN, bearer
from tests.oauth_flow import (
    Forwarding,
    chairman_with_password,
    consent,
    exchange,
    register,
)
from yaaf.server import mcp_oauth


def test_our_own_oauth_client_can_discover_this_server(client, public):
    """The ladder, walked by the code that walks it for real data sources."""
    chairman_with_password(client)

    found = mcp_oauth.discover(f"{public}/mcp", transport=Forwarding(client))
    assert found.authorization_endpoint == f"{public}/oauth/authorize"
    assert found.token_endpoint == f"{public}/oauth/token"
    assert found.registration_endpoint == f"{public}/oauth/register"
    assert found.resource == f"{public}/mcp"
    assert set(found.scopes_supported) >= {"read", "steer"}


def test_an_unauthenticated_call_says_where_to_get_a_token(client, public):
    """Without this header, discovery is guesswork — RFC 9728 §5.1."""
    chairman_with_password(client)
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response.status_code == 401
    header = response.headers["www-authenticate"]
    assert 'resource_metadata="http://testserver/.well-known/oauth-protected-resource/mcp"' \
        in header

    metadata = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert metadata["resource"] == f"{public}/mcp"
    assert metadata["authorization_servers"] == [public]
    # Unauthenticated by spec: a client that cannot read this cannot learn how
    # to authenticate.
    assert client.get("/.well-known/oauth-authorization-server").status_code == 200


def test_registration_consent_and_token_get_a_working_client(client, public):
    chairman_with_password(client)
    registered = register(client)
    assert registered["token_endpoint_auth_method"] == "none"

    code = consent(client, registered)
    issued = exchange(client, registered, code)
    assert issued.status_code == 200, issued.text
    body = issued.json()
    assert body["token_type"] == "Bearer"
    assert body["scope"] == "read steer"
    assert body["expires_in"] == 3600

    # And the access token works on the surface it was minted for.
    handshake = client.post("/mcp", headers=bearer(body["access_token"]),
                            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert handshake.status_code == 200
    assert "steer" in handshake.json()["result"]["instructions"]


def test_the_grant_is_attributed_to_the_person_and_the_client(client, public):
    """Rule 3 starts here: the record says a machine carried the signature."""
    from yaaf.core.db import db_session
    from yaaf.core.models import AuditLog

    chairman_with_password(client)
    registered = register(client)
    exchange(client, registered, consent(client, registered))

    with db_session() as session:
        row = session.query(AuditLog).filter(
            AuditLog.action == "mcp_client.consent").one()
        assert row.actor.startswith("user:")
        assert "via mcp:claude.ai" in row.actor
        assert row.detail["scopes"] == ["read", "steer"]


def test_refresh_rotates_and_the_old_token_keeps_working_until_it_expires(client, public):
    chairman_with_password(client)
    registered = register(client)
    first = exchange(client, registered, consent(client, registered)).json()

    refreshed = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": first["refresh_token"],
        "client_id": registered["client_id"]})
    assert refreshed.status_code == 200, refreshed.text
    second = refreshed.json()
    assert second["access_token"] != first["access_token"]
    assert second["refresh_token"] != first["refresh_token"]

    for token in (first["access_token"], second["access_token"]):
        assert client.post("/mcp", headers=bearer(token),
                           json={"jsonrpc": "2.0", "id": 1,
                                 "method": "initialize"}).status_code == 200


def test_revoking_the_grant_kills_the_tokens_it_issued(client, public):
    """A revoke button that leaves the string working is a button that lies."""
    chairman_with_password(client)
    registered = register(client)
    issued = exchange(client, registered, consent(client, registered)).json()

    grants = client.get("/admin/mcp_clients", headers=ADMIN).json()
    grant = next(g for g in grants if g["name"] == "claude.ai")
    assert client.request("DELETE", f"/admin/mcp_clients/{grant['id']}",
                          headers=ADMIN).status_code == 200

    assert client.post("/mcp", headers=bearer(issued["access_token"]),
                       json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}
                       ).status_code == 401
    refused = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": issued["refresh_token"],
        "client_id": registered["client_id"]})
    assert refused.status_code == 400
    assert refused.json()["error"] == "invalid_grant"


def test_a_cli_token_still_works_after_the_token_table_split(client, public):
    """M0's tokens are now rows in the same table OAuth writes to.

    One validation path for both is the point — a rule proven for the browser
    holds for the terminal — but only if the terminal did not break on the way.
    """
    chairman_with_password(client)
    minted = client.post("/admin/mcp_clients", json={
        "name": "dom-laptop", "chairman_email": "chair@example.com",
        "scopes": ["read"], "projects": []}, headers=ADMIN).json()
    assert client.post("/mcp", headers=bearer(minted["token"]),
                       json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}
                       ).status_code == 200
