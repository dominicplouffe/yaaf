"""`yaaf desktop`: yaaf in the desktop you already use, on any Linux.

Three things, each through a standard nearly every Linux desktop follows, so
the same install works on GNOME, KDE, Hyprland (Omarchy's), Sway and Xfce, on
Ubuntu, Fedora or Arch:

- **App-menu entries** (freedesktop .desktop files in ~/.local/share/applications):
  the dashboard in a window of its own, the desk, the morning letter.
- **A nudge** (`yaaf nudge`, on a systemd user timer): at most one desktop
  notification a day, only when a decision is about to decide itself or a
  project has stopped at its spending wall. Through the desktop's own
  notification service, so Do Not Disturb silences it like anything else.
- **The morning letter** (an XDG autostart entry): at login, the platform's
  briefing in a terminal — only when something happened, once a day.

Everything it writes lives in your own folders, carries a line saying who
wrote it, and is listed in a manifest, so `yaaf desktop remove` takes out
exactly what install put in — and leaves alone a file you have since edited.
Nothing here decides anything: every entry opens a screen that already has
its safeguards (the dashboard, `yaaf desk`, the briefing).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
import typer

desktop_app = typer.Typer(no_args_is_help=True,
                          help="yaaf in your desktop: app menu, nudges, the morning letter.")

MARK = "Managed by `yaaf desktop install`; `yaaf desktop remove` takes it out."
NUDGE_UNIT = "yaaf-nudge"
#: A nudge is never sent before or after these local hours: a decision that
#: expires at 3am was worth hearing about the evening before, and is.
DAY_STARTS, DAY_ENDS = 8, 22
#: What a nudge remembers telling you, so it never tells you twice.
TOLD_DAYS = 7


# ── where things live ───────────────────────────────────────────────────────────


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback)


def data_home() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def config_home() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def state_home() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "yaaf"


def _yaaf_bin() -> str:
    """The yaaf this is, by absolute path: a desktop session's PATH often
    lacks ~/.local/bin, and a launcher that cannot find its command is a menu
    entry that does nothing when clicked."""
    beside = Path(sys.executable).parent / "yaaf"
    if beside.exists():
        return str(beside)
    return shutil.which("yaaf") or "yaaf"


def _api_base() -> str:
    from yaaf.cli import main as cli

    return cli._api_base()


# ── opening things the way this desktop does ────────────────────────────────────

#: Terminals and how each is told to run a command. Order is preference after
#: the desktop's own answers (xdg-terminal-exec, $TERMINAL). kgx and ptyxis
#: are GNOME's newer terminals; their run-a-command spelling is the least
#: settled of the list, which is why YAAF_TERMINAL exists.
TERMINALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gnome-terminal", ("--",)), ("konsole", ("-e",)), ("xfce4-terminal", ("-x",)),
    ("x-terminal-emulator", ("-e",)), ("alacritty", ("-e",)), ("kitty", ()), ("foot", ()),
    ("wezterm", ("start", "--")), ("ghostty", ("-e",)), ("kgx", ("--",)), ("ptyxis", ("--",)),
    ("tilix", ("-e",)), ("xterm", ("-e",)),
)


def terminal_command(argv: list[str]) -> list[str] | None:
    """How to open a terminal window running `argv` here, or None.

    YAAF_TERMINAL (a command prefix, e.g. "foot" or "kitty --class yaaf")
    wins; then xdg-terminal-exec, the standard Omarchy and newer desktops
    ship; then $TERMINAL; then the first terminal installed."""
    custom = os.environ.get("YAAF_TERMINAL", "").strip()
    if custom:
        return [*shlex.split(custom), *argv]
    if shutil.which("omarchy-launch-tui"):
        # Omarchy's own TUI launcher; TUI.float is the app id its window rules
        # float, so the desk opens over your work instead of tiling into it.
        return ["omarchy-launch-tui", "--app-id=TUI.float", *argv]
    if shutil.which("xdg-terminal-exec"):
        return ["xdg-terminal-exec", *argv]
    named = os.environ.get("TERMINAL", "").strip()
    known = dict(TERMINALS)
    if named and shutil.which(named):
        return [named, *known.get(Path(named).name, ("-e",)), *argv]
    for name, prefix in TERMINALS:
        if shutil.which(name):
            return [name, *prefix, *argv]
    return None


CHROMIUM_FAMILY = (("brave", ("brave", "brave-browser")),
                   ("chrome", ("google-chrome-stable", "google-chrome")),
                   ("edge", ("microsoft-edge", "microsoft-edge-stable")),
                   ("vivaldi", ("vivaldi", "vivaldi-stable")),
                   ("chromium", ("chromium", "chromium-browser")))


def browser_command(url: str) -> list[str]:
    """The dashboard in a window of its own when the default browser can make
    one, and in the default browser when it cannot. Your default, not ours: a
    second browser is a second sign-in."""
    if shutil.which("omarchy-launch-webapp"):
        return ["omarchy-launch-webapp", url]
    default = ""
    if shutil.which("xdg-settings"):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            default = subprocess.run(["xdg-settings", "get", "default-web-browser"],
                                     capture_output=True, text=True, timeout=5).stdout.lower()
    for marker, binaries in CHROMIUM_FAMILY:
        if marker in default:
            found = next((b for b in binaries if shutil.which(b)), None)
            if found:
                return [found, f"--app={url}"]
    return ["xdg-open", url]


def _detach(argv: list[str], env: dict | None = None) -> None:
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True,
                     env={**os.environ, **(env or {})})


@desktop_app.command("launch")
def launch(
    what: str = typer.Argument(..., help="dashboard, desk or letter"),
    if_new: bool = typer.Option(False, "--if-new",
                                help="letter only: open it only if something happened and "
                                     "it was not shown today"),
) -> None:
    """Open the dashboard, the desk or the morning letter the way this desktop
    opens things. What the app-menu entries run."""
    if what == "dashboard":
        _detach(browser_command(f"{_api_base()}/app/"))
        return
    if what not in ("desk", "letter"):
        typer.echo("launch what? dashboard, desk or letter", err=True)
        raise typer.Exit(2)
    if what == "letter" and if_new and not _letter_is_due():
        return
    argv = terminal_command([_yaaf_bin(), what])
    if argv is None:
        typer.echo("No terminal found — set YAAF_TERMINAL to the command that opens one "
                   "(e.g. YAAF_TERMINAL=foot).", err=True)
        raise typer.Exit(1)
    _detach(argv)


@desktop_app.command("indicator")
def indicator_command() -> None:
    """yaaf's icon in the top bar of this desktop, with its menu: what needs
    you, each project, the dashboard, the desk, start and stop. Runs until the
    session ends; `yaaf desktop install` starts it at login (everywhere but
    Omarchy, whose bar has yaaf's panel)."""
    from yaaf.cli import indicator

    problem = indicator.available()
    if problem:
        typer.echo(f"No tray icon here: {problem}.", err=True)
        raise typer.Exit(1)
    indicator.run(_yaaf_bin(), str(data_home() / "icons"))


@desktop_app.command("open")
def open_url(url: str = typer.Argument(..., help="A dashboard address, e.g. what the "
                                               "panel's rows carry")) -> None:
    """Open one dashboard screen — an Inbox row, a project, its chat — the way
    this desktop opens the dashboard. What the bar panel and the tray menu run.
    Only yaaf's own address: a click in the bar never opens anything else."""
    base = _api_base().rstrip("/")
    if not (url == base or url.startswith(base + "/")):
        typer.echo(f"not a yaaf address: {url} (yaaf is at {base})", err=True)
        raise typer.Exit(2)
    _detach(browser_command(url))


# ── the morning letter ──────────────────────────────────────────────────────────


def _read_state(name: str) -> dict:
    try:
        return json.loads((state_home() / name).read_text())
    except (OSError, ValueError):
        return {}


def _write_state(name: str, data: dict) -> None:
    folder = state_home()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    (folder / name).write_text(json.dumps(data, indent=2))


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _briefing(client: httpx.Client) -> dict:
    response = client.get("/observe/briefing")
    response.raise_for_status()
    return response.json()


def letter_is_due(briefing: dict, state: dict, today: str) -> bool:
    """Once a day, and only when something happened: a letter that says
    "nothing happened" every morning is a window people learn to close."""
    return state.get("shown_on") != today and not briefing.get("quiet", True)


def _letter_is_due() -> bool:
    from yaaf.cli import main as cli

    try:
        with cli._client() as client:
            return letter_is_due(_briefing(client), _read_state("letter.json"), _today())
    except (httpx.HTTPError, typer.Exit):
        # At login, yaaf may not be up yet, or this machine may be signed
        # out. Either way the answer is to stay quiet, not to open a window
        # that says so.
        return False


def letter() -> None:
    """The morning letter: what happened since you last read one, across your
    live projects, in the platform's own words. `s` marks it read."""
    from yaaf.cli import main as cli
    from yaaf.cli.desk import _read_key
    from yaaf.server.briefing import render_text

    with cli._client() as client:
        try:
            briefing = _briefing(client)
        except httpx.HTTPError as exc:
            typer.echo(f"yaaf did not answer at {cli._api_base()} ({type(exc).__name__}).")
            raise typer.Exit(1) from None
        briefs = briefing.get("projects") or []
        typer.echo(f"yaaf · morning letter · {datetime.now().astimezone():%A %d %B}\n")
        if not briefs:
            typer.echo("No live projects.")
        for brief in briefs:
            typer.echo(f"── {brief['name']} " + "─" * max(0, 60 - len(brief["name"])))
            typer.echo(render_text(brief) + "\n")
        _write_state("letter.json", {"shown_on": _today()})
        if not sys.stdin.isatty() or not briefs:
            return
        typer.echo("[s] mark it read   [d] open the desk   [o] open the dashboard   [q] close")
        key = _read_key(3600)
        if key == "s":
            for brief in briefs:
                client.post(f"/admin/projects/{brief['project_id']}/briefing/seen",
                            json={"until": brief["until"]})
            typer.echo(f"Marked {len(briefs)} read.")
        elif key == "d":
            from yaaf.cli.desk import desk

            desk()
        elif key == "o":
            _detach(browser_command(f"{cli._api_base()}/app/"))


# ── the nudge ───────────────────────────────────────────────────────────────────


@dataclass
class Nudge:
    title: str
    body: str
    keys: list[str]
    #: The button, and what it runs (default: the desk).
    action_label: str = "Open the desk"
    action: list[str] | None = None


#: At least this long between two message notifications, however many arrive.
LETTER_GAP_S = 10 * 60


def plan_letters(desk_payload: dict, state: dict, now: datetime) -> tuple[Nudge | None, dict]:
    """A notification when a CEO has written to you since the last one —
    never at night, at most one every ten minutes, and only for messages that
    are new: each project's unread count is remembered, so reading them (or
    leaving them) never brings the same ones back. Returns the notification,
    if any, and the counts to remember."""
    from yaaf.cli import main as cli
    from yaaf.cli.desk import unread_messages

    unread = {u["project"]["id"]: u for u in unread_messages(desk_payload)}
    counts = {pid: u["count"] for pid, u in unread.items()}
    seen = state.get("seen") or {}
    new = [u for pid, u in unread.items() if u["count"] > int(seen.get(pid, 0))]
    local = now.astimezone()
    quiet = not DAY_STARTS <= local.hour < DAY_ENDS
    recent = now.timestamp() - float(state.get("sent_at") or 0) < LETTER_GAP_S
    if not new or quiet or recent:
        # Held, not dropped: what was not said stays new until it can be.
        keep = {pid: min(c, int(seen.get(pid, 0))) for pid, c in counts.items()}
        return None, {**state, "seen": keep}
    lines = [f"{u['project']['name']}: {u['count'] - int(seen.get(u['project']['id'], 0))} new "
             f"(of {u['count']} unread)" for u in new[:4]]
    first = new[0]["project"]
    title = (f"yaaf: the CEO of {first['name']} wrote to you" if len(new) == 1
             else f"yaaf: {len(new)} of your CEOs wrote to you")
    from yaaf.cli.desk import screen_url

    chat = screen_url(cli._api_base(), f"projects/{first['id']}/chat")
    nudge = Nudge(title=title, body="\n".join(lines), keys=[], action_label="Open the chat",
                  action=[_yaaf_bin(), "desktop", "open", chat])
    return nudge, {**state, "seen": counts, "sent_at": now.timestamp()}


def plan_nudge(desk_payload: dict, state: dict, now: datetime) -> Nudge | None:
    """At most one a day, never at night, and only about something new that
    will happen without you: a decision deciding itself within a day, or a
    project stopped at its wall. Anything else is the Inbox's to say."""
    from yaaf.cli.desk import SOON, at_the_wall, items, when

    local = now.astimezone()
    if not DAY_STARTS <= local.hour < DAY_ENDS:
        return None
    if state.get("sent_on") == local.date().isoformat():
        return None
    stamp = now.timestamp()
    soon = [it for it in items(desk_payload)
            if it.clocked and it.at - stamp <= SOON.total_seconds()]
    walls = at_the_wall(desk_payload)
    told = set((state.get("told") or {}).keys())
    keys = [it.key for it in soon] + [f"wall:{p['id']}" for p in walls]
    if not keys or set(keys) <= told:
        return None
    lines = [f"{it.project['name']}: {it.title} — {when(it.at, stamp)}" for it in soon[:4]]
    lines += [f"{p['name']} is paused at its spending limit" for p in walls[:2]]
    parts = []
    if len(soon) == 1:
        parts.append("a decision decides itself soon")
    elif soon:
        parts.append(f"{len(soon)} decisions decide themselves soon")
    if len(walls) == 1:
        parts.append("a project stopped at its spending limit")
    elif walls:
        parts.append(f"{len(walls)} projects stopped at their spending limits")
    return Nudge(title="yaaf: " + "; ".join(parts), body="\n".join(lines), keys=keys)


def _supports_actions() -> bool:
    try:
        shown = subprocess.run(["notify-send", "--help"], capture_output=True, text=True,
                               timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return "--action" in shown.stdout and "--wait" in shown.stdout


def notify_command(nudge: Nudge) -> list[str] | None:
    """The desktop's own notification service, through notify-send where it
    is installed (with an "Open the desk" button where it can wait for one),
    and straight over D-Bus where only gdbus is. Normal urgency, always: Do
    Not Disturb is the person's, and a nudge is not an emergency."""
    action = nudge.action or [_yaaf_bin(), "desktop", "launch", "desk"]
    if shutil.which("omarchy-notification-send"):
        # Omarchy's own sender keeps the button across shell restarts with no
        # process waiting on it. Named yaaf: its default name, omarchy-action,
        # is one Do Not Disturb lets through, and a nudge must not be.
        return ["omarchy-notification-send", "--app-name", "yaaf", "-u", "normal",
                nudge.title, nudge.body, "--action", nudge.action_label, *action]
    if shutil.which("notify-send"):
        base = ["notify-send", "--app-name=yaaf", "--icon=yaaf", "--urgency=normal",
                nudge.title, nudge.body]
        if not _supports_actions():
            return base
        # notify-send prints the action's name when it is clicked; the shell
        # around it opens the desk then. It waits for the click, so it runs
        # detached, and only as long as the notification lives.
        waiting = shlex.join([*base, f"--action=desk={nudge.action_label}", "--wait"])
        opener = shlex.join(action)
        return ["sh", "-c", f'[ "$({waiting})" = desk ] && exec {opener}']
    if shutil.which("gdbus"):
        return ["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
                "--object-path", "/org/freedesktop/Notifications",
                "--method", "org.freedesktop.Notifications.Notify",
                "yaaf", "0", "yaaf", nudge.title, nudge.body, "[]", "{}", "-1"]
    return None


def nudge(dry_run: bool = typer.Option(False, "--dry-run",
                                       help="say what it would send, send nothing")) -> None:
    """At most one desktop notification a day, only when a decision is about to
    decide itself or a project has stopped at its wall. Run by the timer
    `yaaf desktop install` sets up; safe to run by hand."""
    from yaaf.cli import main as cli
    from yaaf.cli.desk import fetch_desk

    try:
        with cli._client() as client:
            payload = fetch_desk(client)
    except (httpx.HTTPError, typer.Exit):
        return  # down or signed out: the bar says so; a nudge says nothing
    now = datetime.now().astimezone()
    letters_state = _read_state("letters.json")
    letter, remembered = plan_letters(payload, letters_state, now)
    if dry_run and letter is not None:
        typer.echo(f"{letter.title}\n{letter.body}")
    elif letter is not None:
        argv = notify_command(letter)
        if argv is not None:
            _detach(argv)
    if not dry_run:
        _write_state("letters.json", remembered)

    state = _read_state("nudge.json")
    planned = plan_nudge(payload, state, now)
    if planned is None:
        if dry_run and letter is None:
            typer.echo("Nothing to nudge about.")
        return
    if dry_run:
        typer.echo(f"{planned.title}\n{planned.body}")
        return
    argv = notify_command(planned)
    if argv is None:
        typer.echo("No notification service found (notify-send or gdbus).", err=True)
        raise typer.Exit(1)
    _detach(argv)
    cutoff = now.timestamp() - TOLD_DAYS * 86400
    told = {k: t for k, t in (state.get("told") or {}).items() if t >= cutoff}
    told.update({k: now.timestamp() for k in planned.keys})
    _write_state("nudge.json", {"sent_on": now.date().isoformat(), "told": told})


# ── install, remove, status ─────────────────────────────────────────────────────


def _words(*args: str) -> list[str]:
    """The absolute yaaf with these arguments, and the API it talks to when
    that is not the default."""
    from yaaf.core.config import DEFAULT_PORT

    words = [_yaaf_bin(), *args]
    if _api_base() != f"http://localhost:{DEFAULT_PORT}":
        words = ["env", f"YAAF_API={_api_base()}", *words]
    return words


def _exec(*args: str) -> str:
    """An Exec line for a desktop entry."""
    return " ".join(exec_quote(w) for w in _words(*args))


def _exec_start(*args: str) -> str:
    """An ExecStart line for a systemd unit, which quotes C-style and gives
    % and $ meanings of their own."""
    def quote(word: str) -> str:
        word = word.replace("%", "%%").replace("$", "$$")
        if not any(c in word for c in " \t\"'\\;"):
            return word
        return '"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return " ".join(quote(w) for w in _words(*args))


def exec_quote(word: str) -> str:
    """One argument of an Exec line, per the desktop entry spec: quoted when
    it holds a reserved character, with the four that stay special inside
    quotes escaped — and % doubled everywhere, since a bare one is a field
    code the launcher would substitute."""
    word = word.replace("%", "%%")
    if not any(c in word for c in " \t\n\"'\\><~|&;$*?#()`"):
        return word
    # Two backslashes in the file per escape: the string-level unescaping
    # (\\ to \) runs before the quoting rule reads them.
    word = word.replace("\\", "\\" * 4)
    for c in ("\"", "`", "$"):
        word = word.replace(c, "\\" * 2 + c)
    return f'"{word}"'


def _entry(name: str, comment: str, exec_line: str, *, extra: str = "") -> str:
    return (f"# {MARK}\n[Desktop Entry]\nType=Application\nVersion=1.0\nName={name}\n"
            f"Comment={comment}\nExec={exec_line}\nIcon=yaaf\nTerminal=false\n"
            f"Categories=Office;ProjectManagement;\nStartupNotify=false\n{extra}")


def planned_files() -> dict[Path, str]:
    """Every file install writes, with what it writes there."""
    apps = data_home() / "applications"
    icon = Path(__file__).resolve().parents[2] / "web" / "public" / "favicon.svg"
    files: dict[Path, str] = {
        apps / "yaaf.desktop": _entry(
            "yaaf", "Your agent company: the dashboard", _exec("desktop", "launch", "dashboard"),
            extra=("Keywords=agents;company;chairman;inbox;\nActions=desk;letter;\n\n"
                   "[Desktop Action desk]\nName=Open the desk\n"
                   f"Exec={_exec('desktop', 'launch', 'desk')}\n\n"
                   "[Desktop Action letter]\nName=Morning letter\n"
                   f"Exec={_exec('desktop', 'launch', 'letter')}\n")),
        apps / "yaaf-desk.desktop": _entry(
            "yaaf desk", "What needs you, one decision at a time",
            _exec("desktop", "launch", "desk"), extra="Keywords=decide;approve;inbox;\n"),
        apps / "yaaf-letter.desktop": _entry(
            "yaaf morning letter", "What happened since you last read one",
            _exec("desktop", "launch", "letter"), extra="Keywords=briefing;news;\n"),
        config_home() / "autostart" / "yaaf-letter.desktop": _entry(
            "yaaf morning letter", "At login, if something happened",
            _exec("desktop", "launch", "letter", "--if-new"),
            extra="NoDisplay=true\nX-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=20\n"),
        config_home() / "systemd" / "user" / f"{NUDGE_UNIT}.service": (
            f"# {MARK}\n[Unit]\nDescription=yaaf: a nudge when something is about to decide "
            f"itself\n\n[Service]\nType=oneshot\nExecStart={_exec_start('nudge')}\n"
            # The notification waits for its "Open the desk" click in a child
            # that outlives this oneshot; the default KillMode would end it
            # the moment the nudge itself exits, and the button with it.
            "KillMode=process\n"),
        config_home() / "systemd" / "user" / f"{NUDGE_UNIT}.timer": (
            f"# {MARK}\n[Unit]\nDescription=yaaf: look for something worth a nudge\n\n"
            # Every five minutes: a CEO's reply should reach you while you
            # still remember asking. The decision reminder inside it is still
            # once a day at most (plan_nudge).
            "[Timer]\nOnStartupSec=2min\nOnUnitActiveSec=5min\nRandomizedDelaySec=30s\n\n"
            "[Install]\nWantedBy=timers.target\n"),
    }
    from yaaf.cli import indicator, omarchy

    # The app icon and the tray's three faces (normal, a decision about to
    # decide itself, yaaf down), in the user's own icon theme.
    apps_icons = data_home() / "icons" / "hicolor" / "scalable" / "apps"
    for name, svg in indicator.ICONS.items():
        files[apps_icons / f"{name}.svg"] = svg
    if icon.exists():
        files[apps_icons / "yaaf.svg"] = icon.read_text()
    if omarchy.present():
        files.update(omarchy.plugin_files(_yaaf_bin()))
    else:
        # Everywhere else, the tray icon: yaaf always in the top bar.
        files[config_home() / "autostart" / "yaaf-indicator.desktop"] = _entry(
            "yaaf in the top bar", "What needs you, always in sight",
            _exec("desktop", "indicator"),
            extra="NoDisplay=true\nX-GNOME-Autostart-enabled=true\n"
                  "X-GNOME-Autostart-Delay=5\nX-KDE-autostart-after=panel\n")
    return files


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _manifest() -> dict:
    return _read_state("desktop.json")


def _ours(path: Path, manifest: dict) -> bool:
    """A file install wrote and nobody has changed since."""
    try:
        return manifest.get("files", {}).get(str(path)) == _sha(path.read_text())
    except OSError:
        return False


def _systemctl(*args: str) -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _refresh_menus() -> None:
    """Tell the menus to look again, where the desktop has a way to be told."""
    for argv in (["update-desktop-database", str(data_home() / "applications")],
                 ["gtk-update-icon-cache", "-f", "-t", str(data_home() / "icons" / "hicolor")],
                 ["kbuildsycoca6"], ["kbuildsycoca5"]):
        if shutil.which(argv[0]):
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(argv, capture_output=True, timeout=30)


def install_files(dry_run: bool = False) -> tuple[list[Path], list[Path]]:
    """Write every planned file. A file already there that install did not
    write — or wrote and you have since edited — is left as it is and named,
    never overwritten. Returns (written, left alone)."""
    manifest = _manifest()
    written, kept = [], []
    hashes = dict(manifest.get("files", {}))
    for path, text in planned_files().items():
        if path.exists() and not _ours(path, manifest) and path.read_text() != text:
            kept.append(path)
            continue
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            hashes[str(path)] = _sha(text)
        written.append(path)
    if not dry_run:
        _write_state("desktop.json", {"version": 1, "api": _api_base(), "files": hashes})
    return written, kept


def apply(key: str | None = None) -> list[str]:
    """Install everything for this desktop and say what happened, one line a
    piece (✓ done, ! not done and why) — for `install` and for `yaaf setup`."""
    from yaaf.cli import omarchy

    widget = str(omarchy.plugin_dir() / "Widget.qml")
    had = _manifest().get("files", {}).get(widget)
    written, kept = install_files()
    widget_changed = _manifest().get("files", {}).get(widget) != had
    _refresh_menus()
    timer = _systemctl("daemon-reload") and _systemctl("enable", "--now", f"{NUDGE_UNIT}.timer")
    terminal = terminal_command(["yaaf", "desk"])
    said = ["✓ app menu: “yaaf”, “yaaf desk”, “yaaf morning letter”",
            "✓ morning letter at login, when something happened" if terminal
            else "! no terminal found for the desk and the letter — set YAAF_TERMINAL "
                 "(e.g. YAAF_TERMINAL=foot) and run this again",
            "✓ a nudge at most once a day, when something is about to decide itself"
            if timer else "! the nudge timer is written but not running (no systemd user "
                          "session here) — `yaaf nudge` from any scheduler does the same"]
    said += [f"! left {path} alone: it is not the file install wrote" for path in kept]
    if omarchy.present():
        said += omarchy.install(_yaaf_bin(), key or omarchy.KEY, widget_changed=widget_changed)
        manifest = _manifest()
        manifest["omarchy"] = True
        _write_state("desktop.json", manifest)
    return said


@desktop_app.command("install")
def install(
    dry_run: bool = typer.Option(False, "--dry-run", help="list what it would write"),
    key: str = typer.Option("", "--key", help="Omarchy: the key that opens the desk "
                                              "(default SUPER + Y)"),
) -> None:
    """Add yaaf to this desktop: app-menu entries (dashboard, desk, morning
    letter), a nudge when something is about to decide itself, the morning
    letter at login — and on Omarchy, the bar, its menu and Super+Y. Only your
    own folders; `yaaf desktop remove` undoes it."""
    if dry_run:
        written, kept = install_files(dry_run=True)
        for path in written:
            typer.echo(f"  would write {path}")
        for path in kept:
            typer.echo(f"  ! would leave {path} alone: it is not the file install wrote")
        return
    for line in apply(key or None):
        typer.echo(line)


@desktop_app.command("remove")
def remove() -> None:
    """Take out what `yaaf desktop install` put in — and only that. A file you
    edited since is left in place and named."""
    from yaaf.cli import omarchy

    manifest = _manifest()
    _systemctl("disable", "--now", f"{NUDGE_UNIT}.timer")
    if manifest.get("omarchy"):
        for line in omarchy.remove():
            typer.echo(f"  {line}")
        # The theme was the desktop's to give; with the hook gone it would go
        # stale, so the dashboard goes back to its own.
        from yaaf.cli import main as cli

        with contextlib.suppress(httpx.HTTPError), cli._client() as client:
            client.delete("/me/desktop-theme")
    for name in manifest.get("files", {}):
        path = Path(name)
        if not path.exists():
            continue
        if _ours(path, manifest):
            path.unlink()
            typer.echo(f"  removed {path}")
        else:
            typer.echo(f"  ! left {path}: it changed since install wrote it")
    _systemctl("daemon-reload")
    (state_home() / "desktop.json").unlink(missing_ok=True)
    if manifest.get("omarchy"):
        omarchy.rescan()
    _refresh_menus()


@desktop_app.command("status")
def status() -> None:
    """What `yaaf desktop install` put in, and whether each piece is in place."""
    manifest = _manifest()
    if not manifest.get("files"):
        typer.echo("Not installed. `yaaf desktop install` adds it.")
        return
    for name in manifest["files"]:
        path = Path(name)
        state = ("in place" if _ours(path, manifest)
                 else "edited since" if path.exists() else "missing")
        typer.echo(f"  {state:<12} {path}")
    typer.echo("  timer        " + ("running" if _systemctl("is-active", "--quiet",
                                                             f"{NUDGE_UNIT}.timer")
                                    else "not running"))
    terminal = terminal_command(["yaaf", "desk"])
    typer.echo(f"  terminal     {terminal[0] if terminal else 'none found (set YAAF_TERMINAL)'}")
    typer.echo(f"  dashboard    {' '.join(browser_command(_api_base() + '/app/')[:1])}")
    if manifest.get("omarchy"):
        from yaaf.cli import omarchy

        menu = omarchy.menu_file()
        has_menu = menu.exists() and omarchy.BEGIN in menu.read_text()
        typer.echo(f"  omarchy menu {'in place' if has_menu else 'missing'}")
        typer.echo(f"  omarchy key  {'in place' if omarchy.binding_file().exists() else 'not bound'}")
