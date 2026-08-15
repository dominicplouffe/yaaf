"""Invariant selftests: per-role egress generation — scoped roles get their own
squid port and allowlist; everyone else stays on the global default."""

from __future__ import annotations

import pytest

from tests.conftest import seed_role
from yaaf.core.db import db_session
from yaaf.runner.sandbox import _proxy_env
from yaaf.server.egress import generate, role_port


def test_generator_writes_ports_and_allowlists(client, tmp_path):
    seed_role(client, "scoped", tool_permissions={
        "bash": True, "egress_allow": ["api.example.com", ".wildcard.dev"],
        "egress_ports": [3845]})
    seed_role(client, "unscoped", tool_permissions={"bash": True})
    out = tmp_path / "generated"
    with db_session() as session:
        assignments = generate(session, out)

    assert set(assignments) == {"scoped"}
    port = assignments["scoped"]
    assert port == role_port("scoped")  # pure function of the slug: runner-computable
    conf = (out / "roles.conf").read_text()
    assert f"http_port {port} name=role_scoped" in conf
    # Default-deny inside the role scope; port discipline before any allow —
    # 80/443 plus the role's declared extras (an MCP server on 3845).
    assert "acl ok_ports_scoped port 80 443 3845" in conf
    assert conf.index("deny port_scoped !ok_ports_scoped") < conf.index("allow port_scoped")
    assert conf.rstrip().endswith("http_access deny port_scoped")
    allowlist = (out / "allowlist_scoped.txt").read_text().splitlines()
    # Bare domains get the subdomain-matching dot; existing dots are kept.
    assert allowlist == [".api.example.com", ".wildcard.dev"]
    assert "unscoped" not in conf


def test_generator_is_idempotent_and_stable(client, tmp_path):
    seed_role(client, "scoped", tool_permissions={"egress_allow": ["a.example"]})
    with db_session() as session:
        first = generate(session, tmp_path / "g")
        second = generate(session, tmp_path / "g")
    assert first == second


def test_port_collision_is_loud(client, tmp_path, monkeypatch):
    seed_role(client, "role-a", tool_permissions={"egress_allow": ["a.example"]})
    seed_role(client, "role-b", tool_permissions={"egress_allow": ["b.example"]})
    monkeypatch.setattr("yaaf.server.egress.role_port", lambda slug: 3200)
    with db_session() as session, pytest.raises(RuntimeError, match="collision"):
        generate(session, tmp_path / "g")


def test_proxy_env_override_targets_role_port():
    env = _proxy_env("http://proxy:3456")
    assert env["HTTP_PROXY"] == "http://proxy:3456"
    assert env["HTTPS_PROXY"] == "http://proxy:3456"
    # The default path still resolves from settings.
    assert _proxy_env(None)["HTTP_PROXY"].startswith("http://")


def test_egress_config_served_over_the_api(client):
    """The CLI writes files on the compose host; the DB lives in a container —
    so the generated config travels over the admin API, not a DB socket."""
    from tests.conftest import ADMIN

    seed_role(client, "scoped", tool_permissions={"egress_allow": ["a.example"],
                                                  "egress_ports": [3845]})
    payload = client.get("/admin/egress/config", headers=ADMIN).json()
    assert payload["assignments"]["scoped"] == role_port("scoped")
    assert "roles.conf" in payload["files"]
    assert "acl ok_ports_scoped port 80 443 3845" in payload["files"]["roles.conf"]
    assert payload["files"]["allowlist_scoped.txt"] == ".a.example\n"
