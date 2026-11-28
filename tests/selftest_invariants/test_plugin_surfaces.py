"""Plugins where people and model clients meet them (build step 9).

Each surface reads the one registry and goes through the one create route, so
the attacks are about reach and agreement:
  * an MCP client sees the kinds and the spec, and creates a plugin project
    only through the same checks the form does; it never installs one;
  * the Founder can list the kinds, and its tools still do nothing but read
    and draft;
  * the kind list carries the plugin's own create fields, and none when it
    cannot be asked;
  * the CLI's plugin commands are the REST routes, and `yaaf plugin check` is
    the API package's own check.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from tests.conftest import ADMIN, make_project, seed_role
from tests.selftest_invariants.test_mcp_founding import GOOD_SPEC, call, confirmed, founder
from yaaf.cli import main as cli
from yaaf.core.config import settings
from yaaf.core.db import db_session
from yaaf.core.models import Project
from yaaf.plugins import host as H
from yaaf.plugins import registry
from yaaf.server.mcp.registry import TOOLS

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "packages/yaaf_plugin_api/examples/grant-factory"
KIND = "plugin:grant_application"


@pytest.fixture
def installed(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "plugins_root", str(tmp_path / "installed"))
    monkeypatch.setattr(settings(), "allow_local_plugins", True)
    src = tmp_path / "checkout"
    shutil.copytree(EXAMPLE, src, ignore=shutil.ignore_patterns("__pycache__"))
    body = client.post("/admin/plugins/local", json={"path": str(src)}, headers=ADMIN).json()
    assert body["status"] == "ready", body
    yield body
    registry.close_all()


def test_the_kind_list_carries_the_plugins_own_create_fields(client, installed, monkeypatch):
    kinds = client.get("/admin/kinds", headers=ADMIN).json()["kinds"]
    [grant] = [k for k in kinds if k["kind"] == KIND]
    fields = {f["key"]: f for f in grant["fields"]}
    assert fields["funder"]["required"] is True and fields["amount"]["default"] == 10000
    dead = H.HostPool(H.HostSpec.from_checkout(EXAMPLE),
                      H.BrokerLauncher(("127.0.0.1", 1), b"k" * 32))
    monkeypatch.setattr(registry, "pool_for", lambda version: dead)
    [grant] = [k for k in client.get("/admin/kinds", headers=ADMIN).json()["kinds"]
               if k["kind"] == KIND]
    assert grant["fields"] == []  # nothing offered; creation itself refuses, saying why


# ── MCP ────────────────────────────────────────────────────────────────────────


def test_a_model_client_sees_the_kinds_and_the_spec_but_cannot_install(client, installed):
    make_project(client, slug="seed")
    token = founder(client)["token"]
    failed, kinds = call(client, token, "catalog", {"what": "kinds"})
    assert failed is False
    assert KIND in {k["kind"] for k in kinds["kinds"]}
    failed, spec = call(client, token, "plugin_spec")
    assert failed is False and spec["api_version"] == "1.0"
    assert "truncated" not in spec and "oversize_bytes" not in spec
    from yaaf_plugin_api.requirements import REQUIREMENTS

    assert [r["id"] for r in spec["requirements"]] == [r.id for r in REQUIREMENTS]
    assert spec["manifest_schema"]["title"].startswith("yaaf factory plugin manifest")
    # Installing is the instance admin's, on their own screen: no tool reaches it.
    assert not [name for name in TOOLS if "install" in name and "plugin" in name]
    from yaaf.server.mcp.surface import WITHHELD

    assert "POST /admin/plugins" in WITHHELD and "POST /admin/plugins/local" in WITHHELD


def test_a_model_client_creates_a_plugin_project_through_the_same_checks(client, installed):
    make_project(client, slug="seed")
    seed_role(client, "grant-lead", is_ceo=True)
    token = founder(client)["token"]
    spec = {**{k: v for k, v in GOOD_SPEC.items() if k != "mode"}, "slug": "garden",
            "kind": KIND, "ceo_role_slug": "grant-lead", "kind_fields": {"amount": 0}}
    failed, said = call(client, token, "create_project", {"spec": spec})
    assert failed is False and said.get("confirm_required")
    failed, said = confirmed(client, token, "create_project", {"spec": spec})
    assert failed is True and "funder (Funder) is required" in said
    spec["kind_fields"] = {"funder": "Example Foundation"}
    failed, made = confirmed(client, token, "create_project", {"spec": spec})
    assert failed is False, made
    with db_session() as s:
        row = s.query(Project).filter_by(slug="garden").one()
        assert (row.kind, row.kind_mode, row.mode) == (KIND, "draft", "deliver")


# ── the Founder ────────────────────────────────────────────────────────────────


def test_the_founder_can_list_the_kinds_and_its_draft_may_name_one(client, installed):
    from yaaf.founder import tools

    assert "list_kinds" in tools.TOOL_NAMES
    with db_session() as s:
        kinds = tools.list_kinds(s)["kinds"]
    assert {k["kind"] for k in kinds} >= {"deliver", KIND}
    props = tools.PROPOSE_SCHEMA["properties"]["project"]["properties"]
    assert {"kind", "kind_mode", "kind_fields"} <= set(props)
    # The endpoint's own model takes the draft; an unknown kind is the route's to refuse.
    assert tools.validate({"project": {**GOOD_SPEC, "kind": KIND,
                                       "kind_fields": {"funder": "F"}}}) == []


# ── the CLI ────────────────────────────────────────────────────────────────────


@pytest.fixture
def terminal(client, monkeypatch):
    as_admin = TestClient(client.app, headers=ADMIN)
    monkeypatch.setattr(cli, "_client", lambda: contextlib.nullcontext(as_admin))
    return as_admin


def test_the_cli_lists_reports_and_disables_through_the_routes(terminal, installed):
    runner = CliRunner()
    out = runner.invoke(cli.app, ["plugins", "list"])
    assert out.exit_code == 0 and "grant-factory" in out.output
    out = runner.invoke(cli.app, ["plugins", "report", "grant-factory", installed["version"]])
    assert out.exit_code == 0 and f"grant-factory@{installed['version']}: ready" in out.output
    out = runner.invoke(cli.app, ["plugins", "kinds"])
    assert out.exit_code == 0 and KIND in out.output
    out = runner.invoke(cli.app, ["plugins", "disable", "grant-factory", installed["version"]])
    assert out.exit_code == 0 and "disabled" in out.output
    out = runner.invoke(cli.app, ["plugins", "install", "http://example.com/x", "--no-wait"])
    assert "https://github.com/" in out.output


def test_yaaf_plugin_check_is_the_api_packages_own_check(tmp_path):
    runner = CliRunner()
    out = runner.invoke(cli.app, ["plugin", "check", str(EXAMPLE), "--static"])
    assert out.exit_code == 0 and "grant-factory 0.1.0" in out.output
    out = runner.invoke(cli.app, ["plugin", "check", str(tmp_path), "--static"])
    assert out.exit_code == 1
    out = runner.invoke(cli.app, ["plugin", "spec"])
    assert out.exit_code == 0 and json.loads(out.output)["api_version"] == "1.0"
