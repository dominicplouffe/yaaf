"""Values go in and never come back out — and every route has been decided about.

Two invariants, both about the same worry.

The first is direct: no scope, no argument and no tool returns a credential
value. `configure` can *write* one, because storing a credential is a
chairman's setup step; reading one back into a model's context is an
exfiltration wearing a tool call.

The second is structural, and it is the one that keeps the first true next
month. `/admin` grows. A tool list that merely *lacks* a route looks the same
as one that has not caught up, so `surface.py` classifies every route — reached
by a tool, or withheld with a reason — and the test below fails on the first
one that is neither. The failure message is the design review.
"""

from __future__ import annotations

import json

from tests.conftest import ADMIN, make_project
from tests.selftest_invariants.test_mcp_auth import mint, rpc
from tests.selftest_invariants.test_mcp_confirm import call
from yaaf.server.mcp import surface

SLUG = "acme"
VALUE = "sk-ant-not-a-real-key-000000"


def configurer(client, name="setup", projects=()):
    return mint(client, name=name, scopes=("configure",), projects=projects)


def live_admin_routes() -> set[str]:
    from yaaf.server.app import create_app

    spec = create_app().openapi()
    return {f"{method.upper()} {path}"
            for path, operations in spec["paths"].items() if path.startswith("/admin")
            for method in operations}


def test_every_admin_route_has_been_decided_about(client):
    """The one that fails when somebody adds a route and forgets this surface."""
    live = live_admin_routes()
    missing = surface.unclassified(live)
    assert not missing, (
        "these admin routes are neither reached by an MCP tool nor withheld with a "
        "reason — add each to REACHED or WITHHELD in yaaf/server/mcp/surface.py:\n  "
        + "\n  ".join(sorted(missing)))

    fossils = surface.stale(live)
    assert not fossils, (
        "these classifications name routes that no longer exist, and read as decisions "
        "rather than fossils:\n  " + "\n  ".join(sorted(fossils)))


def test_the_reasons_are_reasons(client):
    """A withheld route without a reason is a denylist entry nobody can audit."""
    for route, reason in surface.WITHHELD.items():
        assert len(reason) > 15, route
        assert reason.endswith("."), route


def test_no_tool_at_any_scope_returns_a_credential_value(client):
    project = make_project(client, slug=SLUG)
    stored = client.post("/admin/secrets", json={
        "project_id": project["id"], "name": "wordpress", "value": VALUE,
        "kind": "service", "role_scopes": ["worker"]}, headers=ADMIN)
    assert stored.status_code == 200, stored.text

    everything = mint(client, name="everything",
                      scopes=("read", "steer", "create", "configure"))["token"]
    listed = rpc(client, everything, "tools/list").json()["result"]["tools"]

    seen = []
    for tool in listed:
        arguments = {}
        if "project" in tool["inputSchema"].get("properties", {}):
            arguments["project"] = SLUG
        answer = rpc(client, everything, "tools/call",
                     {"name": tool["name"], "arguments": arguments}).json()
        seen.append(json.dumps(answer))
    assert VALUE not in "\n".join(seen)


def test_a_credential_can_be_written_and_never_read(client):
    """The asymmetry, stated as a test: in yes, out no."""
    make_project(client, slug=SLUG)
    token = configurer(client)["token"]

    failed, preview = call(client, token, "credential", {
        "project": SLUG, "verb": "save", "name": "wordpress", "value": VALUE,
        "roles": ["worker"]})
    assert failed is False
    # Not even the preview echoes it — that is the turn a person reads.
    assert VALUE not in json.dumps(preview)
    assert preview["about_to"]["value_supplied"] is True

    failed, saved = call(client, token, "credential", {
        "project": SLUG, "verb": "save", "name": "wordpress", "value": VALUE,
        "roles": ["worker"], "confirm": preview["confirm"]})
    assert failed is False
    assert VALUE not in json.dumps(saved)

    # It really was stored — the browser can read it back; this surface cannot.
    rows = client.get("/admin/secrets", headers=ADMIN).json()
    row = next(r for r in rows if r["name"] == "wordpress")
    assert client.get(f"/admin/secrets/{row['id']}/value",
                      headers=ADMIN).json()["value"] == VALUE

    failed, listing = call(client, token, "credential", {"project": SLUG, "verb": "list"})
    assert failed is False
    assert {c["name"] for c in listing["credentials"]} == {"wordpress"}
    assert VALUE not in json.dumps(listing)


def test_the_audit_row_does_not_carry_the_value_either(client):
    """A secret in a log is a secret. The record says what happened, not what it was."""
    from yaaf.core.db import db_session
    from yaaf.core.models import AuditLog

    make_project(client, slug=SLUG)
    token = configurer(client)["token"]
    _, preview = call(client, token, "credential", {
        "project": SLUG, "verb": "save", "name": "wordpress", "value": VALUE,
        "roles": ["worker"]})
    call(client, token, "credential", {
        "project": SLUG, "verb": "save", "name": "wordpress", "value": VALUE,
        "roles": ["worker"], "confirm": preview["confirm"]})

    with db_session() as session:
        rows = [json.dumps(r.detail) for r in session.query(AuditLog).all()]
    assert rows
    assert VALUE not in "\n".join(rows)


def test_a_data_source_token_is_scrubbed_from_the_record_too(client):
    from yaaf.core.db import db_session
    from yaaf.core.models import AuditLog

    make_project(client, slug=SLUG)
    token = configurer(client)["token"]
    arguments = {"project": SLUG, "verb": "add", "name": "warehouse",
                 "url": "https://mcp.example.com/mcp", "roles": ["worker"],
                 "bearer": VALUE, "access": "bulk"}

    _, preview = call(client, token, "data_source", arguments)
    assert VALUE not in json.dumps(preview)
    call(client, token, "data_source", {**arguments, "confirm": preview["confirm"]})

    with db_session() as session:
        rows = [json.dumps(r.detail) for r in session.query(AuditLog).all()]
    assert VALUE not in "\n".join(rows)

    failed, sources = call(client, token, "data_source", {"project": SLUG, "verb": "list"})
    assert failed is False
    assert {s["name"] for s in sources["sources"]} == {"warehouse"}
    assert VALUE not in json.dumps(sources)


def test_an_amendment_needs_a_reason_and_a_confirmation(client):
    """The largest thing this scope can do that costs nothing."""
    make_project(client, slug=SLUG)
    token = configurer(client)["token"]

    failed, message = call(client, token, "amend", {
        "project": SLUG, "verb": "propose", "changes": {"max_concurrent_runs": 4},
        "reason": "why not"})
    assert failed is True
    assert "ten characters" in message

    arguments = {"project": SLUG, "verb": "propose",
                 "changes": {"max_concurrent_runs": 4},
                 "reason": "the box has more cores than this project is using"}
    failed, preview = call(client, token, "amend", arguments)
    assert failed is False
    assert preview["about_to"]["changes"] == {"max_concurrent_runs": 4}
    assert "every agent in this project" in preview["about_to"]["warning"]

    failed, proposed = call(client, token, "amend",
                            {**arguments, "confirm": preview["confirm"]})
    assert failed is False, proposed
    listed = client.get(f"/admin/projects/{make_id(client, SLUG)}/amendments",
                        headers=ADMIN).json()
    assert listed


def test_an_invented_rule_is_refused_by_the_platform_not_the_tool(client):
    """`amend` adds no validation of its own — the constitution's schema is the
    one definition, so the refusal is the same one the Settings screen gives."""
    make_project(client, slug=SLUG)
    token = configurer(client)["token"]
    arguments = {"project": SLUG, "verb": "propose",
                 "changes": {"reporting_cadence": 4},
                 "reason": "this rule does not exist and should be refused"}

    _, preview = call(client, token, "amend", arguments)
    failed, message = call(client, token, "amend",
                           {**arguments, "confirm": preview["confirm"]})
    assert failed is True
    assert "reporting_cadence" in message


def test_configure_is_its_own_scope(client):
    make_project(client, slug=SLUG)
    for scopes in (("read",), ("steer",), ("create",)):
        token = mint(client, name=f"c-{scopes[0]}", scopes=scopes)["token"]
        for tool in ("credential", "data_source", "amend", "project_configure",
                     "billing_check"):
            answer = rpc(client, token, "tools/call",
                         {"name": tool, "arguments": {"project": SLUG}}).json()
            assert answer["error"]["code"] == -32601, (scopes, tool)


def make_id(client, slug: str) -> str:
    return next(p["id"] for p in client.get("/observe/projects", headers=ADMIN).json()
                if p["slug"] == slug)
