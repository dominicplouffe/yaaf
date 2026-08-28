"""The MCP surface's principal, attacked the way a client would attack it.

Rule 1 of docs/MCP_SERVER.md: an MCP client is never the admin principal. The
attacks here are the ones a model client actually makes by accident and an
attacker makes on purpose — reach for the admin token, guess a token, keep
using a revoked one, or skip the credential entirely.
"""

from __future__ import annotations

import json

from tests.conftest import ADMIN, bearer, make_project


def mint(client, name="laptop", scopes=("read",), projects=(), chairman="chair@example.com"):
    response = client.post("/admin/mcp_clients", json={
        "name": name, "chairman_email": chairman,
        "scopes": list(scopes), "projects": list(projects),
    }, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def rpc(client, token, method, params=None, message_id=1):
    body = {"jsonrpc": "2.0", "id": message_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=bearer(token))


def test_admin_token_is_not_an_mcp_principal(client):
    """The chairman's bearer opens every admin route and must open none of this.

    The whole surface exists so a model can hold the chairman's eyes without
    holding the chairman's signature; accepting the admin token here would
    hand back everything the design refuses.
    """
    make_project(client)
    response = rpc(client, "test-admin-token", "initialize")
    assert response.status_code == 401
    # And the refusal teaches, rather than reading as a typo.
    assert "admin token is not an MCP principal" in response.json()["detail"]


def test_unknown_and_missing_tokens_are_refused(client):
    make_project(client)
    assert rpc(client, "ymt_not-a-real-token", "initialize").status_code == 401
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                     "method": "initialize"}).status_code == 401


def test_revoked_token_stops_working_immediately(client):
    make_project(client)
    minted = mint(client)
    assert rpc(client, minted["token"], "initialize").status_code == 200

    revoked = client.request("DELETE", f"/admin/mcp_clients/{minted['id']}", headers=ADMIN)
    assert revoked.status_code == 200
    response = rpc(client, minted["token"], "initialize")
    assert response.status_code == 401
    assert "revoked" in response.json()["detail"]

    # Revocation is a timestamp, not a delete: the audit trail names this
    # client, so the row it names has to survive to be readable.
    listed = client.get("/admin/mcp_clients", headers=ADMIN).json()
    assert [c["id"] for c in listed] == [minted["id"]]
    assert listed[0]["revoked_at"] is not None


def test_token_value_is_never_readable_after_minting(client):
    """Only the hash is stored, so nothing can hand the plaintext back."""
    make_project(client)
    minted = mint(client)
    listed = client.get("/admin/mcp_clients", headers=ADMIN).json()
    assert "token" not in json.dumps(listed)
    assert minted["token"] not in json.dumps(listed)


def test_handshake_reports_the_scopes_actually_held(client):
    make_project(client)
    minted = mint(client, scopes=("read",))
    result = rpc(client, minted["token"], "initialize").json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "yaaf"
    assert "read" in result["instructions"]
    assert "steer" not in result["instructions"]


def test_agent_run_token_cannot_speak_mcp(client):
    """A run token is an agent's principal. The chairman's surface is not its.

    Nothing in the sandbox should be able to walk in here and read the desk,
    let alone reach the verbs M2 adds — so the two principals stay disjoint.
    """
    from tests.conftest import live_token

    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    assert rpc(client, token, "initialize").status_code == 401


def test_no_sse_stream_and_no_anonymous_probe(client):
    make_project(client)
    minted = mint(client)
    assert client.get("/mcp", headers=bearer(minted["token"])).status_code == 405
    # Unauthenticated, the endpoint does not even admit to being here.
    assert client.get("/mcp").status_code == 401


def test_minting_refuses_unknown_scopes_and_unchaired_projects(client):
    """A grant that cannot be honoured is refused while the chairman is looking.

    Both halves matter: an unknown scope would silently hold nothing, and a
    project this user does not chair would read as a grant and behave as one
    for exactly as long as it took someone to rely on it.
    """
    make_project(client, slug="acme")
    bad_scope = client.post("/admin/mcp_clients", json={
        "name": "laptop", "chairman_email": "chair@example.com",
        "scopes": ["read", "admin"], "projects": [],
    }, headers=ADMIN)
    assert bad_scope.status_code == 422
    assert "admin" in bad_scope.json()["detail"]

    client.post("/admin/users", json={"email": "other@example.com", "display_name": "Other"},
                headers=ADMIN)
    not_chaired = client.post("/admin/mcp_clients", json={
        "name": "laptop", "chairman_email": "other@example.com",
        "scopes": ["read"], "projects": ["acme"],
    }, headers=ADMIN)
    assert not_chaired.status_code == 422
    assert "does not chair" in not_chaired.json()["detail"]
