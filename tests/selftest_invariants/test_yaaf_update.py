"""Invariant selftests: `yaaf update` never costs anyone their work or their data.

An update moves code under a running install and migrates a database that
cannot be un-migrated by checking the old code out. So what is attacked here
is what it must refuse and what it must do first: refuse a checkout with edits
of its own, refuse to merge a branch that has diverged, back the database up
before anything moves, rebuild only what changed, stop at the first failure
with the way back — and do nothing at all when there is nothing to bring in.

Run against throwaway git repositories, never this checkout.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from yaaf.cli import main as cli
from yaaf.cli import update

GIT = ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test",
       "-c", "init.defaultBranch=main"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run([*GIT, "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def repos(tmp_path):
    """A remote, a developer pushing to it, and an install cloned from it."""
    origin = tmp_path / "origin.git"
    subprocess.run([*GIT, "init", "-q", "--bare", str(origin)], check=True)
    dev = tmp_path / "dev"
    subprocess.run([*GIT, "clone", "-q", str(origin), str(dev)], check=True,
                   capture_output=True)
    _commit(dev, {"pyproject.toml": "v1\n", "yaaf/a.py": "a\n", "web/src/app.ts": "1\n"},
            "first")
    _git(dev, "push", "-q", "origin", "HEAD:main")
    install = tmp_path / "install"
    subprocess.run([*GIT, "clone", "-q", str(origin), str(install)], check=True,
                   capture_output=True)
    (install / "web" / "dist").mkdir(parents=True)
    return dev, install


@pytest.fixture()
def run(repos, tmp_path, monkeypatch):
    """`yaaf update` against the install, with every step past the pull
    recorded instead of run."""
    _, install = repos
    monkeypatch.setattr(update, "REPO", install)
    did: list[str] = []
    monkeypatch.setattr(update, "install_packages", lambda repo, log: did.append("packages"))
    monkeypatch.setattr(update, "build_dashboard", lambda repo, log: did.append("dashboard"))
    monkeypatch.setattr(update, "restart", lambda port, folder: did.append("restart") or "restarted")
    monkeypatch.setattr(update, "refresh_desktop", lambda: did.append("desktop") or None)
    # Tested on its own below; here it must not download a browser.
    monkeypatch.setattr(update, "install_browser", lambda log: True)
    home = tmp_path / "yaaf-home"
    home.mkdir()
    db = home / "yaaf.db"
    with sqlite3.connect(db) as con:
        con.execute("create table users (email text)")
        con.execute("insert into users values ('chair@example.com')")
    monkeypatch.delenv("YAAF_DATABASE_URL", raising=False)

    def invoke(*args: str):
        result = CliRunner().invoke(cli.app, ["update", "--home", str(home), *args])
        return result, did, home
    return invoke


def _push(dev: Path, files: dict[str, str], message: str) -> str:
    sha = _commit(dev, files, message)
    _git(dev, "push", "-q", "origin", "HEAD:main")
    return sha


def test_nothing_to_bring_in_is_nothing_done(run):
    result, did, home = run()
    assert result.exit_code == 0 and "up to date" in result.output
    assert did == [] and not (home / "backups").exists()


def test_an_update_backs_up_first_and_rebuilds_only_what_changed(repos, run):
    dev, install = repos
    there = _push(dev, {"yaaf/a.py": "b\n"}, "python only")
    result, did, home = run()
    assert result.exit_code == 0, result.output
    assert _git(install, "rev-parse", "HEAD") == there
    assert "packages" not in did and "dashboard" not in did, \
        "no package or dashboard work for a Python-only change"
    assert "restart" in did, "restarting is what migrates"
    backups = list((home / "backups").glob("yaaf-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as con:
        assert con.execute("select email from users").fetchone() == ("chair@example.com",)


def test_a_dashboard_or_package_change_is_rebuilt_in_order(repos, run):
    dev, _ = repos
    _push(dev, {"web/src/app.ts": "2\n", "pyproject.toml": "v2\n"}, "both")
    result, did, _ = run()
    assert result.exit_code == 0, result.output
    assert did == ["packages", "dashboard", "restart", "desktop"]


def test_a_checkout_with_edits_of_its_own_is_left_alone(repos, run):
    dev, install = repos
    here = _git(install, "rev-parse", "HEAD")
    _push(dev, {"yaaf/a.py": "b\n"}, "upstream")
    (install / "yaaf" / "a.py").write_text("my local change\n")
    result, did, home = run()
    assert result.exit_code == 1 and "edits to tracked files" in result.output
    assert _git(install, "rev-parse", "HEAD") == here
    assert (install / "yaaf" / "a.py").read_text() == "my local change\n"
    assert did == [] and not (home / "backups").exists()
    assert "Nothing was changed" in result.output


def test_untracked_files_are_not_in_the_way(repos, run):
    dev, install = repos
    there = _push(dev, {"yaaf/a.py": "b\n"}, "upstream")
    (install / "notes.txt").write_text("mine\n")
    result, _, _ = run()
    assert result.exit_code == 0, result.output
    assert _git(install, "rev-parse", "HEAD") == there


def test_a_branch_that_diverged_is_never_merged(repos, run):
    dev, install = repos
    _push(dev, {"yaaf/a.py": "theirs\n"}, "upstream")
    mine = _commit(install, {"yaaf/b.py": "mine\n"}, "a commit of my own")
    result, did, _ = run()
    assert result.exit_code == 1 and "only fast-forwards" in result.output
    assert _git(install, "rev-parse", "HEAD") == mine and did == []


def test_check_says_what_would_come_and_changes_nothing(repos, run):
    dev, install = repos
    here = _git(install, "rev-parse", "HEAD")
    _push(dev, {"yaaf/a.py": "b\n"}, "a fix worth having")
    result, did, home = run("--check")
    assert result.exit_code == 0 and "a fix worth having" in result.output
    assert _git(install, "rev-parse", "HEAD") == here
    assert did == [] and not (home / "backups").exists()


def test_a_failed_step_stops_with_the_way_back(repos, run, monkeypatch):
    dev, install = repos
    here = _git(install, "rev-parse", "HEAD")
    _push(dev, {"web/src/app.ts": "broken\n"}, "a dashboard that will not build")

    def fails(repo, log):
        raise update.Stop("`npm run build` failed")

    monkeypatch.setattr(update, "build_dashboard", fails)
    result, did, home = run()
    assert result.exit_code == 1
    assert f"reset --hard {here}" in result.output
    backup = next((home / "backups").glob("yaaf-*.db"))
    assert f"cp {backup}" in result.output
    assert "restart" not in did, "a half-built update is not started"


def test_backups_are_private_and_the_oldest_go(tmp_path):
    db = tmp_path / "yaaf.db"
    with sqlite3.connect(db) as con:
        con.execute("create table t (x)")
    made = [update.backup_database(f"sqlite:///{db}", tmp_path, f"rev{i}", keep=3)
            for i in range(5)]
    left = sorted((tmp_path / "backups").glob("yaaf-*.db"))
    assert len(left) <= 3 and made[-1] in left
    assert all(oct(p.stat().st_mode)[-3:] == "600" for p in left)
    assert update.backup_database("postgresql+psycopg://x@db/yaaf", tmp_path, "r") is None


def test_an_install_that_is_not_a_checkout_says_how_instead(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "REPO", tmp_path)
    result = CliRunner().invoke(cli.app, ["update", "--home", str(tmp_path)])
    assert result.exit_code == 2 and "not a git checkout" in result.output


def test_an_update_rewrites_the_service_before_restarting_it(tmp_path, monkeypatch):
    """What the service is started with — the CLIs the jail binds, the browser,
    each run's caps — is the new code's to decide. A restart alone kept an old
    install on the old answers: no browser, no mise CLIs, ever."""
    from yaaf.cli import setup

    config = tmp_path / "config"
    unit = config / "systemd" / "user" / setup.SERVICE
    unit.parent.mkdir(parents=True)
    unit.write_text("old unit\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setattr(setup, "_systemd_user", lambda: True)
    monkeypatch.setattr(setup, "_healthy", lambda base: True)
    monkeypatch.setattr(setup, "unit_file", lambda port, folder, bind: "new unit\n")
    calls = []
    monkeypatch.setattr(update.subprocess, "run",
                        lambda args, **k: calls.append((args, unit.read_text())))
    monkeypatch.setattr(update.time, "sleep", lambda s: None)
    update.restart(24247, tmp_path)
    restarted = [text for args, text in calls if "restart" in args]
    assert restarted == ["new unit\n"], "restarted on the old unit"
    assert any("daemon-reload" in args for args, _ in calls)


def test_an_update_fetches_the_browser_only_when_it_is_missing(tmp_path, monkeypatch):
    from yaaf.runner import host_tools

    ran = []
    monkeypatch.setattr(update, "_run", lambda argv, cwd, log: ran.append(argv))
    monkeypatch.setattr(host_tools, "chromium_installed", lambda: True)
    assert update.install_browser(tmp_path / "log") and ran == []
    state = iter([False, True])
    monkeypatch.setattr(host_tools, "chromium_installed", lambda: next(state))
    assert update.install_browser(tmp_path / "log")
    assert ran and ran[0][-3:] == ["playwright", "install", "chromium"]
