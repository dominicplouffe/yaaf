"""The Omarchy layer of `yaaf desktop install`: the bar, the menu, Super+Y.

Read off Omarchy 4's own source (basecamp/omarchy, the shell plugin system,
`omarchy-bar`, the menu's JSONC parser, the Hyprland Lua helpers), so each
piece goes in the way Omarchy documents for a third party, and nothing is
written into Omarchy's own folders — an Omarchy update cannot wipe it:

- **The bar**: a shell plugin (`~/.config/omarchy/plugins/yaaf.signal/`) whose
  widget runs `yaaf signal` and draws it the way Omarchy's own command module
  does. Placed by Omarchy's CLI (`omarchy plugin enable`), never by editing
  `shell.json`, which the running shell owns.
- **The menu**: a `yaaf` submenu in `~/.config/omarchy/extensions/omarchy-menu.jsonc`.
  That file is the user's and has no drop-in folder, so yaaf's entries sit
  between two marked comment lines, and every edit is parsed with Omarchy's
  own rules before it is saved — its parser drops every custom entry when the
  file breaks, so a broken write would cost the user their own menu.
- **Super+Y**: a binding in `~/.config/hypr/yaaf.lua`, loaded by one marked
  line in `~/.config/hypr/bindings.lua` (Hyprland's config has no drop-in
  folder either). Skipped, and said, when the key is already taken.

A file that is a symlink (a dotfiles checkout) is never edited: yaaf says what
to add instead. The first edit of each shared file keeps a copy beside it.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

PLUGIN_ID = "yaaf.signal"
KEY = "SUPER + Y"
BEGIN = "  // yaaf: begin — added by `yaaf desktop install`; `yaaf desktop remove` takes it out"
END = "  // yaaf: end"
REQUIRE = 'require("hypr.yaaf") -- yaaf: added by `yaaf desktop install`'
BACKUP_SUFFIX = ".before-yaaf"


def present() -> bool:
    return bool(shutil.which("omarchy-shell") or shutil.which("omarchy"))


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def plugin_dir() -> Path:
    return config_home() / "omarchy" / "plugins" / PLUGIN_ID


def menu_file() -> Path:
    return config_home() / "omarchy" / "extensions" / "omarchy-menu.jsonc"


def bindings_file() -> Path:
    return config_home() / "hypr" / "bindings.lua"


def binding_file() -> Path:
    return config_home() / "hypr" / "yaaf.lua"


def _sh(words: list[str]) -> str:
    """A command line for `bash -lc` and `bar.run`, which both hand it to bash."""
    return shlex.join(words)


# ── the bar ─────────────────────────────────────────────────────────────────────


WIDGET_QML = """\
// Managed by `yaaf desktop install`; `yaaf desktop remove` takes it out.
// The number `yaaf signal` prints, drawn the way Omarchy's own command module
// draws one (CustomCommandModule in shell/plugins/bar/Bar.qml): hidden while
// nothing needs you, highlighted only when something is about to decide
// itself or a project stopped at its spending limit. Click opens the desk,
// right-click the dashboard.
import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

WidgetButton {
  id: root

  property string moduleName: "yaaf.signal"
  property var settings
  property string outputText: ""
  property string outputTooltip: ""
  property bool outputActive: false

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function update(raw) {
    var data = Util.parseModuleJson(raw)
    var klass = data.class || data.alt || ""
    outputText = data.text || ""
    outputTooltip = data.tooltip || ""
    outputActive = klass === "active"
  }

  visible: outputText !== ""
  text: outputText
  tooltipText: outputTooltip
  active: outputActive

  onPressed: function(button) {
    if (!root.bar) return
    var command = button === Qt.RightButton ? setting("onRightClick", "") : setting("onClick", "")
    if (command) root.bar.run(String(command))
  }

  Process {
    id: signalProc
    command: ["bash", "-lc", String(root.setting("exec", ""))]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.update(text)
    }
  }

  Timer {
    interval: Math.max(5, Number(root.setting("interval", 30))) * 1000
    running: String(root.setting("exec", "")) !== ""
    repeat: true
    triggeredOnStart: true
    onTriggered: if (!signalProc.running) signalProc.running = true
  }
}
"""


def plugin_files(yaaf: str) -> dict[Path, str]:
    manifest = {
        "schemaVersion": 1,
        "id": PLUGIN_ID,
        "name": "yaaf",
        "version": "1.0.0",
        "author": "yaaf",
        "description": "What needs you in yaaf — the number on the dashboard's badge",
        "kinds": ["bar-widget"],
        "entryPoints": {"barWidget": "Widget.qml"},
        "barWidget": {
            "displayName": "yaaf",
            "description": "Decisions waiting on you; highlighted when one is about to "
                           "decide itself",
            "category": "Status",
            "allowMultiple": False,
            # Beside the clock, where Omarchy puts a widget that names none.
            "defaultSection": "centerEnd",
            "defaults": {
                "exec": _sh([yaaf, "signal", "--format", "waybar"]),
                "interval": 30,
                "onClick": _sh([yaaf, "desktop", "launch", "desk"]),
                "onRightClick": _sh([yaaf, "desktop", "launch", "dashboard"]),
            },
            "schema": [{"key": "interval", "type": "integer",
                        "label": "Look again every (seconds)"}],
        },
    }
    return {plugin_dir() / "manifest.json": json.dumps(manifest, indent=2) + "\n",
            plugin_dir() / "Widget.qml": WIDGET_QML,
            theme_hook(): theme_hook_script(yaaf)}


def theme_hook() -> Path:
    """A theme-set hook in its drop-in folder: Omarchy runs every file there
    with bash on each theme switch (bin/omarchy-hook), after the switch."""
    return config_home() / "omarchy" / "hooks" / "theme-set.d" / "yaaf"


def theme_hook_script(yaaf: str) -> str:
    return ("# Managed by `yaaf desktop install`; `yaaf desktop remove` takes it out.\n"
            "# The dashboard follows the Omarchy theme: on every switch, tell yaaf.\n"
            "# Never fails the switch — a yaaf that is down just keeps the last theme.\n"
            f"{_sh([yaaf, 'theme', 'sync', '--quiet'])} >/dev/null 2>&1 || true\n")


def _run(*argv: str) -> tuple[bool, str]:
    if not shutil.which(argv[0]):
        return False, f"{argv[0]} is not installed"
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return done.returncode == 0, (done.stdout + done.stderr).strip()


def enable_plugin() -> tuple[bool, str]:
    """Omarchy's own way in for a hand-dropped plugin: rescan, then enable,
    which places it in its declared section."""
    _run("omarchy-shell", "shell", "rescanPlugins")
    return _run("omarchy", "plugin", "enable", PLUGIN_ID)


def disable_plugin() -> None:
    _run("omarchy", "plugin", "disable", PLUGIN_ID)


def rescan() -> None:
    """After the plugin's files are gone: its folder too, when empty, so the
    shell's rescan finds nothing to puzzle over."""
    folder = plugin_dir()
    if folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
    _run("omarchy-shell", "shell", "rescanPlugins")


# ── the menu ────────────────────────────────────────────────────────────────────


def parse_menu(raw: str) -> dict:
    """The menu file as Omarchy reads it (`stripJsonc` in
    shell/plugins/menu/MenuModel.js): whole-line // comments dropped, a comma
    before a closing bracket dropped, then plain JSON. Inline comments are not
    stripped there, so they are not here either: a file that only parses with
    them stripped is a file Omarchy would drop."""
    stripped = re.sub(r"^\s*//[^\n]*(\n|$)", "", raw, flags=re.M)
    stripped = re.sub(r",(\s*[}\]])", r"\1", stripped)
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("the menu file is not an object")
    return parsed


def menu_entries(yaaf: str) -> dict[str, dict]:
    # Font Awesome glyphs, which every Nerd Font carries.
    return {
        "yaaf": {"icon": "", "label": "yaaf",
                 "description": "Your agent company"},
        "yaaf.desk": {"icon": "", "label": "Desk",
                      "description": "What needs you, one decision at a time",
                      "action": _sh([yaaf, "desktop", "launch", "desk"])},
        "yaaf.dashboard": {"icon": "", "label": "Dashboard",
                           "description": "The whole company, in a window",
                           "action": _sh([yaaf, "desktop", "launch", "dashboard"])},
        "yaaf.letter": {"icon": "", "label": "Morning letter",
                        "description": "What happened since you last read one",
                        "action": _sh([yaaf, "desktop", "launch", "letter"])},
    }


def without_block(raw: str) -> str:
    """The file with yaaf's marked block taken out, and nothing else touched."""
    lines = raw.splitlines(keepends=True)
    out, inside = [], False
    for line in lines:
        if line.rstrip("\n") == BEGIN:
            inside = True
            continue
        if inside and line.rstrip("\n") == END:
            inside = False
            continue
        if not inside:
            out.append(line)
    return "".join(out)


def with_block(raw: str, entries: dict[str, dict]) -> str:
    """The file with yaaf's entries inside its last closing brace, between the
    two markers. Omarchy's parser forgives a trailing comma, so every entry
    carries one; a comma before the block is added only when the entry before
    it has none, since `{ ,` would not parse."""
    base = without_block(raw) if raw.strip() else "{\n}\n"
    close = base.rstrip().rfind("}")
    if close < 0:
        raise ValueError("the menu file has no closing brace")
    before = base[:close]
    meaningful = re.sub(r"^\s*//[^\n]*(\n|$)", "", before, flags=re.M).rstrip()
    lead = "" if meaningful.endswith(("{", ",")) else "  ,\n"
    body = "".join(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},\n"
                   for k, v in entries.items())
    if not before.endswith("\n"):
        before += "\n"
    return f"{before}{BEGIN}\n{lead}{body}{END}\n{base[close:]}"


# ── Super+Y ─────────────────────────────────────────────────────────────────────


def key_taken(key: str) -> str | None:
    """Where `key` is already bound, or None. o.bind adds a binding without
    looking, and two bindings on one key both fire, so a taken key is skipped
    rather than doubled."""
    spelled = f'"{key}"'
    omarchy = Path(os.environ.get("OMARCHY_PATH") or "/usr/share/omarchy") / "default" / "hypr"
    candidates = [bindings_file(), config_home() / "hypr" / "hyprland.lua"]
    if omarchy.is_dir():
        candidates += sorted(omarchy.rglob("*.lua"))
    for path in candidates:
        try:
            text = path.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            if spelled in line and not line.lstrip().startswith("--"):
                return str(path)
    return None


def binding_lua(yaaf: str, key: str) -> str:
    return ("-- Managed by `yaaf desktop install`; `yaaf desktop remove` takes it out.\n"
            "-- Loaded by one line in bindings.lua: Hyprland's config has no drop-in folder.\n"
            f"o.bind({json.dumps(key)}, \"yaaf desk\", "
            f"{json.dumps(_sh([yaaf, 'desktop', 'launch', 'desk']))})\n")


def with_require(raw: str) -> str:
    lines = [line for line in raw.splitlines() if line.strip() != REQUIRE]
    return "\n".join([*lines, REQUIRE]) + "\n"


def without_require(raw: str) -> str:
    kept = [line for line in raw.splitlines() if line.strip() != REQUIRE]
    return "\n".join(kept) + ("\n" if kept else "")


# ── editing a file that is the user's ───────────────────────────────────────────


def edit_shared(path: Path, change, check=None) -> str:
    """Apply `change` to a file the user owns, or say why not. Never through a
    symlink (a dotfiles checkout is theirs to commit), a copy kept beside it
    the first time, and `check` run on the result before anything is saved."""
    if path.is_symlink():
        return f"left {path} alone: it is a symlink"
    raw = path.read_text() if path.exists() else ""
    if check is not None and raw.strip():
        try:
            check(raw)
        except ValueError as exc:
            return f"left {path} alone: Omarchy cannot read it as it is ({exc})"
    new = change(raw)
    if new == raw:
        return "unchanged"
    if check is not None:
        try:
            check(new)
        except ValueError as exc:
            return f"left {path} alone: the change would not parse ({exc})"
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if path.exists() and not backup.exists():
        backup.write_text(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new)
    return "ok"


def install(yaaf: str, key: str = KEY) -> list[str]:
    """Every Omarchy piece but the plugin files (which ride the desktop
    manifest like any file yaaf owns). Returns one line per piece."""
    said = []
    menu = edit_shared(menu_file(), lambda raw: with_block(raw, menu_entries(yaaf)),
                       check=parse_menu)
    if menu in ("ok", "unchanged"):
        _run("omarchy", "menu", "refresh")
        said.append("✓ Omarchy menu: yaaf › Desk, Dashboard, Morning letter")
    else:
        said.append(f"! Omarchy menu: {menu}")
    taken = key_taken(key)
    if taken:
        said.append(f"! {key} is already bound in {taken}; no key added — run "
                    "`yaaf desktop install --key \"SUPER + ALT + Y\"` for another")
    elif not bindings_file().exists():
        said.append(f"! no {bindings_file()} to load a binding from; add "
                    f"o.bind({json.dumps(key)}, \"yaaf desk\", ...) yourself")
    else:
        binding_file().write_text(binding_lua(yaaf, key))
        bound = edit_shared(bindings_file(), with_require)
        if bound in ("ok", "unchanged"):
            _run("hyprctl", "reload")
            said.append(f"✓ {key} opens the desk, floating")
        else:
            said.append(f"! {key}: {bound} — add `{REQUIRE}` to it yourself")
    from yaaf.cli.theme import sync_quietly

    said.append("✓ the dashboard wears your Omarchy theme, and follows it when it changes"
                if sync_quietly() else "! the dashboard will follow your Omarchy theme from "
                "the next theme switch (yaaf did not answer just now)")
    enabled, why = enable_plugin()
    said.append("✓ Omarchy bar: the yaaf number beside the clock, hidden when nothing "
                "needs you" if enabled else f"! Omarchy bar: plugin written, not enabled ({why})")
    return said


def _drop_backup_if_restored(path: Path) -> None:
    """The copy kept before the first edit, gone once the file is back to
    exactly it — and kept whenever it is not, since then it is the only record
    of what was there before."""
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if backup.exists() and path.exists() and backup.read_text() == path.read_text():
        backup.unlink()


def remove() -> list[str]:
    said = []
    disable_plugin()
    if menu_file().exists():
        menu = edit_shared(menu_file(), without_block, check=parse_menu)
        _run("omarchy", "menu", "refresh")
        said.append("removed the yaaf menu entries" if menu in ("ok", "unchanged")
                    else f"! {menu}")
        _drop_backup_if_restored(menu_file())
    if bindings_file().exists():
        edit_shared(bindings_file(), without_require)
        _drop_backup_if_restored(bindings_file())
    if binding_file().exists() and "Managed by `yaaf desktop install`" in binding_file().read_text():
        binding_file().unlink()
        _run("hyprctl", "reload")
        said.append(f"removed the {KEY} binding")
    return said
