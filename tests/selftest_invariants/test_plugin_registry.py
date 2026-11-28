"""The plugin registry, attacked over HTTP (build step 3).

Who may install (the instance admin, and only from a local path when the box
allows it); what an install decides for itself (MIN-02's names, and MIN-04/05
by starting the code in a real host); that a version which failed is kept for
its report but never offered; that a pending one is offered only on a box that
allows unverified plugins; and that a version a project is pinned to cannot be
removed from under it.

Each install starts a real plugin host to check the code, about 0.2 s.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from tests.conftest import ADMIN, make_project, person
from yaaf.core.config import settings
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog, Plugin, PluginVersion, Project

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "packages/yaaf_plugin_api/examples/grant-factory"


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A development box: local installs on, plugins under a temp root."""
    monkeypatch.setattr(settings(), "plugins_root", str(tmp_path / "installed"))
    monkeypatch.setattr(settings(), "allow_local_plugins", True)
    monkeypatch.setattr(settings(), "allow_unverified_plugins", False)
    yield tmp_path


def checkout(box: Path, name: str = "grant-factory") -> Path:
    dest = box / "checkouts" / name
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def install(client, path, headers=ADMIN):
    return client.post("/admin/plugins/local", json={"path": str(path)}, headers=headers)


def result(body: dict, rid: str) -> dict:
    return next(r for r in body["report"]["results"] if r["id"] == rid)


def test_a_local_install_is_off_unless_the_box_says_so(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "allow_local_plugins", False)
    r = install(client, EXAMPLE)
    assert r.status_code == 403 and "YAAF_ALLOW_LOCAL_PLUGINS" in r.text


def test_only_the_instance_admin_installs_and_anyone_signed_in_reads(client, box):
    chair = person("someone@example.com")
    assert install(client, checkout(box), headers=chair).status_code == 403
    assert client.get("/admin/plugins", headers=chair).status_code == 200
    assert client.get("/admin/plugins").status_code == 401


def test_an_install_starts_the_code_and_records_what_only_core_can_decide(client, box):
    r = install(client, checkout(box))
    assert r.status_code == 200, r.text
    body = r.json()
    # Every applicable requirement decided and met, by the static checks, the
    # registry, the conformance suite through a sandboxed host, and the
    # plugin's own tests: the only way a version becomes ready.
    assert body["status"] == "ready" and body["created"] is True, [
        (r["id"], r["status"], r["why"]) for r in body["report"]["results"]
        if r["status"] not in ("pass", "n/a")]
    for rid in ("MIN-01", "MIN-02", "MIN-03", "MIN-04", "MIN-05", "MIN-06", "MIN-07",
                "MIN-08", "MIN-09", "MIN-11", "MIN-13", "MIN-16", "EXT-REV-2"):
        assert result(body, rid)["status"] == "pass", result(body, rid)
    installed = Path(settings().plugins_root) / "grant-factory" / body["version"]
    assert (installed / "yaaf-plugin.toml").is_file()
    with db_session() as s:
        audit = s.execute(select(AuditLog).where(AuditLog.action == "plugin.install")
                          ).scalars().one()
        assert audit.actor.startswith("user:") and audit.detail["status"] == "ready"


def test_the_same_files_are_one_version_and_a_changed_file_is_another(client, box):
    path = checkout(box)
    first = install(client, path).json()
    again = install(client, path).json()
    assert again["created"] is False and again["id"] == first["id"]
    (path / "skills" / "grant-runbook.md").write_text("# Changed\n")
    changed = install(client, path).json()
    assert changed["created"] is True and changed["version"] != first["version"]


from yaaf.plugins.registry import conformance_results as _real_suite  # noqa: E402


def _pending(spec, run_tests=None):
    """The suite with the plugin's own tests unrun."""
    out = _real_suite(spec, run_tests=run_tests)
    out["MIN-16"] = ("pending", ["the plugin's own tests have not been run here"])
    return out


def test_a_pending_version_is_offered_only_on_a_box_that_allows_unverified(client, box,
                                                                           monkeypatch):
    from yaaf.plugins import registry

    monkeypatch.setattr(registry, "conformance_results", _pending)
    assert install(client, checkout(box)).json()["status"] == "pending"
    kinds = client.get("/admin/kinds", headers=ADMIN).json()["kinds"]
    assert all(k["built_in"] for k in kinds)
    monkeypatch.setattr(settings(), "allow_unverified_plugins", True)
    kinds = client.get("/admin/kinds", headers=ADMIN).json()["kinds"]
    plugin = [k for k in kinds if not k["built_in"]]
    assert [k["kind"] for k in plugin] == ["plugin:grant_application"]
    assert plugin[0]["lead"] == "grant-lead" and [m["key"] for m in plugin[0]["modes"]] == ["draft"]


def test_code_that_does_not_match_its_manifest_is_recorded_failed_and_never_offered(
        client, box, monkeypatch):
    path = checkout(box)
    init = path / "src" / "grant_factory" / "__init__.py"
    init.write_text(init.read_text().replace("    @hook.completion_gate\n", "    # gone\n"))
    monkeypatch.setattr(settings(), "allow_unverified_plugins", True)
    body = install(client, path).json()
    assert body["status"] == "failed" and body["selectable"] is False
    assert "completion_gate" in result(body, "MIN-05")["why"][0]
    assert not [k for k in client.get("/admin/kinds", headers=ADMIN).json()["kinds"]
                if not k["built_in"]]
    assert not (Path(settings().plugins_root) / "grant-factory" / body["version"]).exists()


def test_a_plugin_that_reaches_out_at_import_fails_min_04(client, box):
    path = checkout(box)
    init = path / "src" / "grant_factory" / "__init__.py"
    text = init.read_text().replace(
        "from __future__ import annotations\n",
        "from __future__ import annotations\nimport socket\ntry:\n"
        "    socket.create_connection(('127.0.0.1', 9), 1)\nexcept Exception:\n    pass\n")
    init.write_text(text)
    body = install(client, path).json()
    assert body["status"] == "failed"
    assert "network" in result(body, "MIN-04")["why"][0]


def test_two_plugins_cannot_share_a_kind_and_a_plugin_cannot_change_its_own(client, box):
    assert install(client, checkout(box)).status_code == 200
    twin = checkout(box, "twin")
    toml = twin / "yaaf-plugin.toml"
    toml.write_text(toml.read_text().replace('id          = "grant-factory"',
                                             'id          = "grant-twin"'))
    r = install(client, twin)
    assert r.status_code == 409 and "already belongs to plugin grant-factory" in r.text
    moved = checkout(box, "moved")
    toml = moved / "yaaf-plugin.toml"
    toml.write_text(toml.read_text().replace('key   = "grant_application"', 'key   = "grants"'))
    body = install(client, moved).json()
    assert body["status"] == "failed" and "may not change it" in result(body, "MIN-02")["why"][0]


def test_a_path_that_is_not_a_checkout_is_refused(client, box):
    for path in ("relative/path", str(box / "missing"), settings().plugins_root):
        Path(settings().plugins_root).mkdir(parents=True, exist_ok=True)
        assert install(client, path).status_code == 422, path
    broken = checkout(box, "broken")
    (broken / "yaaf-plugin.toml").write_text("[plugin]\nid = 'X'\n")
    r = install(client, broken)
    assert r.status_code == 422 and "not installed" in r.text


def test_a_version_a_project_runs_on_cannot_be_removed_from_under_it(client, box, monkeypatch):
    body = install(client, checkout(box)).json()
    project_id = make_project(client)["id"]
    with db_session() as s:
        s.get(Project, project_id).plugin_version_id = body["id"]
    url = f"/admin/plugins/grant-factory/versions/{body['version']}"
    r = client.delete(url, headers=ADMIN)
    assert r.status_code == 409 and project_id in r.text
    assert client.post(url + "/disable", headers=person("x@example.com")).status_code == 403
    assert client.post(url + "/disable", headers=ADMIN).json()["status"] == "disabled"
    monkeypatch.setattr(settings(), "allow_unverified_plugins", True)
    assert not [k for k in client.get("/admin/kinds", headers=ADMIN).json()["kinds"]
                if not k["built_in"]]
    with db_session() as s:
        s.get(Project, project_id).plugin_version_id = None
    assert client.delete(url, headers=ADMIN).status_code == 200
    with db_session() as s:
        assert s.get(Plugin, "grant-factory") is None
        assert s.execute(select(PluginVersion)).scalars().first() is None
    assert not (Path(settings().plugins_root) / "grant-factory" / body["version"]).exists()


def test_a_version_the_conformance_suite_fails_is_recorded_failed_and_never_offered(
        client, box, monkeypatch):
    """The example's gate, changed to accept anything: its refuse fixture is now
    accepted (MIN-13) and so is every fuzz case (EXT-ART-1)."""
    path = checkout(box)
    init = path / "src" / "grant_factory" / "__init__.py"
    init.write_text(init.read_text().replace(
        "    def check_application(self, ctx, upload):\n",
        "    def check_application(self, ctx, upload):\n        return Accept()\n"))
    monkeypatch.setattr(settings(), "allow_unverified_plugins", True)
    body = install(client, path).json()
    assert body["status"] == "failed" and body["selectable"] is False
    assert "refuse case no-budget.json was accepted" in " ".join(result(body, "MIN-13")["why"])
    assert result(body, "EXT-ART-1")["status"] == "fail"
    assert not [k for k in client.get("/admin/kinds", headers=ADMIN).json()["kinds"]
                if not k["built_in"]]


def test_a_role_that_breaks_the_librarys_rules_fails_min_07_and_joins_nothing(client, box):
    from yaaf.core.models import Role

    path = checkout(box)
    role = path / "roles" / "grant-writer.json"
    role.write_text(role.read_text().replace('"category": "build"', '"category": "vibes"'))
    body = install(client, path).json()
    assert body["status"] == "failed" and "category must be one of" in " ".join(
        result(body, "MIN-07")["why"])
    with db_session() as s:
        assert s.execute(select(Role).where(Role.slug.in_(["grant-lead", "grant-writer"]))
                         ).scalars().first() is None


def test_a_ready_version_adds_its_roles_to_the_library(client, box):
    from yaaf.core.models import Role

    assert install(client, checkout(box)).json()["status"] == "ready"
    with db_session() as s:
        slugs = set(s.execute(select(Role.slug)).scalars())
        assert {"grant-lead", "grant-writer"} <= slugs


def test_installing_a_failed_commit_again_tries_it_again(client, box, monkeypatch):
    """A failure can be the box's, not the plugin's (a build directory the
    sandbox could not write). Answering a second install of that commit with the
    first one's stale report meant it could never recover once the box was fixed."""
    from yaaf.plugins import registry as R

    path = checkout(box)
    real = R.verify

    def box_cannot_build(session, dest, manifest, label, report, **kw):
        _failed, root, log = real(session, dest, manifest, label, report, **kw)
        report["counts"]["fail"] = report["counts"].get("fail", 0) + 1
        return True, root, log

    monkeypatch.setattr(R, "verify", box_cannot_build)
    first = install(client, path).json()
    assert first["status"] == "failed"
    monkeypatch.setattr(R, "verify", real)  # the box is fixed
    again = install(client, path).json()
    assert again["created"] is True and again["status"] == "ready", again
    assert again["version"] == first["version"]
    [plugin] = client.get("/admin/plugins", headers=ADMIN).json()["plugins"]
    assert len(plugin["versions"]) == 1
