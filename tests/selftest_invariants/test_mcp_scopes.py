"""Scopes and reach, attacked over HTTP.

Rule 2 of docs/MCP_SERVER.md: a token holds only the scopes it was issued for,
and reaches only what its chairman chairs. The interesting attacks are not
"call a tool you were not given" — they are the quieter ones: name someone
else's project by slug, name it by id, or ask the list tool and count what
comes back.
"""

from __future__ import annotations

from tests.conftest import ADMIN, make_project
from tests.selftest_invariants.test_mcp_auth import foreign_project, mint, rpc

#: Every tool the `read` scope holds. Spelled out rather than derived from the
#: registry: a test that asks the code what it registered cannot notice a write
#: tool registered under the wrong scope, which is the mistake worth catching.
READ_TOOLS = {"whoami", "projects", "desk", "project", "plan", "spend", "work",
              "proof", "org", "channels", "inbox", "files", "record", "receipt"}
#: And every verb `steer` adds on top of them.
STEER_TOOLS = {"boardroom_say", "approval_decide", "requirement_decide",
               "commitment_decide", "review_decide", "audit_decide", "flag_ack",
               "lifecycle"}


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
    assert tools_of(client, reader["token"]) == READ_TOOLS


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
        assert tools_of(client, steerer["token"]) >= READ_TOOLS
    finally:
        registry.TOOLS.pop("pretend_steer", None)


def test_a_project_founded_after_the_grant_needs_no_new_grant(client):
    """The reason the allowlist went: reach is read now, not frozen at consent.

    A chairman who founds a project on Tuesday should not have to walk a
    consent screen again on Wednesday to see it, and the grant that already
    names him should not have to be reissued to learn it exists.
    """
    make_project(client, slug="acme")
    scoped = mint(client)
    assert _slugs(client, scoped["token"]) == {"acme"}

    make_project(client, slug="beta")
    assert _slugs(client, scoped["token"]) == {"acme", "beta"}
    assert _json(call(client, scoped["token"], "whoami")["result"])["projects"] == [
        "acme", "beta"]
    # And addressable, not merely listed.
    one = _json(call(client, scoped["token"], "projects", {"project": "beta"})["result"])
    assert [p["slug"] for p in one["projects"]] == ["beta"]


def test_another_chairmans_project_is_refused_by_slug_and_by_id(client):
    """Chairmanship is the only gate left, so it carries the oracle attack."""
    make_project(client, slug="acme")
    beta = foreign_project(client, slug="beta")
    scoped = mint(client)

    def refusal(ref: str) -> str:
        result = call(client, scoped["token"], "projects", {"project": ref})["result"]
        assert result["isError"] is True, result
        return result["content"][0]["text"]

    absent = refusal("no-such-thing")
    assert "is reachable by this client" in absent
    for ref in ("beta", beta["id"]):
        # Word for word what a project that does not exist gets, with only the
        # reference swapped — so the surface never becomes an oracle for what
        # other people run.
        assert refusal(ref) == absent.replace("no-such-thing", ref)


def test_reach_stops_at_chairmanship(client):
    """'Every project' means every project this user chairs — not every project.

    Every grant is now the convenient default, so the one gate under it has to
    hold on its own.
    """
    make_project(client, slug="acme")
    client.post("/admin/users", json={"email": "other@example.com", "display_name": "Other"},
                headers=ADMIN)
    other = mint(client, name="others-laptop", chairman="other@example.com")

    payload = _json(call(client, other["token"], "projects")["result"])
    assert payload["projects"] == []
    assert payload["count"] == 0


def test_scope_grants_do_not_leak_between_clients(client):
    """Two tokens, one chairman: widening one must not widen the other."""
    make_project(client, slug="acme")
    reader = mint(client, name="reader", scopes=("read",))
    mint(client, name="operator", scopes=("read", "steer", "create"))
    assert tools_of(client, reader["token"]) == READ_TOOLS


def _slugs(client, token) -> set[str]:
    return {p["slug"] for p in _json(call(client, token, "projects")["result"])["projects"]}


def _json(result: dict) -> dict:
    import json

    assert result.get("isError") is False, result
    return json.loads(result["content"][0]["text"])
