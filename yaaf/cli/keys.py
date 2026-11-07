"""`yaaf rotate-master-key`: move every stored secret to a new master key.

Opens the database directly, like `yaaf users` and `yaaf spend-report`, so it
runs where the server runs. Two kinds of install:

- **Server** (YAAF_MASTER_KEY is set, as it is in the Docker containers): the
  new key comes from --new-key or YAAF_NEW_MASTER_KEY, or is made and printed.
  Whoever runs it puts the new key where the server reads it.
  deploy/rotate_master_key.sh does all of that for a Docker box.
- **Native** (no YAAF_MASTER_KEY; the key lives in ~/.yaaf/master.key): the
  key file is replaced, and the old one kept beside it, because backups taken
  before the rotation still need it.

yaaf should be stopped first. A server still running on the old key cannot
read anything written after the rotation, and anything it writes in the
meantime is under a key the database no longer uses.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import typer

STOP_FIRST = ("Stop yaaf (the server and the runner) before rotating. A process "
              "still running on the old key cannot read secrets after this.")


def _native_folder(home: str) -> Path:
    return Path(home or os.environ.get("YAAF_HOME") or Path.home() / ".yaaf").expanduser()


def _write_key(path: Path, key: str) -> None:
    tmp = path.with_suffix(".new")
    tmp.touch(mode=0o600)
    os.chmod(tmp, 0o600)
    tmp.write_text(key + "\n")
    tmp.replace(path)


def rotate_master_key(
    new_key: str = typer.Option(
        "", "--new-key", envvar="YAAF_NEW_MASTER_KEY",
        help="The key to move to. Read from YAAF_NEW_MASTER_KEY too, which keeps "
             "it off the command line. Made for you when omitted."),
    home: str = typer.Option("", "--home",
                             help="Native installs: yaaf's folder (default ~/.yaaf, or YAAF_HOME)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask; yaaf is already stopped"),
) -> None:
    """Re-encrypt every stored secret under a new master key, all or nothing."""
    from cryptography.fernet import Fernet

    from yaaf.core import ids
    from yaaf.core.config import settings
    from yaaf.core.db import db_session, swap_engine
    from yaaf.core.key_rotation import RotationError, rotate
    from yaaf.core.models import AuditLog

    key_file: Path | None = None
    restore = None
    if settings().master_key:
        old_key = settings().master_key
    else:
        folder = _native_folder(home)
        key_file = folder / "master.key"
        if not key_file.exists():
            typer.echo(f"No YAAF_MASTER_KEY is set and there is no {key_file}. Run this "
                       "where yaaf runs: inside the api container on a Docker install "
                       "(deploy/rotate_master_key.sh does it for you).", err=True)
            raise typer.Exit(1)
        old_key = key_file.read_text().strip()
        url = os.environ.get("YAAF_DATABASE_URL") or f"sqlite:///{folder / 'yaaf.db'}"

    generated = not new_key
    new_key = new_key.strip() or Fernet.generate_key().decode()

    if not yes:
        typer.echo(STOP_FIRST)
        if not typer.confirm("Is yaaf stopped, and do you want to rotate now?", default=False):
            typer.echo("Nothing changed.")
            raise typer.Exit(1)

    if key_file is not None:
        restore = swap_engine(url)
    try:
        with db_session() as session:
            report = rotate(session, old_key, new_key)
            session.add(AuditLog(
                id=ids.new_id(ids.AUDIT), project_id=None, actor="system:shell",
                action="master_key.rotate", entity="install", entity_id=None,
                detail={"secrets": report.secrets, "settings": report.settings,
                        "oauth_verifiers": report.verifiers}))
    except RotationError as exc:
        typer.echo(f"Nothing changed: {exc}", err=True)
        raise typer.Exit(1) from None
    finally:
        if restore is not None:
            restore()

    typer.echo(f"Re-encrypted {report.total} value(s): {report.secrets} secret(s), "
               f"{report.settings} setting(s), {report.verifiers} pending OAuth verifier(s).")

    if key_file is not None:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        kept = key_file.with_name(f"master.key.{stamp}.old")
        _write_key(kept, old_key)
        _write_key(key_file, new_key)
        typer.echo(f"The new key is in {key_file}. The old one is kept in {kept}: "
                   "backups from before now still need it.")
        typer.echo("Start yaaf again: systemctl --user start yaaf (or yaaf up).")
        return

    if generated:
        typer.echo("Your new master key. Set it as YAAF_MASTER_KEY, then start yaaf:")
        typer.echo(f"  {new_key}")
    else:
        typer.echo("Set YAAF_MASTER_KEY to the new key, then start yaaf.")
    typer.echo("Keep the old key: backups from before now still need it.")
