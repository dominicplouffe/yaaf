"""Invariant selftests: project-level data sources — one save wires the URL,
the secrets, the runner merge, and the egress derivation; garbage is
unstorable; delete cleans up completely."""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Project, RunKind, RunQueue, Secret
from yaaf.runner import daemon
from yaaf.server import egress
from yaaf.server.api import admin as admin_module
from yaaf.server.contextpack import role_version_for


@pytest.fixture(autouse=True)
def offline_check(monkeypatch):
    """Reachability checks never leave the test box."""
    monkeypatch.setattr(admin_module, "_check_data_source",
                        lambda url, bearer: {"ok": True, "detail": "reachable (HTTP 200)"})


def _save(client, project_id, **overrides):
    body = {"name": "ppc", "url": "http://data.example.com:3845/mcp",
            "roles": ["worker"], **overrides}
    return client.post(f"/admin/projects/{project_id}/mcp_servers", json=body,
                       headers=ADMIN)


def test_garbage_is_unstorable(client):
    project = make_project(client, start=False)
    refused = _save(client, project["id"], url="wrath1q2w!")
    assert refused.status_code == 422
    assert "not a URL" in refused.json()["detail"]
    with db_session() as session:
        assert "mcp_servers" not in (session.get(Project, project["id"]).config or {})
        assert session.query(Secret).filter(Secret.name.like("mcp-%")).count() == 0

    unknown_role = _save(client, project["id"], roles=["nonexistent"])
    assert unknown_role.status_code == 422


def test_one_save_wires_config_secrets_and_runner(client):
    project = make_project(client)
    saved = _save(client, project["id"], bearer="tok-123")
    assert saved.status_code == 200, saved.text
    assert saved.json()["check"]["ok"] is True

    with db_session() as session:
        entry = session.get(Project, project["id"]).config["mcp_servers"][0]
        assert (entry["url_host"], entry["url_port"]) == ("data.example.com", 3845)
        assert entry["last_check"]["ok"] is True
        url_secret = session.query(Secret).filter_by(name="mcp-ppc-url").one()
        assert url_secret.role_scopes == ["worker"] and url_secret.kind == "service"

    # The runner merges it at lease time — no role edit, no repin involved.
    employee = project["teams"][0]["agents"][0]["id"]
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=employee, kind=RunKind.AGENT_RUN,
                             trigger={"reasons": []}, attempt=1))
    jobs = daemon.lease_and_prepare(limit=1)
    assert len(jobs) == 1
    job = jobs[0]
    specs = job.tool_permissions["mcp_servers"]
    assert {"name": "ppc", "url_env": "MCP_PPC_URL",
            "bearer_env": "MCP_PPC_TOKEN"} in specs
    assert job.billing_env["MCP_PPC_URL"] == "http://data.example.com:3845/mcp"
    assert "DATA SOURCES: ppc (tools mcp__ppc__*)" in job.system_prompt
    # The role routes through its scoped egress port because a host is mapped.
    assert job.egress_proxy_url is not None
    assert job.egress_proxy_url.endswith(str(egress.role_port("worker")))


def test_egress_derives_hosts_and_ports(client):
    project = make_project(client, start=False)
    assert _save(client, project["id"]).status_code == 200
    with db_session() as session:
        assignments, files = egress.build(session)
    assert "worker" in assignments
    assert ".data.example.com" in files["allowlist_worker.txt"].splitlines()
    assert "acl ok_ports_worker port 80 443 3845" in files["roles.conf"]
    # The scoped port keeps the platform floor: without the global allowlist
    # (where the model API lives) a scoped role's sandbox cannot run at all.
    rules = files["roles.conf"].splitlines()
    assert 'acl dst_floor dstdomain "/etc/squid/allowlist.txt"' in rules
    assert rules.index("http_access allow port_worker dst_floor") \
        < rules.index("http_access deny port_worker")


def test_stale_role_declared_servers_are_never_mounted(client):
    """Role versions minted before data sources became project-owned may still
    freeze a legacy tool_permissions.mcp_servers key (the API refuses new
    ones; the migration scrubs stored ones). Belt and braces: even a version
    that somehow kept the key mounts nothing — no ghost sources."""
    project = make_project(client)
    employee = project["teams"][0]["agents"][0]["id"]
    with db_session() as session:
        pinned = role_version_for(session, session.get(Agent, employee))
        pinned.tool_permissions = {
            **(pinned.tool_permissions or {}),
            "mcp_servers": [{"name": "ppc", "url_env": "MCP_SERVER"}],
        }
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=employee, kind=RunKind.AGENT_RUN,
                             trigger={"reasons": []}, attempt=1))
    jobs = daemon.lease_and_prepare(limit=1)
    assert len(jobs) == 1
    assert "mcp_servers" not in jobs[0].tool_permissions
    assert "DATA SOURCES" not in jobs[0].system_prompt


def test_a_source_address_stays_a_url_through_the_secrets_door(client):
    """An imported setup file rebuilds a data source's wiring, then re-asks for
    its address one plain credential at a time — POST /admin/secrets, not the
    Data Sources form. That door must apply the same rule, or the project is
    born with a source that mounts nothing and only says so mid-run."""
    project = make_project(client, start=False)
    _save(client, project["id"])  # config now names mcp-ppc-url as ppc's address

    for garbage in ("data.example.com:3845", "wrath1q2w!", "Bearer tok-123"):
        refused = client.post("/admin/secrets", json={
            "project_id": project["id"], "name": "mcp-ppc-url",
            "value": garbage, "role_scopes": ["worker"], "kind": "service",
        }, headers=ADMIN)
        assert refused.status_code == 422, garbage
        detail = refused.json()["detail"]
        assert "data source 'ppc'" in detail and "full URL" in detail

    # Ordinary credentials stay opaque — the rule is about addresses, not names.
    opaque = client.post("/admin/secrets", json={
        "project_id": project["id"], "name": "wordpress",
        "value": "not-a-url-and-never-will-be", "role_scopes": ["worker"], "kind": "service",
    }, headers=ADMIN)
    assert opaque.status_code == 200, opaque.text

    # A real address goes through, trailing paste whitespace and all.
    stored = client.post("/admin/secrets", json={
        "project_id": project["id"], "name": "mcp-ppc-url",
        "value": "  https://data.example.com:3845/mcp\n", "role_scopes": ["worker"],
    }, headers=ADMIN)
    assert stored.status_code == 200, stored.text
    with db_session() as session:
        from yaaf.core.crypto import decrypt

        row = session.query(Secret).filter_by(project_id=project["id"],
                                              name="mcp-ppc-url").one()
        assert decrypt(row.ciphertext) == "https://data.example.com:3845/mcp"


def test_editing_the_url_alone_keeps_the_token(client, monkeypatch):
    """The edit path saves under the same name. Changing only the address must
    not disarm the auth header — a source that silently loses its token fails
    hours later inside an agent run, which is the failure this form exists to
    prevent. Omitted = keep, "" = remove, a value = rotate."""
    from yaaf.core.crypto import decrypt

    project = make_project(client, start=False)
    _save(client, project["id"], bearer="tok-123")

    # Omitted: the token survives, and the preflight still sees it.
    seen: list[str | None] = []

    def watching_check(url, bearer):
        seen.append(bearer)
        return {"ok": True, "detail": "reachable (HTTP 200)"}

    monkeypatch.setattr(admin_module, "_check_data_source", watching_check)
    moved = _save(client, project["id"], url="http://data.example.com:9999/mcp")
    assert moved.status_code == 200, moved.text
    assert seen == ["tok-123"], "the carried token must be used to preflight"
    with db_session() as session:
        entry = session.get(Project, project["id"]).config["mcp_servers"][0]
        assert entry["url_port"] == 9999
        assert entry["bearer_secret"] == "mcp-ppc-token"
        token = session.query(Secret).filter_by(name="mcp-ppc-token").one()
        assert decrypt(token.ciphertext) == "tok-123"

    # A value rotates it.
    _save(client, project["id"], bearer="tok-456")
    with db_session() as session:
        token = session.query(Secret).filter_by(name="mcp-ppc-token").one()
        assert decrypt(token.ciphertext) == "tok-456"

    # Empty string is the explicit removal, and leaves no orphan row behind.
    _save(client, project["id"], bearer="")
    with db_session() as session:
        entry = session.get(Project, project["id"]).config["mcp_servers"][0]
        assert "bearer_secret" not in entry
        assert session.query(Secret).filter_by(name="mcp-ppc-token").count() == 0


def test_editing_roles_rescopes_both_halves(client):
    """Grants live on the secrets, so an edit that changes who gets the source
    has to move the token with the address — otherwise the role mounts nothing
    and only finds out mid-run."""
    project = make_project(client, start=False)
    _save(client, project["id"], bearer="tok-123", roles=["worker"])
    _save(client, project["id"], roles=["ceo"])

    with db_session() as session:
        for name in ("mcp-ppc-url", "mcp-ppc-token"):
            assert session.query(Secret).filter_by(name=name).one().role_scopes == ["ceo"]


def test_delete_cleans_up_completely(client):
    project = make_project(client, start=False)
    _save(client, project["id"], bearer="tok-123")
    deleted = client.delete(f"/admin/projects/{project['id']}/mcp_servers/ppc",
                            headers=ADMIN)
    assert deleted.status_code == 200
    with db_session() as session:
        assert session.get(Project, project["id"]).config["mcp_servers"] == []
        assert session.query(Secret).filter(Secret.name.like("mcp-ppc%")).count() == 0
    assert client.delete(f"/admin/projects/{project['id']}/mcp_servers/ppc",
                         headers=ADMIN).status_code == 404


def test_egress_watch_applies_only_on_change(client, tmp_path, monkeypatch):
    hups: list[bool] = []
    monkeypatch.setattr(daemon, "_reload_proxy", lambda: hups.append(True))
    project = make_project(client, start=False)
    _save(client, project["id"])

    with db_session() as session:
        first = daemon.egress_watch_once(session, tmp_path, None)
    assert (tmp_path / "roles.conf").exists() and len(hups) == 1

    # Unchanged config: no rewrite, no HUP — even if the files vanished.
    (tmp_path / "roles.conf").unlink()
    with db_session() as session:
        second = daemon.egress_watch_once(session, tmp_path, first)
    assert second == first
    assert not (tmp_path / "roles.conf").exists() and len(hups) == 1

    # A config change re-applies.
    client.delete(f"/admin/projects/{project['id']}/mcp_servers/ppc", headers=ADMIN)
    with db_session() as session:
        third = daemon.egress_watch_once(session, tmp_path, second)
    assert third != second
    assert (tmp_path / "roles.conf").exists() and len(hups) == 2
