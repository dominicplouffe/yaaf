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


def test_unrestricted_role_gets_a_port_with_no_destination_acl(client, tmp_path):
    """Open web: every host, still only 80/443. The role needs a port of its
    own even with no domains — without one the runner would fall back to the
    default port, which is the *most* restricted place to land."""
    seed_role(client, "openweb", tool_permissions={
        "bash": True, "egress_unrestricted": True, "egress_ports": [8443]})
    out = tmp_path / "generated"
    with db_session() as session:
        assignments = generate(session, out)

    assert assignments["openweb"] == role_port("openweb")
    conf = (out / "roles.conf").read_text()
    assert "http_access allow port_openweb\n" in conf + "\n"
    # No destination ACL, and therefore no allowlist file to point one at.
    assert "dst_openweb" not in conf
    assert not (out / "allowlist_openweb.txt").exists()
    # Port discipline survives: open web is not open ports.
    assert "acl ok_ports_openweb port 80 443 8443" in conf
    assert conf.index("deny port_openweb !ok_ports_openweb") < conf.index(
        "allow port_openweb")


def test_unrestricted_wins_over_a_domain_list(client, tmp_path):
    """Both set is a legible state, not a contradiction to refuse: the list is
    kept so unticking the box restores the old scope exactly."""
    seed_role(client, "both", tool_permissions={
        "egress_allow": ["api.example.com"], "egress_unrestricted": True})
    out = tmp_path / "generated"
    with db_session() as session:
        generate(session, out)
    conf = (out / "roles.conf").read_text()
    assert "dst_both" not in conf
    assert not (out / "allowlist_both.txt").exists()


def test_unrestricted_role_reaches_its_own_port(client):
    """The runner reads the PINNED version's permissions — a role that is only
    open-web (no domain list) must still route to its scoped port."""
    from yaaf.runner.daemon import _role_egress_url

    seed_role(client, "openweb2", tool_permissions={"egress_unrestricted": True})
    with db_session() as session:
        from sqlalchemy import select

        from yaaf.core.models import Role, RoleVersion
        role = session.execute(select(Role).where(Role.slug == "openweb2")).scalar_one()
        version = session.execute(
            select(RoleVersion).where(RoleVersion.role_id == role.id)
        ).scalar_one()
        url = _role_egress_url(session, version, version.tool_permissions)
    assert url is not None and url.endswith(f":{role_port('openweb2')}")


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
