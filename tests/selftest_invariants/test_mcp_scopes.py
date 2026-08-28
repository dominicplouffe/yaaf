"""Scopes and the project allowlist, attacked over HTTP.

Rule 2 of docs/MCP_SERVER.md: a token holds only the scopes and the projects it
was issued for. The interesting attacks are not "call a tool you were not
given" — they are the quieter ones: name another project by slug, name it by
id, or ask the list tool and count what comes back.
"""

from __future__ import annotations

from tests.conftest import ADMIN, make_project
from tests.selftest_invariants.test_mcp_auth import mint, rpc


def tools_of(client, token) -> set[str]:
    result = rpc(client, token, "tools/list").json()["result"]
    return {tool["name"] for tool in result["tools"]}


def call(client, token, name, arguments=None):
    response = rpc(client, token, "tools/call",
                   {"name": name, "arguments": arguments or {}})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_read_token_sees_only_read_tools(client):
    make_project(client)
    reader = mint(client, scopes=("read",))
    assert tools_of(client, reader["token"]) == {"whoami", "projects"}


def test_out_of_scope_tools_are_absent_not_merely_refused(client):
    """A tool a client can see and cannot use is an invitation to argue.

    Registered here rather than waiting for M3, so the gate is proven by a real
    registration and not by an empty registry that happens to refuse everything.
    """
    from yaaf.server.mcp import registry

    registry.register(
        "pretend_steer", scope="steer", description="a write, for the test",
        input_schema={"type": "object", "properties": {}},
    )(lambda principal, session, arguments: {"ok": True})
    try:
        make_project(client)
        reader = mint(client, scopes=("read",))
        assert "pretend_steer" not in tools_of(client, reader["token"])

        # And calling it anyway answers exactly as an unknown name does: the
        # server does not confirm that the tool exists.
        answer = rpc(client, reader["token"], "tools/call",
                     {"name": "pretend_steer", "arguments": {}}).json()
        assert answer["error"]["code"] == -32601

        steerer = mint(client, name="desk", scopes=("steer",))
        assert "pretend_steer" in tools_of(client, steerer["token"])
        # `read` is implied by every larger scope, and the handshake says so.
        assert {"whoami", "projects"} <= tools_of(client, steerer["token"])
    finally:
        registry.TOOLS.pop("pretend_steer", None)


def test_the_allowlist_filters_the_list_not_just_the_lookup(client):
    """The cheapest way to learn what a chairman runs is to ask for the list."""
    make_project(client, slug="acme")
    make_project(client, slug="beta")
    scoped = mint(client, projects=["acme"])

    payload = call(client, scoped["token"], "projects")["result"]
    slugs = {p["slug"] for p in _json(payload)["projects"]}
    assert slugs == {"acme"}
    assert _json(call(client, scoped["token"], "whoami")["result"])["projects"] == ["acme"]


def test_a_project_outside_the_allowlist_is_refused_by_slug_and_by_id(client):
    make_project(client, slug="acme")
    beta = make_project(client, slug="beta")
    scoped = mint(client, projects=["acme"])

    def refusal(ref: str) -> str:
        result = call(client, scoped["token"], "projects", {"project": ref})["result"]
        assert result["isError"] is True, result
        return result["content"][0]["text"]

    absent = refusal("no-such-thing")
    assert "is reachable by this client" in absent
    for ref in ("beta", beta["id"]):
        # Word for word what a project that does not exist gets, with only the
        # reference swapped — so the surface never becomes an oracle for what
        # else this chairman runs.
        assert refusal(ref) == absent.replace("no-such-thing", ref)


def test_an_empty_allowlist_still_stops_at_chairmanship(client):
    """'Every project' means every project this user chairs — not every project.

    A token with no allowlist is the convenient default, so it is the one worth
    checking: the second gate has to hold on its own.
    """
    make_project(client, slug="acme")
    client.post("/admin/users", json={"email": "other@example.com", "display_name": "Other"},
                headers=ADMIN)
    other = mint(client, name="others-laptop", chairman="other@example.com", projects=())

    payload = _json(call(client, other["token"], "projects")["result"])
    assert payload["projects"] == []
    assert payload["count"] == 0


def test_scope_grants_do_not_leak_between_clients(client):
    """Two tokens, one chairman: widening one must not widen the other."""
    make_project(client, slug="acme")
    reader = mint(client, name="reader", scopes=("read",))
    mint(client, name="operator", scopes=("read", "steer", "create"))
    assert tools_of(client, reader["token"]) == {"whoami", "projects"}


def _json(result: dict) -> dict:
    import json

    assert result.get("isError") is False, result
    return json.loads(result["content"][0]["text"])
