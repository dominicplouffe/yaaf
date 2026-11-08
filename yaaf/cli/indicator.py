"""`yaaf desktop indicator`: yaaf in the top bar of any Linux desktop.

A StatusNotifierItem — the tray standard Ubuntu's GNOME (through the
AppIndicator support Ubuntu ships turned on), KDE Plasma, Xfce, Cinnamon,
Budgie, Waybar and Omarchy's own shell all host — with a menu. Always there
while it runs: yaaf's icon, and beside it (where the desktop draws a label) the
number of decisions and unread CEO messages waiting on you; an orange dot when
a decision is about to decide itself or a project stopped at its spending
limit; grey when yaaf is not running. A click opens the menu: what needs you,
each project's unread messages and state, each opening its own screen, then the
dashboard, the desk, and starting or stopping yaaf.

It is the same answer the Omarchy bar panel shows (`yaaf signal --format
panel`, yaaf.cli.desk.panel_view), drawn in the desktop's own menu. Omarchy
gets the panel instead, so `yaaf desktop install` starts this everywhere else.

Pure D-Bus (dbus-fast), no GTK: the desktop draws the icon and the menu, from
two interfaces this exports — org.kde.StatusNotifierItem and
com.canonical.dbusmenu.
"""

# dbus-fast reads a D-Bus signature from each annotation: "s", "a(iiay)",
# "(sa(iiay)ss)". They are strings on purpose, and not Python types.
# ruff: noqa: F821, F722, UP037

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field

log = logging.getLogger("yaaf.indicator")

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
WATCHER = "org.kde.StatusNotifierWatcher"
POLL_S = 30.0

ICON = "yaaf"
ICON_ATTENTION = "yaaf-attention"
ICON_OFFLINE = "yaaf-offline"

_TILE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-label="yaaf">
  <!-- Managed by `yaaf desktop install`; `yaaf desktop remove` takes it out. -->
  <rect width="64" height="64" rx="14" fill="{fill}"/>
  <g fill="none" stroke="#ffffff" stroke-width="8.5" stroke-linecap="round" stroke-linejoin="round">
    <path d="M18 15 L33 37"/>
    <path d="M46 15 L26 49"/>
  </g>{extra}
</svg>
"""

#: The tray's three faces, installed into the user's hicolor icon theme.
ICONS = {
    ICON: _TILE.format(fill="#3a86df", extra=""),
    ICON_ATTENTION: _TILE.format(
        fill="#3a86df",
        extra='\n  <circle cx="52" cy="12" r="11" fill="#f59e0b" stroke="#ffffff" '
              'stroke-width="3"/>'),
    ICON_OFFLINE: _TILE.format(fill="#8a8f98", extra=""),
}


# ── what it shows ───────────────────────────────────────────────────────────────


@dataclass
class Entry:
    """One menu row: a label, and what clicking it does (None: a label only)."""
    label: str
    action: tuple[str, ...] | None = None
    enabled: bool = True
    separator: bool = False
    children: list[Entry] = field(default_factory=list)


@dataclass
class Face:
    """Everything the tray draws: the icon, its status, its label, the tooltip."""
    icon: str
    status: str            # Active | NeedsAttention
    label: str             # beside the icon, where the desktop draws one
    title: str
    tooltip: str


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def face_for(view: dict) -> Face:
    state = view.get("state") or view.get("class") or "clear"
    if state in ("offline", "signed-out"):
        return Face(ICON_OFFLINE, "Active", "", "yaaf: not running" if state == "offline"
                    else "yaaf: not signed in", view.get("tooltip") or "")
    waiting = int(view.get("count") or 0) + int(view.get("unread") or 0)
    parts = []
    if view.get("count"):
        parts.append(_plural(int(view["count"]), "decision"))
    if view.get("unread"):
        parts.append(_plural(int(view["unread"]), "unread message"))
    title = "yaaf: " + (", ".join(parts) if parts else "nothing needs you")
    hot = state == "active"
    return Face(ICON_ATTENTION if hot else ICON, "NeedsAttention" if hot else "Active",
                str(waiting) if waiting else "", title, view.get("tooltip") or "")


def menu_for(view: dict, yaaf: str) -> list[Entry]:
    """The menu, top to bottom. Every row that is about something opens that
    thing; the last rows are the ways in and the switch."""
    state = view.get("state") or view.get("class") or "clear"
    rows: list[Entry] = []
    face = face_for(view)
    rows.append(Entry(face.title.removeprefix("yaaf: ").capitalize(), enabled=False))
    if state in ("offline", "signed-out"):
        rows.append(Entry("yaaf is not running, so nothing is being worked on or watched."
                          if state == "offline" else "Run `yaaf login` in a terminal, once.",
                          enabled=False))
    open_ = (yaaf, "desktop", "open")
    if view.get("decisions"):
        rows += [Entry("", separator=True), Entry("Needs you", enabled=False)]
        for d in view["decisions"]:
            when = f" · decides {d['when']}" if d.get("when") else ""
            rows.append(Entry(f"{d['title']} — {d['project']}{when}", (*open_, d["url"])))
    if view.get("messages"):
        rows += [Entry("", separator=True), Entry("Messages", enabled=False)]
        for m in view["messages"]:
            rows.append(Entry(f"{m['project']}: {_plural(m['count'], 'unread message')} "
                              "from the CEO", (*open_, m["url"])))
    if view.get("projects"):
        rows += [Entry("", separator=True), Entry("Projects", enabled=False)]
        for p in view["projects"]:
            status = p.get("headline") or p.get("status") or ""
            rows.append(Entry(f"{p['name']} — {status}" if status else p["name"],
                              (*open_, p["url"])))
    rows.append(Entry("", separator=True))
    offline = state in ("offline", "signed-out")
    rows.append(Entry("Open the dashboard", (yaaf, "desktop", "launch", "dashboard"),
                      enabled=not offline))
    rows.append(Entry("Open the desk", (yaaf, "desktop", "launch", "desk"),
                      enabled=not offline))
    if state != "signed-out":
        rows.append(Entry("Start yaaf" if offline else "Stop yaaf",
                          ("systemctl", "--user", "start" if offline else "stop", "yaaf")))
    return rows


def fetch_view() -> dict:
    """The same answer `yaaf signal --format panel` prints, in-process."""
    import httpx

    from yaaf.cli import main as cli
    from yaaf.cli.desk import fetch_desk, offline_panel, panel_view

    base = cli._api_base()
    try:
        with httpx.Client(base_url=base, timeout=8,
                          headers={"Authorization": f"Bearer {cli._token()}"}) as client:
            return panel_view(fetch_desk(client), base)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            return offline_panel("Not signed in. Run `yaaf login` in a terminal.", base,
                                 "signed-out")
        return offline_panel(f"yaaf answered {exc.response.status_code}", base)
    except Exception:  # noqa: BLE001 — down, or signed out before any token: say so, keep running
        return offline_panel("yaaf is not running.", base)


# ── the D-Bus side ──────────────────────────────────────────────────────────────


def _interfaces():
    """Built on first use: dbus-fast is the native install's extra, and a box
    without it must still import `yaaf desktop`."""
    from dbus_fast import PropertyAccess, Variant
    from dbus_fast.service import ServiceInterface, dbus_property, method, signal

    class Item(ServiceInterface):
        def __init__(self, owner: Indicator):
            super().__init__("org.kde.StatusNotifierItem")
            self.owner = owner

        @dbus_property(access=PropertyAccess.READ)
        def Category(self) -> "s":
            return "ApplicationStatus"

        @dbus_property(access=PropertyAccess.READ)
        def Id(self) -> "s":
            return "yaaf"

        @dbus_property(access=PropertyAccess.READ)
        def Title(self) -> "s":
            return self.owner.face.title

        @dbus_property(access=PropertyAccess.READ)
        def Status(self) -> "s":
            return self.owner.face.status

        @dbus_property(access=PropertyAccess.READ)
        def WindowId(self) -> "i":
            return 0

        @dbus_property(access=PropertyAccess.READ)
        def IconName(self) -> "s":
            return self.owner.face.icon

        @dbus_property(access=PropertyAccess.READ)
        def IconThemePath(self) -> "s":
            return self.owner.icon_theme_path

        @dbus_property(access=PropertyAccess.READ)
        def AttentionIconName(self) -> "s":
            return ICON_ATTENTION

        @dbus_property(access=PropertyAccess.READ)
        def OverlayIconName(self) -> "s":
            return ""

        @dbus_property(access=PropertyAccess.READ)
        def IconPixmap(self) -> "a(iiay)":
            return []

        @dbus_property(access=PropertyAccess.READ)
        def ToolTip(self) -> "(sa(iiay)ss)":
            return [self.owner.face.icon, [], self.owner.face.title, self.owner.face.tooltip]

        @dbus_property(access=PropertyAccess.READ)
        def ItemIsMenu(self) -> "b":
            # A click opens the menu: the one behaviour every host agrees on.
            return True

        @dbus_property(access=PropertyAccess.READ)
        def Menu(self) -> "o":
            return MENU_PATH

        @dbus_property(access=PropertyAccess.READ)
        def XAyatanaLabel(self) -> "s":
            return self.owner.face.label

        @dbus_property(access=PropertyAccess.READ)
        def XAyatanaLabelGuide(self) -> "s":
            return "99"

        @method()
        def Activate(self, x: "i", y: "i"):
            self.owner.run((self.owner.yaaf, "desktop", "launch", "dashboard"))

        @method()
        def SecondaryActivate(self, x: "i", y: "i"):
            self.owner.run((self.owner.yaaf, "desktop", "launch", "desk"))

        @method()
        def ContextMenu(self, x: "i", y: "i"):
            return

        @method()
        def Scroll(self, delta: "i", orientation: "s"):
            return

        @signal()
        def NewTitle(self):
            return

        @signal()
        def NewIcon(self):
            return

        @signal()
        def NewAttentionIcon(self):
            return

        @signal()
        def NewToolTip(self):
            return

        @signal()
        def NewStatus(self) -> "s":
            return self.owner.face.status

        @signal()
        def XAyatanaNewLabel(self) -> "ss":
            return [self.owner.face.label, "99"]

    class Menu(ServiceInterface):
        def __init__(self, owner: Indicator):
            super().__init__("com.canonical.dbusmenu")
            self.owner = owner

        def _node(self, node_id: int, depth: int) -> Variant:
            entry = self.owner.items.get(node_id)
            children = [] if depth == 0 else [
                self._node(child, depth - 1 if depth > 0 else -1)
                for child in self.owner.children.get(node_id, [])]
            return Variant("(ia{sv}av)", [node_id, self.owner.props(node_id, entry), children])

        @method()
        def GetLayout(self, parent_id: "i", recursion_depth: "i",
                      property_names: "as") -> "u(ia{sv}av)":
            node = self._node(parent_id, recursion_depth).value
            return [self.owner.revision, node]

        @method()
        def GetGroupProperties(self, ids: "ai", property_names: "as") -> "a(ia{sv})":
            wanted = ids or list(self.owner.items)
            return [[i, self.owner.props(i, self.owner.items.get(i))] for i in wanted
                    if i in self.owner.items or i == 0]

        @method()
        def GetProperty(self, node_id: "i", name: "s") -> "v":
            props = self.owner.props(node_id, self.owner.items.get(node_id))
            return props.get(name, Variant("s", ""))

        @method()
        def Event(self, node_id: "i", event_id: "s", data: "v", timestamp: "u"):
            if event_id == "clicked":
                self.owner.clicked(node_id)

        @method()
        def EventGroup(self, events: "a(isvu)") -> "ai":
            for node_id, event_id, _data, _ts in events:
                if event_id == "clicked":
                    self.owner.clicked(node_id)
            return []

        @method()
        def AboutToShow(self, node_id: "i") -> "b":
            self.owner.wake.set()  # look again now: the menu is about to be read
            return False

        @method()
        def AboutToShowGroup(self, ids: "ai") -> "aiai":
            self.owner.wake.set()
            return [[], []]

        @dbus_property(access=PropertyAccess.READ)
        def Version(self) -> "u":
            return 3

        @dbus_property(access=PropertyAccess.READ)
        def TextDirection(self) -> "s":
            return "ltr"

        @dbus_property(access=PropertyAccess.READ)
        def Status(self) -> "s":
            return "normal"

        @dbus_property(access=PropertyAccess.READ)
        def IconThemePath(self) -> "as":
            return [self.owner.icon_theme_path]

        @signal()
        def LayoutUpdated(self) -> "ui":
            return [self.owner.revision, 0]

        @signal()
        def ItemsPropertiesUpdated(self) -> "a(ia{sv})a(ias)":
            return [[], []]

    return Item, Menu


class Indicator:
    """The tray item: its face, its menu, the loop that keeps both current."""

    def __init__(self, yaaf: str, icon_theme_path: str = ""):
        self.yaaf = yaaf
        self.icon_theme_path = icon_theme_path
        self.view: dict = {"state": "loading"}
        self.face = Face(ICON, "Active", "", "yaaf", "")
        self.revision = 0
        self.items: dict[int, Entry] = {}
        self.children: dict[int, list[int]] = {0: []}
        self.wake = asyncio.Event()

    def set_view(self, view: dict) -> tuple[bool, bool]:
        """Take a new answer; returns (face changed, menu changed)."""
        face = face_for(view)
        rows = menu_for(view, self.yaaf)
        face_changed = face != self.face
        old = [(e.label, e.action, e.enabled, e.separator) for e in self.items.values()]
        new = [(e.label, e.action, e.enabled, e.separator) for e in rows]
        self.view, self.face = view, face
        menu_changed = old != new
        if menu_changed:
            self.items = {i + 1: e for i, e in enumerate(rows)}
            self.children = {0: list(self.items)}
            self.revision += 1
        return face_changed, menu_changed

    def props(self, node_id: int, entry: Entry | None) -> dict:
        from dbus_fast import Variant

        if node_id == 0 or entry is None:
            return {"children-display": Variant("s", "submenu")}
        if entry.separator:
            return {"type": Variant("s", "separator")}
        # Underscores doubled: dbusmenu reads a single one as a mnemonic.
        return {"label": Variant("s", entry.label.replace("_", "__")),
                "enabled": Variant("b", entry.enabled and entry.action is not None),
                "visible": Variant("b", True)}

    def clicked(self, node_id: int) -> None:
        entry = self.items.get(node_id)
        if entry and entry.action:
            self.run(entry.action)
            self.wake.set()

    @staticmethod
    def run(argv: tuple[str, ...]) -> None:
        with contextlib.suppress(OSError):
            subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)


async def serve(indicator: Indicator, poll_s: float = POLL_S) -> None:
    """Export the item and its menu, register with the desktop's tray, and keep
    both current — re-registering whenever the tray host restarts."""
    from dbus_fast.aio import MessageBus
    from dbus_fast.constants import NameFlag

    Item, Menu = _interfaces()
    bus = await MessageBus().connect()
    item, menu = Item(indicator), Menu(indicator)
    bus.export(ITEM_PATH, item)
    bus.export(MENU_PATH, menu)
    name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
    await bus.request_name(name, NameFlag.DO_NOT_QUEUE)

    async def register() -> bool:
        try:
            intro = await bus.introspect(WATCHER, "/StatusNotifierWatcher")
            watcher = bus.get_proxy_object(WATCHER, "/StatusNotifierWatcher", intro)
            await watcher.get_interface(WATCHER).call_register_status_notifier_item(name)
            return True
        except Exception as exc:  # noqa: BLE001 — no tray host yet: wait for one
            log.info("no tray to register with yet (%s)", exc)
            return False

    registered = await register()
    if not registered:
        print("No system tray is running yet. On GNOME (Ubuntu ships it on) the "
              "AppIndicator extension hosts it; yaaf shows up as soon as one starts.",
              flush=True)

    def on_message(message) -> None:
        if (message.member == "NameOwnerChanged" and message.body
                and message.body[0] == WATCHER and message.body[2]):
            asyncio.ensure_future(register())

    await bus.call(_match_rule("type='signal',interface='org.freedesktop.DBus',"
                               "member='NameOwnerChanged',arg0='org.kde.StatusNotifierWatcher'"))
    bus.add_message_handler(on_message)

    loop = asyncio.get_running_loop()
    while True:
        view = await loop.run_in_executor(None, fetch_view)
        face_changed, menu_changed = indicator.set_view(view)
        if face_changed:
            for emit in (item.NewIcon, item.NewStatus, item.NewTitle, item.NewToolTip,
                         item.XAyatanaNewLabel):
                emit()
        if menu_changed:
            menu.LayoutUpdated()
        indicator.wake.clear()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(indicator.wake.wait(), timeout=poll_s)


def _match_rule(rule: str):
    from dbus_fast import Message

    return Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                   interface="org.freedesktop.DBus", member="AddMatch", signature="s",
                   body=[rule])


def run(yaaf: str, icon_theme_path: str = "") -> None:
    logging.basicConfig(level=logging.WARNING)
    indicator = Indicator(yaaf, icon_theme_path)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(indicator))


def available() -> str | None:
    """Why the tray icon cannot run here, or None."""
    try:
        import dbus_fast  # noqa: F401
    except ImportError:
        return "the dbus-fast library is missing (it comes with yaaf's [host] extra)"
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") and not shutil.which("dbus-daemon") \
            and not os.path.exists(f"/run/user/{os.getuid()}/bus"):
        return "there is no desktop session bus"
    return None
