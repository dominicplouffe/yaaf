"""Invariant selftests: external MCP servers mount from role permissions plus
role-scoped secret env vars — and never from garbage."""

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


def test_external_mcp_servers_mount_from_role_and_env(sandbox_entry, monkeypatch):
    sandbox_entry.PERMS = {
        "mcp_servers": [
            {"name": "ppc", "url_env": "MCP_SERVER_URL", "bearer_env": "MCP_SERVER_TOKEN"},
            {"name": "events", "url_env": "EVENTS_URL", "transport": "sse"},
        ]
    }
    monkeypatch.setenv("MCP_SERVER_URL", "https://data.example.com:3845/mcp")
    monkeypatch.setenv("MCP_SERVER_TOKEN", "tok-123")
    monkeypatch.setenv("EVENTS_URL", "http://events.example.com/sse")
    servers = sandbox_entry._external_mcp_servers()
    assert servers["ppc"] == {
        "type": "http",
        "url": "https://data.example.com:3845/mcp",
        "headers": {"Authorization": "Bearer tok-123"},
    }
    assert servers["events"] == {"type": "sse", "url": "http://events.example.com/sse"}
    # Server-level tool grant appears alongside the yaaf platform tools.
    allowed = sandbox_entry._allowed_tools()
    assert "mcp__ppc" in allowed and "mcp__events" in allowed


def test_ungranted_or_garbage_values_mount_nothing(sandbox_entry):
    sandbox_entry.PERMS = {
        "mcp_servers": [{"name": "ppc", "url_env": "MCP_SERVER"}],
    }
    # Role not granted the secret: env var absent → no server, silently —
    # the same way an ungranted role gets no secret.
    assert sandbox_entry._external_mcp_servers({}) == {}
    # A password-shaped value is not an address; mounting it would produce
    # exactly the confusing dead-ends this feature exists to prevent.
    assert sandbox_entry._external_mcp_servers({"MCP_SERVER": "wrath1q2w!"}) == {}
    assert "mcp__ppc" not in sandbox_entry._allowed_tools()


def test_no_declaration_means_yaaf_only(sandbox_entry):
    sandbox_entry.PERMS = {}
    os.environ["MCP_SERVER"] = "https://data.example.com/mcp"
    try:
        # An env var alone mounts nothing: the ROLE must declare the server.
        assert sandbox_entry._external_mcp_servers() == {}
    finally:
        del os.environ["MCP_SERVER"]
