"""Slack Socket Mode: the chairman chats from Slack, no public URL required.

The signed HTTP webhook (yaaf.server.api.webhooks) needs Slack to reach this
box; most self-hosted yaaf boxes are not reachable. Socket Mode inverts the
arrow: we open an outbound WebSocket to Slack with an app-level token and
events arrive over it. Same trust model as the webhook — the ONLY event that
does anything is a plain message from the configured chairman Slack user in
the mapped boardroom channel; it becomes a chairman boardroom message on the
bus (stamped with its slack_ts so the mirror never echoes it back). The chat
lane answers it in seconds and the mirror carries the reply out — chat in
Slack, bus canonical throughout.

Enabled by an app token (the xapp- token, an ordinary envelope-encrypted
Secret) resolving for the project — app-wide in Settings, or named as
``app_token_secret`` in ``project.integrations["slack"]`` to override it. No
agent ever holds either token.

**One connection per app token, not per project.** Slack balances an app's
events across whatever Socket Mode connections that app has open and delivers
each event to exactly *one* of them — a second connection on the same token
does not double the coverage, it halves it. Since the normal case is every
project inheriting the one app-wide token, a per-project connection meant the
chairman's boardroom message landed on some other project's socket, failed its
channel check, and was acked and dropped with nothing logged: he was answered
one time in N and the misses looked like the CEO ignoring him. So the token
owns the connection and carries a route per project listening on it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
from sqlalchemy import select

from yaaf.core.db import db_session
from yaaf.core.models import Project
from yaaf.server.slack.bridge import post_boardroom_from_chairman
from yaaf.server.slack.settings import mapped_rooms, resolve

log = logging.getLogger("yaaf.slack.socket")

CONFIG_POLL_S = 15.0
RECONNECT_DELAY_S = 5.0
OPEN_TIMEOUT_S = 10.0


def socket_configs() -> dict[str, list[dict]]:
    """app_token -> the routes that token carries, one per listening project.

    Three things have to resolve — an app token, a mapped boardroom and a
    chairman — and any of the three may be inherited from the app-wide
    Slack connection. A project whose only per-project setting is its channel map is
    the normal case, and it shares the app-wide token's connection.

    Routes are sorted because row order out of the database is not a promise,
    and two projects that both claim a channel should be woken in the same
    order every time rather than in whatever order the rows arrived.
    """
    out: dict[str, list[dict]] = {}
    with db_session() as session:
        for project in session.execute(select(Project)).scalars():
            if not mapped_rooms(project).get("boardroom"):
                continue  # no room to listen in; nothing to resolve
            effective = resolve(session, project)
            if effective is None or not effective.can_receive:
                continue
            if effective.app_token is None:
                continue
            out.setdefault(effective.app_token, []).append({
                "project_id": project.id,
                "channel": effective.channel_map["boardroom"],
                "chairman": effective.chairman_slack_user,
            })
    return {token: sorted(routes, key=lambda r: r["project_id"])
            for token, routes in out.items()}


def chairman_text(cfg: dict, envelope: dict) -> tuple[str, str] | None:
    """(text, ts) when the envelope is a plain chairman message in the mapped
    boardroom channel — the single trusted inbound shape. Everything else
    (bots, other users, other channels, edits/joins/threads-broadcasts) is
    acked and dropped."""
    if envelope.get("type") != "events_api":
        return None
    event = ((envelope.get("payload") or {}).get("event")) or {}
    if event.get("type") != "message" or event.get("subtype"):
        return None
    if event.get("bot_id"):
        return None
    if event.get("channel") != cfg["channel"] or event.get("user") != cfg["chairman"]:
        return None
    text = (event.get("text") or "").strip()
    if not text:
        return None
    return text, str(event.get("ts", ""))


def route_targets(routes: list[dict], envelope: dict) -> list[tuple[str, str, str]]:
    """``(project_id, text, ts)`` for every project this envelope belongs to.

    One shared connection carries every project on the token, so deciding whose
    boardroom an event is happens here rather than by which socket it arrived
    on. Each route applies its own trust check, so a project only ever sees its
    own chairman in its own boardroom.

    Two projects claiming one boardroom channel is a hand-edit rather than
    anything the product does — cloning drops the channel map — but if it
    happens both are woken. A duplicate is visible; a silent drop is the
    failure this module exists to not have.
    """
    targets = []
    for route in routes:
        accepted = chairman_text(route, envelope)
        if accepted is not None:
            text, ts = accepted
            targets.append((route["project_id"], text, ts))
    return targets


def _deliver(project_id: str, text: str, ts: str) -> None:
    with db_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            return
        post_boardroom_from_chairman(session, project, text, slack_ts=ts or None)
    log.info("socket-mode boardroom message delivered for %s", project_id)


async def _open_socket_url(app_token: str) -> str:
    async with httpx.AsyncClient(timeout=OPEN_TIMEOUT_S) as http:
        response = await http.post(
            "https://slack.com/api/apps.connections.open",
            headers={"Authorization": f"Bearer {app_token}"},
        )
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"apps.connections.open failed: {payload.get('error')}")
    return str(payload["url"])


async def _token_socket(app_token: str, route_table: dict[str, list[dict]]) -> None:
    """One app token's connection, forever: connect, ack, route, reconnect.

    Routes are read out of ``route_table`` per event rather than captured when
    the task starts. That is the difference between a shared workspace working
    and nearly working: projects are provisioned while others are mid-
    conversation, and rebuilding the connection every time one appeared would
    blink the inbound door shut for every project on the token. The connection
    depends on the token; the routes are only data it reads.
    """
    import websockets  # shipped by uvicorn[standard]; imported lazily for tests

    while True:
        try:
            url = await _open_socket_url(app_token)
            async with websockets.connect(url, max_size=2 ** 20) as ws:
                log.info("socket mode connected for %s",
                         _listening(route_table, app_token))
                async for raw in ws:
                    envelope = json.loads(raw)
                    if envelope.get("envelope_id"):
                        await ws.send(json.dumps({"envelope_id": envelope["envelope_id"]}))
                    if envelope.get("type") == "disconnect":
                        break  # Slack rotates connections; reconnect fresh
                    routes = route_table.get(app_token, ())
                    for project_id, text, ts in route_targets(routes, envelope):
                        await asyncio.to_thread(_deliver, project_id, text, ts)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("socket mode connection for %s dropped; reconnecting",
                        _listening(route_table, app_token), exc_info=True)
            await asyncio.sleep(RECONNECT_DELAY_S)


def _listening(route_table: dict[str, list[dict]], app_token: str) -> str:
    return ", ".join(r["project_id"] for r in route_table.get(app_token, ())) or "no projects"


async def _manager() -> None:
    """Start/stop a socket per app token as config changes. Config is re-read
    every tick, so enabling socket mode in Settings needs no restart.

    A token gaining or losing a project only rewrites the shared route table —
    the connection is left alone. Only a token that disappears entirely (Slack
    switched off for the last project using it, or the app token rotated) costs
    a reconnect.
    """
    tasks: dict[str, asyncio.Task] = {}
    route_table: dict[str, list[dict]] = {}
    while True:
        try:
            configs = await asyncio.to_thread(socket_configs)
            for token in list(tasks):
                if token not in configs:
                    tasks.pop(token).cancel()
                    route_table.pop(token, None)
            route_table.update(configs)  # live sockets read this on the next event
            for token in configs:
                if token not in tasks:
                    tasks[token] = asyncio.create_task(
                        _token_socket(token, route_table))
        except Exception:
            log.warning("socket-mode manager tick failed", exc_info=True)
        await asyncio.sleep(CONFIG_POLL_S)


def socket_mode_loop() -> None:  # pragma: no cover - thin thread entrypoint
    asyncio.run(_manager())


def fast_mirror_loop() -> None:  # pragma: no cover - thin thread entrypoint
    """Chat-grade outbound latency: a bus reply should appear in Slack in a
    couple of seconds, not on the janitor's enforcement cadence."""
    from yaaf.server.slack.bridge import mirror_once

    while True:
        try:
            with db_session() as session:
                mirror_once(session)
        except Exception:
            log.warning("fast mirror tick failed", exc_info=True)
        time.sleep(2.0)
