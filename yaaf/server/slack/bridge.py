"""The per-project Slack bridge: mirror the bus out, let the chairman in.

The Postgres bus is ALWAYS canonical. Slack is a viewport onto it, plus exactly
one inbound path — the chairman speaking in the boardroom (the webhook in
:mod:`yaaf.server.api.webhooks`). Nothing here may block or corrupt the bus:
every Slack call is individually best-effort, an unmirrored message simply
keeps ``slack_ts`` NULL and waits for the next tick, and one misconfigured
project never stops another from mirroring.

Where a project's tokens come from is :mod:`yaaf.server.slack.settings`: by
default the app-wide Slack connection configured once in Settings, overridden field by
field by ``project.integrations["slack"]``:

    {
      "enabled": true,               # false switches Slack off for this project
      "bot_token_secret": "<Secret name: the xoxb- bot token>",   # optional
      "signing_secret": "<Secret name: the Slack app signing secret>",  # optional
      "chairman_slack_user": "U…",   # the only Slack user the inbound path trusts
      "channel_map": {               # always the project's own
        "boardroom": "C…",
        "leadership": "C…",
        "team:<team_id>": "C…"
      }
    }

Every token is an ordinary envelope-encrypted Secret row — at platform scope
(``project_id IS NULL``) for the shared connection, or at project scope when a
project overrides one — named there and decrypted server-side only. Their
``role_scopes`` stay empty, so no agent sandbox ever holds the bot token: an
agent cannot speak to Slack except by writing to the bus and letting the mirror
carry it — which is exactly the comms policy, enforced by key custody.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:  # the resolver imports this module for project_secret
    from yaaf.server.slack.settings import SlackSettings

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


#: Slack's upload flow tops out well above this; the artifact route caps a
#: deliverable at 5 MB, so anything larger cannot exist. Kept as its own name
#: so the fallback below has something to say when it trips.
MAX_SLACK_UPLOAD_BYTES = 5 * 1024 * 1024


def _upload_file(token: str, channel: str, filename: str, data: bytes,
                 comment: str) -> str:
    """Put the actual file in the room, via Slack's three-step external upload.

    `files.upload` is retired; the live flow is getUploadURLExternal → PUT →
    completeUploadExternal, and it needs the `files:write` scope. An app
    without it gets `missing_scope` here, which is a perfectly ordinary state
    for an install that was only ever wired for messages — the caller falls
    back to posting the summary rather than treating it as breakage.
    """
    with httpx.Client(timeout=30) as http:
        ticket = http.get(
            "https://slack.com/api/files.getUploadURLExternal",
            params={"filename": filename, "length": str(len(data))},
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        if not ticket.get("ok"):
            raise RuntimeError(f"getUploadURLExternal failed: {ticket.get('error')}")

        upload = http.post(ticket["upload_url"], content=data)
        if upload.status_code >= 400:
            raise RuntimeError(f"upload PUT failed: {upload.status_code}")

        done = http.post(
            "https://slack.com/api/files.completeUploadExternal",
            data={
                "files": json.dumps([{"id": ticket["file_id"], "title": filename}]),
                "channel_id": channel,
                "initial_comment": comment,
            },
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        if not done.get("ok"):
            raise RuntimeError(f"completeUploadExternal failed: {done.get('error')}")
    files = done.get("files") or [{}]
    # completeUploadExternal returns the file, not the message; the share ts
    # lives under the channel it landed in. Any truthy marker will do — this
    # column only ever answers "delivered?".
    return str(files[0].get("id") or done.get("ts") or "uploaded")


def _artifact_blurb(session: Session, artifact) -> str:
    """What this deliverable is, in the chairman's terms rather than the
    store's: what it is called, how big, and what it was FOR."""
    from yaaf.core.models import Requirement

    keys = []
    for requirement_id in artifact.requirement_ids or []:
        requirement = session.get(Requirement, requirement_id)
        if requirement is not None:
            keys.append(requirement.key)
    size = f"{artifact.size_bytes / 1024:.0f} KB" if artifact.size_bytes >= 1024 \
        else f"{artifact.size_bytes} B"
    serves = f" — {', '.join(keys)}" if keys else ""
    version = f" (v{artifact.version})" if artifact.version > 1 else ""
    return f"Deliverable: *{artifact.name}*{version}{serves} · {size}"


def _mirror_artifacts(session: Session, project: Project,
                      effective: SlackSettings) -> int:
    """Push published deliverables into the boardroom, newest last.

    The shelf is a screen the chairman has to remember to visit, and the record
    of how that goes is a run of messages asking where the work went — while
    the work sat published the whole time. Slack is where he already is.

    The file itself is the point ("can you push to me every morning at 8:30
    instead of me going to look for a file"), so the upload is tried first and
    a summary is posted when it cannot be. Either way the row is stamped: a
    deliverable is announced exactly once.
    """
    from yaaf.core.artifacts import store
    from yaaf.core.models import Artifact

    channel = effective.channel_map.get(str(ChannelKind.BOARDROOM))
    token = effective.bot_token
    if not channel or not token:
        return 0
    pending = session.execute(
        select(Artifact)
        .where(Artifact.project_id == project.id, Artifact.slack_ts.is_(None))
        .order_by(Artifact.published_at)
        .limit(MIRROR_BATCH)
    ).scalars()
    delivered = 0
    for artifact in pending:
        blurb = _artifact_blurb(session, artifact)
        try:
            data = None
            if artifact.size_bytes <= MAX_SLACK_UPLOAD_BYTES:
                try:
                    data = store().get_bytes(artifact.storage_key)
                except Exception:
                    log.warning("artifact %s is on the shelf but not in the store; "
                                "announcing it without the file", artifact.id)
            if data is not None:
                try:
                    artifact.slack_ts = _upload_file(
                        token, channel, artifact.name, data, blurb)
                    delivered += 1
                    continue
                except Exception as exc:
                    # An app wired only for messages has no files:write, which
                    # is a configuration fact and not a failure. Say what he is
                    # missing in the message rather than retrying forever.
                    log.info("slack upload unavailable for %s (%s); posting the "
                             "summary instead", artifact.name, exc)
                    blurb += ("\n_(Attach the file itself by adding the `files:write` "
                              "scope to the Slack app.)_")
            artifact.slack_ts = _post_message(token, channel, blurb)
            delivered += 1
        except Exception:
            # slack_ts stays NULL, so this exact artifact retries next tick;
            # later ones still get their chance now.
            log.warning("delivery of artifact %s to %s failed; will retry",
                        artifact.id, channel, exc_info=True)
    return delivered


def _channel_key(channel: Channel) -> str:
    """The channel_map key for a bus channel: its kind, or team:<team_id>."""
    if channel.kind == ChannelKind.TEAM:
        return f"team:{channel.team_id}"
    return str(channel.kind)


def _author_display(session: Session, message: Message) -> str:
    """The prefix Slack readers see: "Chair" for the chairman, a co-chair by
    name, and agents as "CEO" or their given name."""
    if message.sender_user_id is not None:
        from yaaf.server.authority import speaker

        project = session.get(Project, message.project_id)
        return (speaker(session, project, message.sender_user_id) if project else None) or "Chair"
    if message.sender_agent_id is not None:
        agent = session.get(Agent, message.sender_agent_id)
        if agent is not None:
            return "CEO" if agent.kind == AgentKind.CEO else agent.name
    return "yaaf"


def mirror_once(session: Session) -> int:
    """Post every not-yet-mirrored bus message in a mapped channel to Slack and
    stamp its slack_ts. Returns how many were posted. Failure anywhere leaves
    slack_ts NULL for the next tick — the bus never waits on Slack."""
    from yaaf.server.slack.settings import mapped_rooms, resolve

    posted = 0
    for project in session.execute(select(Project)).scalars():
        # Cheap first: a project with nothing mapped mirrors nothing, and this
        # loop ticks every two seconds over every project there is.
        if not mapped_rooms(project):
            continue
        effective = resolve(session, project)
        if effective is None or not effective.can_mirror:
            continue
        # One bad project (revoked token, deleted secret) never blocks another.
        try:
            posted += _mirror_project(session, project, effective)
        except Exception:
            log.warning("slack mirror failed for project %s; the bus is canonical "
                        "and untouched — retrying next tick", project.slug, exc_info=True)
        # Separately guarded: a deliverable that cannot be delivered must never
        # stop the messages, which are the older and more important promise.
        try:
            posted += _mirror_artifacts(session, project, effective)
        except Exception:
            log.warning("slack deliverable push failed for project %s; the shelf is "
                        "canonical and untouched — retrying next tick",
                        project.slug, exc_info=True)
    return posted


def _mirror_project(session: Session, project: Project,
                    effective: SlackSettings) -> int:
    token = effective.bot_token
    if token is None:
        log.warning("project %s has a slack channel_map but no usable bot token; "
                    "mirroring nothing", project.slug)
        return 0
    channel_map: dict = effective.channel_map
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
