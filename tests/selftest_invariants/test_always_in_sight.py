"""Invariant selftests: yaaf is always in sight on the desktop, and a click
opens the way into whatever waits.

The chairman's complaint: nothing on the bar said yaaf was running, nor that
his CEOs had written to him. The Omarchy widget hid itself at zero, and also
never ran (it read its command from manifest defaults Omarchy never hands a
third-party widget); every other desktop had no top-bar presence at all. Now:

- the bar's answer is always drawn — the mark, a number when decisions or
  unread CEO messages wait, hollow when yaaf is down;
- a click opens a panel (Omarchy) or a menu (every StatusNotifierItem tray:
  Ubuntu's GNOME, KDE, Xfce, …) whose rows open their own screens;
- a CEO writing to you is a notification, during the day, once per message.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from datetime import datetime

import pytest

from yaaf.cli import desk, desktop, indicator

BASE = "http://localhost:24247"


def _project(pid: str, name: str, *, unread: int = 0, status: str = "running",
             approvals: list | None = None) -> dict:
    return {"id": pid, "name": name, "status": status, "boardroom_unread": unread,
            "pending_approvals": approvals or [], "pending_asks": [], "awaiting_you": {},
            "status_line": {"verdict": "on_track", "headline": "Working."}}


def _approval(aid: str, hours: float) -> dict:
    expires = datetime.fromtimestamp(time.time() + hours * 3600).astimezone().isoformat()
    return {"id": aid, "title": "Buy a data pull", "kind": "spend", "expires_at": expires,
            "if_silent": "declined"}


# ── the bar's answer ────────────────────────────────────────────────────────────


def test_unread_messages_from_a_ceo_are_in_the_bar_but_not_on_the_badge():
    payload = {"projects": [_project("p1", "Daily", unread=9),
                            _project("p2", "Watch", unread=3, approvals=[_approval("a1", 72)])]}
    view = desk.signal_view(payload)
    assert view["text"] == "Y 13", "decisions and messages: both wait on him"
    assert view["count"] == 1 and view["unread"] == 12, "count stays the badge's number"
    assert "Daily: 9 unread messages from the CEO" in view["tooltip"]
    assert view["class"] == "waiting"


def test_a_paused_projects_messages_do_not_count():
    view = desk.signal_view({"projects": [_project("p1", "Old", unread=5, status="paused")]})
    assert view["text"] == "Y" and view["unread"] == 0


def test_the_panel_rows_each_open_their_own_screen():
    payload = {"projects": [_project("p1", "Daily", unread=9,
                                     approvals=[_approval("appr_1", 3)])]}
    view = desk.panel_view(payload, BASE)
    assert view["dashboard"] == f"{BASE}/app/"
    (decision,) = view["decisions"]
    assert decision["url"] == f"{BASE}/app/inbox?item=appr_1" and decision["soon"]
    assert view["messages"] == [{"project": "Daily", "count": 9,
                                 "url": f"{BASE}/app/projects/p1/chat"}]
    assert view["projects"][0]["url"] == f"{BASE}/app/projects/p1"
    assert view["state"] == "active", "a decision deciding itself within a day lights it"


def test_down_is_a_panel_too_with_a_way_to_start_it():
    view = desk.offline_panel("yaaf is not running.", BASE)
    assert view["text"] == desk.OFFLINE_MARK and view["state"] == "offline"
    assert view["decisions"] == view["messages"] == view["projects"] == []


def test_a_click_opens_only_yaafs_own_screens(monkeypatch):
    from typer.testing import CliRunner

    from yaaf.cli.main import app

    opened = []
    monkeypatch.setattr(desktop, "_detach", lambda argv, env=None: opened.append(argv))
    monkeypatch.setattr(desktop, "browser_command", lambda url: ["open", url])
    monkeypatch.setenv("YAAF_API", BASE)
    runner = CliRunner()
    assert runner.invoke(app, ["desktop", "open", f"{BASE}/app/inbox?item=x"]).exit_code == 0
    assert opened == [["open", f"{BASE}/app/inbox?item=x"]]
    refused = runner.invoke(app, ["desktop", "open", "https://example.com/phish"])
    assert refused.exit_code == 2 and len(opened) == 1


# ── the tray (Ubuntu, KDE, Xfce, …) ─────────────────────────────────────────────


VIEW = {"state": "waiting", "count": 1, "unread": 12, "tooltip": "t",
        "decisions": [{"title": "Confirm the brief", "project": "Watch", "when": "in 6 days",
                       "url": f"{BASE}/app/inbox?item=x"}],
        "messages": [{"project": "Daily", "count": 9, "url": f"{BASE}/app/projects/p1/chat"}],
        "projects": [{"name": "Daily", "headline": "Working.",
                      "url": f"{BASE}/app/projects/p1"}]}


def test_the_tray_face_says_how_many_and_greys_out_when_down():
    face = indicator.face_for(VIEW)
    assert (face.icon, face.status, face.label) == (indicator.ICON, "Active", "13")
    hot = indicator.face_for({**VIEW, "state": "active"})
    assert hot.icon == indicator.ICON_ATTENTION and hot.status == "NeedsAttention"
    down = indicator.face_for({"state": "offline", "tooltip": "yaaf is not running."})
    assert down.icon == indicator.ICON_OFFLINE and down.label == ""


def test_the_tray_menu_opens_each_thing_and_ends_with_the_ways_in():
    rows = indicator.menu_for(VIEW, "/usr/bin/yaaf")
    actions = {r.label: r.action for r in rows if r.action}
    assert actions["Confirm the brief — Watch · decides in 6 days"] == (
        "/usr/bin/yaaf", "desktop", "open", f"{BASE}/app/inbox?item=x")
    assert actions["Daily: 9 unread messages from the CEO"][-1].endswith("/chat")
    assert actions["Open the desk"] == ("/usr/bin/yaaf", "desktop", "launch", "desk")
    assert actions["Stop yaaf"] == ("systemctl", "--user", "stop", "yaaf")
    down = indicator.menu_for({"state": "offline"}, "/usr/bin/yaaf")
    assert ("systemctl", "--user", "start", "yaaf") in [r.action for r in down]
    assert not [r for r in down if r.action and r.action[1:3] == ("desktop", "open")]


def test_the_menu_is_only_rebuilt_when_it_changed():
    ind = indicator.Indicator("/usr/bin/yaaf")
    assert ind.set_view(VIEW) == (True, True) and ind.revision == 1
    assert ind.set_view(VIEW) == (False, False) and ind.revision == 1
    ind.set_view({**VIEW, "unread": 13})
    assert ind.face.label == "14"


@pytest.mark.skipif(shutil.which("dbus-daemon") is None, reason="needs dbus-daemon")
def test_a_tray_host_reads_the_item_and_its_menu_over_dbus(monkeypatch, tmp_path):
    """The whole round trip a desktop's tray makes, on a private session bus:
    the item's properties, then the menu's layout, then a click."""
    from dbus_fast.aio import MessageBus

    daemon = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                              stdout=subprocess.PIPE, text=True)
    try:
        address = daemon.stdout.readline().strip()
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", address)
        monkeypatch.setattr(indicator, "fetch_view", lambda: VIEW)
        clicked = []
        monkeypatch.setattr(indicator.Indicator, "run",
                            staticmethod(lambda argv: clicked.append(argv)))

        async def scenario():
            ind = indicator.Indicator("/usr/bin/yaaf")
            server = asyncio.ensure_future(indicator.serve(ind, poll_s=0.2))
            await asyncio.sleep(0.6)
            client = await MessageBus().connect()
            names = (await client.introspect("org.freedesktop.DBus", "/org/freedesktop/DBus"))
            assert names
            owner = next(n for n in await _list_names(client)
                         if n.startswith("org.kde.StatusNotifierItem-"))
            item = client.get_proxy_object(owner, indicator.ITEM_PATH,
                                           await client.introspect(owner, indicator.ITEM_PATH))
            props = item.get_interface("org.kde.StatusNotifierItem")
            assert await props.get_icon_name() == "yaaf"
            assert await props.get_x_ayatana_label() == "13"
            assert await props.get_item_is_menu() is True
            menu = client.get_proxy_object(owner, indicator.MENU_PATH,
                                           await client.introspect(owner, indicator.MENU_PATH))
            dbusmenu = menu.get_interface("com.canonical.dbusmenu")
            revision, (root_id, _props, children) = await dbusmenu.call_get_layout(0, -1, [])
            labels = [c.value[1]["label"].value for c in children if "label" in c.value[1]]
            assert "Open the desk" in labels and revision >= 1
            desk_id = next(c.value[0] for c in children
                           if c.value[1].get("label") and
                           c.value[1]["label"].value == "Open the desk")
            from dbus_fast import Variant
            await dbusmenu.call_event(desk_id, "clicked", Variant("s", ""), 0)
            await asyncio.sleep(0.1)
            server.cancel()
            client.disconnect()

        asyncio.run(scenario())
        assert ("/usr/bin/yaaf", "desktop", "launch", "desk") in clicked
    finally:
        daemon.terminate()
        daemon.wait(timeout=5)


async def _list_names(bus) -> list[str]:
    from dbus_fast import Message

    reply = await bus.call(Message(destination="org.freedesktop.DBus",
                                   path="/org/freedesktop/DBus",
                                   interface="org.freedesktop.DBus", member="ListNames"))
    return reply.body[0]


# ── what goes where ─────────────────────────────────────────────────────────────


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path


def test_every_desktop_but_omarchy_starts_the_tray_at_login(home, monkeypatch):
    from yaaf.cli import omarchy

    monkeypatch.setattr(omarchy, "present", lambda: False)
    files = desktop.planned_files()
    autostart = home / "config" / "autostart" / "yaaf-indicator.desktop"
    assert "desktop indicator" in files[autostart]
    icons = home / "data" / "icons" / "hicolor" / "scalable" / "apps"
    assert {icons / f"{n}.svg" for n in indicator.ICONS} <= set(files)

    monkeypatch.setattr(omarchy, "present", lambda: True)
    assert autostart not in desktop.planned_files(), "Omarchy's bar has the panel"


def test_a_changed_widget_restarts_omarchys_shell_and_an_unchanged_one_does_not(home,
                                                                               monkeypatch):
    """A running Omarchy shell keeps the widget it compiled at start: the new
    one is not seen until the shell restarts. Only when it changed, though."""
    from yaaf.cli import omarchy

    monkeypatch.setattr(omarchy, "present", lambda: True)
    monkeypatch.setattr(omarchy, "config_home", lambda: home / "config")
    seen = []
    monkeypatch.setattr(omarchy, "install",
                        lambda yaaf, key, widget_changed=False: seen.append(widget_changed) or [])
    monkeypatch.setattr(desktop, "_systemctl", lambda *a: True)
    monkeypatch.setattr(desktop, "_refresh_menus", lambda: None)
    desktop.apply()
    desktop.apply()
    assert seen == [True, False]


# ── a CEO writing to you ────────────────────────────────────────────────────────


def _noon() -> datetime:
    return datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)


def test_a_ceo_writing_is_a_notification_once_per_message(monkeypatch):
    monkeypatch.setenv("YAAF_API", BASE)
    payload = {"projects": [_project("p1", "Daily", unread=2)]}
    first, state = desktop.plan_letters(payload, {}, _noon())
    assert first is not None and "Daily" in first.title and first.action[-1].endswith("/p1/chat")
    later = _noon().replace(minute=30)
    again, state = desktop.plan_letters(payload, state, later)
    assert again is None, "the same two messages are not news twice"
    more = {"projects": [_project("p1", "Daily", unread=3)]}
    third, state = desktop.plan_letters(more, state, later)
    assert third is not None and "1 new (of 3 unread)" in third.body


def test_never_at_night_and_never_twice_in_ten_minutes_but_nothing_is_lost(monkeypatch):
    monkeypatch.setenv("YAAF_API", BASE)
    payload = {"projects": [_project("p1", "Daily", unread=2)]}
    night = _noon().replace(hour=23)
    held, state = desktop.plan_letters(payload, {}, night)
    assert held is None
    sent, state = desktop.plan_letters(payload, state, _noon())
    assert sent is not None, "held overnight, said in the morning"
    soon = _noon().replace(minute=5)
    burst, _ = desktop.plan_letters({"projects": [_project("p1", "Daily", unread=4)]},
                                    state, soon)
    assert burst is None, "ten minutes between two"
