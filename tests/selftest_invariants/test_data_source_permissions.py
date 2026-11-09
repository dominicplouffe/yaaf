"""Invariant selftests: a data source's tools, their permissions, and the wall in front of them.

What the chairman asked for, each held by an attack rather than a sentence:

1. Connecting a source records every tool it offers, with what it does.
2. A tool that reads starts ALLOWED; one that writes — or that the platform cannot
   tell about — starts DENIED.
3. A denied call is refused immediately before it would be sent: the gateway
   checks it, and the fake vendor below records that nothing arrived. The sandbox
   holds nothing that reaches the source around the gateway — no address, no
   token — so there is no other path to try.
4. The CEO can ask the chairman to move a tool, in his Inbox, and cannot move it
   itself: no /agent route writes a permission, an approval only he can decide is
   the CEO's one lever, silence never decides it, and his own model client cannot
   approve it either. What he says in conversation changes nothing, because nothing
   on the call path reads conversation.

The vendor is faked at the transport (`tests/fake_mcp.py`), so every request the
platform makes — the save's check, discovery, the forwarded call — lands somewhere
that keeps count.
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib
from datetime import timedelta

import httpx
import pytest

from tests import fake_mcp
from tests.conftest import (
    ADMIN,
    agent_by_name,
    bearer,
    live_token,
    make_project,
)
from yaaf.core.db import db_session
from yaaf.core.models import (
    Approval,
    ApprovalStatus,
    DataSourceTool,
    Project,
    Run,
    Secret,
    utcnow,
)
from yaaf.server import datasource_tools

SOURCE = "ads"
REPO = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture()
def vendor(monkeypatch):
    return fake_mcp.install(monkeypatch)


def _attach(client, project_id: str, **overrides) -> dict:
    body = {"name": SOURCE, "url": "https://vendor.example.com/mcp", "bearer": "vendor-token",
            "roles": ["worker"], "access": "inline", **overrides}
    response = client.post(f"/admin/projects/{project_id}/mcp_servers", json=body,
                           headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def _tools(project_id: str) -> dict[str, DataSourceTool]:
    with db_session() as session:
        rows = session.query(DataSourceTool).filter_by(project_id=project_id,
                                                       source=SOURCE).all()
        session.expunge_all()
    return {r.name: r for r in rows}


def _permission(project_id: str, tool: str) -> str:
    return _tools(project_id)[tool].permission


def _worker(project) -> tuple[str, dict]:
    run_id, token = live_token(project["id"], agent_by_name(project, "Lead"))
    return run_id, bearer(token)


def _ceo(project) -> dict:
    return bearer(live_token(project["id"], project["ceo_agent_id"])[1])


def _call(client, headers, tool, arguments=None) -> dict:
    return fake_mcp.rpc(client, SOURCE, headers, "tools/call",
                        {"name": tool, "arguments": arguments or {}})["result"]


def _set(client, project_id, tool, permission, headers=ADMIN):
    return client.post(f"/admin/projects/{project_id}/mcp_servers/{SOURCE}/permissions",
                       json={"tool": tool, "permission": permission}, headers=headers)


# ── 1, 2. discovered, recorded, and classified ────────────────────────────────


def test_saving_a_source_records_every_tool_it_offers(client, vendor):
    project = make_project(client)
    saved = _attach(client, project["id"])
    assert saved["tools"] == {"total": 4, "allowed": 2, "denied": 2}

    listed = client.get(f"/admin/projects/{project['id']}/data_source_tools",
                        headers=ADMIN).json()
    by_name = {t["name"]: t for t in listed}
    assert set(by_name) == {"get_stats", "get_table_fields", "create_campaigns",
                            "pricing_actions"}
    # What the chairman needs to read it: what it is, what it takes, why it is
    # classified the way it is, and who decided its permission.
    stats = by_name["get_stats"]
    assert stats["description"] == "Read campaign statistics."
    assert stats["input_schema"]["properties"] == {"days": {"type": "integer"}}
    assert (stats["kind"], stats["permission"], stats["decided_by"]) == \
        ("read", "allow", "default")
    assert by_name["create_campaigns"]["kind"] == "write"
    assert by_name["create_campaigns"]["permission"] == "deny"
    assert "server marks it" in by_name["pricing_actions"]["kind_reason"]


@pytest.mark.parametrize("name,annotations,kind", [
    ("get_campaigns", None, "read"),
    ("listOrders", None, "read"),
    ("search_records", None, "read"),
    ("notion-search", None, "read"),
    ("get_post", None, "read"),
    ("create_campaign", None, "write"),
    ("slack_post_message", None, "write"),
    ("updateBid", None, "write"),
    ("delete_rows", None, "write"),
    ("find_and_replace", None, "write"),
    ("search_and_delete", None, "write"),
    # Nothing in it says read: not a read.
    ("campaign_budget", None, "unknown"),
    ("do_the_thing", None, "unknown"),
    # The server's hints count, but only ever toward deny.
    ("campaign_budget", {"readOnlyHint": True}, "read"),
    ("get_stats", {"readOnlyHint": False}, "write"),
    ("get_stats", {"destructiveHint": True}, "write"),
    ("delete_rows", {"readOnlyHint": True}, "write"),
])
def test_a_tool_is_a_read_only_when_nothing_says_otherwise(name, annotations, kind):
    assert datasource_tools.classify(name, annotations)[0] == kind


def test_only_a_read_starts_allowed():
    for kind in ("write", "unknown"):
        assert datasource_tools.default_permission(kind, "x", {})[0] == "deny"
    assert datasource_tools.default_permission("read", "x", {})[0] == "allow"
    # A list the chairman saved outranks the kind, both ways.
    assert datasource_tools.default_permission("read", "x", {"tools": []})[0] == "deny"
    assert datasource_tools.default_permission("write", "x", {"tools": ["x"]})[0] == "allow"


def test_a_tool_the_server_adds_later_gets_its_default_and_a_vanished_one_keeps_its_row(
        client, vendor):
    project = make_project(client)
    _attach(client, project["id"])
    vendor.tools = [t for t in vendor.tools if t["name"] != "get_table_fields"] + [
        {"name": "delete_campaign", "description": "Delete a campaign."}]
    client.post(f"/admin/projects/{project['id']}/mcp_servers/{SOURCE}/check", json={},
                headers=ADMIN)
    tools = _tools(project["id"])
    assert tools["delete_campaign"].permission == "deny"
    assert tools["get_table_fields"].gone_at is not None
    assert tools["get_table_fields"].permission == "allow"


def test_a_server_rewording_a_tool_never_loosens_it(client, vendor):
    """A default deny is never turned into an allow by the server changing its
    own description or hints; a default allow IS tightened when the tool
    starts to say it writes. A person's decision is never touched either way."""
    project = make_project(client)
    _attach(client, project["id"])
    for tool in vendor.tools:
        if tool["name"] == "create_campaigns":
            tool["annotations"] = {"readOnlyHint": True}
            tool["name"] = "create_campaigns"
        if tool["name"] == "get_stats":
            tool["annotations"] = {"destructiveHint": True}
    client.post(f"/admin/projects/{project['id']}/mcp_servers/{SOURCE}/check", json={},
                headers=ADMIN)
    assert _permission(project["id"], "create_campaigns") == "deny"
    assert _permission(project["id"], "get_stats") == "deny"


# ── 3. the wall: refused immediately before it would be sent ──────────────────


def test_a_denied_tool_is_refused_and_nothing_reaches_the_vendor(client, vendor):
    project = make_project(client)
    _attach(client, project["id"])
    run_id, headers = _worker(project)

    out = _call(client, headers, "create_campaigns", {"budget": 5000})
    assert out["isError"] is True and out["_meta"]["yaaf/refused"] is True
    assert "DENIED" in out["content"][0]["text"]
    assert vendor.called == []
    # A refusal costs what every refusal costs: it counts toward the run's cap.
    with db_session() as session:
        run = session.get(Run, run_id)
        assert run.refusal_count == 1 and run.last_refusal_route == f"/agent/mcp/{SOURCE}"


def test_an_allowed_tool_is_forwarded_with_the_platforms_credential(client, vendor):
    project = make_project(client)
    _attach(client, project["id"])
    _, headers = _worker(project)

    out = _call(client, headers, "get_stats", {"days": 7})
    assert json.loads(out["content"][0]["text"]) == {"tool": "get_stats", "ok": True}
    assert vendor.calls == [("get_stats", {"days": 7}, "Bearer vendor-token")]


def test_a_denied_tool_is_not_listed_to_a_harness_but_is_to_discovery(client, vendor):
    project = make_project(client)
    _attach(client, project["id"])
    _, headers = _worker(project)

    mounted = fake_mcp.rpc(client, SOURCE, headers, "tools/list")["result"]["tools"]
    assert {t["name"] for t in mounted} == {"get_stats", "get_table_fields"}
    everything = fake_mcp.rpc(client, SOURCE, headers, "tools/list",
                              extra={"X-Yaaf-Listing": "all"})["result"]["tools"]
    assert {t["name"]: t["_meta"]["yaaf/permission"] for t in everything} == {
        "get_stats": "allow", "get_table_fields": "allow",
        "create_campaigns": "deny", "pricing_actions": "deny"}


def test_a_tool_the_server_never_offered_is_refused_without_being_called(client, vendor):
    project = make_project(client)
    _attach(client, project["id"])
    _, headers = _worker(project)
    out = _call(client, headers, "drop_everything")
    assert out["_meta"]["yaaf/refused"] is True
    assert vendor.called == []


def test_a_role_not_given_the_source_reaches_nothing(client, vendor):
    project = make_project(client)
    _attach(client, project["id"])  # granted to "worker" only
    for method, params in (("initialize", {}), ("tools/list", {}),
                           ("tools/call", {"name": "get_stats"})):
        reply = fake_mcp.rpc(client, SOURCE, _ceo(project), method, params)
        refused = reply.get("error") or reply["result"]
        assert "not given to your role" in json.dumps(refused)
    assert vendor.called == []


def test_a_run_token_from_another_project_reaches_nothing(client, vendor):
    mine = make_project(client, slug="mine")
    theirs = make_project(client, slug="theirs")
    _attach(client, theirs["id"])
    _, headers = _worker(mine)
    reply = fake_mcp.rpc(client, SOURCE, headers, "tools/call", {"name": "get_stats"})
    assert "not one of this project's data sources" in json.dumps(reply)
    assert vendor.called == []


def test_the_sandbox_is_never_handed_the_sources_address_or_token(client, vendor):
    """Granted to the role on the Secrets page or not, a data source's secrets are
    the platform's: a sandbox holding them could call the source around the wall."""
    from yaaf.core.models import Agent
    from yaaf.runner.daemon import _role_secret_env
    from yaaf.server.contextpack import role_version_for

    project = make_project(client)
    _attach(client, project["id"])
    with db_session() as session:
        names = {s.name: s.role_scopes for s in session.query(Secret).filter(
            Secret.project_id == project["id"], Secret.name.like("mcp-ads-%"))}
        assert names == {"mcp-ads-url": ["worker"], "mcp-ads-token": ["worker"]}
        agent = session.get(Agent, agent_by_name(project, "Lead"))
        env = _role_secret_env(session, session.get(Project, project["id"]),
                               role_version_for(session, agent))
    assert not [k for k in env if k.startswith("MCP_ADS")]
    assert "vendor-token" not in env.values()


def test_the_ceo_cannot_scope_a_sources_secret_onto_a_role(client, vendor):
    from yaaf.core.constitution import AUTONOMY_PRESETS

    project = make_project(client, constitution=AUTONOMY_PRESETS["autonomous"])
    _attach(client, project["id"])
    headers = _ceo(project)
    roster = client.get("/agent/credentials", headers=headers).json()
    assert not [c for c in roster if c["name"].startswith("mcp-ads")]
    refused = client.post("/agent/credentials/mcp-ads-token/scope", headers=headers,
                          json={"add_roles": ["ceo"], "reason": "I want to call it myself"})
    assert refused.status_code == 403 and "belongs to a data source" in refused.text


# ── the chairman moves a tool; nothing else does ───────────────────────────────


def test_the_chairmans_tick_is_in_force_on_the_next_call(client, vendor):
    project = make_project(client)
    _attach(client, project["id"])
    _, headers = _worker(project)

    assert _set(client, project["id"], "create_campaigns", "allow").status_code == 200
    assert not _call(client, headers, "create_campaigns", {"budget": 5}).get("isError")
    assert vendor.called == ["create_campaigns"]
    row = _tools(project["id"])["create_campaigns"]
    assert (row.decided_by, row.decided_by_user_id is not None) == ("chairman", True)

    assert _set(client, project["id"], "get_stats", "deny").status_code == 200
    assert _call(client, headers, "get_stats")["_meta"]["yaaf/refused"] is True
    assert vendor.called == ["create_campaigns"]


def test_a_run_token_cannot_reach_the_chairmans_door(client, vendor):
    project = make_project(client)
    _attach(client, project["id"])
    for headers in (_ceo(project), _worker(project)[1]):
        assert _set(client, project["id"], "create_campaigns", "allow",
                    headers=headers).status_code == 401
    assert _permission(project["id"], "create_campaigns") == "deny"


@pytest.mark.parametrize("path,body", [
    ("/agent/mcp/ads", {"jsonrpc": "2.0", "id": 1, "method": "tools/permission",
                        "params": {"name": "create_campaigns", "permission": "allow"}}),
    ("/agent/data_sources/ads/permissions", {"tool": "create_campaigns", "permission": "allow"}),
    ("/agent/mcp_servers/ads/permissions", {"tool": "create_campaigns", "permission": "allow"}),
    ("/agent/data_sources/tool_permission/decide", {"approve": True}),
])
def test_no_agent_route_moves_a_permission(client, vendor, path, body):
    project = make_project(client)
    _attach(client, project["id"])
    client.post(path, json=body, headers=_ceo(project))
    assert _permission(project["id"], "create_campaigns") == "deny"


def test_only_the_chairmans_two_doors_write_a_permission():
    """"No agent route writes a permission" is not provable one request at a
    time, so it is read off the source: `set_permission` is called from the
    chairman's tick and his decision on a request, both in admin.py, and nothing
    on the agent surface assigns a DataSourceTool's permission at all."""
    callers = []
    for path in (REPO / "yaaf").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "set_permission"):
                callers.append(str(path.relative_to(REPO)))
    assert sorted(callers) == ["yaaf/server/api/admin.py", "yaaf/server/api/admin.py"]

    for surface in ("yaaf/server/api/agent.py", "yaaf/runner/sandbox_entry.py"):
        tree = ast.parse((REPO / surface).read_text())
        assigned = [node for node in ast.walk(tree)
                    if isinstance(node, ast.Assign) and any(
                        isinstance(t, ast.Attribute) and t.attr == "permission"
                        for t in node.targets)]
        assert not assigned, f"{surface} assigns a permission"


# ── 4. the CEO asks; the chairman decides ──────────────────────────────────────


def _ask(client, project, tool="create_campaigns", permission="allow", headers=None):
    return client.post("/agent/data_sources/tool_permission",
                       headers=headers or _ceo(project),
                       json={"source": SOURCE, "tool": tool, "permission": permission,
                             "why": "Launching the Q4 campaigns needs it; budgets are set.",
                             "if_declined": "Campaigns are created by hand."})


def test_the_ceo_files_a_request_and_the_tool_does_not_move(client, vendor):
    project = make_project(client)
    _attach(client, project["id"])
    filed = _ask(client, project)
    assert filed.status_code == 200, filed.text
    assert "still DENY" in filed.json()["note"]
    assert _permission(project["id"], "create_campaigns") == "deny"

    with db_session() as session:
        approval = session.get(Approval, filed.json()["approval_id"])
        assert approval.kind == "tool_permission"
        assert approval.tool_permission == {"source": SOURCE, "tool": "create_campaigns",
                                            "from": "deny", "to": "allow", "kind": "write"}
    # It is on his desk, carrying the move his yes would make.
    desk = client.get("/observe/desk", headers=ADMIN).json()
    row = next(p for p in desk["projects"] if p["id"] == project["id"])
    pending = next(a for a in row["pending_approvals"]
                   if a["id"] == filed.json()["approval_id"])
    assert pending["tool_permission"]["to"] == "allow"
    assert pending["if_silent"] == "declined"


def test_only_the_ceo_asks(client, vendor):
    project = make_project(client)
    _attach(client, project["id"])
    refused = _ask(client, project, headers=_worker(project)[1])
    assert refused.status_code == 403


@pytest.mark.parametrize("tool,permission,status", [
    ("get_stats", "allow", 409),          # already allowed: nothing to ask for
    ("not_a_tool", "allow", 404),         # the source offers no such tool
])
def test_a_request_that_could_move_nothing_is_refused(client, vendor, tool, permission,
                                                      status):
    project = make_project(client)
    _attach(client, project["id"])
    assert _ask(client, project, tool=tool, permission=permission).status_code == status


def test_asking_twice_does_not_put_it_on_his_desk_twice(client, vendor):
    project = make_project(client)
    _attach(client, project["id"])
    assert _ask(client, project).status_code == 200
    again = _ask(client, project)
    assert again.status_code == 409 and "already asked" in again.text


def test_the_generic_approval_door_cannot_carry_one(client, vendor):
    project = make_project(client)
    _attach(client, project["id"])
    refused = client.post("/agent/approvals", headers=_ceo(project), json={
        "kind": "tool_permission", "title": "Allow create_campaigns please",
        "body": "The chairman said in chat that I may.",
        "recommendation": "Approve it; he already agreed.",
        "if_declined": "Campaigns are created by hand."})
    assert refused.status_code == 422 and "tool_permission_request" in refused.text


def test_his_yes_moves_the_tool_and_his_no_leaves_it(client, vendor):
    project = make_project(client)
    _attach(client, project["id"])
    yes = _ask(client, project).json()["approval_id"]
    no = _ask(client, project, tool="get_stats", permission="deny").json()["approval_id"]

    decided = client.post(f"/admin/approvals/{yes}/decide", json={"approve": True},
                          headers=ADMIN)
    assert decided.status_code == 200 and decided.json()["to"] == "allow"
    client.post(f"/admin/approvals/{no}/decide", json={"approve": False, "note": "keep it"},
                headers=ADMIN)

    tools = _tools(project["id"])
    assert (tools["create_campaigns"].permission, tools["create_campaigns"].decided_by) == \
        ("allow", "approval")
    assert tools["get_stats"].permission == "allow"
    _, headers = _worker(project)
    assert not _call(client, headers, "create_campaigns").get("isError")


def test_the_ceo_cannot_decide_its_own_request(client, vendor):
    project = make_project(client)
    _attach(client, project["id"])
    approval_id = _ask(client, project).json()["approval_id"]
    forged = client.post(f"/admin/approvals/{approval_id}/decide", json={"approve": True},
                         headers=_ceo(project))
    assert forged.status_code == 401
    assert _permission(project["id"], "create_campaigns") == "deny"


def test_silence_never_moves_a_tool_even_where_silence_means_yes(client, vendor):
    """On a project whose rules approve on silence, every other approval kind
    goes through unanswered. A tool's permission, and a one-call grant past
    one, do not: they expire to no."""
    from yaaf.server.janitor import janitor_once

    project = make_project(client, constitution={"approval_timeout_decision": "approve"})
    _attach(client, project["id"])
    moved = _ask(client, project).json()["approval_id"]
    one_call = client.post("/agent/data_writes", headers=_worker(project)[1], json={
        "source": SOURCE, "tool": "create_campaigns", "arguments": {"budget": 5}}).json()["id"]
    with db_session() as session:
        for approval_id in (moved, one_call):
            session.get(Approval, approval_id).expires_at = utcnow() - timedelta(minutes=1)
    with db_session() as session:
        janitor_once(session)
    with db_session() as session:
        assert session.get(Approval, moved).status == ApprovalStatus.EXPIRED
        assert session.get(Approval, one_call).status == ApprovalStatus.EXPIRED
    assert _permission(project["id"], "create_campaigns") == "deny"


def test_his_own_model_client_cannot_approve_it(client, vendor):
    """The request is a model's prose; a yes to it must be a person's hand. His
    Claude may decline one, and may read every tool, but not move one."""
    from tests.selftest_invariants.test_mcp_auth import mint
    from tests.selftest_invariants.test_mcp_auth import rpc as mcp_rpc

    project = make_project(client)
    _attach(client, project["id"])
    approval_id = _ask(client, project).json()["approval_id"]
    token = mint(client, scopes=("read", "steer", "configure"))["token"]
    arguments = {"project": project["id"], "approval": approval_id, "approve": True}
    preview = mcp_rpc(client, token, "tools/call",
                      {"name": "approval_decide", "arguments": arguments}).json()
    confirm = json.loads(preview["result"]["content"][0]["text"]).get("confirm")
    assert confirm, preview
    confirmed = mcp_rpc(client, token, "tools/call", {
        "name": "approval_decide", "arguments": {**arguments, "confirm": confirm}}).json()
    assert "decided by a person, in the dashboard" in json.dumps(confirmed)
    with db_session() as session:
        assert session.get(Approval, approval_id).status == ApprovalStatus.PENDING
    assert _permission(project["id"], "create_campaigns") == "deny"

    shown = mcp_rpc(client, token, "tools/call", {"name": "data_source", "arguments": {
        "project": project["id"], "verb": "tools"}}).json()
    assert "create_campaigns" in json.dumps(shown)


def test_the_ceo_is_told_what_is_denied_and_offered_the_way_to_ask(client, vendor):
    from yaaf.core import ids
    from yaaf.core.models import RunKind, RunQueue
    from yaaf.runner import daemon

    project = make_project(client)
    _attach(client, project["id"])  # given to "worker", not to the CEO
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=project["ceo_agent_id"], kind=RunKind.AGENT_RUN,
                             trigger={"reasons": []}, attempt=1))
    job = daemon.lease_and_prepare(limit=1)[0]
    assert job.tool_permissions["tool_requests"] == {
        SOURCE: ["create_campaigns", "pricing_actions"]}
    assert "DENIED TOOLS" in job.system_prompt and "create_campaigns" in job.system_prompt
    assert "tool_permission_request" in job.system_prompt


# ── the pieces that travel with a source ───────────────────────────────────────


def test_removing_a_source_removes_its_tools(client, vendor):
    project = make_project(client)
    _attach(client, project["id"])
    client.delete(f"/admin/projects/{project['id']}/mcp_servers/{SOURCE}", headers=ADMIN)
    assert _tools(project["id"]) == {}


def test_an_export_carries_what_may_be_called_as_its_list(client, vendor):
    project = make_project(client)
    _attach(client, project["id"])
    _set(client, project["id"], "get_table_fields", "deny")
    exported = client.get(f"/admin/projects/{project['id']}/export", headers=ADMIN).json()
    source = next(s for s in exported["project"]["integrations"]["mcp_servers"]
                  if s["name"] == SOURCE)
    # Every tool not on it is denied on the far side, reads he never looked at
    # included — a copy errs toward the wall.
    assert source["tools"] == ["get_stats"]


# ── the gateway is an MCP server a real client can mount ──────────────────────


def test_the_mcp_client_library_mounts_the_gateway(client, vendor, _app):
    """The harnesses speak MCP to the gateway, so it is checked with the same
    client library the sandbox uses — initialize, list, call, and a refusal
    that arrives as a tool error rather than a broken connection."""
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    project = make_project(client)
    _attach(client, project["id"])
    _, headers = _worker(project)

    async def session_run():
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=_app),
                              base_url="http://testserver", headers=headers) as http,
            streamable_http_client(f"http://testserver/agent/mcp/{SOURCE}",
                                   http_client=http) as streams,
            ClientSession(streams[0], streams[1]) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            allowed = await session.call_tool("get_stats", {"days": 3})
            denied = await session.call_tool("create_campaigns", {"budget": 1})
            return listed, allowed, denied

    listed, allowed, denied = asyncio.run(session_run())
    assert {t.name for t in listed.tools} == {"get_stats", "get_table_fields"}
    assert not allowed.is_error if hasattr(allowed, "is_error") else not allowed.isError
    assert (denied.is_error if hasattr(denied, "is_error") else denied.isError) is True
    assert vendor.called == ["get_stats"]
