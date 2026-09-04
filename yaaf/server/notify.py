"""Outbound chairman notification: email and/or webhook, best-effort.

Notices to *agents* ride the context pack; the chairman is a human who is not
polling a dashboard on vacation. This module is deliberately dumb transport:
whether a nag exists and when it fires is the janitor's (constitution-backed)
business — this only carries the message. Every path is best-effort and
swallowed: a broken SMTP server must never break an enforcement tick.

Configure with YAAF_SMTP_HOST (+ optional YAAF_SMTP_USERNAME/PASSWORD/FROM)
and/or YAAF_CHAIRMAN_WEBHOOK_URL (receives a JSON POST). Unconfigured = log only.

A project may name its own webhook in `integrations.chairman_webhook_url`, which
takes precedence over the app-wide one. That exists because the app-wide setting
cannot say WHOSE notice it is carrying: with several projects on one deployment
the receiver gets a stream of messages and no way to route them. A project-scoped
URL also gets a signature, under `integrations.chairman_webhook_secret`, so the
receiver can tell a real delivery from anything else that can reach the address.

That secret is deliberately NOT the `webhook_secret` the inbound connectors use.
That one authenticates senders TO yaaf; this one authenticates yaaf to a
receiver. Different directions and different parties, so losing one must not
hand over the other.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import smtplib
import urllib.request
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.models import (
    Channel,
    ChannelKind,
    Message,
    Project,
    User,
)

log = logging.getLogger("yaaf.notify")


def boardroom_line(session: Session, project_id: str, body: str) -> None:
    """A platform-authored boardroom line: no sender, so it spends no reporting
    cadence, mirrors to Slack through the bridge, and — because the dispatcher
    skips sender-less lines — never wakes an agent. The one channel that reaches
    the chairman wherever he is, for free.

    Email is not that channel. A chairman who is mid-conversation in the
    boardroom does not see an SMTP notice, and the one thing the platform had to
    tell him about a finished project went only there.
    """
    boardroom = session.execute(
        select(Channel).where(Channel.project_id == project_id,
                              Channel.kind == ChannelKind.BOARDROOM)
    ).scalar_one_or_none()
    if boardroom is None:
        return
    session.add(Message(
        id=ids.new_id(ids.MESSAGE),
        project_id=project_id,
        channel_id=boardroom.id,
        body=body,
    ))


def chairman_notify(session: Session, project: Project, subject: str, body: str) -> None:
    """Send subject/body to the project's chairman via every configured channel.

    Delivery happens on a fire-and-forget thread: SMTP and webhook I/O carry
    15s timeouts each, and several agent request handlers call this with a DB
    transaction open — a slow mail host must never hold row locks or pin a
    request worker. Everything the thread needs is resolved from the session
    BEFORE it starts; the thread itself never touches the database."""
    chairman = session.get(User, project.chairman_user_id)
    log.info("chairman notify [%s] %s: %s", project.slug, subject, body)
    cfg = settings()
    email = chairman.email if chairman is not None else None
    slug = project.slug
    # Resolved here, not in the thread: the thread never touches the database.
    integrations = project.integrations or {}
    hook = integrations.get("chairman_webhook_url") or cfg.chairman_webhook_url
    # Only a project's own hook is signed. The app-wide one predates signing and
    # has no per-project secret to sign under, so adding a header there would be
    # a signature every receiver would have to ignore.
    secret = (
        integrations.get("chairman_webhook_secret")
        if integrations.get("chairman_webhook_url")
        else None
    )
    project_id = project.id

    def _deliver() -> None:
        try:
            if cfg.smtp_host and email:
                _send_email(email, f"[yaaf/{slug}] {subject}", body)
            if hook:
                _send_webhook(hook, slug, subject, body, secret=secret, project_id=project_id)
        except Exception:  # pragma: no cover - best-effort by contract
            log.warning("chairman notify delivery failed for %s", slug, exc_info=True)

    import threading

    threading.Thread(target=_deliver, daemon=True).start()


def _send_email(to: str, subject: str, body: str) -> None:
    cfg = settings()
    msg = EmailMessage()
    msg["From"] = cfg.smtp_from or (cfg.smtp_username or f"yaaf@{cfg.smtp_host}")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if cfg.smtp_username:
                smtp.login(cfg.smtp_username, cfg.smtp_password)
            smtp.send_message(msg)
    except Exception:
        log.warning("chairman email to %s failed", to, exc_info=True)


def _send_webhook(url: str, project_slug: str, subject: str, body: str,
                  *, secret: str | None = None, project_id: str | None = None) -> None:
    """POST the notice, signed when the project provisioned a secret.

    The `text` field keeps the Slack-compatible shape the app-wide hook has
    always sent, so an existing receiver is unaffected. `project_id` is added
    for a receiver that has to route: a slug is the chairman's name for the
    project and can be edited, an id cannot.
    """
    envelope = {"project": project_slug, "subject": subject, "text": f"*{subject}*\n{body}"}
    if project_id:
        envelope["project_id"] = project_id
    payload = json.dumps(envelope).encode()
    headers = {"Content-Type": "application/json"}
    if secret:
        # Over the raw body, hex, `sha256=` prefixed — computed exactly as
        # `_require_signature` verifies an inbound one, so one convention
        # serves both directions and a receiver can reuse yaaf's own wording.
        headers["X-Yaaf-Signature"] = "sha256=" + hmac.new(
            secret.encode(), payload, hashlib.sha256
        ).hexdigest()
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        log.warning("chairman webhook failed", exc_info=True)
