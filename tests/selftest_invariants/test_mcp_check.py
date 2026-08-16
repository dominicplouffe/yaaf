"""Invariant selftests: a data source check answers the only question that
matters — would an agent get tools here?

The check this replaced did a GET and called any HTTP status a pass. A healthy
MCP endpoint answers GET with 405 (it wants POST), so a working source and a
wrong path and a rejected token all reported the same cheerful nonsense, and a
green badge stopped meaning anything. Every case below must now be told apart."""

from __future__ import annotations

import json

import httpx

from yaaf.server.api.admin import _check_data_source

URL = "https://data.example.com:3845/mcp"


def _mcp(tools: list[str], *, server: str = "Trellis", version: str = "3.4.1",
         session_id: str | None = "sess-1"):
    """A polite MCP server: JSON-RPC over POST, tools on request."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("method") == "initialize":
            return httpx.Response(200, json={
                "jsonrpc": "2.0", "id": 1,
                "result": {"protocolVersion": "2025-06-18",
                           "serverInfo": {"name": server, "version": version}},
            }, headers={"mcp-session-id": session_id} if session_id else {})
        if body.get("method") == "tools/list":
            return httpx.Response(200, json={
                "jsonrpc": "2.0", "id": 2,
                "result": {"tools": [{"name": name} for name in tools]},
            })
        return httpx.Response(202)
    return httpx.MockTransport(handler)


def test_a_working_server_reports_the_tools_an_agent_would_get():
    check = _check_data_source(URL, None, transport=_mcp(["search", "fetch"]))
    assert check["ok"] is True
    assert check["detail"] == "MCP server Trellis 3.4.1 ready — 2 tool(s): search, fetch"


def test_a_long_toolset_is_summarised_not_dumped():
    check = _check_data_source(URL, None, transport=_mcp([f"t{i}" for i in range(21)]))
    assert check["ok"] is True
    assert check["detail"].endswith("21 tool(s): t0, t1, t2, t3, +17 more")


def test_sse_framed_answers_are_understood():
    """Servers may answer a POST with a one-event stream instead of JSON."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        payload = ({"jsonrpc": "2.0", "id": 1,
                    "result": {"serverInfo": {"name": "Streamy", "version": "9"}}}
                   if body.get("method") == "initialize" else
                   {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "only"}]}})
        return httpx.Response(200, text=f"event: message\ndata: {json.dumps(payload)}\n\n",
                              headers={"content-type": "text/event-stream"})

    check = _check_data_source(URL, None, transport=httpx.MockTransport(handler))
    assert check["ok"] is True and "Streamy 9 ready — 1 tool(s): only" in check["detail"]


def test_the_bearer_token_travels_on_every_call():
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return _mcp(["one"]).handler(request)

    _check_data_source(URL, "tok-123", transport=httpx.MockTransport(handler))
    assert seen and all(header == "Bearer tok-123" for header in seen)


def test_a_refused_credential_says_so_instead_of_passing():
    transport = httpx.MockTransport(lambda request: httpx.Response(401, text="nope"))
    check = _check_data_source(URL, "wrong", transport=transport)
    assert check["ok"] is False
    assert "refused the credential" in check["detail"] and "401" in check["detail"]


def test_a_wrong_path_is_named_as_a_wrong_path():
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    check = _check_data_source(URL, None, transport=transport)
    assert check["ok"] is False and "no MCP endpoint at that path" in check["detail"]


def test_the_old_false_pass_is_gone():
    """A plain web server answering 405 to a POST is not a data source — this
    is the exact shape that used to report 'reachable (HTTP 405)' and pass."""
    transport = httpx.MockTransport(lambda request: httpx.Response(405))
    check = _check_data_source(URL, None, transport=transport)
    assert check["ok"] is False and "did not accept an MCP handshake" in check["detail"]


def test_a_web_page_is_not_an_mcp_server():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>hello</html>"))
    check = _check_data_source(URL, None, transport=transport)
    assert check["ok"] is False and "not with MCP" in check["detail"]


def test_a_jsonrpc_refusal_is_quoted_back():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": -32600, "message": "unsupported protocol version"},
    }))
    check = _check_data_source(URL, None, transport=transport)
    assert check["ok"] is False
    assert "unsupported protocol version" in check["detail"]


def test_a_server_with_no_tools_is_not_a_working_source():
    """It handshakes, so the address and token are right — but an agent that
    mounts it finds nothing to call, and the badge must not claim otherwise."""
    check = _check_data_source(URL, None, transport=_mcp([]))
    assert check["ok"] is False and "offers no tools" in check["detail"]


def test_nothing_listening_stays_a_plain_english_answer():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    check = _check_data_source(URL, None, transport=httpx.MockTransport(handler))
    assert check["ok"] is False and "nothing is listening" in check["detail"]
