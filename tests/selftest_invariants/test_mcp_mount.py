"""Invariant selftests: external MCP servers mount from the lease-time-merged
permissions (project data sources assigned to the role) — and always through
the platform's gateway, never from an address or token in the environment.

They used to mount from role-scoped secret env vars: the source's URL and its
bearer token, inside the sandbox, one `curl` away from any check on which tools
could be called. A sandbox now gets the gateway's address and its own run
token, and nothing that reaches the source around the gateway."""

from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture()
def sandbox_entry(monkeypatch):
    monkeypatch.setenv("YAAF_API_BASE_URL", "http://api:8080")
    monkeypatch.setenv("YAAF_API_TOKEN", "test-token")
    monkeypatch.setenv("YAAF_RUN_ID", "run_test")
    monkeypatch.setenv("YAAF_TOOL_PERMISSIONS", "{}")
    import yaaf.runner.sandbox_entry as mod

    return importlib.reload(mod)


def test_external_mcp_servers_mount_through_the_gateway(sandbox_entry, monkeypatch):
    sandbox_entry.PERMS = {
        "mcp_servers": [
            # A spec minted before the gateway still names env vars; they are
            # ignored, and holding them changes nothing.
            {"name": "ppc", "url_env": "MCP_SERVER_URL", "bearer_env": "MCP_SERVER_TOKEN"},
            {"name": "events", "transport": "sse"},
        ]
    }
    monkeypatch.setenv("MCP_SERVER_URL", "https://data.example.com:3845/mcp")
    monkeypatch.setenv("MCP_SERVER_TOKEN", "tok-123")
    servers = sandbox_entry._external_mcp_servers()
    assert servers["ppc"] == {
        "type": "http",
        "url": "http://api:8080/agent/mcp/ppc",
        "headers": {"Authorization": "Bearer test-token"},
    }
    # The gateway speaks streamable HTTP whatever the source does upstream.
    assert servers["events"]["url"] == "http://api:8080/agent/mcp/events"
    assert "tok-123" not in repr(servers) and "data.example.com" not in repr(servers)
    # Server-level tool grant appears alongside the yaaf platform tools.
    allowed = sandbox_entry._allowed_tools()
    assert "mcp__ppc" in allowed and "mcp__events" in allowed


def test_a_source_is_granted_tool_by_tool_when_the_lease_names_its_tools(sandbox_entry):
    sandbox_entry.PERMS = {"mcp_servers": [{"name": "ppc", "tools": ["get_stats"]}]}
    allowed = sandbox_entry._allowed_tools()
    assert "mcp__ppc__get_stats" in allowed and "mcp__ppc" not in allowed


def test_builtin_tools_mirror_permissions(sandbox_entry):
    # Defaults: files on, bash and web off — and never Task/TodoWrite/etc.
    sandbox_entry.PERMS = {}
    assert sandbox_entry._builtin_tools() == ["Read", "Write", "Edit", "Glob", "Grep"]

    sandbox_entry.PERMS = {"files": True, "bash": True, "web": True}
    tools = sandbox_entry._builtin_tools()
    assert "Bash" in tools and "WebFetch" in tools and "WebSearch" in tools

    # Everything off → no built-in declarations at all: an undeclared schema
    # is input tokens the role would otherwise pay on every call for nothing.
    sandbox_entry.PERMS = {"files": False, "bash": False, "web": False}
    assert sandbox_entry._builtin_tools() == []
    assert all(t.startswith("mcp__yaaf__") for t in sandbox_entry._allowed_tools())


def test_no_declaration_means_yaaf_only(sandbox_entry):
    sandbox_entry.PERMS = {}
    os.environ["MCP_SERVER"] = "https://data.example.com/mcp"
    try:
        # An env var alone mounts nothing: a project data source must be
        # assigned to this role for a server to exist.
        assert sandbox_entry._external_mcp_servers() == {}
    finally:
        del os.environ["MCP_SERVER"]
