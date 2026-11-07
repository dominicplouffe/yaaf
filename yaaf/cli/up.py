"""`yaaf up`: one command from a checkout to a dashboard.

The manual dev path is eleven steps, and missing any one gives a box
that looks alive and never moves: no master key and saving a credential
answers 500; no runner and every run sits in the queue; no admin and nobody
can sign in; no role library and the creation form has nothing to hire. This
does all of them, keeps what it made (the database and the key) in one
folder so the next `yaaf up` finds them, and prints one address.

The one thing it will not decide for you is isolation. `subprocess` mode runs
agents' code as you, with no sandbox; the platform refuses to assume it, and
so does this command — you say `--no-isolation`, `--host` for agents on this
machine inside bubblewrap, or you configure docker.
"""

from __future__ import annotations

import ipaddress
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import typer

from yaaf.core.config import DEFAULT_PORT

#: The repository this package was installed from, when it was (an editable
#: install from a checkout). Its web/dist, roles/ and crews/ are what a dev
#: box serves and seeds.
REPO = Path(__file__).resolve().parents[2]

NO_ISOLATION_HELP = (
    "Run agents as yourself, with no sandbox. Fine on a dev machine; never on a "
    "box other people can reach. Real agent runs still need Docker in this mode; "
    "--host runs them on this machine instead. The alternative is "
    "YAAF_SANDBOX_MODE=docker with the sandbox image built (docs/INSTALL.md).")

HOST_HELP = (
    "Run agents on this machine, without Docker, each inside a bubblewrap jail: "
    "its workspace at /workspace, your home folder and yaaf's data out of sight. "
    "Needs `bwrap` (pacman -S bubblewrap / apt install bubblewrap).")


def ensure_key(home: Path) -> str:
    """The master key that encrypts every stored secret: YAAF_MASTER_KEY if
    set, else the one this folder already holds, else a new one written there
    readable by you alone. Losing it loses every stored credential, which is
    why it is kept rather than regenerated."""
    if os.environ.get("YAAF_MASTER_KEY"):
        return os.environ["YAAF_MASTER_KEY"]
    path = home / "master.key"
    if path.exists():
        return path.read_text().strip()
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    path.touch(mode=0o600)
    path.write_text(key + "\n")
    os.chmod(path, 0o600)
    return key


#: Where a desktop install listens unless told otherwise: this machine only.
#: The server's own default is 0.0.0.0, which is right inside a container (the
#: published port decides who reaches it) and wrong on a laptop, where it put
#: the sign-in page on every network the laptop joined.
LOOPBACK = "127.0.0.1"


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def exposure(host: str, port: int) -> str:
    """Who can reach the dashboard, said where it is chosen."""
    if is_loopback(host):
        return "this machine only (--bind 0.0.0.0, or an address, to open it to a network)"
    where = "every network this machine is on" if host in ("0.0.0.0", "::") else host
    return (f"{where}, on port {port}, over plain HTTP — anyone on that network can read "
            "the traffic, your password at sign-in included. To reach it from another "
            "device privately, use an SSH tunnel or a private network (Tailscale, "
            "WireGuard) and bind to that address.")


def environment(home: Path, port: int, sandbox: str, bind: str = "") -> dict[str, str]:
    """The process environment both children run under. Anything already set
    wins: this fills in what a dev box is missing, it does not override a
    configuration somebody chose. `bind` is a choice made on this command
    line, so it wins over the environment too."""
    base = f"http://localhost:{port}"
    env = dict(os.environ)
    env["YAAF_API_HOST"] = bind or env.get("YAAF_API_HOST") or LOOPBACK
    env.setdefault("YAAF_DATABASE_URL", f"sqlite:///{home / 'yaaf.db'}")
    # The server default is /var/lib/yaaf, which a desktop user cannot write:
    # the first run of every project died making its workspace. The folder
    # that already holds the database holds the work too.
    env.setdefault("YAAF_WORKSPACES_ROOT", str(home / "workspaces"))
    env.setdefault("YAAF_ARTIFACTS_ROOT", str(home / "artifacts"))
    env["YAAF_MASTER_KEY"] = ensure_key(home)
    env["YAAF_SANDBOX_MODE"] = sandbox
    env["YAAF_API_PORT"] = str(port)
    env.setdefault("YAAF_API_BASE_URL", base)
    env.setdefault("YAAF_API", base)
    dist = REPO / "web" / "dist"
    if "YAAF_WEB_DIST" not in env and dist.is_dir():
        env["YAAF_WEB_DIST"] = str(dist)
    return env


def _python(code: str, env: dict[str, str], log=None) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", code], env=env,
                            stdout=log or subprocess.DEVNULL, stderr=subprocess.STDOUT)


def _cli(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "yaaf.cli.main", *args], env=env,
                          capture_output=True, text=True)


def _tail(path: Path, lines: int = 15) -> str:
    try:
        return "\n".join(path.read_text().splitlines()[-lines:])
    except OSError:
        return ""


def _seed(env: dict[str, str], base: str) -> None:
    """Fill an empty role library from the checkout, as `seed-roles` and
    `seed-crews` would — with a ten-minute token, the way the deploy scripts
    do, since nobody has signed in yet."""
    roles, crews = REPO / "roles", REPO / "crews"
    if not roles.is_dir():
        return
    minted = _cli(["users", "token", "--first-admin", "--name", "yaaf up", "--minutes", "10"],
                  env)
    token = minted.stdout.strip()
    if minted.returncode != 0 or not token:
        return
    listed = httpx.get(f"{base}/admin/roles", headers={"Authorization": f"Bearer {token}"},
                       timeout=10)
    if listed.status_code != 200 or listed.json():
        return
    seeding = {**env, "YAAF_TOKEN": token}
    for command, folder in (("seed-roles", roles), ("seed-crews", crews)):
        if folder.is_dir():
            done = _cli([command, str(folder)], seeding)
            typer.echo(f"{command}: {'done' if done.returncode == 0 else 'failed'}")


def up(
    no_isolation: bool = typer.Option(False, "--no-isolation", help=NO_ISOLATION_HELP),
    host: bool = typer.Option(False, "--host", help=HOST_HELP),
    email: str = typer.Option("", "--email",
                              help="First run: who the first admin is. Prints their "
                                   "one-time link to choose a password."),
    home: str = typer.Option("", "--home",
                             help="Where the database and the master key live "
                                  "(default ~/.yaaf, or YAAF_HOME)"),
    port: int = typer.Option(DEFAULT_PORT, "--port",
                             help="The port the dashboard and API listen on"),
    bind: str = typer.Option("", "--bind",
                             help="The address to listen on (default 127.0.0.1: this "
                                  "machine only). 0.0.0.0 opens it to every network."),
) -> None:
    """Start yaaf on this machine — API, dashboard, runner — in one command, and
    print the address. Ctrl-C stops it."""
    if host and no_isolation:
        typer.echo("--host and --no-isolation are two different answers; pick one.", err=True)
        raise typer.Exit(2)
    sandbox = (os.environ.get("YAAF_SANDBOX_MODE")
               or ("host" if host else "subprocess" if no_isolation else ""))
    if not sandbox:
        typer.echo("yaaf up will not choose how agents are isolated for you.\n"
                   "  Agents on this machine, no Docker:  yaaf up --host\n"
                   "  On a dev machine, scripted agents:  yaaf up --no-isolation\n"
                   "  Otherwise:         set YAAF_SANDBOX_MODE=docker and build the sandbox "
                   "image (docs/INSTALL.md)", err=True)
        raise typer.Exit(2)
    if sandbox == "host":
        from yaaf.runner import host_jail

        problem = host_jail.unavailable()
        if problem:
            # Said before anything starts: a runner that cannot jail fails every
            # run it leases, which reads as a broken install rather than a
            # missing package.
            typer.echo(problem, err=True)
            raise typer.Exit(2)
    folder = Path(home or os.environ.get("YAAF_HOME") or Path.home() / ".yaaf").expanduser()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = environment(folder, port, sandbox, bind)
    base = env["YAAF_API"]

    # Migrated, not create_all'd: create_all never adds a column to a table
    # that exists, so a desktop that updated answered 500 on the first new one
    # (yaaf.core.schema_upkeep).
    schema = subprocess.run(
        [sys.executable, "-c", "import os; from yaaf.core.schema_upkeep import prepare; "
         "print(prepare(os.environ['YAAF_DATABASE_URL']))"],
        env=env, capture_output=True, text=True)
    if schema.returncode != 0:
        typer.echo(f"Could not prepare the database at {env['YAAF_DATABASE_URL']}:\n"
                   f"{schema.stderr.strip().splitlines()[-1] if schema.stderr else ''}",
                   err=True)
        raise typer.Exit(1)
    if "YAAF_WEB_DIST" not in env and not Path("web/dist").is_dir():
        typer.echo("The dashboard is not built, so only the API will answer. Build it once "
                   "with: cd web && npm install && npm run build", err=True)

    # A plain kill (a terminal closing, `timeout`, a service manager) must stop
    # both children too, not orphan them on the port: SIGTERM takes the same
    # path as Ctrl-C.
    def _stop(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    server_log, runner_log = folder / "server.log", folder / "runner.log"
    server = _python("from yaaf.server.main import main; main()", env,
                     server_log.open("a"))
    runner = _python("from yaaf.runner.daemon import main; main()", env,
                     runner_log.open("a"))
    children = (server, runner)
    try:
        ready = False
        for _ in range(60):
            if server.poll() is not None:
                break
            try:
                ready = httpx.get(f"{base}/healthz", timeout=1).status_code == 200
            except httpx.HTTPError:
                ready = False
            if ready:
                break
            time.sleep(0.5)
        if not ready:
            what = ("stopped while starting" if server.poll() is not None
                    else f"did not answer on {base} within 30 seconds")
            typer.echo(f"The server {what}. Its log ({server_log}):\n{_tail(server_log)}",
                       err=True)
            raise typer.Exit(1)

        if email:
            made = _cli(["users", "bootstrap", "--email", email, "--base-url", base], env)
            typer.echo((made.stdout or made.stderr).strip())
        _seed(env, base)

        typer.echo(f"\nyaaf is up: {base}/app")
        typer.echo(f"  data and key: {folder}   logs: {server_log}, {runner_log}")
        typer.echo(f"  reachable from: {exposure(env['YAAF_API_HOST'], port)}")
        typer.echo("  agents run: " + {
            "host": "on this machine, each in a bubblewrap jail",
            "subprocess": "scripted only, with no isolation (real runs need Docker)",
        }.get(sandbox, f"in {sandbox} sandboxes"))
        # Only on a box with nobody to sign in: under `yaaf setup` the service
        # starts after the admin exists, and this line in its journal was a
        # wrong instruction.
        if not email and _cli(["users", "has-admin"], env).returncode != 0:
            typer.echo("  first time here? run `yaaf setup`, or run again with "
                       "--email you@example.com for your sign-in link")
        typer.echo("  check it any time with: yaaf doctor   ·   Ctrl-C stops both processes")
        while all(child.poll() is None for child in children):
            time.sleep(1)
        stopped = "server" if server.returncode is not None else "runner"
        log = server_log if stopped == "server" else runner_log
        typer.echo(f"The {stopped} stopped. Its log ({log}):\n{_tail(log)}", err=True)
        raise typer.Exit(1)
    except KeyboardInterrupt:
        typer.echo("\nstopping…")
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
