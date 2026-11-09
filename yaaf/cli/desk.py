"""`yaaf signal` and `yaaf desk`: what needs you, from a status bar or a terminal.

Both read the one payload every screen reads (`GET /observe/desk`) and draw
the rows the dashboard's Inbox draws — the same rows, keyed the same way, so a
snooze made in the browser hides the row here too, and the number in a status
bar is the number on the dashboard's badge. Neither counts anything itself:
the server counts each project's decisions (`policy.needs_you`), and the row
builder below is held to that count by a test, so the day it drifts the suite
says so.

`yaaf desk` settles exactly what the Inbox settles in place, and no more:
an approval, a question, proposed scope, a finished delivery. Everything else
opens that row in the dashboard. A terminal is a door to a decision, never a
second way to take one with fewer safeguards than the first — so a purchase
asks before it spends, a decline asks why, accepting scope and completing a
project ask twice, and every write goes out on this CLI's own token, which the
record names.
"""

from __future__ import annotations

import json
import math
import os
import select
import sys
import time
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx
import typer

from yaaf.core import inbox
from yaaf.core.inbox import HARD_STOP

#: Inside this, a decision that decides itself lights the bar.
SOON = timedelta(hours=24)
NEVER = math.inf
#: How close to its own deadline a snooze may run (yaaf/server/api/me.py).
SNOOZE_MARGIN = timedelta(hours=2)
#: Unattended, the desk re-reads the payload this often.
REFRESH_S = 30


@dataclass
class Item:
    """One Inbox row: what is waiting, on which project, and what silence does."""

    key: str
    type: str  # approval | ask | scope | commitment | verdict | bet | frozen | signoff
    tier: int  # 1 stopped · 2 on a clock — the Inbox's own order
    at: float  # epoch seconds of the consequence; 0 already happened; inf never
    clocked: bool
    project: dict
    kind: str
    title: str
    consequence: str
    approval: dict | None = None
    ask: dict | None = None
    #: Open questions on the same project asked before this one: any reply
    #: settles them too, which the person answering should know.
    earlier_asks: int = 0

    @property
    def settles_here(self) -> bool:
        return self.type in ("approval", "ask", "scope", "signoff")


def _epoch(stamp: str | None) -> float | None:
    if not stamp:
        return None
    at = datetime.fromisoformat(stamp)
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).timestamp()


def when(at: float, now: float | None = None) -> str:
    """'in 3 hours', 'in 2 days', 'now' — the distance a person reads."""
    if math.isinf(at):
        return "never"
    left = at - (now if now is not None else time.time())
    if left <= 60:
        return "now"
    hours = left / 3600
    if hours < 1:
        return f"in {int(left // 60)} min"
    if hours < 48:
        return f"in {int(hours)} hour{'s' if int(hours) != 1 else ''}"
    return f"in {int(hours // 24)} days"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def project_items(p: dict) -> list[Item]:
    """Every decision waiting on the chairman for one project row — the rows
    `itemsFor` in web/src/desk.tsx marks `yours`, in its words."""
    out: list[Item] = []
    pid = p["id"]

    def add(**kw) -> None:
        out.append(Item(project=p, clocked=kw.pop("clocked", False), **kw))

    for bet in p.get("expired_bets") or []:
        add(key=bet["id"], type="bet", tier=1, at=0, kind="Bet expired",
            title=f"A bet hit its kill date: “{bet['statement']}”",
            consequence="The CEO pivots. You hold the one extension.")
    if p.get("frozen_threads"):
        n = int(p["frozen_threads"])
        add(key=inbox.frozen_item(pid), type="frozen", tier=1, at=0, kind="Thread frozen",
            title=f"{_plural(n, 'conversation')} frozen — two agents were looping",
            consequence="Those agents stay stuck until you unfreeze the thread.")
    for thread in p.get("frozen_mail") or []:
        add(key=inbox.frozen_mail_item(pid, thread["id"]), type="frozen", tier=1, at=0,
            kind="Email thread frozen",
            title=f"The email thread with {thread['counterpart']} — “{thread['subject']}” — "
                  "reached its limit",
            consequence="Their replies are held and the CEO cannot answer until you reopen it.")
    waiting = p.get("awaiting_you") or {}
    for v in waiting.get("verdicts") or []:
        add(key=inbox.verdict_item(pid, v["key"]), type="verdict", tier=1, at=0,
            kind="Your verdict", title=f"Is this done? “{v.get('name') or v['statement']}”",
            consequence="It stays open. Nobody on the project can mark it met.")
    if waiting.get("plan"):
        n = int(waiting["plan"])
        add(key=inbox.scope_item(pid), type="scope", tier=1, at=0, kind="Proposed scope",
            title=f"The CEO proposed {_plural(n, 'requirement')} for you to accept",
            consequence="Proposed scope is not scope: no work can be built against it.")
    if waiting.get("commitment"):
        c = waiting["commitment"]
        by = datetime.fromisoformat(c["deliver_by"])
        add(key=inbox.commitment_item(pid), type="commitment", tier=2, at=NEVER,
            kind="Sign the date",
            title=f"Sign a delivery date: {_plural(c.get('scope_count', 0), 'requirement')} "
                  f"by {by:%a %d %b}",
            consequence="The project keeps working, but it is promising nothing until you sign.")
    signoff = p.get("pending_signoff")
    if signoff:
        closes = _epoch(signoff.get("closes_at"))
        add(key=inbox.signoff_item(pid), type="signoff", tier=2,
            at=closes if closes is not None else NEVER, clocked=closes is not None,
            kind="Sign off",
            title=f"Finished: {_plural(signoff.get('met', 0), 'requirement')} true — sign it off",
            consequence=(f"It completes itself {when(closes)} unless its scope moves. "
                         "You can reopen it for 14 days." if closes is not None
                         else "It waits, spending nothing, until you complete or reopen it."))
    for a in p.get("pending_approvals") or []:
        at = _epoch(a["expires_at"]) or NEVER
        add(key=a["id"], type="approval", tier=2, at=at, clocked=True,
            kind="For you to do" if a["kind"] == "assist"
            else f"Decision · {a['kind'].replace('_', ' ')}",
            title=a["title"], approval=a,
            consequence=f"It is {a.get('if_silent') or 'declined'} {when(at)}. "
                        "Silence is a decision here.")
    asks = sorted(p.get("pending_asks") or [], key=lambda a: a["answer_by"])
    for i, ask in enumerate(asks):
        at = _epoch(ask["answer_by"]) or NEVER
        add(key=ask["id"], type="ask", tier=2, at=at, clocked=True, kind="Question",
            title=ask["title"], ask=ask, earlier_asks=i,
            consequence=f"It stops waiting {when(at)}, then: {ask['default_action']}")
    return out


def items(desk: dict) -> list[Item]:
    """What needs you, across every project you can open: running projects
    only (a paused one is a state, not a request), minus what you snoozed —
    the rows the dashboard's badge counts, in the Inbox's order."""
    snoozed = {(s["project_id"], s["item_key"]) for s in desk.get("snoozed") or []}
    found = [it for p in desk.get("projects") or [] if p.get("status") == "running"
             for it in project_items(p) if (p["id"], it.key) not in snoozed]
    return sorted(found, key=lambda it: (it.tier, it.at))


def at_the_wall(desk: dict) -> list[dict]:
    """Projects the hard stop paused. Not a decision row — the CEO cannot
    restart one — but the one stopped thing only the chairman can move."""
    return [p for p in desk.get("projects") or [] if p.get("status") == "paused"
            and (p.get("status_reason") or "").startswith(HARD_STOP)]


# ── yaaf signal ─────────────────────────────────────────────────────────────────


#: yaaf in the bar: its initial. The chairman asked for a Y.
MARK = "Y"
#: What the bar draws when yaaf cannot be reached — still there, and plainly down.
OFFLINE_MARK = "Y ✕"


def unread_messages(desk: dict) -> list[dict]:
    """Boardroom messages the chairman has not read, per running project — the
    CEO talking to him. Not decisions, so not on the badge; but a person who
    wrote to his CEO and was answered must see that he was."""
    return [{"project": p, "count": int(p.get("boardroom_unread") or 0)}
            for p in desk.get("projects") or []
            if p.get("status") == "running" and int(p.get("boardroom_unread") or 0) > 0]


def signal_view(desk: dict, now: float | None = None, prefix: str = MARK) -> dict:
    """The status bar's whole answer, in the shape Waybar reads and Omarchy's
    command module reads: `text`, `tooltip`, `class`.

    Always there while yaaf runs: the mark alone when nothing waits, the mark
    and a number when something does — decisions and unread messages from a
    CEO together, since both are waiting on the chairman. `count` stays the
    decisions alone, the dashboard badge's number. Only a decision deciding
    itself within a day, or a project at its wall, is `active` — the one class
    every bar draws as a highlight. It used to be empty while nothing needed
    him, which was indistinguishable from yaaf not running at all."""
    now = now if now is not None else time.time()
    found = items(desk)
    walls = at_the_wall(desk)
    unread = unread_messages(desk)
    letters = sum(u["count"] for u in unread)
    soon = [it for it in found if it.clocked and it.at - now <= SOON.total_seconds()]
    waiting = len(found) + letters
    text = f"{prefix} {waiting}" if waiting else prefix
    if walls:
        text += f" ⏸ {len(walls)}"
    lines = [f"{it.project['name']}: {it.title}" for it in found[:12]]
    if len(found) > 12:
        lines.append(f"… and {len(found) - 12} more")
    lines += [f"{u['project']['name']}: {_plural(u['count'], 'unread message')} from the CEO"
              for u in unread]
    lines += [f"{p['name']}: paused at its spending limit" for p in walls]
    tooltip = "\n".join(lines) if lines else "yaaf is running. Nothing needs you."
    state = "active" if soon or walls else ("waiting" if waiting else "clear")
    return {"text": text, "tooltip": tooltip, "class": state, "alt": state,
            "count": len(found), "unread": letters, "soon": len(soon), "paused": len(walls)}


def offline_view(why: str, state: str = "offline") -> dict:
    return {"text": OFFLINE_MARK, "tooltip": why, "class": state, "alt": state,
            "count": None, "unread": None, "soon": 0, "paused": 0}


def panel_view(desk: dict, base: str, now: float | None = None) -> dict:
    """Everything the click-open panel and the tray menu show: the bar's
    answer, then what is behind it — each decision, each project's unread
    messages, each project's one-line status — every row with the dashboard
    address that opens it. One shape for every desktop's front end."""

    now = now if now is not None else time.time()
    view = signal_view(desk, now)
    app = f"{base.rstrip('/')}/app"
    view["state"] = view["class"]
    view["dashboard"] = f"{app}/"
    view["decisions"] = [
        {"project": it.project["name"], "title": it.title, "kind": it.kind,
         "when": when(it.at, now) if it.clocked else "",
         "soon": bool(it.clocked and it.at - now <= SOON.total_seconds()),
         "url": inbox_url(base, it)}
        for it in items(desk)[:12]]
    view["messages"] = [
        {"project": u["project"]["name"], "count": u["count"],
         "url": screen_url(base, f"projects/{u['project']['id']}/chat")}
        for u in unread_messages(desk)]
    view["projects"] = [
        {"name": p["name"], "status": p.get("status"),
         "verdict": (p.get("status_line") or {}).get("verdict", ""),
         "headline": (p.get("status_line") or {}).get("headline", ""),
         "url": screen_url(base, f"projects/{p['id']}")}
        for p in desk.get("projects") or [] if p.get("status") in ("running", "paused")]
    return view


def offline_panel(why: str, base: str, state: str = "offline") -> dict:
    view = offline_view(why, state)
    view.update(state=state, dashboard=f"{base.rstrip('/')}/app/",
                decisions=[], messages=[], projects=[])
    return view


def fetch_desk(client: httpx.Client) -> dict:
    response = client.get("/observe/desk", params={"live": "true"})
    response.raise_for_status()
    return response.json()


def signal(
    fmt: str = typer.Option("text", "--format",
                            help="text (a line for any bar), waybar or json (Waybar's and "
                                 "Omarchy's custom-module JSON), count (just the number), or "
                                 "panel (everything the click-open panel shows, as JSON)"),
    prefix: str = typer.Option(MARK, "--prefix", help="What the number is drawn after"),
) -> None:
    """What needs you, as one line for a status bar: the mark, and a number
    when something waits. Run it on an interval; it exits at once."""
    from yaaf.cli import main as cli

    if fmt not in ("text", "waybar", "json", "count", "panel"):
        typer.echo("--format is one of: text, waybar, json, count, panel", err=True)
        raise typer.Exit(2)
    base = cli._api_base()
    try:
        with httpx.Client(base_url=base, timeout=8,
                          headers={"Authorization": f"Bearer {cli._token()}"}) as client:
            desk = fetch_desk(client)
        view = (panel_view(desk, base) if fmt == "panel"
                else signal_view(desk, prefix=prefix))
    except httpx.HTTPStatusError as exc:
        signed_out = exc.response.status_code == 401
        why, state = (("Not signed in. Run `yaaf login` in a terminal.", "signed-out")
                      if signed_out else (f"yaaf answered {exc.response.status_code}",
                                          "offline"))
        view = (offline_panel(why, base, state) if fmt == "panel"
                else offline_view(why, state))
    except httpx.HTTPError:
        why = "yaaf is not running."
        view = offline_panel(why, base) if fmt == "panel" else offline_view(why)
    if fmt == "count":
        if view["count"] is None:
            typer.echo(view["tooltip"], err=True)
            raise typer.Exit(1)
        typer.echo(view["count"])
    elif fmt in ("waybar", "json", "panel"):
        # One line: a bar module reads the last line of what a command prints.
        typer.echo(json.dumps(view, ensure_ascii=False))
    else:
        typer.echo(view["text"])


# ── the verbs ───────────────────────────────────────────────────────────────────


class Refused(Exception):
    """A verb this desk will not send, with the reason a person reads."""


def _sent(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise Refused(detail if isinstance(detail, str) else json.dumps(detail))
    return response.json() if response.content else {}


def purchase_warning(item: Item) -> str | None:
    """Money that leaves cannot be called back, so that one yes asks first —
    the dashboard's own rule, in its words."""
    a = item.approval or {}
    if a.get("kind") == "spend" and (a.get("amount_usd") or 0) > 0:
        return f"This spends ${a['amount_usd']:,.0f} — a purchase cannot be taken back."
    return None


def decide(client: httpx.Client, item: Item, approve: bool, note: str = "") -> dict:
    """Approve or decline. A decline carries its reason or is not sent: the
    CEO re-plans from it, and a bare "no" is a project left guessing."""
    if item.type != "approval":
        raise Refused("only an approval is decided this way")
    if not approve and not note.strip():
        raise Refused("Say why — the CEO reads it and re-plans.")
    body: dict = {"approve": approve, "note": note.strip()}
    capacity = (item.approval or {}).get("capacity")
    if approve and capacity:
        body["to"] = capacity["to"]
    return _sent(client.post(f"/admin/approvals/{item.key}/decide", json=body))


def answer(client: httpx.Client, item: Item, text: str) -> dict:
    """An answer is a line in the boardroom, as it is from the Inbox: the CEO
    reads every message there, and it settles the questions asked before it."""
    if item.type != "ask":
        raise Refused("only a question is answered this way")
    if not text.strip():
        raise Refused("An empty answer answers nothing.")
    return say(client, item.project, f"Re: “{item.title}” — {text.strip()}")


def say(client: httpx.Client, project: dict, text: str) -> dict:
    if not text.strip():
        raise Refused("Nothing to say.")
    return _sent(client.post(f"/admin/projects/{project['id']}/boardroom",
                             json={"body": text.strip()}))


def accept_scope(client: httpx.Client, item: Item) -> list[str]:
    """Every proposed requirement, accepted — what the Inbox's compact row does.
    Reversible (a requirement can be cut later), so two presses, not a typed
    confirmation. Rewording and cutting stay on the Roadmap."""
    if item.type != "scope":
        raise Refused("only proposed scope is accepted this way")
    done = []
    for req in item.project.get("scope_items") or []:
        _sent(client.post(f"/admin/requirements/{req['id']}/accept", json={}))
        done.append(req["key"])
    return done


def complete(client: httpx.Client, item: Item) -> dict:
    """Close a finished delivery and write its receipt — reopenable for 14
    days, which is why it is two presses and not a typed confirmation."""
    if item.type != "signoff":
        raise Refused("only a finished delivery is signed off this way")
    return _sent(client.post(f"/admin/projects/{item.project['id']}/complete"))


def snooze_until(item: Item, choice: str, now: datetime | None = None) -> datetime:
    """"Not now", never past the moment the row decides itself: a row with a
    clock comes back at least two hours before it."""
    now = now or datetime.now(UTC)
    if choice == "hour":
        until = now + timedelta(hours=1)
    else:
        local = now.astimezone()
        morning = (local + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        until = morning.astimezone(UTC)
    if item.clocked:
        latest = datetime.fromtimestamp(item.at, UTC) - SNOOZE_MARGIN
        if latest <= now + timedelta(minutes=5):
            raise Refused("Too close to its deadline to snooze.")
        until = min(until, latest)
    return until


def snooze(client: httpx.Client, item: Item, until: datetime) -> dict:
    return _sent(client.post("/me/snoozes", json={
        "project_id": item.project["id"], "item_key": item.key,
        "until": until.isoformat()}))


def screen_url(base: str, route: str) -> str:
    """A dashboard screen by its route. The dashboard routes in the fragment
    (HashRouter, web/src/main.tsx): the server serves /app/ alone, so
    /app/projects/… is a 404 and only /app/#/projects/… opens the screen."""
    return f"{base.rstrip('/')}/app/#/{route.lstrip('/')}"


def inbox_url(base: str, item: Item) -> str:
    """The row in the dashboard: the link the mail carries, from this machine's
    own address. Opening it decides nothing."""
    return screen_url(base, f"inbox?item={quote(item.key, safe='')}")


# ── the screen ──────────────────────────────────────────────────────────────────


def _read_key(timeout: float) -> str | None:
    """One key press, or None when nobody pressed one in `timeout` seconds."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return None
        key = os.read(fd, 1).decode(errors="ignore")
        if key == "\x1b":
            more, _, _ = select.select([sys.stdin], [], [], 0.05)
            if more:
                key += os.read(fd, 2).decode(errors="ignore")
        return {"\x1b[A": "k", "\x1b[B": "j", "\r": "\n"}.get(key, key)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _verbs(item: Item) -> str:
    if item.type == "approval":
        return "[a] approve  [n] approve with a note  [d] decline…"
    if item.type == "ask":
        return ("[1-9] answer with an option  [r] answer in your own words"
                if (item.ask or {}).get("options") else "[r] answer")
    if item.type == "scope":
        return "[a] accept every proposed requirement"
    if item.type == "signoff":
        return "[a] complete & write the receipt"
    return "decided in the dashboard — [o] opens it"


def _render(console, found: list[Item], chosen: int, note: str, as_of: str) -> None:
    from rich.panel import Panel
    from rich.text import Text

    console.clear()
    head = Text(f"yaaf · desk   {_plural(len(found), 'decision')} waiting on you", style="bold")
    head.append(f"   as of {as_of}", style="dim")
    console.print(head)
    console.print()
    if not found:
        console.print("Nothing needs you. The CEO has the rest.", style="green")
    for i, it in enumerate(found[:9]):
        # No numbers on the rows: the digits answer a question's options.
        mark = "›" if i == chosen else " "
        line = Text(f"{mark} ", style="bold" if i == chosen else "")
        line.append(f"{it.project['name']}  ", style="cyan")
        line.append(f"{it.kind}  ", style="dim")
        line.append(it.title, style="bold" if i == chosen else "")
        console.print(line, overflow="ellipsis", no_wrap=True)
    if len(found) > 9:
        console.print(f"  … and {len(found) - 9} more in the dashboard", style="dim")
    if found:
        it = found[chosen]
        body = Text()
        body.append(f"{it.title}\n", style="bold")
        rec = (it.approval or it.ask or {}).get("recommendation")
        if it.ask and it.ask.get("body"):
            body.append(f"{it.ask['body']}\n", style="")
        if rec:
            body.append("CEO recommends: ", style="dim")
            body.append(f"{rec}\n")
        for n, option in enumerate((it.ask or {}).get("options") or [], 1):
            body.append(f"  {n}. {option}\n")
        if it.earlier_asks:
            body.append(f"Any answer also settles the {_plural(it.earlier_asks, 'question')} "
                        "asked before this one.\n", style="yellow")
        body.append("If you do nothing: ", style="dim")
        body.append(f"{it.consequence}\n")
        body.append(_verbs(it), style="bold")
        console.print()
        console.print(Panel(body, title=f"{it.project['name']} · {it.kind}", title_align="left"))
    console.print()
    if note:
        console.print(note, markup=False)
    # Plain text, not markup: rich reads "[j/k]" as a style tag and drops it.
    console.print("[j/k] move  [o] open in the dashboard  [s] say to the CEO  "
                  "[z] not now  [g] refresh  [q] quit", style="dim", markup=False)


def _act(client: httpx.Client, base: str, it: Item, key: str) -> tuple[str, bool]:
    """Run one verb; return what to say about it, and whether it settled the
    row. Prompts happen with the terminal back in its normal mode, so typing a
    reason is ordinary typing."""
    if key == "o":
        webbrowser.open(inbox_url(base, it))
        return f"Opened in the dashboard: {inbox_url(base, it)}", False
    if key == "s":
        text = typer.prompt(f"To the CEO of {it.project['name']}", default="", show_default=False)
        if not text.strip():
            return "Nothing sent.", False
        say(client, it.project, text)
        return "Sent to the boardroom.", False
    if key == "z":
        choice = typer.prompt("Not now — for an [h]our or until [t]omorrow morning?",
                              default="t")
        until = snooze_until(it, "hour" if choice.lower().startswith("h") else "morning")
        snooze(client, it, until)
        return f"Back {until.astimezone():%a %H:%M}.", True
    if it.type == "approval" and key in ("a", "n", "d"):
        if key == "d":
            why = typer.prompt("Why decline? The CEO reads it and re-plans", default="",
                               show_default=False)
            decide(client, it, False, why)
            return "Declined.", True
        note = typer.prompt("A note for the CEO", default="", show_default=False) \
            if key == "n" else ""
        warning = purchase_warning(it)
        if warning and not typer.confirm(f"{warning} Approve?", default=False):
            return "Nothing spent.", False
        if not warning and not typer.confirm(f"Approve “{it.title}”?", default=False):
            return "Not approved.", False
        decide(client, it, True, note)
        return "Approved.", True
    if it.type == "ask":
        options = (it.ask or {}).get("options") or []
        if key.isdigit() and 1 <= int(key) <= len(options):
            text = options[int(key) - 1]
        elif key == "r":
            text = typer.prompt("Your answer", default="", show_default=False)
        else:
            return "", False
        if not text.strip() or not typer.confirm(f"Send “{text.strip()}” to the CEO?",
                                                 default=True):
            return "Nothing sent.", False
        answer(client, it, text)
        return "Answered in the boardroom.", True
    if it.type == "scope" and key == "a":
        names = [f"{r['key']} {r['name']}" for r in it.project.get("scope_items") or []]
        typer.echo("\n".join(f"  {n}" for n in names))
        if not typer.confirm(f"Accept {_plural(len(names), 'outcome')}? You can cut one later "
                             "on the Roadmap.", default=False):
            return "Not accepted.", False
        return f"Accepted {', '.join(accept_scope(client, it))}.", True
    if it.type == "signoff" and key == "a":
        if not typer.confirm("Complete it? This writes the receipt and closes the project; "
                             "you can reopen it for 14 days.", default=False):
            return "Not completed.", False
        complete(client, it)
        return "Completed. The receipt is written.", True
    return "", False


def desk() -> None:
    """What needs you, one decision at a time, in this terminal — the Inbox's
    rows with the Inbox's safeguards. What it does not settle here, it opens in
    the dashboard."""
    from rich.console import Console

    from yaaf.cli import main as cli

    if not sys.stdin.isatty():
        typer.echo("yaaf desk needs a terminal. For a status bar, use `yaaf signal`.", err=True)
        raise typer.Exit(2)
    console = Console()
    base = cli._api_base()
    chosen, note = 0, ""
    # Rows acted on here, hidden until the server stops listing them — an
    # answer is settled by the janitor's next pass, not by the reply itself —
    # and for two minutes at most, so a write that did not land comes back.
    settled: dict[str, float] = {}
    with cli._client() as client:
        while True:
            try:
                data = fetch_desk(client)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 401:
                    typer.echo("Not signed in: run `yaaf login`.", err=True)
                    raise typer.Exit(1) from None
                raise
            listed = items(data)
            now = time.time()
            settled = {k: t for k, t in settled.items()
                       if now - t < 120 and any(i.key == k for i in listed)}
            found = [i for i in listed if i.key not in settled]
            chosen = max(0, min(chosen, len(found) - 1))
            _render(console, found, chosen, note,
                    datetime.now().astimezone().strftime("%H:%M"))
            key = _read_key(REFRESH_S)
            note = ""
            if key is None or key == "g":
                continue
            if key in ("q", "\x1b"):
                return
            if key == "j":
                chosen = min(chosen + 1, max(len(found) - 1, 0))
                continue
            if key == "k":
                chosen = max(chosen - 1, 0)
                continue
            if not found:
                continue
            try:
                note, done = _act(client, base, found[chosen], key)
                if done:
                    settled[found[chosen].key] = time.time()
            except Refused as why:
                note = f"Not sent: {why}"
            except httpx.HTTPError as exc:
                note = f"Not sent: yaaf did not answer ({type(exc).__name__})."
            except typer.Abort:
                note = "Cancelled."
