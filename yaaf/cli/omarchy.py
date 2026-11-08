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
// yaaf in the bar, always: its Y alone while nothing waits, the Y and a
// number when decisions or a CEO's messages wait on you, "Y ✕" when yaaf is
// not running. Highlighted only when a decision is about to decide itself or a
// project stopped at its spending limit. Click opens the panel — what waits,
// each project's state, and the way into each — right-click the dashboard.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "yaaf.signal"
  ipcTarget: "yaaf"

  property var view: ({ text: "Y", state: "loading", decisions: [], messages: [], projects: [] })
  property bool loaded: false
  property int cursorIndex: 0
  property bool cursorActive: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color accent: Color.accent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool offline: view.state === "offline" || view.state === "signed-out"
  readonly property var rows: (view.decisions || []).concat(view.messages || [], view.projects || [])

  // Written in at install: Omarchy hands a third-party widget only the
  // settings in its shell.json entry, never its manifest's defaults, so a
  // command read from settings was never there — the old widget never ran.
  readonly property string yaaf: String(setting("yaaf", "__YAAF__"))

  function refresh() { if (!signalProc.running) signalProc.running = true }
  function run(argv) { Quickshell.execDetached(argv) }
  // Straight to the yaaf command, never through a shell: a row's address is
  // data, and `yaaf desktop open` refuses any address that is not yaaf's own.
  function openUrl(url) {
    if (!url) return
    run([root.yaaf, "desktop", "open", String(url)])
    close()
  }
  function openDesk() { run([root.yaaf, "desktop", "launch", "desk"]); close() }
  function openDashboard() { run([root.yaaf, "desktop", "launch", "dashboard"]); close() }
  function startStop() {
    run(["systemctl", "--user", root.offline ? "start" : "stop", "yaaf"])
    statusLine = root.offline ? "Starting yaaf…" : "Stopping yaaf…"
    soonRefresh.restart()
  }
  function moveCursor(delta) {
    cursorActive = true
    if (rows.length === 0) return
    cursorIndex = Math.max(0, Math.min(rows.length - 1, cursorIndex + delta))
  }
  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s") }
  function heroMeta() {
    if (!loaded) return "Looking…"
    if (view.state === "offline") return "Not running"
    if (view.state === "signed-out") return "Not signed in"
    var parts = []
    if ((view.count || 0) > 0) parts.push(plural(view.count, "decision"))
    if ((view.unread || 0) > 0) parts.push(plural(view.unread, "unread message"))
    return parts.length ? parts.join(" · ") : "Nothing needs you"
  }

  property string statusLine: ""

  Process {
    id: signalProc
    command: [root.yaaf, "signal", "--format", "panel"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var lines = text.trim().split("\\n")
        try { root.view = JSON.parse(lines[lines.length - 1]) } catch (e) { return }
        root.loaded = true
        root.statusLine = ""
        if (root.cursorIndex >= root.rows.length) root.cursorIndex = Math.max(0, root.rows.length - 1)
      }
    }
  }

  Timer {
    interval: root.opened ? 5000 : Math.max(5, Number(root.setting("interval", 30))) * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }
  Timer { id: soonRefresh; interval: 3000; onTriggered: root.refresh() }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    cursorActive = false
    cursorIndex = 0
    refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // The text keeps the button drawn and its tooltip and highlight working;
    // its own label is off because it has one weight, and yaaf's Y is heavy.
    text: root.view.text || "Y"
    labelVisible: false
    fixedWidth: mark.implicitWidth + button.scaledHorizontalMargin * 2
    tooltipText: root.view.tooltip || "yaaf"
    useActiveColor: true
    active: root.view.state === "active"
    opacity: root.offline ? 0.55 : (root.view.state === "clear" ? 0.8 : 1.0)
    horizontalMargin: 8.5
    onPressed: function(code) {
      if (code === Qt.RightButton) root.openDashboard()
      else root.toggle()
    }

    readonly property string shown: root.view.text || "Y"
    readonly property color ink: button.active && button.useActiveColor
      ? button.activeColor : button.foreground

    FontMetrics { id: markMetrics; font: markY.font }

    Row {
      id: mark
      anchors.centerIn: parent
      // The figures on the bar's middle, as Omarchy's own label sets them.
      anchors.verticalCenterOffset: (markMetrics.descent - markMetrics.ascent
        + markMetrics.tightBoundingRect("0").height) / 2
      scale: button.pressScale
      Text {
        id: markY
        textFormat: Text.PlainText
        text: "Y"
        color: button.ink
        font.family: button.fontFamily
        // Bold, a size up: the bar's font (JetBrainsMono Nerd Font) has
        // Regular and Bold only, and a heavier weight falls back to Regular.
        font.pixelSize: button.fontSize + 1
        font.bold: true
        // An outline in its own colour thickens every stroke: bolder than
        // the font's Bold alone, which reads as regular at bar size.
        style: Text.Outline
        styleColor: button.ink
        renderType: Text.NativeRendering
      }
      Text {
        textFormat: Text.PlainText
        text: button.shown.charAt(0) === "Y" ? button.shown.slice(1) : " " + button.shown
        visible: text !== ""
        color: button.ink
        font.family: button.fontFamily
        font.pixelSize: button.fontSize
        renderType: Text.NativeRendering
      }
    }
  }

  KeyboardPanel {
    id: panel
    bar: root.bar
    anchorItem: button
    owner: root
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(440))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(620))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (dy === 0) return
        if (!root.cursorActive) { root.cursorActive = true; return }
        root.moveCursor(dy)
      }
      onActivateRequested: if (root.cursorActive && root.rows[root.cursorIndex]) root.openUrl(root.rows[root.cursorIndex].url)
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "d") root.openDesk()
        else if (t === "o") root.openDashboard()
        else if (t === "r") root.refresh()
      }

      Flickable {
        id: flick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: flick.width
          spacing: Style.space(8)

          PanelHero {
            width: parent.width
            title: "yaaf"
            meta: root.heroMeta()
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              Text {
                textFormat: Text.PlainText
                text: "Y"
                color: root.view.state === "active" ? root.urgent : root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
                font.bold: true
              }
            }
            trailingControl: Component {
              PanelActionButton {
                iconText: "󰑐"
                tooltipText: "Refresh (r)"
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.refresh()
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: root.offline || root.statusLine !== ""
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.statusLine !== "" ? root.statusLine
              : (root.view.state === "signed-out" ? "Run `yaaf login` in a terminal, once."
                 : "yaaf is not running, so nothing is being worked on or watched.")
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          PanelSectionHeader {
            visible: (root.view.decisions || []).length > 0
            text: "NEEDS YOU"
            foreground: root.foreground
            fontFamily: root.fontFamily
          }
          Repeater {
            model: root.view.decisions || []
            Row_ {
              required property var modelData
              required property int index
              width: column.width
              rowIndex: index
              title: modelData.title
              meta: modelData.project + (modelData.when ? " · decides " + modelData.when : "") + " · " + modelData.kind
              hot: modelData.soon === true
              url: modelData.url
            }
          }

          PanelSectionHeader {
            visible: (root.view.messages || []).length > 0
            text: "MESSAGES"
            foreground: root.foreground
            fontFamily: root.fontFamily
          }
          Repeater {
            model: root.view.messages || []
            Row_ {
              required property var modelData
              required property int index
              width: column.width
              rowIndex: (root.view.decisions || []).length + index
              title: root.plural(modelData.count, "unread message") + " from the CEO"
              meta: modelData.project
              url: modelData.url
            }
          }

          PanelSectionHeader {
            visible: (root.view.projects || []).length > 0
            text: "PROJECTS"
            foreground: root.foreground
            fontFamily: root.fontFamily
          }
          Repeater {
            model: root.view.projects || []
            Row_ {
              required property var modelData
              required property int index
              width: column.width
              rowIndex: (root.view.decisions || []).length + (root.view.messages || []).length + index
              title: modelData.name
              meta: modelData.headline || modelData.status
              hot: ["blocked", "failing", "late", "capped"].indexOf(modelData.verdict) >= 0
              url: modelData.url
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: root.loaded && !root.offline && root.rows.length === 0
            width: parent.width
            text: "No projects yet. Open the dashboard to start one."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          PanelSeparator { foreground: root.foreground }

          RowLayout {
            width: parent.width
            spacing: Style.space(8)
            Button {
              text: "Dashboard"
              foreground: root.foreground
              fontFamily: root.fontFamily
              enabled: !root.offline
              onClicked: root.openDashboard()
            }
            Button {
              text: "The desk"
              foreground: root.foreground
              fontFamily: root.fontFamily
              enabled: !root.offline
              onClicked: root.openDesk()
            }
            Item { Layout.fillWidth: true }
            Button {
              text: root.offline ? "Start yaaf" : "Stop yaaf"
              foreground: root.foreground
              fontFamily: root.fontFamily
              visible: root.view.state !== "signed-out"
              onClicked: root.startStop()
            }
          }
        }
      }
    }
  }

  // One row: what it is, where it is from, and the screen a click opens.
  component Row_: CursorSurface {
    id: row
    property int rowIndex: 0
    property string title: ""
    property string meta: ""
    property string url: ""
    property bool hot: false

    hasCursor: root.cursorActive && root.cursorIndex === rowIndex
    foreground: root.foreground
    accent: root.accent
    implicitHeight: body.implicitHeight + Style.space(12)

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: { root.cursorActive = true; root.cursorIndex = row.rowIndex }
      onClicked: root.openUrl(row.url)
    }

    RowLayout {
      id: body
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(8)
      anchors.rightMargin: Style.space(8)
      spacing: Style.space(10)

      Rectangle {
        Layout.preferredWidth: Style.space(3)
        Layout.preferredHeight: body.implicitHeight
        radius: width
        color: row.hot ? root.urgent : root.accent
        opacity: row.hot ? 0.9 : 0.45
      }
      ColumnLayout {
        Layout.fillWidth: true
        spacing: 0
        Text {
          Layout.fillWidth: true
          textFormat: Text.PlainText
          text: row.title
          elide: Text.ElideRight
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }
        Text {
          Layout.fillWidth: true
          textFormat: Text.PlainText
          text: row.meta
          elide: Text.ElideRight
          color: row.hot ? root.urgent : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }
      }
    }
  }
}
"""


def widget_qml(yaaf: str) -> str:
    """The widget, with the path of the yaaf it runs written in (as a QML
    string literal: JSON's escaping is a subset QML reads)."""
    return WIDGET_QML.replace('"__YAAF__"', json.dumps(yaaf))


def plugin_files(yaaf: str) -> dict[Path, str]:
    manifest = {
        "schemaVersion": 1,
        "id": PLUGIN_ID,
        "name": "yaaf",
        "version": "1.0.0",
        "author": "yaaf",
        "description": "yaaf in the bar: what waits on you, and a panel with the way into it",
        "kinds": ["bar-widget"],
        "entryPoints": {"barWidget": "Widget.qml"},
        "barWidget": {
            "displayName": "yaaf",
            "description": "Always there: decisions and unread messages waiting on you, "
                           "highlighted when one is about to decide itself; click for the "
                           "panel",
            "category": "Status",
            "allowMultiple": False,
            # Beside the clock, where Omarchy puts a widget that names none.
            "defaultSection": "centerEnd",
            "defaults": {
                "exec": _sh([yaaf, "signal", "--format", "panel"]),
                "interval": 30,
                # The command the panel's rows and buttons run, by path:
                # never a shell, so a row's address is never parsed as one.
                "yaaf": yaaf,
            },
            "schema": [{"key": "interval", "type": "integer",
                        "label": "Look again every (seconds)"}],
        },
    }
    return {plugin_dir() / "manifest.json": json.dumps(manifest, indent=2) + "\n",
            plugin_dir() / "Widget.qml": widget_qml(yaaf),
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


def restart_shell() -> bool:
    """Omarchy's own restart of its shell: the bar and its widgets, a second's
    flicker, no window touched. Needed when the widget's code changes — a
    running shell keeps the compile it started with (Qt caches the type and
    the folder listing it was loaded from), so a changed file is not seen and
    a new one is refused as a "file name case mismatch"."""
    return _run("omarchy", "restart", "shell")[0]


def install(yaaf: str, key: str = KEY, widget_changed: bool = False) -> list[str]:
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
    if not enabled:
        said.append(f"! Omarchy bar: plugin written, not enabled ({why})")
        return said
    said.append("✓ Omarchy bar: a Y beside the clock, always — a number when decisions or "
                "messages wait, Y ✕ when yaaf is down; click it for the panel")
    if widget_changed:
        said.append("✓ Omarchy's shell restarted to load it" if restart_shell() else
                    "! restart Omarchy's shell to load the new bar: omarchy restart shell")
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
