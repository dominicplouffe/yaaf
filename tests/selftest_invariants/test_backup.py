"""Invariant selftests: a native backup is one file you can restore from.

The gap this closes: `yaaf update` kept database copies in ~/.yaaf/backups,
on the same disk, without the master key that opens their secrets or the
artifacts agents produced. A dead disk took all of it.
"""

from __future__ import annotations

import sqlite3
import tarfile

import pytest
from typer.testing import CliRunner

from yaaf.cli.backup import make_backup, restore_backup
from yaaf.cli.main import app as cli


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("YAAF_DATABASE_URL", raising=False)
    monkeypatch.delenv("YAAF_HOME", raising=False)
    folder = tmp_path / "yaaf"
    folder.mkdir()
    with sqlite3.connect(folder / "yaaf.db") as db:
        db.execute("create table t (v text)")
        db.execute("insert into t values ('before')")
    (folder / "master.key").write_text("old-key\n")
    (folder / "artifacts" / "p1").mkdir(parents=True)
    (folder / "artifacts" / "p1" / "report.md").write_text("findings")
    (folder / "workspaces" / "p1").mkdir(parents=True)
    (folder / "workspaces" / "p1" / "scratch.txt").write_text("wip")
    return folder


def _value(folder) -> str:
    with sqlite3.connect(folder / "yaaf.db") as db:
        return db.execute("select v from t").fetchone()[0]


def test_a_backup_holds_the_database_the_key_and_the_artifacts(home, tmp_path):
    archive = make_backup(home, tmp_path / "out" / "b.tar.gz")
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
    assert {"yaaf.db", "master.key", "artifacts/p1/report.md"} <= names
    assert not any(n.startswith("workspaces") for n in names), "only when asked for"
    assert oct(archive.stat().st_mode & 0o777) == "0o600", "it holds the master key"


def test_workspaces_come_along_when_asked(home, tmp_path):
    archive = make_backup(home, tmp_path / "b.tar.gz", workspaces=True)
    with tarfile.open(archive) as tar:
        assert "workspaces/p1/scratch.txt" in tar.getnames()


def test_restore_puts_it_all_back_and_keeps_what_was_there(home, tmp_path):
    archive = make_backup(home, tmp_path / "b.tar.gz")
    with sqlite3.connect(home / "yaaf.db") as db:
        db.execute("update t set v = 'after'")
    (home / "master.key").write_text("new-key\n")
    (home / "artifacts" / "p1" / "report.md").unlink()

    aside = restore_backup(archive, home)

    assert _value(home) == "before"
    assert (home / "master.key").read_text().strip() == "old-key"
    assert (home / "artifacts" / "p1" / "report.md").read_text() == "findings"
    # Nothing is deleted by a restore: the state it replaced is kept.
    with sqlite3.connect(aside / "yaaf.db") as db:
        assert db.execute("select v from t").fetchone()[0] == "after"
    assert (aside / "master.key").read_text().strip() == "new-key"
    assert (home / "workspaces" / "p1" / "scratch.txt").exists(), \
        "a backup without workspaces leaves them where they are"


def test_restore_refuses_a_file_that_is_not_a_backup(home, tmp_path):
    stray = tmp_path / "stray.tar.gz"
    with tarfile.open(stray, "w:gz") as tar:
        tar.add(home / "master.key", "master.key")
    with pytest.raises(ValueError, match="not a yaaf backup"):
        restore_backup(stray, home)
    assert _value(home) == "before"


def test_restore_never_writes_outside_the_folder(home, tmp_path):
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(home / "yaaf.db", "yaaf.db")
        tar.add(home / "master.key", "master.key")
        tar.add(home / "master.key", "artifacts/../../escaped.txt")
    with pytest.raises(tarfile.TarError):
        restore_backup(evil, home)
    assert not (tmp_path / "escaped.txt").exists()
    # And a refused archive leaves the install exactly as it was.
    assert _value(home) == "before"
    assert (home / "master.key").read_text().strip() == "old-key"
    assert sorted(p.name for p in home.iterdir()) == \
        ["artifacts", "master.key", "workspaces", "yaaf.db"]


def test_the_commands_round_trip(home, tmp_path):
    runner = CliRunner()
    out = tmp_path / "b.tar.gz"
    made = runner.invoke(cli, ["backup", "--home", str(home), "--out", str(out)])
    assert made.exit_code == 0, made.output
    assert "holds your master key" in made.output
    with sqlite3.connect(home / "yaaf.db") as db:
        db.execute("update t set v = 'after'")
    back = runner.invoke(cli, ["restore", str(out), "--home", str(home), "--yes"])
    assert back.exit_code == 0, back.output
    assert _value(home) == "before"


def test_backup_says_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("YAAF_DATABASE_URL", raising=False)
    done = CliRunner().invoke(cli, ["backup", "--home", str(tmp_path)])
    assert done.exit_code == 1 and "no database" in done.output
