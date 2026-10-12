"""The MCP surface's principal, attacked the way a client would attack it.

Rule 1 of docs/MCP_SERVER.md: an MCP client is never a person's own principal.
The attacks here are the ones a model client actually makes by accident and an
attacker makes on purpose — reach for its person's own token, guess a token,
keep using a revoked one, or skip the credential entirely.
"""

from __future__ import annotations

import json

from tests.conftest import (
    ADMIN,
    CHAIR_EMAIL,
    CHAIR_TOKEN,
    bearer,
    headers_of,
    make_project,
    person,
)


def mint(client, name="laptop", scopes=("read",), chairman=CHAIR_EMAIL):
    """A model client for `chairman`, minted by them: nobody mints one in
    somebody else's name, an instance admin included."""
    headers = ADMIN if chairman == CHAIR_EMAIL else headers_of(person(chairman))
    response = client.post("/admin/mcp_clients", json={
        "name": name, "scopes": list(scopes),
    }, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def foreign_project(client, slug="beta", email="other@example.com"):
    """A project this suite's chairman does not chair.

    Reach follows chairmanship and nothing else, so this is now the only way to
    build a project an MCP grant cannot see — and the shape every "refused in
    the same words a nonexistent one gets" attack needs.
    """
    client.post("/admin/users", json={"email": email, "display_name": "Other"},
                headers=ADMIN)
    return make_project(client, slug=slug, chairman_email=email)


def rpc(client, token, method, params=None, message_id=1):
    body = {"jsonrpc": "2.0", "id": message_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=bearer(token))


def test_a_persons_own_token_is_not_an_mcp_principal(client):
    """A personal token opens everything its person may do, and must open none
    of this.

    The whole surface exists so a model can hold the chairman's eyes without
    holding the chairman's signature; accepting the person's own credential here
    would hand back everything the design refuses. (This test used to be about
    the admin token. The token was retired; the rule was not.)
    """
    make_project(client)
    response = rpc(client, CHAIR_TOKEN, "initialize")
    assert response.status_code == 401
    # And the refusal teaches, rather than reading as a typo.
    assert "personal token is not an MCP principal" in response.json()["detail"]
    # The retired bearer is nothing at all here, as everywhere.
    assert rpc(client, "test-admin-token", "initialize").status_code == 401


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
    let alone reach the verbs M3 adds — so the two principals stay disjoint.
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


def test_minting_refuses_an_unknown_scope(client):
    """A grant that cannot be honoured is refused while the chairman is looking.

    An unknown scope would hold nothing and say nothing, for exactly as long as
    it took someone to rely on it.
    """
    make_project(client, slug="acme")
    bad_scope = client.post("/admin/mcp_clients", json={
        "name": "laptop", "chairman_email": "chair@example.com",
        "scopes": ["read", "admin"],
    }, headers=ADMIN)
    assert bad_scope.status_code == 422
    assert "admin" in bad_scope.json()["detail"]


def test_the_handshake_answers_in_the_version_the_client_asked_for(client):
    """A client handed a version it did not ask for may simply hang up.

    The failure is silent from the user's side — "could not connect", nothing
    to act on — so the negotiation is worth an attack of its own.
    """
    make_project(client)
    minted = mint(client)

    for asked in ("2025-06-18", "2025-03-26"):
        result = rpc(client, minted["token"], "initialize",
                     {"protocolVersion": asked, "capabilities": {},
                      "clientInfo": {"name": "test", "version": "1"}}).json()["result"]
        assert result["protocolVersion"] == asked

    # A version we do not implement gets our latest, not a claim we speak it:
    # the client then decides. 2024-11-05 is HTTP+SSE and we open no stream.
    for asked in ("2024-11-05", "1999-01-01", None):
        params = {"capabilities": {}} if asked is None else {"protocolVersion": asked}
        result = rpc(client, minted["token"], "initialize", params).json()["result"]
        assert result["protocolVersion"] == "2025-06-18"
