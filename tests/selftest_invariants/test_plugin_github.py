"""Installing a plugin from a public GitHub repo, pinned to a commit (build step 7).

The repos are real git repositories served from a directory
(`settings.plugin_github_base`), so `git ls-remote`, the shallow pinned fetch
and the background build run as they do against GitHub, without a network.
The attacks: what the URL may be, a private or missing repo, a moved tag,
an unhashed or missing lock, a symlink, an install the server stopped in the
middle of, the dependency build itself (a real wheel, installed with
--require-hashes from a local index), and the broker's build and test jobs.

Each install runs the whole pipeline, about 2 to 4 s. The wheel case builds a
package first.
"""

from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from tests.conftest import ADMIN, person
from yaaf.core.config import settings
from yaaf.core.db import db_session
from yaaf.core.models import Plugin, PluginVersion
from yaaf.plugins import registry

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "packages/yaaf_plugin_api/examples/grant-factory"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c",
       "init.defaultBranch=main", "-c", "protocol.file.allow=always"]


def git(*args, cwd) -> str:
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


class Hub:
    """A tiny GitHub: bare repos at <base>/<owner>/<repo>, pushed from work trees."""

    def __init__(self, base: Path):
        self.base = base

    def repo(self, owner: str, name: str, tree: Path) -> Path:
        bare = self.base / owner / name
        bare.parent.mkdir(parents=True, exist_ok=True)
        git("init", "-q", "--bare", str(bare), cwd=self.base)
        git("config", "uploadpack.allowReachableSHA1InWant", "true", cwd=bare)
        git("init", "-q", cwd=tree)
        git("add", "-A", cwd=tree)
        git("commit", "-q", "-m", "first", cwd=tree)
        git("remote", "add", "origin", str(bare), cwd=tree)
        git("push", "-q", "origin", "main", cwd=tree)
        return tree

    def commit(self, tree: Path, message: str, *, tag: str | None = None) -> str:
        git("add", "-A", cwd=tree)
        git("commit", "-q", "--allow-empty", "-m", message, cwd=tree)
        if tag:
            git("tag", "-f", tag, cwd=tree)
            git("push", "-q", "-f", "origin", f"refs/tags/{tag}", cwd=tree)
        git("push", "-q", "origin", "main", cwd=tree)
        return git("rev-parse", "HEAD", cwd=tree)


@pytest.fixture
def hub(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "plugins_root", str(tmp_path / "installed"))
    monkeypatch.setattr(settings(), "plugin_github_base", f"file://{tmp_path / 'gh'}")
    monkeypatch.setattr(settings(), "allow_unverified_plugins", False)
    (tmp_path / "gh").mkdir()
    yield Hub(tmp_path / "gh")
    registry.close_all()


def plugin_tree(tmp_path: Path, name: str = "work") -> Path:
    tree = tmp_path / name
    shutil.copytree(EXAMPLE, tree, ignore=shutil.ignore_patterns("__pycache__"))
    return tree


def install(client, url, ref=None, headers=ADMIN):
    return client.post("/admin/plugins", headers=headers,
                       json={"url": url, **({"ref": ref} if ref else {})})


def finished(client, plugin: str, version: str, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/admin/plugins/{plugin}/versions/{version}", headers=ADMIN).json()
        if body["status"] != "installing":
            return body
        time.sleep(0.2)
    raise AssertionError(f"{plugin}@{version} is still installing")


def why(body: dict, rid: str) -> str:
    return " ".join(next(r for r in body["report"]["results"] if r["id"] == rid)["why"])


def test_a_public_repo_installs_at_the_commit_its_tag_names_and_becomes_ready(
        client, hub, tmp_path):
    tree = hub.repo("acme", "grant-factory", plugin_tree(tmp_path))
    sha = hub.commit(tree, "release", tag="v1.0.0")
    r = install(client, "https://github.com/acme/grant-factory", "v1.0.0")
    assert r.status_code == 200, r.text
    assert r.json()["version"] == sha and r.json()["status"] == "installing"
    body = finished(client, "grant-factory", sha)
    assert body["status"] == "ready", [(x["id"], x["why"]) for x in body["report"]["results"]
                                       if x["status"] not in ("pass", "n/a")]
    assert (body["source"], body["ref"]) == ("github", "v1.0.0")
    assert "no dependencies" in why(body, "MIN-03")
    with db_session() as s:
        assert s.get(Plugin, "grant-factory").repo_url == "https://github.com/acme/grant-factory"
    kinds = client.get("/admin/kinds", headers=ADMIN).json()["kinds"]
    assert any(k["kind"] == "plugin:grant_application" and k["version"] == sha for k in kinds)
    installed = Path(settings().plugins_root) / "grant-factory" / sha
    assert (installed / "yaaf-plugin.toml").is_file() and not (installed / ".git").exists()


def test_a_moved_tag_never_changes_an_installed_version(client, hub, tmp_path):
    tree = hub.repo("acme", "grant-factory", plugin_tree(tmp_path))
    first = hub.commit(tree, "one", tag="stable")
    install(client, "https://github.com/acme/grant-factory/tree/stable")
    assert finished(client, "grant-factory", first)["status"] == "ready"
    (tree / "skills" / "grant-runbook.md").write_text("# Changed after release\n")
    second = hub.commit(tree, "two", tag="stable")
    again = install(client, "https://github.com/acme/grant-factory/tree/stable").json()
    assert again["version"] == second != first
    finished(client, "grant-factory", second)
    installed = Path(settings().plugins_root) / "grant-factory" / first
    assert "Changed after release" not in (installed / "skills/grant-runbook.md").read_text()
    with db_session() as s:
        versions = {v.version for v in s.execute(select(PluginVersion)).scalars()}
        assert versions == {first, second}


@pytest.mark.parametrize(("url", "ref", "status", "said"), [
    ("http://github.com/acme/grant-factory", None, 422, "https://github.com/"),
    ("https://gitlab.com/acme/grant-factory", None, 422, "https://github.com/"),
    ("https://github.com/acme/grant-factory/blob/main/x", None, 422, "https://github.com/"),
    ("https://github.com/acme/../../etc", None, 422, "https://github.com/"),
    ("https://github.com/acme/grant-factory", "../../HEAD", 422, "not a branch"),
    ("https://github.com/acme/grant-factory", "--upload-pack=touch", 422, "not a branch"),
    ("https://github.com/acme/no-such-repo", None, 404, "not found or is not public"),
    ("https://github.com/acme/grant-factory", "no-such-branch", 404, "has no branch or tag"),
], ids=["http", "not-github", "a-file", "traversal", "ref-traversal", "ref-option",
        "missing-repo", "missing-ref"])
def test_what_may_be_installed_from(client, hub, tmp_path, url, ref, status, said):
    hub.repo("acme", "grant-factory", plugin_tree(tmp_path))
    r = install(client, url, ref)
    assert r.status_code == status and said in r.text, r.text
    with db_session() as s:
        assert s.execute(select(PluginVersion)).scalars().first() is None


def test_only_the_instance_admin_installs_from_github(client, hub, tmp_path):
    hub.repo("acme", "grant-factory", plugin_tree(tmp_path))
    r = install(client, "https://github.com/acme/grant-factory",
                headers=person("chair2@example.com"))
    assert r.status_code == 403


@pytest.mark.parametrize(("lock", "said"), [
    (None, "there is no requirements.lock"),
    ("gfhelper==1.0\n", "has no sha256 hash"),
    ("gfhelper>=1.0 --hash=sha256:" + "0" * 64 + "\n", "not pinned to one version"),
    ("--index-url https://evil.example/simple\n", "is not allowed"),
], ids=["no-lock", "unhashed", "unpinned", "own-index"])
def test_a_dependency_that_is_not_pinned_with_a_hash_fails_min_03(client, hub, tmp_path,
                                                                    lock, said):
    tree = plugin_tree(tmp_path)
    (tree / "pyproject.toml").write_text(
        '[project]\nname = "grant-factory"\nversion = "0.1.0"\ndependencies = ["gfhelper"]\n')
    if lock is not None:
        (tree / "requirements.lock").write_text(lock)
    hub.repo("acme", "grant-factory", tree)
    sha = git("rev-parse", "HEAD", cwd=tree)
    install(client, "https://github.com/acme/grant-factory")
    body = finished(client, "grant-factory", sha)
    assert body["status"] == "failed" and said in why(body, "MIN-03")
    assert not (Path(settings().plugins_root) / "grant-factory" / sha).exists()


def test_a_symlink_in_the_repo_is_refused_before_anything_runs(client, hub, tmp_path):
    tree = plugin_tree(tmp_path)
    (tree / "skills" / "leak.md").symlink_to("/etc/hostname")
    hub.repo("acme", "grant-factory", tree)
    sha = git("rev-parse", "HEAD", cwd=tree)
    body = install(client, "https://github.com/acme/grant-factory").json()
    assert body["status"] == "failed" and "symlink" in why(body, "MIN-01")
    assert finished(client, "grant-factory", sha)["status"] == "failed"


def _write_wheel(path: Path, name: str, version: str, files: dict[str, str]) -> None:
    """A minimal, valid wheel by hand: no build toolchain needed."""
    import zipfile

    dist = f"{name}-{version}.dist-info"
    meta = {f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
            f"{dist}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n"
                             "Tag: py3-none-any\n"}
    body = {**files, **meta}
    record = []
    for rel, text in body.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=")
        record.append(f"{rel},sha256={digest.decode()},{len(text.encode())}")
    record.append(f"{dist}/RECORD,,")
    with zipfile.ZipFile(path, "w") as z:
        for rel, text in body.items():
            z.writestr(rel, text)
        z.writestr(f"{dist}/RECORD", "\n".join(record) + "\n")


def test_dependencies_are_built_from_the_hashed_lock_and_reach_the_plugin(
        client, hub, tmp_path, monkeypatch):
    """A real wheel, installed with --require-hashes --no-deps from a local index,
    and imported by the plugin's hook inside its sandboxed host."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel = wheels / "gfhelper-1.0-py3-none-any.whl"
    _write_wheel(wheel, "gfhelper", "1.0",
                 {"gfhelper/__init__.py": "def greet():\n    return 'built from lock'\n"})
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    monkeypatch.setattr(settings(), "plugin_pip_args", f"--no-index --find-links {wheels}")

    tree = plugin_tree(tmp_path)
    (tree / "pyproject.toml").write_text(
        '[project]\nname = "grant-factory"\nversion = "0.1.0"\ndependencies = ["gfhelper"]\n')
    (tree / "requirements.lock").write_text(f"gfhelper==1.0 --hash=sha256:{digest}\n")
    init = tree / "src" / "grant_factory" / "__init__.py"
    init.write_text(init.read_text().replace(
        'return Brief("Draft application.json', 'import gfhelper\n'
        '        return Brief(gfhelper.greet() + ": Draft application.json'))
    hub.repo("acme", "grant-factory", tree)
    sha = git("rev-parse", "HEAD", cwd=tree)
    install(client, "https://github.com/acme/grant-factory")
    body = finished(client, "grant-factory", sha)
    assert body["status"] == "ready", why(body, "MIN-03") or body["report"]
    assert "--require-hashes" in why(body, "MIN-03")
    with db_session() as s:
        version = s.execute(select(PluginVersion)).scalars().one()
        from datetime import date

        from yaaf_plugin_api import HookKey, ProjectSnapshot

        brief = registry.pool_for(version).call(
            HookKey("brief"), [ProjectSnapshot("p", "k", "draft", "v", date(2027, 1, 1))])
        assert brief.text.startswith("built from lock")


def test_an_install_the_server_stopped_is_marked_failed_at_start(client, hub, tmp_path):
    tree = hub.repo("acme", "grant-factory", plugin_tree(tmp_path))
    sha = git("rev-parse", "HEAD", cwd=tree)
    with db_session() as s:
        s.add(Plugin(id="grant-factory", name="G", kind_key="grant_application"))
        s.flush()
        s.add(PluginVersion(id="plgv_interrupted", plugin_id="grant-factory", version=sha,
                            source="github", manifest={}, api_version="1.0",
                            status="installing", report={"results": []}, root=""))
    from yaaf.server import acting

    with acting.acting(acting.Actor("system", "test")), db_session() as s:
        assert registry.recover_interrupted(s) == 1
    body = client.get(f"/admin/plugins/grant-factory/versions/{sha}", headers=ADMIN).json()
    assert body["status"] == "failed" and "stopped before" in body["report"]["interrupted"]


# ── the broker's build and test jobs (docker mode) ─────────────────────────────


def test_the_broker_runs_only_its_own_build_and_test_jobs(tmp_path):
    import threading

    from yaaf.plugins import broker as B
    from yaaf.plugins import broker_client

    root = (tmp_path / "plugins").resolve()
    (root / "x" / ".site").mkdir(parents=True)
    ran = []
    server = B.BrokerServer(("127.0.0.1", 0), key=b"k" * 32, root=root,
                            launch=lambda spec: None, run_job=lambda job: (ran.append(job)
                                                                           or (0, "ok")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        code, out = broker_client.run_job("build", root / "x", root / "x" / ".site",
                                          network=True, timeout_s=60,
                                          address=server.server_address, key=b"k" * 32)
        assert (code, out) == (0, "ok") and ran[0]["op"] == "build"
        code, out = broker_client.run_job("build", tmp_path, None, network=True, timeout_s=60,
                                          address=server.server_address, key=b"k" * 32)
        assert code == 126 and "not under the plugins root" in out
        code, out = broker_client.run_job("rm-rf", root / "x", None, network=True,
                                          timeout_s=60, address=server.server_address,
                                          key=b"k" * 32)
        assert code == 126 and "op must be one of" in out
        code, out = broker_client.run_job("tests", root / "x", None, network=False,
                                          timeout_s=60, address=server.server_address,
                                          key=b"wrong" * 8)
        assert code == 126 and "authentication failed" in out
        assert len(ran) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_a_build_job_reaches_the_index_and_writes_only_its_site_and_tests_reach_nothing():
    from yaaf.plugins import broker as B

    root, site = "/var/lib/yaaf/plugins/x/abc", "/var/lib/yaaf/plugins/x/abc/.site"
    build = B.job_options({"op": "build", "root": root, "site": site, "timeout_s": 60},
                          "img", {}, "yaaf_sandbox")
    assert build["network_mode"] == "yaaf_sandbox" and build["read_only"] is True
    assert build["volumes"] == {root: {"bind": root, "mode": "ro"},
                                site: {"bind": site, "mode": "rw"}}
    assert "--require-hashes" in build["command"] and "--no-deps" in build["command"]
    tests = B.job_options({"op": "tests", "root": root, "site": site, "timeout_s": 60},
                          "img", {}, "yaaf_sandbox")
    assert tests["network_mode"] == "none"
    assert all(v["mode"] == "ro" for v in tests["volumes"].values())
    for opts in (build, tests):
        assert opts["cap_drop"] == ["ALL"] and "no-new-privileges" in opts["security_opt"]
        assert not any(k.startswith("YAAF") for k in opts["environment"])


def test_a_docker_build_can_write_its_site_as_the_sandbox_user(tmp_path, monkeypatch):
    """The build container runs as the sandbox's own user, not the one that made
    the site directory. A site only its maker could write failed every real
    install with a dependency (MIN-03: PermissionError under .site)."""
    import stat

    from yaaf.plugins import broker_client, build

    (tmp_path / "requirements.lock").write_text(
        "et-xmlfile==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
    monkeypatch.setattr(settings(), "sandbox_mode", "docker")
    seen = {}

    def run_job(op, root, site, **_kw):
        seen["mode"] = stat.S_IMODE(site.stat().st_mode)
        return 0, ""
    monkeypatch.setattr(broker_client, "run_job", run_job)
    ok, _log, site = build.build(tmp_path)
    assert ok and site == tmp_path / ".site"
    assert seen["mode"] == 0o777
