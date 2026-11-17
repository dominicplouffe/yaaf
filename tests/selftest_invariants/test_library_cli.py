"""`yaaf library` and `yaaf install`, from a terminal.

The same doors as the dashboard, so the same refusals: a role installs beside
one of the same name and never over it, and a project is not written while a
blank is still empty.
"""

from __future__ import annotations

import contextlib
import json

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from tests.conftest import ADMIN, seed_role
from tests.selftest_invariants.test_library_install import WRITER, Registry
from yaaf.cli import main as cli
from yaaf.server import library

runner = CliRunner()


@pytest.fixture
def registry(tmp_path, monkeypatch):
    reg = Registry(tmp_path / "reg")
    reg.root.mkdir()
    reg.publish("@rae/writer", "role", {"role": WRITER})
    reg.publish("@rae/desk", "project", {
        "bundle": {"yaaf_bundle": 1, "kind": "project_setup", "values_included": False,
                   "project": {"slug": "desk", "name": "Desk", "goal": "Write about {{subject}} weekly.",
                               "ceo_role_slug": "ceo", "teams": [
                                   {"role_slug": "writer", "name": "Words", "employees": ["Ann"]}]},
                   "roles": [WRITER]},
        "uses": [{"id": "@rae/writer", "version": 1, "slug": "writer"}],
        "inputs": [{"id": "subject", "question": "About what?", "type": "text",
                    "example": "tea", "required": True}]})
    monkeypatch.setattr(library, "configured_sources", lambda: [reg.url])
    yield reg
    library.clear_cache()


@pytest.fixture
def terminal(client, monkeypatch, tmp_path):
    as_admin = TestClient(client.app, headers=ADMIN)
    monkeypatch.setattr(cli, "_client", lambda: contextlib.nullcontext(as_admin))
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.chdir(tmp_path)
    return as_admin


def test_search_lists_both_kinds(client, terminal, registry):
    out = runner.invoke(cli.app, ["library", "search"])
    assert out.exit_code == 0, out.output
    assert "@rae/writer" in out.output and "@rae/desk" in out.output


def test_install_a_role_beside_our_own(client, terminal, registry):
    seed_role(client, "writer", system_prompt="Our own writer, years in the making.")
    out = runner.invoke(cli.app, ["install", "@rae/writer"])
    assert out.exit_code == 0, out.output
    assert "writer-rae" in out.output and "your own writer is untouched" in out.output
    assert client.get("/admin/roles/writer", headers=ADMIN).json()["version"] == 1


def test_a_project_is_not_written_with_a_blank_left(client, terminal, registry, tmp_path):
    out = runner.invoke(cli.app, ["install", "@rae/desk"])
    assert out.exit_code == 2 and "--answer subject=" in out.output
    assert not (tmp_path / "desk-setup.json").exists()


def test_a_project_is_written_with_its_answers(client, terminal, registry, tmp_path):
    out = runner.invoke(cli.app, ["install", "@rae/desk", "--answer", "subject=green tea"])
    assert out.exit_code == 0, out.output
    written = json.loads((tmp_path / "desk-setup.json").read_text())
    assert written["project"]["goal"] == "Write about green tea weekly."
    assert written["roles"][0]["provenance"]["source"] == "@rae/writer"


def test_updates_says_what_changed(client, terminal, registry):
    runner.invoke(cli.app, ["install", "@rae/writer"])
    registry.publish("@rae/writer", "role", {"role": {**WRITER, "description": "Sharper."}})
    out = runner.invoke(cli.app, ["library", "updates"])
    assert "v2 available" in out.output and "yaaf install @rae/writer --version 2" in out.output
