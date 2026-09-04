"""Invariant selftests: a data source's tool allowlist, and the one way past it.

A data source can name the tools it may be called with. Without that, `data_fetch`
validates the *server* name and passes the *tool* name straight to the vendor — so
a source connected for reading serves its write tools too, and "read-only" is a
sentence in a prompt rather than a rule. These tests attack the rule over HTTP the
way an agent would.

The way past it is a TOOL_REQUEST approval carrying a grant: source, tool, and the
SHA-256 of the arguments the chairman decided on. One call, one approval. The tests
that matter most here are the ones that try to spend an approval on something other
than what it was approved for — different tool, different arguments, twice — because
those are the shapes an agent under pressure actually produces.

The allowlist itself is enforced inside the sandbox (`data_fetch` refuses before it
resolves a path or reaches the vendor), so its unit is exercised directly; everything
the *server* owns is attacked through the API with a real run token.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
from types import SimpleNamespace

import pytest

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Approval, ApprovalStatus, Project

SOURCE = "ads"
READS = ["get_stats", "get_table_fields"]
WRITE = "create_campaigns"


def _attach(client, project_id: str, tools: list[str] | None, access: str = "bulk") -> dict:
    body = {"name": SOURCE, "url": "https://vendor.example.com/mcp",
            "bearer": "vendor-token", "roles": ["worker"], "access": access}
    if tools is not None:
        body["tools"] = tools
    response = client.post(f"/admin/projects/{project_id}/mcp_servers", json=body,
                           headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def _source(project_id: str) -> dict:
    with db_session() as session:
        integrations = session.get(Project, project_id).integrations or {}
    return next(s for s in integrations["mcp_servers"] if s["name"] == SOURCE)


def _agent(client, project: dict) -> dict:
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    return bearer(token)


def _digest(arguments: dict) -> str:
    return hashlib.sha256(
        json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ── the allowlist is stored, and survives an edit that does not mention it ──────


def test_a_source_saved_with_an_allowlist_keeps_it(client):
    project = make_project(client)
    _attach(client, project["id"], READS)
    assert _source(project["id"])["tools"] == sorted(READS)


def test_a_source_saved_without_one_is_unrestricted(client):
    project = make_project(client)
    _attach(client, project["id"], None)
    assert "tools" not in _source(project["id"])


def test_regranting_roles_does_not_disarm_the_allowlist(client):
    """The bug this shape exists to prevent: an edit that only changes roles must
    not silently turn enforcement off, the same way it must not drop the token."""
    project = make_project(client)
    _attach(client, project["id"], READS)
    client.post(f"/admin/projects/{project['id']}/mcp_servers",
                json={"name": SOURCE, "url": "https://vendor.example.com/mcp",
                      "roles": ["worker", "ceo"], "access": "bulk"},
                headers=ADMIN)
    assert _source(project["id"])["tools"] == sorted(READS)


def test_an_empty_allowlist_is_the_strict_end_not_a_way_to_clear_it(client):
    project = make_project(client)
    _attach(client, project["id"], [])
    assert _source(project["id"])["tools"] == []


# ── data_fetch refuses an ungranted tool before it does anything ────────────

pytest.importorskip("claude_agent_sdk")


def _entry(monkeypatch, tmp_path, tools, access="bulk"):
    """The sandbox module, reloaded with the env it reads at import."""
    monkeypatch.setenv("YAAF_API_BASE_URL", "http://api:8080")
    monkeypatch.setenv("YAAF_API_TOKEN", "test-token")
    monkeypatch.setenv("YAAF_RUN_ID", "run_test")
    monkeypatch.setenv("YAAF_TOOL_PERMISSIONS", "{}")
    monkeypatch.setenv("MCP_ADS_URL", "https://vendor.example.com/mcp")
    import yaaf.runner.sandbox_entry as mod

    entry = importlib.reload(mod)
    spec = {"name": SOURCE, "url_env": "MCP_ADS_URL", "access": access}
    if tools is not None:
        spec["tools"] = tools
    entry.PERMS = {"mcp_servers": [spec]}
    workspace = tmp_path / "workspace"
    (workspace / ".yaaf").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(entry, "WORKSPACE", workspace)
    monkeypatch.setattr(entry, "STAGING", workspace / ".yaaf")
    return entry


def _result(text="{}"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
                           structuredContent=None, isError=False)


def _fetch(entry, monkeypatch, args, reached=None, platform=None):
    async def fake_call_tool(config, tool_name, arguments):  # noqa: ARG001
        if reached is not None:
            reached.append((tool_name, arguments))
        return _result()

    monkeypatch.setattr(entry, "_mcp_call_tool", fake_call_tool)
    monkeypatch.setattr(entry, "_call", platform or (
        lambda *a, **k: pytest.fail("no platform call was expected")))
    reply = asyncio.run(entry.t_data_fetch.handler({"server": SOURCE, **args}))
    return json.loads(reply["content"][0]["text"])


def test_an_ungranted_tool_is_refused_naming_what_is_callable(monkeypatch, tmp_path):
    entry = _entry(monkeypatch, tmp_path, READS)
    reached = []
    out = _fetch(entry, monkeypatch, {"tool": WRITE, "arguments": {"budget": 5}},
                 reached=reached)

    assert out["status"] == 403
    assert "not one of the tools" in out["body"]["error"]
    assert "get_stats" in out["body"]["error"]
    assert "data_write_request" in out["body"]["error"]
    # Refused before the vendor was reached: a denied write must not have happened.
    assert reached == []


def test_a_granted_tool_needs_no_approval(monkeypatch, tmp_path):
    entry = _entry(monkeypatch, tmp_path, READS)
    reached = []
    out = _fetch(entry, monkeypatch, {"tool": "get_stats", "arguments": {}},
                 reached=reached)
    assert out["status"] == 200, out
    assert [t for t, _ in reached] == ["get_stats"]


def test_an_unrestricted_source_still_calls_anything(monkeypatch, tmp_path):
    """Every install that predates the allowlist keeps behaving exactly as it did."""
    entry = _entry(monkeypatch, tmp_path, None)
    reached = []
    _fetch(entry, monkeypatch, {"tool": WRITE, "arguments": {"budget": 5}}, reached=reached)
    assert [t for t, _ in reached] == [WRITE]


def test_discovery_says_which_tools_need_an_approval(monkeypatch, tmp_path):
    """So an agent plans around the rule instead of finding it one refusal at a time."""
    entry = _entry(monkeypatch, tmp_path, READS)

    async def fake_list(config):  # noqa: ARG001
        return SimpleNamespace(tools=[
            SimpleNamespace(name="get_stats", description="read", inputSchema={}),
            SimpleNamespace(name=WRITE, description="write", inputSchema={}),
        ])

    monkeypatch.setattr(entry, "_mcp_list_tools", fake_list)
    monkeypatch.setattr(entry, "_call", lambda *a, **k: pytest.fail("no platform call"))
    reply = asyncio.run(entry.t_data_fetch.handler({"server": SOURCE}))
    body = json.loads(reply["content"][0]["text"])["body"]

    flags = {t["name"]: t["needs_approval"] for t in body["tools"]}
    assert flags == {"get_stats": False, WRITE: True}
    assert "data_write_request" in body["note"]


# ── the sandbox spends server-side, and refuses when the server does ────────────


def test_data_fetch_spends_the_approval_before_calling(monkeypatch, tmp_path):
    entry = _entry(monkeypatch, tmp_path, READS)
    spent, reached = [], []

    def platform(method, path, payload=None, params=None):  # noqa: ARG001
        spent.append((path, payload))
        return {"status": 200, "body": {"spent": True}}

    out = _fetch(entry, monkeypatch,
                 {"tool": WRITE, "arguments": {"budget": 5}, "approval_id": "apr_1"},
                 reached=reached, platform=platform)

    assert out["status"] == 200, out
    assert spent == [("/agent/data_writes/apr_1/spend",
                      {"source": SOURCE, "tool": WRITE, "arguments": {"budget": 5}})]
    assert [t for t, _ in reached] == [WRITE]


def test_a_refused_spend_stops_the_call(monkeypatch, tmp_path):
    entry = _entry(monkeypatch, tmp_path, READS)
    reached = []
    out = _fetch(entry, monkeypatch,
                 {"tool": WRITE, "arguments": {"budget": 5}, "approval_id": "apr_1"},
                 reached=reached,
                 platform=lambda *a, **k: {"status": 409,
                                           "body": {"detail": "already spent"}})

    assert out["status"] == 409
    # The refusal reached the agent AND the vendor was never called.
    assert reached == []


# ── an inline source is granted tool by tool, not server-wide ──────────────


def test_an_inline_source_with_an_allowlist_is_not_granted_server_wide(monkeypatch, tmp_path):
    """Otherwise the server-level grant hands an inline source exactly the tools
    its allowlist exists to refuse."""
    entry = _entry(monkeypatch, tmp_path, READS, access="inline")
    allowed = entry._allowed_tools()
    assert f"mcp__{SOURCE}" not in allowed
    assert f"mcp__{SOURCE}__get_stats" in allowed
    assert f"mcp__{SOURCE}__{WRITE}" not in allowed


def test_an_inline_source_without_one_keeps_the_server_wide_grant(monkeypatch, tmp_path):
    entry = _entry(monkeypatch, tmp_path, None, access="inline")
    assert f"mcp__{SOURCE}" in entry._allowed_tools()


def test_data_write_request_is_absent_when_nothing_is_restricted(monkeypatch, tmp_path):
    """An undeclared schema is input-token cost on every call; a role with no
    allowlist has nothing to ask permission for."""
    unrestricted = _entry(monkeypatch, tmp_path, None)
    names = {t.name for t in unrestricted.run_tools()}
    assert "data_fetch" in names and "data_write_request" not in names

    restricted = _entry(monkeypatch, tmp_path, READS)
    assert "data_write_request" in {t.name for t in restricted.run_tools()}


# ── filing a request ───────────────────────────────────────────────────────────


def test_a_request_records_the_arguments_fingerprint_not_the_agents(client):
    project = make_project(client)
    _attach(client, project["id"], READS)
    arguments = {"campaigns": [{"name": "Q4", "budget": 50}]}

    response = client.post("/agent/data_writes", headers=_agent(client, project),
                           json={"source": SOURCE, "tool": WRITE, "arguments": arguments,
                                 "why": "Launch the Q4 campaign."})
    assert response.status_code == 200, response.text

    with db_session() as session:
        approval = session.get(Approval, response.json()["id"])
        grant, body, status = approval.grant, approval.body, approval.status
    assert grant == {"source": SOURCE, "tool": WRITE,
                     "arguments_sha256": _digest(arguments)}
    assert status == ApprovalStatus.PENDING
    # The chairman decides on the literal call, so the arguments are in front of him.
    assert '"budget": 50' in body


def test_a_request_for_an_already_callable_tool_is_refused(client):
    project = make_project(client)
    _attach(client, project["id"], READS)
    response = client.post("/agent/data_writes", headers=_agent(client, project),
                           json={"source": SOURCE, "tool": "get_stats", "arguments": {}})
    assert response.status_code == 422
    assert "already yours to call" in response.text


def test_a_request_against_an_unknown_source_is_refused(client):
    project = make_project(client)
    _attach(client, project["id"], READS)
    response = client.post("/agent/data_writes", headers=_agent(client, project),
                           json={"source": "not-a-source", "tool": WRITE, "arguments": {}})
    assert response.status_code == 404


# ── spending one ───────────────────────────────────────────────────────────────


def _approved(client, project: dict, arguments: dict, tool: str = WRITE) -> str:
    headers = _agent(client, project)
    approval_id = client.post(
        "/agent/data_writes", headers=headers,
        json={"source": SOURCE, "tool": tool, "arguments": arguments}).json()["id"]
    decided = client.post(f"/admin/approvals/{approval_id}/decide",
                          json={"approve": True, "note": "go ahead"}, headers=ADMIN)
    assert decided.status_code == 200, decided.text
    return approval_id


def test_an_approved_grant_is_spendable_once(client):
    project = make_project(client)
    _attach(client, project["id"], READS)
    arguments = {"budget": 50}
    approval_id = _approved(client, project, arguments)
    headers = _agent(client, project)
    call = {"source": SOURCE, "tool": WRITE, "arguments": arguments}

    first = client.post(f"/agent/data_writes/{approval_id}/spend", json=call, headers=headers)
    assert first.status_code == 200 and first.json()["spent"] is True

    second = client.post(f"/agent/data_writes/{approval_id}/spend", json=call, headers=headers)
    assert second.status_code == 409
    assert "already spent" in second.text


def test_an_approval_cannot_be_spent_on_different_arguments(client):
    """The whole point of the digest: approval for a $50 change is not approval
    for a $5,000 one."""
    project = make_project(client)
    _attach(client, project["id"], READS)
    approval_id = _approved(client, project, {"budget": 50})

    response = client.post(
        f"/agent/data_writes/{approval_id}/spend", headers=_agent(client, project),
        json={"source": SOURCE, "tool": WRITE, "arguments": {"budget": 5000}})
    assert response.status_code == 409
    assert "not the arguments" in response.text
    with db_session() as session:
        assert session.get(Approval, approval_id).spent_at is None


def test_an_approval_cannot_be_spent_on_a_different_tool(client):
    project = make_project(client)
    _attach(client, project["id"], READS)
    approval_id = _approved(client, project, {"budget": 50})

    response = client.post(
        f"/agent/data_writes/{approval_id}/spend", headers=_agent(client, project),
        json={"source": SOURCE, "tool": "pricing_actions", "arguments": {"budget": 50}})
    assert response.status_code == 409
    assert WRITE in response.text


def test_a_pending_approval_is_not_spendable(client):
    project = make_project(client)
    _attach(client, project["id"], READS)
    arguments = {"budget": 50}
    approval_id = client.post(
        "/agent/data_writes", headers=_agent(client, project),
        json={"source": SOURCE, "tool": WRITE, "arguments": arguments}).json()["id"]

    response = client.post(
        f"/agent/data_writes/{approval_id}/spend", headers=_agent(client, project),
        json={"source": SOURCE, "tool": WRITE, "arguments": arguments})
    assert response.status_code == 409 and "pending" in response.text


def test_a_denied_approval_is_not_spendable(client):
    project = make_project(client)
    _attach(client, project["id"], READS)
    arguments = {"budget": 50}
    approval_id = client.post(
        "/agent/data_writes", headers=_agent(client, project),
        json={"source": SOURCE, "tool": WRITE, "arguments": arguments}).json()["id"]
    client.post(f"/admin/approvals/{approval_id}/decide",
                json={"approve": False, "note": "no"}, headers=ADMIN)

    response = client.post(
        f"/agent/data_writes/{approval_id}/spend", headers=_agent(client, project),
        json={"source": SOURCE, "tool": WRITE, "arguments": arguments})
    assert response.status_code == 409 and "denied" in response.text


def test_an_approval_from_another_project_is_invisible(client):
    """yaaf has no tenancy, so a grant that leaked across projects would be a
    capability an agent could reach by guessing an id."""
    mine = make_project(client, slug="mine")
    theirs = make_project(client, slug="theirs")
    _attach(client, mine["id"], READS)
    _attach(client, theirs["id"], READS)
    arguments = {"budget": 50}
    theirs_approval = _approved(client, theirs, arguments)

    response = client.post(
        f"/agent/data_writes/{theirs_approval}/spend", headers=_agent(client, mine),
        json={"source": SOURCE, "tool": WRITE, "arguments": arguments})
    assert response.status_code == 404


def test_an_ordinary_approval_grants_no_call(client):
    """A prose approval the chairman happened to say yes to is not a capability."""
    project = make_project(client)
    _attach(client, project["id"], READS)
    approval_id = client.post(
        "/agent/approvals", headers=_agent(client, project),
        json={"kind": "tool_request", "title": "Please let me change budgets",
              "body": "I would like to raise the Q4 budget."}).json()["id"]
    client.post(f"/admin/approvals/{approval_id}/decide",
                json={"approve": True}, headers=ADMIN)

    response = client.post(
        f"/agent/data_writes/{approval_id}/spend", headers=_agent(client, project),
        json={"source": SOURCE, "tool": WRITE, "arguments": {}})
    assert response.status_code == 422 and "carries no grant" in response.text

