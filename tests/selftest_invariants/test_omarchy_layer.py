"""Invariant selftests: yaaf's Omarchy layer is accepted by Omarchy's own code,
and never costs the user anything of theirs.

Two of the three pieces edit a file the user owns — the menu and the key
bindings have no drop-in folder — and Omarchy's menu parser drops every
custom entry when that file breaks. So the attacks here are: does Omarchy's
own validator accept the bar plugin, does Omarchy's own parser read the edited
menu (with every entry the user already had), does removing put each file back
exactly as it was, and does yaaf ever double a key, walk through Do Not
Disturb, edit a file it cannot read, or write through a dotfiles symlink.

Omarchy's own files are in tests/fixtures/omarchy (MIT, copied unchanged).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from yaaf.cli import desktop, omarchy

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "omarchy"
YAAF = "/home/me/yaaf/.venv/bin/yaaf"


@pytest.fixture()
def box(tmp_path, monkeypatch):
    """A fresh Omarchy user's config: its own menu and bindings files, an
    Omarchy install whose default bindings use SUPER + SHIFT + Y, and Omarchy's
    commands recorded rather than run."""
    config = tmp_path / "config"
    (config / "omarchy" / "extensions").mkdir(parents=True)
    (config / "hypr").mkdir(parents=True)
    shutil.copy(FIXTURES / "omarchy-menu.jsonc", config / "omarchy" / "extensions")
    shutil.copy(FIXTURES / "bindings.lua", config / "hypr")
    defaults = tmp_path / "omarchy" / "default" / "hypr"
    defaults.mkdir(parents=True)
    (defaults / "apps.lua").write_text(
        'o.bind("SUPER + SHIFT + Y", "YouTube", { webapp = "https://youtube.com/" })\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("OMARCHY_PATH", str(tmp_path / "omarchy"))
    monkeypatch.setattr(omarchy, "present", lambda: True)
    monkeypatch.setattr("yaaf.cli.theme.sync_quietly", lambda: True)
    ran: list[tuple] = []
    monkeypatch.setattr(omarchy, "_run", lambda *argv: (ran.append(argv), (True, "ok"))[1])
    return config, ran


def _omarchy_reads(raw: str) -> list[dict] | None:
    """The menu file as Omarchy's own MenuModel.js reads it."""
    script = ("const vm=require('vm');const fs=require('fs');"
              "const ctx={};vm.createContext(ctx);"
              f"vm.runInContext(fs.readFileSync({json.dumps(str(FIXTURES / 'MenuModel.js'))},"
              "'utf8'),ctx);"
              "const raw=fs.readFileSync(0,'utf8');"
              "process.stdout.write(JSON.stringify(ctx.readMenuJsonc(raw)));")
    done = subprocess.run(["node", "-e", script], input=raw, capture_output=True, text=True,
                          timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")


# ── the bar ─────────────────────────────────────────────────────────────────────


@needs_jq
def test_the_bar_plugin_passes_omarchys_own_validator(box, tmp_path):
    for path, text in omarchy.plugin_files(YAAF).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    checked = subprocess.run([str(FIXTURES / "omarchy-plugin-validate"),
                              str(omarchy.plugin_dir())], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr


def test_the_bar_runs_yaaf_signal_itself_and_a_click_opens_its_panel(box):
    """Omarchy hands a third-party widget only its shell.json entry's settings,
    never the manifest's defaults — so the old widget, which read its command
    from them, never ran at all. The command is written into the widget."""
    files = {p.name: t for p, t in omarchy.plugin_files(YAAF).items()}
    widget = files["Widget.qml"]
    assert f'setting("yaaf", {json.dumps(YAAF)})' in widget
    assert 'command: [root.yaaf, "signal", "--format", "panel"]' in widget
    assert "__YAAF__" not in widget
    assert "\nPanel {\n" in widget and "root.toggle()" in widget, "a click opens the panel"
    # The Y is yaaf's own label, bold: the button's has one weight.
    assert "labelVisible: false" in widget and "font.bold: true" in widget
    # Every action goes straight to yaaf, never through a shell.
    assert '"bash"' not in widget and "execDetached([root.yaaf" not in widget
    assert 'run([root.yaaf, "desktop", "open", String(url)])' in widget
    manifest = json.loads(files["manifest.json"])
    assert manifest["entryPoints"]["barWidget"] == "Widget.qml"
    assert manifest["id"] == omarchy.PLUGIN_ID and not manifest["id"].startswith("omarchy.")


def test_the_bar_is_placed_by_omarchys_cli_never_by_editing_its_config(box, monkeypatch):
    """shell.json is owned by the running shell, which holds it in memory —
    an edit behind it is a race the shell wins. So the plugin is enabled the
    way Omarchy documents for a hand-dropped one, and shell.json is untouched."""
    config, ran = box
    omarchy.install(YAAF)
    assert ("omarchy-shell", "shell", "rescanPlugins") in ran
    assert ("omarchy", "plugin", "enable", omarchy.PLUGIN_ID) in ran
    assert not (config / "omarchy" / "shell.json").exists()


# ── the menu ────────────────────────────────────────────────────────────────────


@needs_node
def test_the_menu_omarchy_reads_has_yaaf_in_it(box):
    config, _ = box
    omarchy.install(YAAF)
    items = {i["id"]: i for i in _omarchy_reads(omarchy.menu_file().read_text())}
    assert items["yaaf"]["parent"] == "root" and items["yaaf"]["kind"] == "menu"
    assert items["yaaf.desk"]["kind"] == "action"
    assert items["yaaf.desk"]["action"] == f"{YAAF} desktop launch desk"
    assert {"yaaf.dashboard", "yaaf.letter"} <= set(items)


@needs_node
def test_the_entries_already_in_the_menu_survive_yaafs(box):
    """A user's own entries, the last one without a trailing comma — the case
    where a block added after it needs a comma of its own."""
    menu = omarchy.menu_file()
    menu.write_text('{\n  // mine\n  "personal": {"icon":"","label":"Personal"},\n'
                    '  "personal.notes": {"label":"Notes","action":"notes"}\n}\n')
    omarchy.install(YAAF)
    ids = {i["id"] for i in _omarchy_reads(menu.read_text())}
    assert {"personal", "personal.notes", "yaaf", "yaaf.desk"} <= ids
    assert "// mine" in menu.read_text(), "the user's comments are theirs"


def test_installing_twice_adds_one_block_and_removing_restores_the_file(box):
    menu = omarchy.menu_file()
    original = menu.read_text()
    omarchy.install(YAAF)
    omarchy.install(YAAF)
    assert menu.read_text().count(omarchy.BEGIN) == 1
    assert (menu.parent / (menu.name + omarchy.BACKUP_SUFFIX)).read_text() == original
    omarchy.remove()
    assert menu.read_text() == original, "remove must put the file back as it was"
    assert not (menu.parent / (menu.name + omarchy.BACKUP_SUFFIX)).exists(), \
        "a copy of a file that is back as it was is clutter"


def test_a_menu_omarchy_cannot_read_is_left_alone(box):
    """Already broken is the user's to fix: Omarchy is dropping their entries
    right now, and an edit would bury the cause under yaaf's."""
    menu = omarchy.menu_file()
    menu.write_text('{ "mine": {"label": "x"} // an inline comment Omarchy does not strip\n}\n')
    said = omarchy.install(YAAF)
    assert "an inline comment" in menu.read_text() and omarchy.BEGIN not in menu.read_text()
    assert any("cannot read it" in line for line in said)


def test_a_dotfiles_symlink_is_never_written_through(box, tmp_path):
    menu = omarchy.menu_file()
    target = tmp_path / "dotfiles-omarchy-menu.jsonc"
    target.write_text(menu.read_text())
    menu.unlink()
    menu.symlink_to(target)
    before = target.read_text()
    said = omarchy.install(YAAF)
    assert target.read_text() == before
    assert any("symlink" in line for line in said)


# ── Super+Y ─────────────────────────────────────────────────────────────────────


def test_super_y_opens_the_desk_and_comes_out_cleanly(box):
    bindings = omarchy.bindings_file()
    original = bindings.read_text()
    said = omarchy.install(YAAF)
    assert any("SUPER + Y" in line and line.startswith("✓") for line in said)
    assert omarchy.binding_file().read_text().count('o.bind("SUPER + Y", "yaaf desk"') == 1
    omarchy.install(YAAF)
    assert bindings.read_text().count(omarchy.REQUIRE) == 1
    omarchy.remove()
    assert bindings.read_text() == original and not omarchy.binding_file().exists()


def test_a_key_already_taken_is_not_doubled(box):
    """o.bind does not look, and two bindings on one key both fire."""
    bindings = omarchy.bindings_file()
    bindings.write_text(bindings.read_text() + 'o.bind("SUPER + Y", "Mine", "my-thing")\n')
    said = omarchy.install(YAAF)
    assert not omarchy.binding_file().exists()
    assert omarchy.REQUIRE not in bindings.read_text()
    assert any("already bound" in line for line in said)


def test_a_commented_out_binding_and_a_longer_chord_do_not_count(box):
    bindings = omarchy.bindings_file()
    bindings.write_text(bindings.read_text() + '-- o.bind("SUPER + Y", "old", "x")\n')
    assert omarchy.key_taken("SUPER + Y") is None, \
        'neither "-- o.bind(...)" nor Omarchy\'s SUPER + SHIFT + Y is SUPER + Y'


# ── the desk and the nudge, on Omarchy ──────────────────────────────────────────


def _only(monkeypatch, *present: str) -> None:
    monkeypatch.setattr(desktop.shutil, "which",
                        lambda name: f"/usr/bin/{name}" if name in present else None)


def test_the_desk_floats_on_omarchy(monkeypatch):
    monkeypatch.delenv("YAAF_TERMINAL", raising=False)
    _only(monkeypatch, "omarchy-launch-tui", "xdg-terminal-exec")
    assert desktop.terminal_command(["yaaf", "desk"]) == \
        ["omarchy-launch-tui", "--app-id=TUI.float", "yaaf", "desk"]


def test_a_nudge_on_omarchy_still_answers_to_do_not_disturb(monkeypatch):
    """omarchy-notification-send's default app name is the one Do Not Disturb
    lets through. A nudge is not an emergency, so it is sent under its own."""
    _only(monkeypatch, "omarchy-notification-send", "notify-send")
    argv = desktop.notify_command(desktop.Nudge(title="t", body="b", keys=["k"]))
    assert argv[0] == "omarchy-notification-send"
    assert argv[argv.index("--app-name") + 1] == "yaaf"
    assert argv[argv.index("-u") + 1] == "normal"
    words = argv[argv.index("--action") + 2:]
    assert words[-3:] == ["desktop", "launch", "desk"], \
        "Omarchy takes a button's command as separate words, never one string"


def test_a_backup_is_kept_when_the_file_did_not_come_back_as_it_was(box):
    """Edited by hand after yaaf's install, the file cannot be restored to the
    copy kept before — so the copy is the only record, and stays."""
    menu = omarchy.menu_file()
    original = menu.read_text()
    omarchy.install(YAAF)
    menu.write_text(menu.read_text().replace(
        omarchy.END, omarchy.END + '\n  "mine": {"label": "m"},'))
    omarchy.remove()
    assert (menu.parent / (menu.name + omarchy.BACKUP_SUFFIX)).read_text() == original
    assert '"mine"' in menu.read_text()
