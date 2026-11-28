"""A running project moves onto a newer version of its plugin (§5.5).

How a bug fixed in a plugin reaches a project created before the fix: the
chairman moves the project to another installed version of the same plugin.
The project's state goes through the new version's migrate_state, the move is
in the audit log, and from then on the project's hooks run on the new version.
Then every way a move is refused, with the project left exactly where it was:
not the chairman, a version that did not pass, another plugin, the same
version, a new lead, a lost mode, a migration that fails, a built-in project.

Each test installs the example plugin twice (two versions) and starts real
hosts, about 1 s.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from tests.conftest import ADMIN, make_project, person, seed_role
from yaaf.core.config import settings
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog, PluginVersion, Project
from yaaf.plugins import primitives, registry

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "packages/yaaf_plugin_api/examples/grant-factory"
KIND = "plugin:grant_application"
MODULE = "src/grant_factory/__init__.py"
MIGRATE = "        return dict(old_state)\n"


def checkout(tmp_path: Path, name: str, *, release: str = "0.1.0",
             edits: dict[str, tuple[str, str]] | None = None) -> Path:
    """A copy of the example plugin, as a version of it would be."""
    root = tmp_path / name
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = root / "yaaf-plugin.toml"
    manifest.write_text(manifest.read_text().replace('version     = "0.1.0"',
                                                     f'version     = "{release}"'))
    for rel, (old, new) in (edits or {}).items():
        path = root / rel
        text = path.read_text()
        assert old in text, (rel, old)
        path.write_text(text.replace(old, new))
    return root


def install(client, root: Path) -> dict:
    r = client.post("/admin/plugins/local", json={"path": str(root)}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def box(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "plugins_root", str(tmp_path / "installed"))
    monkeypatch.setattr(settings(), "allow_local_plugins", True)
    monkeypatch.setattr(settings(), "allow_unverified_plugins", True)
    seed_role(client, "grant-lead", is_ceo=True)
    old = install(client, checkout(tmp_path, "v1"))
    project = make_project(client, slug="garden", kind=KIND, ceo_role_slug="grant-lead",
                           kind_fields={"funder": "Example Foundation"})
    with db_session() as s:  # the state an accepted application leaves behind
        s.get(Project, project["id"]).kind_state = {"create": {"funder": "Example Foundation",
                                                               "amount": 10000},
                                                    "asked": 9000}
    yield {"old": old, "project": project, "tmp": tmp_path}
    registry.close_all()


def fixed(client, box, **kw) -> dict:
    """Version 0.2.0: the fix, with a migrate_state that marks what it did."""
    kw.setdefault("edits", {MODULE: (MIGRATE, "        return {**old_state, "
                                              "'migrated_from': from_version}\n")})
    return install(client, checkout(box["tmp"], kw.pop("name", "v2"), release="0.2.0", **kw))


def move(client, box, version: str, headers=ADMIN):
    return client.post(f"/admin/projects/{box['project']['id']}/plugin",
                       json={"version": version}, headers=headers)


def pinned(box) -> tuple[str, dict]:
    with db_session() as s:
        row = s.get(Project, box["project"]["id"])
        return row.plugin_version_id, dict(row.kind_state or {})


# ── moving ─────────────────────────────────────────────────────────────────────


def test_the_chairman_moves_a_running_project_onto_the_fixed_version(client, box):
    new = fixed(client, box)
    listed = client.get(f"/admin/projects/{box['project']['id']}/plugin", headers=ADMIN).json()
    assert listed["current"]["release"] == "0.1.0"
    assert [v["version"] for v in listed["available"]] == [new["version"]]

    r = move(client, box, new["version"])
    assert r.status_code == 200, r.text
    assert r.json()["moved"] | {} == {"plugin": "grant-factory", "from": box["old"]["version"],
                                     "from_release": "0.1.0", "to": new["version"],
                                     "to_release": "0.2.0"}
    version_id, state = pinned(box)
    assert version_id == new["id"]
    # The new version's migrate_state carried the state; the creation answers stay.
    assert state == {"create": {"funder": "Example Foundation", "amount": 10000},
                     "asked": 9000, "migrated_from": "0.1.0"}
    with db_session() as s:
        row = s.get(Project, box["project"]["id"])
        version = s.get(PluginVersion, row.plugin_version_id)
        ctx = primitives.snapshot(s, row, version, registry.manifest_for(version))
        assert ctx.plugin_version == new["version"]
        audit = s.execute(select(AuditLog).where(AuditLog.action == "plugin.move")).scalar_one()
        assert audit.project_id == row.id and audit.detail["to_release"] == "0.2.0"
    # The old version now runs nothing, so it can be removed.
    gone = client.delete(f"/admin/plugins/grant-factory/versions/{box['old']['version']}",
                         headers=ADMIN)
    assert gone.status_code == 200, gone.text


def test_the_plugin_list_says_which_release_each_version_is_and_who_uses_it(client, box):
    new = fixed(client, box)
    [plugin] = client.get("/admin/plugins", headers=ADMIN).json()["plugins"]
    rows = {v["version"]: v for v in plugin["versions"]}
    old = rows[box["old"]["version"]]
    assert (old["release"], old["projects"], old["for_new_projects"]) == ("0.1.0", 1, False)
    latest = rows[new["version"]]
    assert (latest["release"], latest["projects"], latest["for_new_projects"]) == \
        ("0.2.0", 0, True)


# ── every refusal leaves the project where it was ──────────────────────────────


def test_only_the_projects_chairman_may_move_it(client, box):
    new = fixed(client, box)
    stranger = person("someone-else@example.com")
    r = move(client, box, new["version"], headers={"Authorization": stranger["Authorization"]})
    assert r.status_code in (403, 404), r.text
    assert pinned(box)[0] == box["old"]["id"]


def test_a_version_that_is_not_offered_cannot_be_moved_to(client, box):
    new = fixed(client, box)
    client.post(f"/admin/plugins/grant-factory/versions/{new['version']}/disable", headers=ADMIN)
    r = move(client, box, new["version"])
    assert r.status_code == 409 and "passed every check" in r.json()["detail"]
    assert pinned(box)[0] == box["old"]["id"]


def test_the_version_it_already_runs_on_is_no_move(client, box):
    r = move(client, box, box["old"]["version"])
    assert r.status_code == 409 and "already runs" in r.json()["detail"]


def test_a_version_of_another_plugin_is_not_found_for_this_one(client, box):
    root = checkout(box["tmp"], "other")
    manifest = root / "yaaf-plugin.toml"
    manifest.write_text(manifest.read_text().replace('"grant-factory"', '"other-factory"')
                        .replace('"grant_application"', '"other_application"'))
    other = install(client, root)
    r = move(client, box, other["version"])
    assert r.status_code == 404, r.text
    assert pinned(box)[0] == box["old"]["id"]


def test_a_version_with_another_lead_is_a_new_project_not_a_move(client, box):
    root = checkout(box["tmp"], "v2-lead", release="0.2.0")
    role = (root / "roles/grant-lead.json").read_text().replace('"grant-lead"', '"grant-chief"')
    (root / "roles/grant-lead.json").unlink()
    (root / "roles/grant-chief.json").write_text(role)
    for rel in ("yaaf-plugin.toml", "crews/grant-writers.json"):
        path = root / rel
        path.write_text(path.read_text().replace('"grant-lead"', '"grant-chief"'))
    new = install(client, root)
    r = move(client, box, new["version"])
    assert r.status_code == 422 and "new lead" in r.json()["detail"], r.text
    assert pinned(box)[0] == box["old"]["id"]


def test_a_version_that_closes_the_projects_mode_is_refused(client, box):
    new = fixed(client, box, name="v2-mode", edits={"yaaf-plugin.toml": (
        'key   = "draft"\n  shape = "ships_once"\n  open  = true',
        'key   = "draft"\n  shape = "ships_once"\n  open  = false\n\n'
        '  [[kind.modes]]\n  key   = "write"\n  shape = "ships_once"\n  open  = true')})
    r = move(client, box, new["version"])
    assert r.status_code == 422 and "no open mode 'draft'" in r.json()["detail"], r.text
    assert pinned(box)[0] == box["old"]["id"]


def test_a_migration_that_fails_leaves_the_state_and_the_version_alone(client, box):
    """This version cannot migrate from 0.1.0 (it raises for that release only,
    so its own conformance run, which migrates from 0.0.0, still passes)."""
    new = fixed(client, box, name="v2-bad", edits={MODULE: (MIGRATE, (
        "        if from_version == '0.1.0':\n"
        "            raise ValueError('no path from 0.1.0')\n" + MIGRATE))})
    before = pinned(box)
    r = move(client, box, new["version"])
    assert r.status_code == 409 and "stays where it is" in r.json()["detail"], r.text
    assert pinned(box) == before


def test_a_built_in_project_has_no_plugin_to_move(client, box):
    plain = make_project(client, slug="plain")
    r = client.post(f"/admin/projects/{plain['id']}/plugin", json={"version": "x"},
                    headers=ADMIN)
    assert r.status_code == 409 and "not a plugin project" in r.json()["detail"]


def test_yaaf_plugins_move_lists_then_moves_through_the_route(client, box, monkeypatch):
    import contextlib

    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from yaaf.cli import main as cli

    as_admin = TestClient(client.app, headers=ADMIN)
    monkeypatch.setattr(cli, "_client", lambda: contextlib.nullcontext(as_admin))
    new = fixed(client, box)
    runner = CliRunner()
    out = runner.invoke(cli.app, ["plugins", "move", "garden"])
    assert out.exit_code == 0 and new["version"] in out.output, out.output
    out = runner.invoke(cli.app, ["plugins", "move", "garden", new["version"]])
    assert out.exit_code == 0 and '"to_release": "0.2.0"' in out.output, out.output
    assert pinned(box)[0] == new["id"]


def test_the_daily_brief_says_the_project_moved_plugin_version(client, box):
    """The move is the chairman's, but a co-chair learns from the brief why a
    file the plugin accepted yesterday is refused today."""
    import json

    new = fixed(client, box)
    assert move(client, box, new["version"]).status_code == 200
    brief = client.get(f"/observe/projects/{box['project']['id']}/briefing?hours=1",
                       headers=ADMIN).json()
    text = json.dumps(brief)
    assert '"plugin_moved"' in text
    assert "moved from plugin version 0.1.0 to 0.2.0" in text
