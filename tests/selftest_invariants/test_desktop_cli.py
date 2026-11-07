"""Invariant selftests: `yaaf desktop` writes only what it can take back, and
nudges only when silence would decide something.

A desktop install touches places a person arranges: their app menu, their
login, their notifications. So what is attacked here is what it may not do —
write outside their own folders, overwrite or delete a file they changed,
write a secret into a file the desktop reads, or interrupt them about
something that is not about to happen without them, more than once a day.
"""

from __future__ import annotations

import configparser
import shutil
import subprocess
from datetime import datetime, timedelta

import pytest

from yaaf.cli import desktop, setup


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A desktop user's folders, empty, and a yaaf on the default address."""
    for var, sub in (("XDG_DATA_HOME", "share"), ("XDG_CONFIG_HOME", "config"),
                     ("XDG_STATE_HOME", "state")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("YAAF_API", raising=False)
    monkeypatch.setattr(desktop, "_systemctl", lambda *a: False)
    monkeypatch.setattr(desktop, "_refresh_menus", lambda: None)
    return tmp_path


def _entries(home) -> dict:
    parsed = {}
    for path in (home / "share" / "applications").glob("yaaf*.desktop"):
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string(path.read_text())
        parsed[path.name] = parser["Desktop Entry"]
    return parsed


# ── what install writes ─────────────────────────────────────────────────────────


def test_install_writes_only_into_your_own_folders(home):
    written, kept = desktop.install_files()
    assert written and not kept
    assert all(str(p).startswith(str(home)) for p in written), \
        "a desktop install has no business outside the person's own folders"
    entries = _entries(home)
    assert set(entries) == {"yaaf.desktop", "yaaf-desk.desktop", "yaaf-letter.desktop"}
    for entry in entries.values():
        assert entry["Exec"].split()[0].startswith("/"), \
            "a session PATH often lacks ~/.local/bin: the command is named by absolute path"
        assert entry["Icon"] == "yaaf"


@pytest.mark.skipif(shutil.which("desktop-file-validate") is None,
                    reason="desktop-file-validate is not installed")
def test_every_entry_passes_the_specs_own_validator(home):
    for path in desktop.install_files()[0]:
        if path.suffix == ".desktop":
            checked = subprocess.run(["desktop-file-validate", str(path)],
                                     capture_output=True, text=True)
            assert checked.returncode == 0, checked.stdout + checked.stderr


def test_no_file_the_desktop_reads_carries_a_secret(home, monkeypatch):
    monkeypatch.setenv("YAAF_MASTER_KEY", "the-master-key")
    monkeypatch.setenv("YAAF_TOKEN", "ypt_the-cli-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-the-chairmans-own")
    for path in desktop.install_files()[0]:
        text = path.read_text()
        for secret in ("the-master-key", "ypt_the-cli-token", "sk-ant-the-chairmans-own"):
            assert secret not in text, f"{path.name} wrote down {secret!r}"


def test_a_yaaf_on_another_port_is_named_in_every_command(home, monkeypatch):
    monkeypatch.setenv("YAAF_API", "http://localhost:9099")
    desktop.install_files()
    for entry in _entries(home).values():
        assert entry["Exec"].startswith("env YAAF_API=http://localhost:9099 ")
    service = home / "config" / "systemd" / "user" / "yaaf-nudge.service"
    assert "YAAF_API=http://localhost:9099" in service.read_text()


def test_the_nudge_button_outlives_the_oneshot_that_sent_it(home):
    """A oneshot's leftover processes are killed when it exits, by default —
    and the one waiting for the notification's click is a leftover."""
    desktop.install_files()
    service = (home / "config" / "systemd" / "user" / "yaaf-nudge.service").read_text()
    assert "Type=oneshot" in service and "KillMode=process" in service


@pytest.mark.parametrize(("word", "line"), [
    ("/usr/bin/yaaf", "/usr/bin/yaaf"),
    ("/home/me/My Apps/yaaf", '"/home/me/My Apps/yaaf"'),
    ("50%", "50%%"),
    ('a"b', '"a\\\\"b"'),
    ("c\\d", '"c\\\\\\\\d"'),
])
def test_an_exec_line_is_quoted_the_way_the_spec_says(word, line):
    assert desktop.exec_quote(word) == line


# ── what remove takes back ──────────────────────────────────────────────────────


def test_a_file_you_edited_is_never_overwritten_or_removed(home, capsys):
    desktop.install_files()
    mine = home / "share" / "applications" / "yaaf-desk.desktop"
    mine.write_text(mine.read_text() + "X-My-Note=keep this\n")
    _, kept = desktop.install_files()
    assert mine in kept and "keep this" in mine.read_text(), "install overwrote an edit"

    desktop.remove()
    assert mine.exists() and "keep this" in mine.read_text(), "remove deleted an edit"
    assert not (home / "share" / "applications" / "yaaf.desktop").exists()
    assert "changed since install wrote it" in capsys.readouterr().out


def test_a_file_yaaf_did_not_write_is_left_alone(home):
    theirs = home / "share" / "applications" / "yaaf.desktop"
    theirs.parent.mkdir(parents=True)
    theirs.write_text("[Desktop Entry]\nName=Somebody else's yaaf\n")
    _, kept = desktop.install_files()
    assert theirs in kept
    desktop.remove()
    assert theirs.read_text() == "[Desktop Entry]\nName=Somebody else's yaaf\n"


def test_remove_takes_out_everything_install_put_in(home):
    written, _ = desktop.install_files()
    desktop.remove()
    assert not any(p.exists() for p in written)


# ── how it opens things ─────────────────────────────────────────────────────────


def _only(monkeypatch, *present: str) -> None:
    monkeypatch.setattr(desktop.shutil, "which",
                        lambda name: f"/usr/bin/{name}" if name in present else None)


def test_a_terminal_is_chosen_the_way_the_desktop_would(monkeypatch):
    monkeypatch.delenv("YAAF_TERMINAL", raising=False)
    monkeypatch.delenv("TERMINAL", raising=False)
    _only(monkeypatch, "gnome-terminal", "xdg-terminal-exec")
    assert desktop.terminal_command(["yaaf", "desk"]) == ["xdg-terminal-exec", "yaaf", "desk"]
    _only(monkeypatch, "gnome-terminal", "konsole")
    assert desktop.terminal_command(["yaaf", "desk"]) == ["gnome-terminal", "--", "yaaf", "desk"]
    monkeypatch.setenv("TERMINAL", "konsole")
    assert desktop.terminal_command(["yaaf"]) == ["konsole", "-e", "yaaf"]
    monkeypatch.setenv("YAAF_TERMINAL", "foot --app-id yaaf")
    assert desktop.terminal_command(["yaaf"]) == ["foot", "--app-id", "yaaf", "yaaf"]
    monkeypatch.delenv("YAAF_TERMINAL")
    monkeypatch.delenv("TERMINAL")
    _only(monkeypatch)
    assert desktop.terminal_command(["yaaf"]) is None


def test_the_dashboard_opens_in_your_browser_not_ours(monkeypatch):
    """App mode only when the default browser can do it: a second browser is a
    second sign-in."""
    url = "http://localhost:24247/app/"
    _only(monkeypatch, "omarchy-launch-webapp", "chromium")
    assert desktop.browser_command(url) == ["omarchy-launch-webapp", url]

    def default_is(name):
        monkeypatch.setattr(desktop.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout=f"{name}\n", stderr=""))

    _only(monkeypatch, "xdg-settings", "chromium", "firefox")
    default_is("chromium.desktop")
    assert desktop.browser_command(url) == ["chromium", f"--app={url}"]
    default_is("firefox_firefox.desktop")
    assert desktop.browser_command(url) == ["xdg-open", url]


# ── when it speaks up ───────────────────────────────────────────────────────────


MORNING = datetime(2026, 11, 9, 10, 0).astimezone()


def _payload(*, hours: float, status: str = "running", reason: str | None = None) -> dict:
    """One approval deciding itself `hours` after MORNING."""
    expires = (MORNING + timedelta(hours=hours)).isoformat()
    return {"snoozed": [], "projects": [{
        "id": "proj_1", "name": "Ad desk", "status": status, "status_reason": reason,
        "pending_approvals": [{"id": "appr_1", "kind": "spend", "title": "Renew the licence",
                               "expires_at": expires, "if_silent": "declined"}],
    }]}



def test_only_what_decides_itself_within_a_day_is_worth_a_nudge():
    assert desktop.plan_nudge(_payload(hours=72), {}, MORNING) is None, \
        "a decision days away is the Inbox's to say, not a notification's"
    planned = desktop.plan_nudge(_payload(hours=6), {}, MORNING)
    assert planned is not None and "Renew the licence" in planned.body


def test_a_nudge_is_once_a_day_never_twice_about_one_thing_and_never_at_night():
    assert desktop.plan_nudge(_payload(hours=8), {}, MORNING.replace(hour=23)) is None, \
        "nobody is nudged at 23:00"
    first = desktop.plan_nudge(_payload(hours=8), {}, MORNING)
    told = {k: MORNING.timestamp() for k in first.keys}
    same_day = {"sent_on": MORNING.date().isoformat(), "told": told}
    assert desktop.plan_nudge(_payload(hours=8), same_day, MORNING + timedelta(hours=3)) is None
    assert desktop.plan_nudge(_payload(hours=8), {"told": told},
                              MORNING + timedelta(days=1)) is None, \
        "the same decision is never the subject of two nudges"


def test_a_project_at_its_wall_is_worth_one(monkeypatch):
    from yaaf.core.inbox import HARD_STOP

    walled = _payload(hours=72, status="paused", reason=f"{HARD_STOP}: 61 USD against 20")
    planned = desktop.plan_nudge(walled, {}, MORNING)
    assert planned is not None and "spending limit" in planned.title


def test_a_nudge_is_never_an_emergency(monkeypatch):
    """Do Not Disturb is the person's. Critical urgency is how a sender walks
    through it, so a nudge never uses it."""
    planned = desktop.Nudge(title="yaaf: a decision decides itself soon", body="x", keys=["k"])
    _only(monkeypatch, "notify-send")
    monkeypatch.setattr(desktop, "_supports_actions", lambda: False)
    argv = desktop.notify_command(planned)
    assert "--urgency=normal" in argv and not any("critical" in a for a in argv)
    monkeypatch.setattr(desktop, "_supports_actions", lambda: True)
    clickable = " ".join(desktop.notify_command(planned))
    assert "--action=desk=" in clickable and "desktop launch desk" in clickable
    _only(monkeypatch, "gdbus")
    assert desktop.notify_command(planned)[0] == "gdbus"
    _only(monkeypatch)
    assert desktop.notify_command(planned) is None


def test_the_morning_letter_comes_once_a_day_and_only_with_news():
    today = "2026-11-07"
    assert desktop.letter_is_due({"quiet": False}, {}, today)
    assert not desktop.letter_is_due({"quiet": True}, {}, today), \
        "a letter that says nothing happened, every morning, is a window people learn to close"
    assert not desktop.letter_is_due({"quiet": False}, {"shown_on": today}, today)


# ── the wizard asks; it does not assume ─────────────────────────────────────────


def test_setup_adds_nothing_to_a_desktop_it_cannot_see(home, monkeypatch, capsys):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    setup._desktop(None)
    assert not (home / "share").exists()
    assert "yaaf desktop install" in capsys.readouterr().out


def test_a_new_decision_later_the_same_day_still_waits_for_tomorrow():
    """The daily cap is its own rule, not a side effect of never-twice: a
    second decision arriving after the day's nudge waits for the next day —
    when it, being new, is worth one."""
    first = desktop.plan_nudge(_payload(hours=8), {}, MORNING)
    sent = {"sent_on": MORNING.date().isoformat(),
            "told": {k: MORNING.timestamp() for k in first.keys}}
    # Deciding itself at noon tomorrow: within a day both this afternoon and
    # tomorrow at ten, so only the cap stands between it and a nudge today.
    later = _payload(hours=26)
    later["projects"][0]["pending_approvals"][0]["id"] = "appr_2"
    assert desktop.plan_nudge(later, sent, MORNING + timedelta(hours=4)) is None
    tomorrow = desktop.plan_nudge(later, {"told": sent["told"]}, MORNING + timedelta(hours=24))
    assert tomorrow is not None and tomorrow.keys == ["appr_2"]
