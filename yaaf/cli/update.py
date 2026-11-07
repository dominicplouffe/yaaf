"""`yaaf update`: a desktop install, brought to the newest yaaf in one command.

By hand an update was four steps, and the one people skip is the one that
matters: the dashboard is built, not shipped, so a pull without a rebuild
serves yesterday's screens against today's API. This does them in order, does
only the ones the incoming changes need, and stops at the first that fails
with the way back written out:

1. **Refuse to step on your work.** A checkout with edits to tracked files is
   left alone; a branch that has diverged is never merged into.
2. **Back up the database** (SQLite's own backup, safe while yaaf runs) —
   before anything moves, because a migration is the one step that cannot be
   undone by checking out the old code.
3. **Pull**, fast-forward only.
4. **Reinstall the Python packages** only when pyproject.toml changed — the
   install is editable, so code changes are live without it.
5. **Rebuild the dashboard** only when something under web/ changed.
6. **Restart yaaf**, which migrates the database as it starts
   (yaaf.core.schema_upkeep), and wait for it to answer.
7. **Refresh the desktop pieces** with the new code — the bar widget, the
   menu, the hooks — when `yaaf desktop install` put them in; files you edited
   are left alone, as always.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import typer

from yaaf.core.config import DEFAULT_PORT

REPO = Path(__file__).resolve().parents[2]
#: Backups kept beside the database. Each is the whole database, and five
#: updates back is further than anyone rolls.
KEEP_BACKUPS = 5


class Stop(Exception):
    """An update that cannot go on, and why — nothing past this point ran."""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          timeout=300)


def _out(repo: Path, *args: str) -> str:
    done = _git(repo, *args)
    if done.returncode != 0:
        raise Stop((done.stderr or done.stdout).strip() or f"git {' '.join(args)} failed")
    return done.stdout.strip()


def local_edits(repo: Path) -> list[str]:
    """Tracked files changed in the checkout. Untracked files are not in the
    way of a fast-forward, and a build leaves plenty of them."""
    return [line for line in _out(repo, "status", "--porcelain",
                                  "--untracked-files=no").splitlines() if line.strip()]


def incoming(repo: Path) -> tuple[str, str, list[str]]:
    """(here, there, the commits between): what an update would bring."""
    upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream.returncode != 0:
        raise Stop("this checkout's branch follows no remote branch, so there is nothing "
                   "to update from — `git -C {repo} branch -u origin/<branch>` names one")
    _out(repo, "fetch", "--quiet")
    here, there = _out(repo, "rev-parse", "HEAD"), _out(repo, "rev-parse", "@{u}")
    if here == there:
        return here, there, []
    if _git(repo, "merge-base", "--is-ancestor", "HEAD", "@{u}").returncode != 0:
        raise Stop("this checkout has commits the remote does not, so updating would mean "
                   "merging — yaaf update only fast-forwards. Sort the branch out with git "
                   "first.")
    return here, there, _out(repo, "log", "--oneline", "HEAD..@{u}").splitlines()


def changed_files(repo: Path, here: str, there: str) -> list[str]:
    return _out(repo, "diff", "--name-only", here, there).splitlines()


def needs_web(repo: Path, changed: list[str]) -> bool:
    """A dashboard to rebuild: its sources moved, or it was never built."""
    if not (repo / "web" / "dist").is_dir():
        return True
    return any(f.startswith("web/") and not f.startswith("web/dist/") for f in changed)


def needs_packages(changed: list[str]) -> bool:
    return "pyproject.toml" in changed


def backup_database(url: str, folder: Path, label: str, keep: int = KEEP_BACKUPS) -> Path | None:
    """A copy of a SQLite database through SQLite's own backup API — page by
    page, consistent while the server keeps writing — and only the newest
    `keep` of them. Postgres is the server deploy's, which backs up with
    pg_dump in deploy/update_single_server.sh; None here."""
    if not url.startswith("sqlite:///"):
        return None
    source = Path(url.removeprefix("sqlite:///"))
    if not source.exists():
        return None
    backups = folder / "backups"
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = backups / f"yaaf-{datetime.now():%Y%m%d-%H%M%S}-{label[:8]}.db"
    with sqlite3.connect(source) as live, sqlite3.connect(target) as copy:
        live.backup(copy)
    os.chmod(target, 0o600)
    for old in sorted(backups.glob("yaaf-*.db"))[:-keep]:
        old.unlink()
    return target


def _run(argv: list[str], cwd: Path, log: Path) -> None:
    with log.open("a") as out:
        out.write(f"\n$ {' '.join(argv)}\n")
        out.flush()
        done = subprocess.run(argv, cwd=cwd, stdout=out, stderr=subprocess.STDOUT)
    if done.returncode != 0:
        raise Stop(f"`{' '.join(argv)}` failed — its output is in {log}")


def install_packages(repo: Path, log: Path) -> None:
    """The [host] extra, into the Python yaaf runs on: through uv where it is
    (a uv-made environment has no pip), else that Python's own pip."""
    target = f"{repo}[host]"
    if shutil.which("uv"):
        _run(["uv", "pip", "install", "--quiet", "--python", sys.executable, "-e", target],
             repo, log)
    else:
        _run([sys.executable, "-m", "pip", "install", "--quiet", "-e", target], repo, log)


def install_browser(log: Path) -> bool:
    """Chromium for Playwright, when this machine does not have it yet: an
    install from before the browser lane came to native mode has the library
    (the [host] extra) and nothing for it to drive. False when the download
    failed — the doctor then says agents are not offered a browser."""
    from yaaf.runner import host_tools

    if host_tools.chromium_installed():
        return True
    try:
        _run([sys.executable, "-m", "playwright", "install", "chromium"], REPO, log)
    except Stop:
        return False
    return host_tools.chromium_installed()


def build_dashboard(repo: Path, log: Path) -> None:
    npm = shutil.which("npm")
    if npm is None:
        raise Stop("the dashboard changed and npm is not installed to rebuild it — "
                   "sudo pacman -S nodejs npm, then yaaf update again")
    _run([npm, "install"], repo / "web", log)
    _run([npm, "run", "build"], repo / "web", log)


def setup_module():
    from yaaf.cli import setup

    return setup


def restart(port: int, folder: Path) -> str:
    """Restart yaaf the way it was started, and wait for it to answer. Started
    is what migrates the database, so a yaaf that does not come back is a
    migration that failed — said, with its log."""
    from yaaf.cli import setup

    base = f"http://localhost:{port}"
    unit = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") \
        / "systemd" / "user" / setup.SERVICE
    if unit.exists() and setup._systemd_user():
        # Rewritten with the new code first: what the service is started with
        # — the CLIs the jail binds, where the browser is, each run's caps —
        # is decided by this yaaf, and a restart alone kept the old answers.
        bind, _ = setup.chosen_bind(folder, "")
        unit.write_text(setup.unit_file(port, folder, bind))
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        subprocess.run(["systemctl", "--user", "restart", setup.SERVICE], capture_output=True)
        how = "restarted (systemctl --user)"
    elif setup._healthy(base):
        subprocess.run(["pkill", "-f", f"yaaf.cli.main up --host --port {port}"],
                       capture_output=True)
        for _ in range(30):
            if not setup._healthy(base):
                break
            time.sleep(1)
        bind, _ = setup.chosen_bind(folder, "")
        log = (folder / "yaaf-up.log").open("a")
        subprocess.Popen(setup._up_command(port, folder, bind), stdout=log,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True,
                         env={**os.environ, **setup.service_environment(folder)})
        how = "restarted"
    else:
        return "not running — it migrates the next time it starts (yaaf setup)"
    time.sleep(2)  # a restart answers from the old process for a moment
    for _ in range(120):
        if setup._healthy(base):
            return how
        time.sleep(1)
    raise Stop(f"yaaf did not come back at {base} — the database migrates as it starts, "
               f"so the reason is likely in {folder / 'server.log'}")


def refresh_desktop() -> str | None:
    """`yaaf desktop install` again, on the new code, when it was installed.
    Run as a new process: this one still holds the old modules."""
    manifest = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") \
        / "yaaf" / "desktop.json"
    if not manifest.exists():
        return None
    done = subprocess.run([sys.executable, "-m", "yaaf.cli.main", "desktop", "install"],
                          capture_output=True, text=True)
    notes = [line for line in done.stdout.splitlines() if line.startswith("!")]
    return "refreshed" + (f" ({len(notes)} note{'s' if len(notes) != 1 else ''}: "
                          + "; ".join(n[2:] for n in notes) + ")" if notes else "")


def update(
    check: bool = typer.Option(False, "--check", help="Say what an update would bring, "
                                                      "change nothing"),
    home: str = typer.Option("", "--home", help="Where the database lives (default ~/.yaaf)"),
    port: int = typer.Option(0, "--port", help="The port yaaf listens on (default: YAAF_API's, "
                                               f"else {DEFAULT_PORT})"),
) -> None:
    """Update this install to the newest yaaf: back up the database, pull,
    reinstall and rebuild what changed, restart (which migrates), and refresh
    the desktop pieces. Stops at the first step that fails, with the way back."""
    from urllib.parse import urlparse

    folder = Path(home or os.environ.get("YAAF_HOME") or Path.home() / ".yaaf").expanduser()
    port = port or urlparse(os.environ.get("YAAF_API", "")).port or DEFAULT_PORT
    url = os.environ.get("YAAF_DATABASE_URL") or f"sqlite:///{folder / 'yaaf.db'}"
    log = folder / "update.log"

    def ok(text: str) -> None:
        typer.echo(f"✓ {text}")

    if not (REPO / ".git").exists():
        typer.echo(f"{REPO} is not a git checkout, so yaaf update has nothing to pull. "
                   "Update it the way it was installed.", err=True)
        raise typer.Exit(2)
    here = backup = None
    moved = False
    try:
        edits = local_edits(REPO)
        if edits:
            said = ("your checkout has edits to tracked files, which an update would have "
                    "to step on:\n  " + "\n  ".join(edits[:10])
                    + "\nCommit or stash them, then yaaf update again.")
            if not check:
                raise Stop(said)
            typer.echo(f"! {said}")
        here, there, commits = incoming(REPO)
        if not commits:
            ok(f"yaaf is up to date ({here[:8]})")
            return
        typer.echo(f"{len(commits)} change{'s' if len(commits) != 1 else ''} to bring in:")
        for line in commits[:15]:
            typer.echo(f"  {line}")
        if len(commits) > 15:
            typer.echo(f"  … and {len(commits) - 15} more")
        if check:
            return
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        changed = changed_files(REPO, here, there)
        backup = backup_database(url, folder, here)
        if backup:
            ok(f"database backed up to {backup}")
        _out(REPO, "merge", "--ff-only", "--quiet", there)
        moved = True
        ok(f"code updated {here[:8]} → {there[:8]}")
        if needs_packages(changed):
            install_packages(REPO, log)
            ok("Python packages reinstalled")
        if install_browser(log):
            ok("the agents' browser is installed")
        else:
            typer.echo(f"! Chromium did not install; agents are not offered a browser ({log})")
        if not (folder / setup_module().ACCEPTED_FILE).exists():
            # Installed before native mode said what it gives up: said once now.
            typer.echo(setup_module().NATIVE_WARNING)
            (folder / setup_module().ACCEPTED_FILE).write_text("shown by yaaf update\n")
        if needs_web(REPO, changed):
            typer.echo("… rebuilding the dashboard (a minute)")
            build_dashboard(REPO, log)
            ok("dashboard rebuilt")
        ok(f"yaaf {restart(port, folder)}")
        refreshed = refresh_desktop()
        if refreshed:
            ok(f"desktop pieces {refreshed}")
    except Stop as why:
        typer.echo(f"✗ {why}", err=True)
        if not moved:
            typer.echo("Nothing was changed.", err=True)
        else:
            # The checkout had no edits of its own (that was checked first), so
            # moving the branch back loses nothing but the update.
            typer.echo("\nTo go back to where you were:", err=True)
            typer.echo(f"  git -C {REPO} reset --hard {here}", err=True)
            if backup:
                typer.echo(f"  cp {backup} {folder / 'yaaf.db'}", err=True)
            typer.echo("  then restart yaaf (systemctl --user restart yaaf, or yaaf setup)",
                       err=True)
        raise typer.Exit(1) from None
