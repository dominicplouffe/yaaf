"""Invariant selftests: a data source's tool allowlist, and that nothing gets past it.

A data source can name the tools it may be called with. Without that, `data_fetch`
validates the *server* name and passes the *tool* name straight to the vendor — so
a source connected for reading serves its write tools too, and "read-only" is a
sentence in a prompt rather than a rule. These tests attack the rule over HTTP the
way an agent would.

The allowlist used to be enforced inside the sandbox — by `data_fetch`, in the same
process that held the vendor's URL and token. It is enforced by the platform now: every
call goes through the gateway (`/agent/mcp/<source>`), which checks the tool's recorded
permission before anything is sent. So the attacks below are made over HTTP with a
real run token, against a fake vendor that records what actually reached it
(`tests/fake_mcp.py`).

There used to be one way past a denied tool: a TOOL_REQUEST approval carrying a
grant bought ONE call with exact arguments. It was retired — a switch that says
deny while a call goes through is not a switch — and the last section here attacks
that it stays retired: an approved grant, forged straight into the database, still
gets nothing through.

A rule here changed on purpose: a source saved without a list is no longer
unrestricted. Each tool starts allowed if it reads and denied if it writes.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
from types import SimpleNamespace

import pytest

from tests import fake_mcp
from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Approval, ApprovalStatus, DataSourceTool, Project

SOURCE = "ads"
READS = ["get_stats", "get_table_fields"]
WRITE = "create_campaigns"


@pytest.fixture(autouse=True)
def vendor(monkeypatch):
    return fake_mcp.install(monkeypatch)


def _permissions(project_id: str) -> dict[str, str]:
    with db_session() as session:
        return {r.name: r.permission for r in session.query(DataSourceTool).filter_by(
            project_id=project_id, source=SOURCE)}


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


def test_a_source_saved_without_one_decides_by_what_each_tool_does(client):
    """It used to mean "unrestricted". Now a read starts allowed and a write
    starts denied — the chairman moves either on the Data sources page."""
    project = make_project(client)
    _attach(client, project["id"], None)
    assert "tools" not in _source(project["id"])
    assert _permissions(project["id"]) == {"get_stats": "allow", "get_table_fields": "allow",
                                           WRITE: "deny", "pricing_actions": "deny"}


def test_an_allowlist_decides_every_tool_including_reads(client):
    project = make_project(client)
    _attach(client, project["id"], ["get_stats"])
    assert _permissions(project["id"])["get_table_fields"] == "deny"


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


# ── the gateway refuses a denied tool before it does anything ──────────────────


def _call(client, project, tool, arguments=None, extra=None) -> dict:
    return fake_mcp.rpc(client, SOURCE, _agent(client, project), "tools/call",
                        {"name": tool, "arguments": arguments or {}}, extra)["result"]


def test_an_ungranted_tool_is_refused_naming_what_is_callable(client, vendor):
    project = make_project(client)
    _attach(client, project["id"], READS)
    out = _call(client, project, WRITE, {"budget": 5})

    assert out["isError"] is True and out["_meta"]["yaaf/refused"] is True
    error = out["content"][0]["text"]
    assert "DENIED" in error and "get_stats" in error
    assert "Nothing else gets a call through a denied tool" in error
    # Refused before the vendor was reached: a denied write must not have happened.
    assert vendor.called == []


def test_a_granted_tool_is_forwarded(client, vendor):
    project = make_project(client)
    _attach(client, project["id"], READS)
    out = _call(client, project, "get_stats", {"days": 7})
    assert not out.get("isError"), out
    assert vendor.called == ["get_stats"]
    # The vendor's credential was presented by the platform, not by the agent.
    assert vendor.calls[0][2] == "Bearer vendor-token"


def test_a_source_without_a_list_still_refuses_its_writes(client, vendor):
    """Every install that predates the allowlist used to keep calling anything.
    That was the hole: a source connected for reading served its writes too."""
    project = make_project(client)
    _attach(client, project["id"], None)
    assert _call(client, project, WRITE, {"budget": 5})["_meta"]["yaaf/refused"] is True
    assert vendor.called == []
    assert not _call(client, project, "get_stats").get("isError")
    assert vendor.called == ["get_stats"]


pytest.importorskip("claude_agent_sdk")


def _entry(monkeypatch, tmp_path, tools, access="bulk"):
    """The sandbox module, reloaded with the env it reads at import."""
    monkeypatch.setenv("YAAF_API_BASE_URL", "http://api:8080")
    monkeypatch.setenv("YAAF_API_TOKEN", "test-token")
    monkeypatch.setenv("YAAF_RUN_ID", "run_test")
    monkeypatch.setenv("YAAF_TOOL_PERMISSIONS", "{}")
    import yaaf.runner.sandbox_entry as mod

    entry = importlib.reload(mod)
    spec = {"name": SOURCE, "access": access}
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


def test_discovery_says_which_tools_are_denied(monkeypatch, tmp_path):
    """So an agent plans around the rule instead of finding it one refusal at a
    time. The sandbox asks the gateway for the whole list and reports its marks."""
    entry = _entry(monkeypatch, tmp_path, READS)
    asked = []

    async def fake_list(config):
        asked.append(config)
        return SimpleNamespace(tools=[
            SimpleNamespace(name="get_stats", description="read", inputSchema={},
                            meta={"yaaf/permission": "allow"}),
            SimpleNamespace(name=WRITE, description="write", inputSchema={},
                            meta={"yaaf/permission": "deny"}),
        ])

    monkeypatch.setattr(entry, "_mcp_list_tools", fake_list)
    monkeypatch.setattr(entry, "_call", lambda *a, **k: pytest.fail("no platform call"))
    reply = asyncio.run(entry.t_data_fetch.handler({"server": SOURCE}))
    body = json.loads(reply["content"][0]["text"])["body"]

    flags = {t["name"]: t["needs_approval"] for t in body["tools"]}
    assert flags == {"get_stats": False, WRITE: True}
    assert "tool_permission_request" in body["note"]
    assert asked[0]["url"] == f"http://api:8080/agent/mcp/{SOURCE}"
    assert asked[0]["headers"]["X-Yaaf-Listing"] == "all"


def test_data_fetch_carries_nothing_that_could_unlock_a_tool(monkeypatch, tmp_path):
    """data_fetch decides nothing and has nothing to present: no approval id, no
    vendor credential — the run's own token and the call, to the gateway."""
    entry = _entry(monkeypatch, tmp_path, READS)
    sent = []

    async def fake_call_tool(config, tool_name, arguments):
        sent.append((config, tool_name, arguments))
        return _result()

    monkeypatch.setattr(entry, "_mcp_call_tool", fake_call_tool)
    monkeypatch.setattr(entry, "_call", lambda *a, **k: pytest.fail("no platform call"))
    asyncio.run(entry.t_data_fetch.handler(
        {"server": SOURCE, "tool": WRITE, "arguments": {"budget": 5},
         "approval_id": "apr_1"}))
    config, tool_name, _ = sent[0]
    assert tool_name == WRITE
    assert config["headers"] == {"Authorization": "Bearer test-token"}
    assert "approval_id" not in entry.t_data_fetch.input_schema["properties"]


def test_a_platform_refusal_reaches_the_agent_as_one(monkeypatch, tmp_path):
    entry = _entry(monkeypatch, tmp_path, READS)

    async def refused(config, tool_name, arguments):  # noqa: ARG001
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ads.create_campaigns is DENIED")],
            structuredContent=None, isError=True,
            meta={"yaaf/refused": True, "yaaf/status": 403})

    monkeypatch.setattr(entry, "_mcp_call_tool", refused)
    out = json.loads(asyncio.run(entry.t_data_fetch.handler(
        {"server": SOURCE, "tool": WRITE, "arguments": {}}))["content"][0]["text"])
    assert out["status"] == 403 and "DENIED" in out["body"]["error"]


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


def test_no_tool_offers_a_way_past_a_denied_one(monkeypatch, tmp_path):
    entry = _entry(monkeypatch, tmp_path, None)
    names = {t.name for t in entry.run_tools()}
    assert "data_fetch" in names
    assert not {"data_write_request", "data_write_outcome"} & names


# ── the retired one-call approval stays retired ───────────────────────────────


def _approved_grant(project_id: str, arguments: dict, tool: str = WRITE) -> str:
    """A grant exactly as the retired path would have left it — approved by a
    person, unspent, for these exact arguments — written straight to the
    database, since no route files one any more."""
    from datetime import timedelta

    from yaaf.core import ids
    from yaaf.core.models import User, utcnow

    with db_session() as session:
        chair = session.query(User).first()
        row = Approval(
            id=ids.new_id(ids.APPROVAL), project_id=project_id, kind="tool_request",
            requested_by_agent_id="agnt_x", title="Run ads.create_campaigns",
            body="one call", status=ApprovalStatus.APPROVED,
            expires_at=utcnow() + timedelta(days=7), decided_at=utcnow(),
            decided_by_user_id=chair.id,
            grant={"source": SOURCE, "tool": tool, "arguments_sha256": _digest(arguments)})
        session.add(row)
        return row.id


def test_an_approved_grant_gets_no_call_through_a_denied_tool(client, vendor):
    project = make_project(client)
    _attach(client, project["id"], READS)
    arguments = {"budget": 50}
    approval_id = _approved_grant(project["id"], arguments)

    for extra in (None, {"X-Yaaf-Approval": approval_id}, {"x-yaaf-approval": approval_id}):
        out = _call(client, project, WRITE, arguments, extra)
        assert out["_meta"]["yaaf/refused"] is True
    params_meta = fake_mcp.rpc(client, SOURCE, _agent(client, project), "tools/call", {
        "name": WRITE, "arguments": arguments,
        "_meta": {"approval_id": approval_id}})["result"]
    assert params_meta["_meta"]["yaaf/refused"] is True
    assert vendor.called == []
    with db_session() as session:
        assert session.get(Approval, approval_id).spent_at is None


@pytest.mark.parametrize("path", ["/agent/data_writes", "/agent/data_writes/x/spend",
                                  "/agent/data_writes/x/outcome"])
def test_no_route_files_or_spends_a_grant(client, vendor, path):
    project = make_project(client)
    _attach(client, project["id"], READS)
    response = client.post(path, headers=_agent(client, project),
                           json={"source": SOURCE, "tool": WRITE, "arguments": {}})
    assert response.status_code in (403, 404, 405)
    with db_session() as session:
        assert session.query(Approval).filter(Approval.grant.isnot(None)).count() == 0


def test_filing_one_is_refused_naming_what_replaced_it(client, vendor):
    project = make_project(client)
    _attach(client, project["id"], READS)
    refused = client.post("/agent/data_writes", headers=_agent(client, project),
                          json={"source": SOURCE, "tool": WRITE, "arguments": {}})
    assert refused.status_code == 403 and "retired" in refused.text
    ceo = bearer(live_token(project["id"], project["ceo_agent_id"])[1])
    as_ceo = client.post("/agent/data_writes", headers=ceo, json={})
    assert "tool_permission_request" in as_ceo.text


def test_the_chairman_cannot_approve_a_grant_left_pending(client, vendor):
    """A yes would let nothing through, and saying it had would be untrue to him
    and to the agent — so a grant still on his desk can only be declined."""
    project = make_project(client)
    _attach(client, project["id"], READS)
    approval_id = _approved_grant(project["id"], {"budget": 50})
    with db_session() as session:
        row = session.get(Approval, approval_id)
        row.status, row.decided_at, row.decided_by_user_id = ApprovalStatus.PENDING, None, None
    approve = client.post(f"/admin/approvals/{approval_id}/decide",
                          json={"approve": True}, headers=ADMIN)
    assert approve.status_code == 409 and "Data sources" in approve.text
    decline = client.post(f"/admin/approvals/{approval_id}/decide",
                          json={"approve": False, "note": "use the switch"}, headers=ADMIN)
    assert decline.status_code == 200
