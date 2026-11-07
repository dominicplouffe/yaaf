"""`yaaf backup` and `yaaf restore`, for a native install.

One archive holds everything a native install cannot rebuild: the database,
the master key that opens its secrets, and the artifacts agents produced.
Workspaces are working files and are left out unless asked for.

The archive holds the master key, so it is written readable by you alone and
should be stored like a password. A Docker install does the same with
deploy/backup_single_server.sh and deploy/restore_single_server.sh.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

import typer

#: What a backup holds, by archive name. A restore reads only these.
DATABASE, KEY, ARTIFACTS, WORKSPACES = "yaaf.db", "master.key", "artifacts", "workspaces"


def _folder(home: str) -> Path:
    return Path(home or os.environ.get("YAAF_HOME") or Path.home() / ".yaaf").expanduser()


def _database(folder: Path) -> Path:
    url = os.environ.get("YAAF_DATABASE_URL") or f"sqlite:///{folder / DATABASE}"
    if not url.startswith("sqlite:///"):
        typer.echo("This install's database is not SQLite. On a Docker install, use "
                   "deploy/backup_single_server.sh.", err=True)
        raise typer.Exit(1)
    return Path(url.removeprefix("sqlite:///"))


def make_backup(folder: Path, out: Path, workspaces: bool = False) -> Path:
    """Write the archive and return its path. The database is copied through
    SQLite's own backup API, so it is consistent even while yaaf runs."""
    database = _database(folder)
    if not database.exists():
        raise FileNotFoundError(f"no database at {database}")
    key = folder / KEY
    if not key.exists():
        raise FileNotFoundError(f"no master key at {key}")

    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / DATABASE
        with sqlite3.connect(database) as live, sqlite3.connect(copy) as target:
            live.backup(target)
        out.touch(mode=0o600)
        os.chmod(out, 0o600)
        with tarfile.open(out, "w:gz") as archive:
            archive.add(copy, DATABASE)
            archive.add(key, KEY)
            for name, wanted in ((ARTIFACTS, True), (WORKSPACES, workspaces)):
                if wanted and (folder / name).is_dir():
                    archive.add(folder / name, name)
    return out


def restore_backup(archive_path: Path, folder: Path) -> Path:
    """Put an archive's contents back into `folder`. What is there now is moved
    aside, not deleted, into a folder this returns.

    The archive is unpacked into a staging folder first, so a broken or hostile
    archive fails before anything in place is touched."""
    parts = (DATABASE, KEY, ARTIFACTS, WORKSPACES)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    staging = folder / f".restore-{stamp}"
    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())
        if DATABASE not in names or KEY not in names:
            raise ValueError(f"{archive_path} is not a yaaf backup (no {DATABASE} or {KEY})")
        staging.mkdir(parents=True, mode=0o700)
        try:
            # "data" refuses absolute paths, links out of the folder and devices.
            archive.extractall(staging, members=[m for m in archive.getmembers()
                                                 if m.name.split("/", 1)[0] in parts],
                               filter="data")
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    aside = folder / f"before-restore-{stamp}"
    aside.mkdir(mode=0o700)
    for name in parts:
        if (staging / name).exists():
            if (folder / name).exists():
                shutil.move(folder / name, aside / name)
            shutil.move(staging / name, folder / name)
    shutil.rmtree(staging, ignore_errors=True)
    os.chmod(folder / KEY, 0o600)
    os.chmod(folder / DATABASE, 0o600)
    return aside


def backup(
    out: str = typer.Option("", "--out", help="Where to write the archive "
                            "(default: ~/.yaaf/backups/yaaf-backup-<date>.tar.gz)"),
    workspaces: bool = typer.Option(False, "--workspaces",
                                    help="Also include the agents' working folders"),
    home: str = typer.Option("", "--home", help="yaaf's folder (default ~/.yaaf, or YAAF_HOME)"),
) -> None:
    """Back up a native install: database, master key and artifacts, in one file."""
    folder = _folder(home)
    target = Path(out).expanduser() if out else (
        folder / "backups" / f"yaaf-backup-{datetime.now():%Y%m%d-%H%M%S}.tar.gz")
    try:
        written = make_backup(folder, target, workspaces)
    except FileNotFoundError as exc:
        typer.echo(f"Nothing backed up: {exc}", err=True)
        raise typer.Exit(1) from None
    size = written.stat().st_size
    typer.echo(f"Backed up to {written} ({size // 1024} KB).")
    typer.echo("It holds your master key: store it like a password, and copy it off "
               "this machine. Restore with: yaaf restore <file>")


def restore(
    archive: str = typer.Argument(..., help="A file made by yaaf backup"),
    home: str = typer.Option("", "--home", help="yaaf's folder (default ~/.yaaf, or YAAF_HOME)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask; yaaf is already stopped"),
) -> None:
    """Restore a native install from a backup. Stop yaaf first."""
    folder = _folder(home)
    path = Path(archive).expanduser()
    if not path.is_file():
        typer.echo(f"No such file: {path}", err=True)
        raise typer.Exit(1)
    if not yes:
        typer.echo(f"This replaces the database, master key and artifacts in {folder}. "
                   "What is there now is moved aside, not deleted. Stop yaaf first "
                   "(systemctl --user stop yaaf).")
        if not typer.confirm("Restore now?", default=False):
            typer.echo("Nothing changed.")
            raise typer.Exit(1)
    try:
        aside = restore_backup(path, folder)
    except (ValueError, tarfile.TarError) as exc:
        typer.echo(f"Nothing changed: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"Restored into {folder}. What was there before is in {aside}.")
    typer.echo("Start yaaf: systemctl --user start yaaf (or yaaf up). It migrates an "
               "older database as it starts.")
