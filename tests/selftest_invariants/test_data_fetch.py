"""Invariant selftests: bulk data reaches an agent as a FILE, not as context.

The expensive default this exists to remove: a data source mounted as tools
returns its rows straight into the model's conversation, where they are paid
for by the token on that turn and every turn after — and where "analysis" means
a model doing arithmetic by reading rows, which is both dearer and less correct
than three lines of python.

Two invariants, then. data_fetch writes the whole result to the workspace and
hands back only a pointer. And a source marked `bulk` is not mounted at all, so
for the sources that actually carry volume the expensive path does not merely
carry a warning — it does not exist.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("claude_agent_sdk")


@pytest.fixture()
def sandbox_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("YAAF_API_BASE_URL", "http://api:8080")
    monkeypatch.setenv("YAAF_API_TOKEN", "test-token")
    monkeypatch.setenv("YAAF_RUN_ID", "run_test")
    monkeypatch.setenv("YAAF_TOOL_PERMISSIONS", "{}")
    monkeypatch.setenv("MCP_PPC_URL", "https://data.example.com:3845/mcp")
    monkeypatch.setenv("MCP_PPC_TOKEN", "tok-123")
    import yaaf.runner.sandbox_entry as mod

    entry = importlib.reload(mod)
    entry.PERMS = {"mcp_servers": [
        {"name": "ppc", "url_env": "MCP_PPC_URL", "bearer_env": "MCP_PPC_TOKEN"},
    ]}
    workspace = tmp_path / "workspace"
    (workspace / ".yaaf").mkdir(parents=True)
    monkeypatch.setattr(entry, "WORKSPACE", workspace)
    monkeypatch.setattr(entry, "STAGING", workspace / ".yaaf")
    # The platform API is a different thing entirely; a data pull must never
    # route through it (nor bill it).
    monkeypatch.setattr(entry, "_call",
                        lambda *a, **k: pytest.fail("data_fetch must not call the platform API"))
    return entry


def _result(*, text=None, blocks=None, structured=None, is_error=False):
    content = blocks if blocks is not None else (
        [SimpleNamespace(type="text", text=text)] if text is not None else [])
    return SimpleNamespace(content=content, structuredContent=structured, isError=is_error)


def _fetch(entry, monkeypatch, args, result=None, capture=None):
    async def fake_call_tool(config, tool_name, arguments):
        if capture is not None:
            capture.append((config, tool_name, arguments))
        return result

    monkeypatch.setattr(entry, "_mcp_call_tool", fake_call_tool)
    reply = asyncio.run(entry.t_data_fetch.handler(args))
    return json.loads(reply["content"][0]["text"])


# ── the pointer, not the payload ───────────────────────────────────────────────


def test_the_result_lands_on_disk_and_only_a_pointer_comes_back(
    sandbox_entry, monkeypatch
):
    rows = "asin,spend\n" + "".join(f"B{i:08d},{i}.50\n" for i in range(5000))
    calls = []
    out = _fetch(sandbox_entry, monkeypatch,
                 {"server": "ppc", "tool": "export_spend", "arguments": {"days": 90}},
                 result=_result(text=rows), capture=calls)

    assert out["status"] == 200
    body = out["body"]
    written = (sandbox_entry.WORKSPACE / body["path"]).read_text()
    # Byte-for-byte: a source that returns CSV leaves a CSV on disk, not CSV
    # wrapped in JSON that every later reader has to unwrap.
    assert written == rows
    assert body["bytes"] == len(rows.encode())
    assert body["sha256"] == hashlib.sha256(rows.encode()).hexdigest()
    assert body["lines"] == 5001

    # The whole point: the rows did NOT come back. A preview did.
    assert len(body["preview"]) == sandbox_entry.DATA_FETCH_PREVIEW_CHARS
    assert body["preview_truncated"] is True
    assert len(json.dumps(out)) < len(rows) / 10
    assert body["path"].startswith("data/ppc/export_spend-")

    # The source was called with its own url, auth and arguments, verbatim.
    config, tool_name, arguments = calls[0]
    assert config["url"] == "https://data.example.com:3845/mcp"
    assert config["headers"] == {"Authorization": "Bearer tok-123"}
    assert (tool_name, arguments) == ("export_spend", {"days": 90})


def test_a_stable_path_is_a_cache_the_next_run_can_reuse(sandbox_entry, monkeypatch):
    out = _fetch(sandbox_entry, monkeypatch,
                 {"server": "ppc", "tool": "export", "path": "data/spend-90d.csv"},
                 result=_result(text="asin,spend\nB1,4.00\n"))
    assert out["body"]["path"] == "data/spend-90d.csv"
    assert (sandbox_entry.WORKSPACE / "data/spend-90d.csv").exists()


def test_the_full_tool_id_a_model_will_paste_is_accepted(sandbox_entry, monkeypatch):
    calls = []
    out = _fetch(sandbox_entry, monkeypatch,
                 {"server": "ppc", "tool": "mcp__ppc__export_spend"},
                 result=_result(text="ok"), capture=calls)
    assert out["status"] == 200
    assert calls[0][1] == "export_spend"


# ── refusals ───────────────────────────────────────────────────────────────────


def test_unknown_sources_and_escaping_paths_are_refused(sandbox_entry, monkeypatch):
    monkeypatch.setattr(
        sandbox_entry, "_mcp_call_tool",
        lambda *a, **k: pytest.fail("a refused fetch must never reach the source"))

    unknown = asyncio.run(sandbox_entry.t_data_fetch.handler(
        {"server": "crm", "tool": "export"}))
    unknown = json.loads(unknown["content"][0]["text"])
    assert unknown["status"] == 422
    assert "ppc" in unknown["body"]["error"]  # says what IS configured

    for path in ("../outside.csv", "/etc/passwd", ".yaaf/context-run_test.json"):
        blocked = asyncio.run(sandbox_entry.t_data_fetch.handler(
            {"server": "ppc", "tool": "export", "path": path}))
        assert json.loads(blocked["content"][0]["text"])["status"] == 422, path


def test_a_source_error_is_reported_and_nothing_is_written(sandbox_entry, monkeypatch):
    out = _fetch(sandbox_entry, monkeypatch,
                 {"server": "ppc", "tool": "export", "path": "data/x.json"},
                 result=_result(text="rate limit exceeded", is_error=True))
    assert out["status"] == 502
    assert "rate limit exceeded" in out["body"]["error"]
    assert not (sandbox_entry.WORKSPACE / "data/x.json").exists()


def test_an_unreachable_source_is_an_answer_not_a_crash(sandbox_entry, monkeypatch):
    async def boom(config, tool_name, arguments):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(sandbox_entry, "_mcp_call_tool", boom)
    out = json.loads(asyncio.run(sandbox_entry.t_data_fetch.handler(
        {"server": "ppc", "tool": "export"}))["content"][0]["text"])
    assert out["status"] == 0
    assert "connection refused" in out["body"]["error"]


def test_an_oversize_result_says_how_to_ask_for_less(sandbox_entry, monkeypatch):
    monkeypatch.setattr(sandbox_entry, "DATA_FETCH_MAX_BYTES", 64)
    out = _fetch(sandbox_entry, monkeypatch,
                 {"server": "ppc", "tool": "export", "path": "data/big.json"},
                 result=_result(text="x" * 100))
    assert out["status"] == 413
    assert "pagination" in out["body"]["error"]
    assert not (sandbox_entry.WORKSPACE / "data/big.json").exists()


# ── serialization ──────────────────────────────────────────────────────────────


def test_structured_and_multi_block_results_serialize_predictably(
    sandbox_entry, monkeypatch
):
    structured = _fetch(sandbox_entry, monkeypatch,
                        {"server": "ppc", "tool": "rows", "path": "data/s.json"},
                        result=_result(structured=[{"asin": "B1"}, {"asin": "B2"}]))
    assert structured["body"]["rows"] == 2
    assert json.loads((sandbox_entry.WORKSPACE / "data/s.json").read_text()) == [
        {"asin": "B1"}, {"asin": "B2"}]

    multi = _fetch(sandbox_entry, monkeypatch,
                   {"server": "ppc", "tool": "mixed", "path": "data/m.json"},
                   result=_result(blocks=[SimpleNamespace(type="text", text="part one"),
                                          SimpleNamespace(type="image", data="...")]))
    written = json.loads((sandbox_entry.WORKSPACE / "data/m.json").read_text())
    assert written[0] == {"type": "text", "text": "part one"}
    assert written[1]["type"] == "image"
    # A row count is only ever a count of real records, never a guess.
    assert multi["body"]["rows"] is None


def test_listing_a_sources_tools_writes_nothing(sandbox_entry, monkeypatch):
    async def fake_list(config):
        return SimpleNamespace(tools=[
            SimpleNamespace(name="export_spend", description="90 days of spend",
                            inputSchema={"type": "object"})])

    monkeypatch.setattr(sandbox_entry, "_mcp_list_tools", fake_list)
    monkeypatch.setattr(
        sandbox_entry, "_mcp_call_tool",
        lambda *a, **k: pytest.fail("listing must not call a tool"))

    out = json.loads(asyncio.run(sandbox_entry.t_data_fetch.handler(
        {"server": "ppc"}))["content"][0]["text"])
    assert out["status"] == 200
    assert out["body"]["tools"][0]["name"] == "export_spend"
    assert not (sandbox_entry.WORKSPACE / "data").exists()


# ── enforcement: a bulk source has no expensive path ───────────────────────────


def test_a_bulk_source_is_never_mounted_but_data_fetch_still_reaches_it(sandbox_entry):
    sandbox_entry.PERMS = {"mcp_servers": [
        {"name": "ppc", "url_env": "MCP_PPC_URL", "access": "bulk"},
        {"name": "crm", "url_env": "MCP_PPC_URL", "access": "inline"},
    ]}
    mounted = sandbox_entry._external_mcp_servers()
    assert "ppc" not in mounted, "a bulk source must not become tools"
    assert "crm" in mounted
    # No server-level grant either: the tools are not merely discouraged.
    allowed = sandbox_entry._allowed_tools()
    assert "mcp__ppc" not in allowed and "mcp__crm" in allowed
    # But data_fetch still knows it, which is the whole trade.
    assert "ppc" in sandbox_entry._data_source_specs()
    # The mount config stays clean — access is ours, not the SDK's.
    assert "access" not in mounted["crm"]


def test_data_fetch_is_declared_only_when_a_source_exists(sandbox_entry):
    sandbox_entry.PERMS = {}
    assert sandbox_entry.t_data_fetch not in sandbox_entry.run_tools()
    assert "mcp__yaaf__data_fetch" not in sandbox_entry._allowed_tools()

    sandbox_entry.PERMS = {"mcp_servers": [{"name": "ppc", "url_env": "MCP_PPC_URL"}]}
    assert sandbox_entry.t_data_fetch in sandbox_entry.run_tools()
    assert "mcp__yaaf__data_fetch" in sandbox_entry._allowed_tools()


def test_data_processing_grants_the_tool_that_does_the_processing(sandbox_entry):
    # The permission exists so an analyst role can aggregate with code; a role
    # that can fetch bulk data but not process it is worse off than before.
    sandbox_entry.PERMS = {"data_processing": True}
    assert "Bash" in sandbox_entry._builtin_tools()

    sandbox_entry.PERMS = {"bash": True}
    assert "Bash" in sandbox_entry._builtin_tools()

    sandbox_entry.PERMS = {"bash": False, "data_processing": False}
    assert "Bash" not in sandbox_entry._builtin_tools()
