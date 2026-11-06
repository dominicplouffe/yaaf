"""`yaaf setup`: from a fresh desktop to a working yaaf, in five steps.

`yaaf up --host` starts everything, but everything around it was still a page
of instructions: open a one-time link to choose a password, sign the CLI in
separately, create a credential, point the Founder at it, keep a terminal open
forever, and know that a Codex installed in your home folder needs a setting
before an agent can see it. Each step is easy and each one, missed, leaves an
install that looks alive and does nothing — so they are asked here, in order,
once, and checked.

It is safe to run again: every step looks first and skips what is done. It
asks for exactly three things — an email, a password, a key — and never echoes
the last two.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import typer

from yaaf.cli import up as up_cmd

STEPS = 5

#: The providers this wizard offers, with where a person gets a key. Every
#: other provider is one form away in Settings → Providers; a menu of thirty
#: is not a wizard.
PROVIDERS = {
    "1": ("anthropic", "Anthropic (Claude)", "https://console.anthropic.com/settings/keys"),
    "2": ("openai", "OpenAI", "https://platform.openai.com/api-keys"),
}

SERVICE = "yaaf.service"


def _step(n: int, title: str) -> None:
    typer.echo(f"\n{n}/{STEPS}  {title}")


def _ok(text: str) -> None:
    typer.echo(f"     ✓ {text}")


def _note(text: str) -> None:
    typer.echo(f"     ! {text}")


def _stop(text: str, fix: str = "") -> None:
    typer.echo(f"     ✗ {text}")
    if fix:
        typer.echo(f"       → {fix}")
    raise typer.Exit(1)


# ── step 1: this machine ────────────────────────────────────────────────────────


def _check_machine(folder: Path) -> None:
    from yaaf.runner import host_jail

    problem = host_jail.unavailable()
    if problem:
        _stop(problem.removeprefix("Refused: "),
              "sudo pacman -S bubblewrap   (Debian/Ubuntu: sudo apt install bubblewrap)")
    _ok("bubblewrap can make a sandbox, so agents run on this machine without Docker")
    dist = up_cmd.REPO / "web" / "dist"
    if dist.is_dir():
        _ok("the dashboard is built")
        return
    npm = shutil.which("npm")
    if npm is None:
        _stop("the dashboard is not built, and npm is not installed to build it",
              "sudo pacman -S nodejs npm, then run yaaf setup again")
    typer.echo("     … building the dashboard (once, about a minute)")
    log = folder / "dashboard-build.log"
    with log.open("w") as out:
        for args in ([npm, "install"], [npm, "run", "build"]):
            if subprocess.run(args, cwd=up_cmd.REPO / "web", stdout=out,
                              stderr=subprocess.STDOUT).returncode != 0:
                _stop(f"building the dashboard failed: {' '.join(args[1:])}",
                      f"the output is in {log}")
    _ok("the dashboard is built")


# ── step 2: the account ─────────────────────────────────────────────────────────


def _ask_password() -> str:
    from yaaf.server import identity

    while True:
        password = typer.prompt(f"     Choose a password ({identity.MIN_PASSWORD}+ characters)",
                                hide_input=True)
        if len(password) < identity.MIN_PASSWORD:
            typer.echo(f"       That is {len(password)}. It stands between the network and "
                       "everything you chair — try a longer one.")
            continue
        if typer.prompt("     Type it again", hide_input=True) != password:
            typer.echo("       Those were different. Once more.")
            continue
        return password


def _account() -> tuple[str, str] | None:
    """Make the first admin, with a password, straight in the database — the
    door `yaaf users bootstrap` uses, because a shell that can reach the
    database already holds the box. Returns the email and password to sign
    the CLI in with, or None when the box already has its admin."""
    from yaaf.cli.users import _active_admins, _audit
    from yaaf.core import ids
    from yaaf.core.db import db_session, engine
    from yaaf.core.models import Base, User
    from yaaf.server import identity

    Base.metadata.create_all(engine())
    with db_session() as session:
        if _active_admins(session):
            _ok("this box already has its admin")
            return None
    while True:
        email = identity.normalise_email(typer.prompt("     Your email"))
        if "@" in email:
            break
        typer.echo("       That is not an email address.")
    password = _ask_password()
    with db_session() as session:
        user = identity.find_user(session, email)
        created = user is None
        if created:
            user = User(id=ids.new_id(ids.USER), email=email,
                        display_name=email.split("@")[0])
            session.add(user)
            session.flush()
        user.is_admin = True
        user.deactivated_at = None
        identity.set_password(user, password)
        _audit(session, "user.bootstrap", user, {"email": email, "created": created,
                                                 "via": "yaaf setup"})
        _audit(session, "user.password_set", user, {"via": "yaaf setup"})
    _ok(f"{email} is this box's admin")
    return email, password


# ── step 3: keep it running ─────────────────────────────────────────────────────


def _healthy(base: str) -> bool:
    try:
        return httpx.get(f"{base}/healthz", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def _npm_global_bind() -> str | None:
    """Where `npm install -g` put the harness CLIs, when that is somewhere the
    jail does not already see. On Arch it is /usr and needs nothing; with a
    version manager it is in the home folder, and a Codex the agent cannot
    find is a seat that dies on its first run."""
    npm = shutil.which("npm")
    if npm is None:
        return None
    try:
        prefix = subprocess.run([npm, "prefix", "-g"], capture_output=True, text=True,
                                timeout=20).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not prefix or not Path(prefix).is_dir():
        return None
    if prefix == "/usr" or prefix.startswith(("/usr/", "/opt/")):
        return None
    return prefix


def service_environment() -> dict[str, str]:
    """What the background service needs from this shell, and nothing more:
    the PATH that finds bwrap, git and the harness CLIs, and where those CLIs
    live. Never a key — yaaf up reads the master key from its own folder, and a
    unit file is not a secret store."""
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
    binds = [b for b in os.environ.get("YAAF_HOST_JAIL_BINDS", "").split(os.pathsep) if b]
    npm = _npm_global_bind()
    if npm and npm not in binds:
        binds.append(npm)
    if binds:
        env["YAAF_HOST_JAIL_BINDS"] = os.pathsep.join(binds)
    return env


def _up_command(port: int, folder: Path) -> list[str]:
    return [sys.executable, "-m", "yaaf.cli.main", "up", "--host",
            "--port", str(port), "--home", str(folder)]


def unit_file(port: int, folder: Path) -> str:
    env = service_environment()
    lines = "\n".join(f'Environment="{k}={v}"' for k, v in env.items())
    return (
        "# Written by `yaaf setup`. Run it again to rewrite this file.\n"
        "[Unit]\n"
        "Description=yaaf: your agent company\n"
        "After=network.target\n\n"
        "[Service]\n"
        f"ExecStart={' '.join(_up_command(port, folder))}\n"
        f"{lines}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _systemd_user() -> bool:
    if shutil.which("systemctl") is None:
        return False
    return subprocess.run(["systemctl", "--user", "show-environment"],
                          capture_output=True).returncode == 0


def _start(port: int, folder: Path, base: str, service: bool) -> str:
    """Start yaaf so it outlives this terminal. A user service where systemd
    is running — it starts again when you log in — and a detached process
    where it is not. Returns how to stop it."""
    detached_stop = f"pkill -f 'yaaf.cli.main up --host --port {port}'"
    if _healthy(base):
        _ok(f"yaaf is already running at {base}")
        return "systemctl --user stop yaaf" if service and _systemd_user() else detached_stop
    if service and _systemd_user():
        unit = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") \
            / "systemd" / "user" / SERVICE
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(unit_file(port, folder))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE],
                       capture_output=True, check=False)
        how, stop = "as a background service that starts when you log in", \
            "systemctl --user stop yaaf"
    else:
        log = (folder / "yaaf-up.log").open("a")
        subprocess.Popen(_up_command(port, folder), stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True,
                         env={**os.environ, **service_environment()})
        how = "in the background until you log out (run yaaf setup again after a reboot)"
        stop = detached_stop
    typer.echo("     … starting (the first start loads the role library)")
    for _ in range(120):
        if _healthy(base):
            _ok(f"yaaf is running {how}")
            return stop
        time.sleep(1)
    _stop(f"yaaf did not answer at {base} within two minutes",
          f"its log: {folder / 'server.log'}"
          + ("   ·   journalctl --user -u yaaf -n 50" if service and _systemd_user() else ""))
    return stop


def _signed_in(base: str) -> str | None:
    from yaaf.cli import main as cli

    token = cli._token()
    if not token:
        return None
    try:
        me = httpx.get(f"{base}/auth/me", headers={"Authorization": f"Bearer {token}"},
                       timeout=10)
    except httpx.HTTPError:
        return None
    return me.json().get("email") if me.status_code == 200 else None


def _sign_in(base: str, account: tuple[str, str] | None) -> None:
    """Give this terminal a personal token, as `yaaf login` would, with the
    password just chosen rather than asking for it a second time."""
    import socket

    from yaaf.cli import main as cli

    who = _signed_in(base)
    if who:
        _ok(f"this terminal is signed in as {who}")
        return
    for _attempt in range(3):
        email, password = account or (typer.prompt("     Your email"),
                                      typer.prompt("     Your password", hide_input=True))
        answer = httpx.post(f"{base}/auth/token", timeout=30, json={
            "email": email, "password": password,
            "name": f"cli@{socket.gethostname()}"[:80], "days": 365})
        if answer.status_code == 200:
            body = answer.json()
            store = cli._read_credentials()
            store[base] = {"token": body["token"], "email": email, "name": body["name"],
                           "expires_at": body["expires_at"]}
            cli._write_credentials(store)
            _ok(f"this terminal is signed in as {email} (until {body['expires_at'][:10]})")
            return
        account = None
        typer.echo(f"       Not signed in: {cli._detail(answer)}")
    _stop("could not sign in", "yaaf login")


def _library(base: str) -> None:
    """Wait for the role library. `yaaf up` loads it just after it starts
    answering, so a wizard that moved on at the first healthy answer finished
    with doctor reporting an empty library that filled two seconds later."""
    if not (up_cmd.REPO / "roles").is_dir():
        return
    # Crews load after roles, so a library with roles and no crews yet is
    # still loading — not a box whose checkout ships no crews.
    want_crews = (up_cmd.REPO / "crews").is_dir()
    with _client(base) as client:
        for _ in range(90):
            roles = client.get("/admin/roles")
            crews = client.get("/admin/crews")
            hired = roles.json() if roles.status_code == 200 else []
            teams = crews.json() if crews.status_code == 200 else []
            if hired and (teams or not want_crews):
                _ok(f"{len(hired)} roles and {len(teams)} ready-made teams to hire from")
                return
            time.sleep(1)
    _note("the role library is still empty — run: yaaf seed-roles roles/ && yaaf seed-crews crews/")


# ── steps 4 and 5: a key, and the Founder ───────────────────────────────────────


def _client(base: str) -> httpx.Client:
    from yaaf.cli import main as cli

    return httpx.Client(base_url=base, timeout=60,
                        headers={"Authorization": f"Bearer {cli._token()}"})


def _key(base: str) -> dict | None:
    """One LLM credential, checked with the provider's own free model list
    before it is kept: a key that does not answer is removed and asked for
    again, so the box never holds a credential nobody has seen work."""
    from yaaf.cli import main as cli

    with _client(base) as client:
        have = client.get("/admin/settings/llm-credentials")
        listed = have.json() if have.status_code == 200 else []
        if listed:
            _ok(f"{len(listed)} key(s) on file: {', '.join(c['name'] for c in listed)}")
            return listed[0]
        typer.echo("     Which provider should pay for your agents?")
        for choice, (_, label, _url) in PROVIDERS.items():
            typer.echo(f"       {choice}  {label}" + ("   (recommended)" if choice == "1" else ""))
        typer.echo("       s  skip for now (Settings → Providers, any time)")
        choice = typer.prompt("     Choose", default="1").strip().lower()
        if choice not in PROVIDERS:
            _note("no key yet — projects cannot start until one is added")
            return None
        provider, label, url = PROVIDERS[choice]
        typer.echo(f"     Create a key at {url}")
        for _ in range(3):
            value = typer.prompt(f"     Paste your {label} key (hidden)", hide_input=True).strip()
            made = client.post("/admin/settings/llm-credentials", json={
                "name": provider, "mode": "api_key", "value": value, "provider": provider})
            if made.status_code != 201:
                typer.echo(f"       Not saved: {cli._detail(made)}")
                continue
            row = made.json()
            answer = client.get(f"/admin/settings/llm-credentials/{row['id']}/models")
            if answer.status_code == 200:
                served = answer.json().get("models") or []
                _ok(f"{label} answered{f' — {len(served)} models' if served else ''}; "
                    f"saved as {provider!r}")
                return row
            client.delete(f"/admin/settings/llm-credentials/{row['id']}")
            typer.echo(f"       {label} did not accept it: {cli._detail(answer)}")
        _note("no key yet — add one under Settings → Providers")
        return None


def _founder(base: str, key: dict | None) -> None:
    with _client(base) as client:
        status = client.get("/admin/founder/status")
        if status.status_code == 200 and status.json().get("credential_configured"):
            _ok("the Founder can draft projects with you")
            return
        if key is None:
            _note("the Founder needs a key first")
            return
        done = client.put("/admin/founder/credential/llm", json={"credential_id": key["id"]})
        if done.status_code == 200:
            _ok(f"the Founder drafts on {key['name']!r}, the same key")
        else:
            _note(f"the Founder is not set: {done.text[:200]}")


def _problems(base: str) -> list[dict]:
    with _client(base) as client:
        report = client.get("/admin/settings/doctor")
    if report.status_code != 200:
        return []
    return [c for c in report.json().get("checks", [])
            if not c["ok"] and c["level"] == "problem"]


# ── the command ─────────────────────────────────────────────────────────────────


def setup(
    port: int = typer.Option(8080, "--port", help="The port the dashboard listens on"),
    home: str = typer.Option("", "--home",
                             help="Where the database and key live (default ~/.yaaf)"),
    service: bool = typer.Option(True, "--service/--no-service",
                                 help="Keep yaaf running as a systemd user service"),
) -> None:
    """Set yaaf up on this machine, step by step: checks, your account, a
    background service, a key, the Founder. Safe to run again."""
    folder = Path(home or os.environ.get("YAAF_HOME") or Path.home() / ".yaaf").expanduser()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    base = f"http://localhost:{port}"
    # This process opens the same database the service will, and signs in to
    # the same address: the environment `yaaf up` would give its children.
    os.environ.update({k: v for k, v in up_cmd.environment(folder, port, "host").items()
                       if k in ("YAAF_DATABASE_URL", "YAAF_MASTER_KEY", "YAAF_SANDBOX_MODE",
                                "YAAF_WORKSPACES_ROOT", "YAAF_ARTIFACTS_ROOT")})
    os.environ["YAAF_API"] = base
    from yaaf.core.config import settings

    settings.cache_clear()

    typer.echo("yaaf setup — about two minutes. Safe to run again: it skips what is done.")
    _step(1, "This machine")
    _check_machine(folder)
    _step(2, "Your account")
    account = _account()
    _step(3, "Starting yaaf")
    stop = _start(port, folder, base, service)
    _sign_in(base, account)
    _library(base)
    _step(4, "A key to pay for the agents")
    key = _key(base)
    _step(5, "The Founder, who drafts projects with you")
    _founder(base, key)

    problems = _problems(base)
    typer.echo("")
    for check in problems:
        typer.echo(f"✗ {check['name']}: {check['detail']}")
        if check.get("fix"):
            typer.echo(f"    → {check['fix']}")
    typer.echo(f"{'Almost' if problems else 'Done'}. yaaf is at {base}/app")
    typer.echo(f"  data and key: {folder}  (back up master.key: it unlocks every stored key)")
    typer.echo(f"  stop it: {stop}")
    if port != 8080:
        typer.echo(f"  this CLI talks to it with: export YAAF_API={base}")
    typer.echo("  try a company for $0 first: yaaf rehearse examples/01-your-first-hour.json")
    graphical = os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")
    if graphical and shutil.which("xdg-open") and sys.stdin.isatty() \
            and typer.confirm("Open the dashboard now?", default=True):
        subprocess.Popen(["xdg-open", f"{base}/app"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    if problems:
        raise typer.Exit(1)
