"""The per-project Slack bridge: mirror the bus out, let the chairman in.

The Postgres bus is ALWAYS canonical. Slack is a viewport onto it, plus exactly
one inbound path — the chairman speaking in the boardroom (the webhook in
:mod:`yaaf.server.api.webhooks`). Nothing here may block or corrupt the bus:
every Slack call is individually best-effort, an unmirrored message simply
keeps ``slack_ts`` NULL and waits for the next tick, and one misconfigured
project never stops another from mirroring.

Enabled per project through the ordinary config merge
(``PATCH /admin/projects/{id}``); the shape of ``project.config["slack"]``:

    {
      "bot_token_secret": "<Secret name: the xoxb- bot token>",
      "signing_secret": "<Secret name: the Slack app signing secret>",
      "chairman_slack_user": "U…",   # the only Slack user the inbound path trusts
      "channel_map": {
        "boardroom": "C…",
        "leadership": "C…",
        "team:<team_id>": "C…"
      }
    }

Both secrets are ordinary envelope-encrypted Secret rows (``POST
/admin/secrets``) at PROJECT scope, named here and decrypted server-side only.
Their ``role_scopes`` stay empty, so no agent sandbox ever holds the bot token:
an agent cannot speak to Slack except by writing to the bus and letting the
mirror carry it — which is exactly the comms policy, enforced by key custody.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.crypto import decrypt
from yaaf.core.db import notify
from yaaf.core.models import (
    Agent,
    AgentKind,
    Channel,
    ChannelKind,
    Message,
    Project,
    Secret,
)
from yaaf.server.api.agent import WAKE_CHANNEL

log = logging.getLogger("yaaf.slack")

# Per project per tick. A backlog flush (first enable, long Slack outage) must
# not stall the janitor's enforcement work; the remainder goes next tick.
MIRROR_BATCH = 50

POST_TIMEOUT_S = 10.0

# Slack's documented replay window for signed requests.
SIGNATURE_WINDOW_S = 300


# ── secrets ─────────────────────────────────────────────────────────────────────


def project_secret(session: Session, project_id: str, name: str | None) -> str | None:
    """Decrypt a project-level secret by the name the slack config points at.
    Missing or undecryptable returns None — the caller degrades (mirror skips,
    webhook refuses) rather than raising into an enforcement tick."""
    if not name:
        return None
    row = session.execute(
        select(Secret).where(Secret.project_id == project_id, Secret.name == name)
    ).scalar_one_or_none()
    if row is None:
        return None
    try:
        return decrypt(row.ciphertext)
    except Exception:
        log.warning("secret %r for project %s failed to decrypt", name, project_id)
        return None


# ── outbound mirror ─────────────────────────────────────────────────────────────


def _post_message(token: str, channel: str, text: str) -> str:
    """One chat.postMessage call; returns Slack's ts. Module-level so tests
    monkeypatch the wire, not the logic."""
    with httpx.Client(timeout=POST_TIMEOUT_S) as http:
        response = http.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": channel, "text": text},
        )
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(
            f"chat.postMessage failed: {payload.get('error') or response.status_code}"
        )
    return str(payload["ts"])


def _channel_key(channel: Channel) -> str:
    """The channel_map key for a bus channel: its kind, or team:<team_id>."""
    if channel.kind == ChannelKind.TEAM:
        return f"team:{channel.team_id}"
    return str(channel.kind)


def _author_display(session: Session, message: Message) -> str:
    """The prefix Slack readers see. The chairman is the only human on the bus;
    agents show as "CEO" or their given name."""
    if message.sender_user_id is not None:
        return "Chair"
    if message.sender_agent_id is not None:
        agent = session.get(Agent, message.sender_agent_id)
        if agent is not None:
            return "CEO" if agent.kind == AgentKind.CEO else agent.name
    return "yaaf"


def mirror_once(session: Session) -> int:
    """Post every not-yet-mirrored bus message in a mapped channel to Slack and
    stamp its slack_ts. Returns how many were posted. Failure anywhere leaves
    slack_ts NULL for the next tick — the bus never waits on Slack."""
    posted = 0
    for project in session.execute(select(Project)).scalars():
        slack_cfg = (project.config or {}).get("slack") or {}
        if not slack_cfg.get("channel_map"):
            continue
        # One bad project (revoked token, deleted secret) never blocks another.
        try:
            posted += _mirror_project(session, project, slack_cfg)
        except Exception:
            log.warning("slack mirror failed for project %s; the bus is canonical "
                        "and untouched — retrying next tick", project.slug, exc_info=True)
    return posted


def _mirror_project(session: Session, project: Project, slack_cfg: dict) -> int:
    token = project_secret(session, project.id, slack_cfg.get("bot_token_secret"))
    if token is None:
        log.warning("project %s has a slack channel_map but no usable bot token; "
                    "mirroring nothing", project.slug)
        return 0
    channel_map: dict = slack_cfg.get("channel_map") or {}
    targets = {
        channel.id: channel_map[_channel_key(channel)]
        for channel in session.execute(
            select(Channel).where(Channel.project_id == project.id)
        ).scalars()
        if channel_map.get(_channel_key(channel))
    }
    if not targets:
        return 0
    # Oldest first so Slack reads in bus order. No frozen_at filter: a frozen
    # thread refuses new *posts*, but what was said is still said — mirroring
    # is visibility, not enforcement.
    pending = session.execute(
        select(Message)
        .where(
            Message.project_id == project.id,
            Message.channel_id.in_(targets),
            Message.slack_ts.is_(None),
        )
        .order_by(Message.created_at)
        .limit(MIRROR_BATCH)
    ).scalars()
    posted = 0
    for message in pending:
        text = f"{_author_display(session, message)}: {message.body}"
        try:
            message.slack_ts = _post_message(token, targets[message.channel_id], text)
            posted += 1
        except Exception:
            # slack_ts stays NULL, so this exact message is retried next tick;
            # later messages still get their chance this tick.
            log.warning("mirror of message %s to %s failed; will retry",
                        message.id, targets[message.channel_id], exc_info=True)
    return posted


# ── inbound: signature + the chairman's boardroom path ──────────────────────────


def verify_slack_signature(signing_secret: str, timestamp: str | None,
                           body: bytes, signature: str | None) -> bool:
    """Slack's v0 signing scheme: HMAC-SHA256 of ``v0:<timestamp>:<body>``
    under the app's signing secret, compared in constant time. Deliveries older
    (or newer) than the ±5 minute window fail — a replayed request proves only
    that someone kept a valid one."""
    if not signing_secret or not timestamp or not signature:
        return False
    try:
        sent_at = int(float(timestamp))
    except ValueError:
        return False
    if abs(time.time() - sent_at) > SIGNATURE_WINDOW_S:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def post_boardroom_from_chairman(
    session: Session, project: Project, body: str, slack_ts: str | None = None
) -> Message:
    """Create a boardroom message exactly as ``POST /admin/projects/{id}/boardroom``
    does — chairman sender, dispatcher wake — plus a slack_ts stamp so
    mirror_once never echoes an inbound Slack message back to Slack. The logic
    is duplicated here rather than imported because the admin handler couples
    it to its route body, and this module must not depend on the admin router."""
    channel = session.execute(
        select(Channel).where(
            Channel.project_id == project.id, Channel.kind == ChannelKind.BOARDROOM
        )
    ).scalar_one()
    message = Message(
        id=ids.new_id(ids.MESSAGE),
        project_id=project.id,
        channel_id=channel.id,
        sender_user_id=project.chairman_user_id,
        body=body,
        slack_ts=slack_ts,
    )
    session.add(message)
    notify(session, WAKE_CHANNEL, project.id)
    return message
