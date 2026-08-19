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

Enabled by naming an ``app_token_secret`` (the xapp- token, an ordinary
envelope-encrypted Secret) in ``project.integrations["slack"]`` next to the existing
bridge fields. No agent ever holds either Slack token.
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
from yaaf.server.slack.bridge import post_boardroom_from_chairman, project_secret

log = logging.getLogger("yaaf.slack.socket")

CONFIG_POLL_S = 15.0
RECONNECT_DELAY_S = 5.0
OPEN_TIMEOUT_S = 10.0


def socket_configs() -> dict[str, dict]:
    """project_id -> connection config, for every project that opted in."""
    out: dict[str, dict] = {}
    with db_session() as session:
        for project in session.execute(select(Project)).scalars():
            cfg = (project.integrations or {}).get("slack") or {}
            boardroom = (cfg.get("channel_map") or {}).get("boardroom")
            if not (cfg.get("app_token_secret") and boardroom
                    and cfg.get("chairman_slack_user")):
                continue
            token = project_secret(session, project.id, cfg["app_token_secret"])
            if token is None:
                log.warning("project %s names app_token_secret %r but it is unusable",
                            project.slug, cfg["app_token_secret"])
                continue
            out[project.id] = {
                "app_token": token,
                "channel": boardroom,
                "chairman": cfg["chairman_slack_user"],
            }
    return out


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


async def _project_socket(project_id: str, cfg: dict) -> None:
    """One project's connection, forever: connect, ack, deliver, reconnect."""
    import websockets  # shipped by uvicorn[standard]; imported lazily for tests

    while True:
        try:
            url = await _open_socket_url(cfg["app_token"])
            async with websockets.connect(url, max_size=2 ** 20) as ws:
                log.info("socket mode connected for %s", project_id)
                async for raw in ws:
                    envelope = json.loads(raw)
                    if envelope.get("envelope_id"):
                        await ws.send(json.dumps({"envelope_id": envelope["envelope_id"]}))
                    if envelope.get("type") == "disconnect":
                        break  # Slack rotates connections; reconnect fresh
                    accepted = chairman_text(cfg, envelope)
                    if accepted is not None:
                        text, ts = accepted
                        await asyncio.to_thread(_deliver, project_id, text, ts)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("socket mode connection for %s dropped; reconnecting",
                        project_id, exc_info=True)
            await asyncio.sleep(RECONNECT_DELAY_S)


async def _manager() -> None:
    """Start/stop per-project sockets as config changes. Config is re-read
    every tick, so enabling socket mode in Settings needs no restart."""
    tasks: dict[str, asyncio.Task] = {}
    snapshots: dict[str, dict] = {}
    while True:
        try:
            configs = await asyncio.to_thread(socket_configs)
            for project_id in list(tasks):
                if project_id not in configs or configs[project_id] != snapshots.get(project_id):
                    tasks.pop(project_id).cancel()
                    snapshots.pop(project_id, None)
            for project_id, cfg in configs.items():
                if project_id not in tasks:
                    tasks[project_id] = asyncio.create_task(
                        _project_socket(project_id, cfg))
                    snapshots[project_id] = cfg
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
