"""Outbound chairman notification: email and/or webhook, best-effort.

Notices to *agents* ride the context pack; the chairman is a human who is not
polling a dashboard on vacation. This module is deliberately dumb transport:
whether a nag exists and when it fires is the janitor's (constitution-backed)
business — this only carries the message. Every path is best-effort and
swallowed: a broken SMTP server must never break an enforcement tick.

Configure with YAAF_SMTP_HOST (+ optional YAAF_SMTP_USERNAME/PASSWORD/FROM)
and/or YAAF_CHAIRMAN_WEBHOOK_URL (receives a JSON POST). Unconfigured = log only.
"""

from __future__ import annotations

import json
import logging
import smtplib
import urllib.request
from email.message import EmailMessage

from sqlalchemy.orm import Session

from yaaf.core.config import settings
from yaaf.core.models import Project, User

log = logging.getLogger("yaaf.notify")


def chairman_notify(session: Session, project: Project, subject: str, body: str) -> None:
    """Send subject/body to the project's chairman via every configured channel."""
    chairman = session.get(User, project.chairman_user_id)
    log.info("chairman notify [%s] %s: %s", project.slug, subject, body)
    cfg = settings()
    if cfg.smtp_host and chairman is not None:
        _send_email(chairman.email, f"[yaaf/{project.slug}] {subject}", body)
    if cfg.chairman_webhook_url:
        _send_webhook(cfg.chairman_webhook_url, project.slug, subject, body)


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


def _send_webhook(url: str, project_slug: str, subject: str, body: str) -> None:
    payload = json.dumps(
        {"project": project_slug, "subject": subject, "text": f"*{subject}*\n{body}"}
    ).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        log.warning("chairman webhook failed", exc_info=True)
