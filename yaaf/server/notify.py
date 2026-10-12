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

## What earns an email

Every notice used to become an email, which made the inbox the one surface in
this system with no theory of whose news is whose. The briefing has had one for
as long as it has existed — `LINE_OWNER` files each line against the autonomy
dial, so a chairman who set `scope_authority: ceo` reads "the CEO handled this
itself" instead of a management report. The mail path ignored that setting
entirely and shouted every delegated decision at him anyway. One briefing
window in this repo's own record (see `briefing._ordered`) held "20
deliverables published" and "the CEO exercised 26 decisions" — two lines in the
brief, and forty-six separate emails in the inbox.

So a notice now names how much of the chairman's attention it is asking for,
and that is what decides the inbox:

* `Attention.ALARM` — the project stopped, or is about to. Always mails, never
  capped, because the failure mode of a missed alarm is a project sitting dead
  for a week.
* `Attention.ACTION` — the chairman is himself the blocker: something is waiting on
  a decision only he can make. Mails, but spends against a daily budget.
* `Attention.BRIEF` — the daily letter. Always mails. It is the digest that makes
  the alarms legible, and capping the thing that summarises everything else
  would be exactly backwards.
* `Attention.FYI` — a record of something the platform or the CEO already did.
  Never mails. It is not dropped: the webhook still carries it, the boardroom
  and the audit log still hold it, and the daily brief already reports it in
  batched form. This is the class that was drowning the inbox.

The webhook is deliberately exempt from all of this. It is a machine receiver
that routes on `kind`, it is not the party who is drowning, and muting it would
silently break integrations that are counting on a complete stream.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import smtplib
import urllib.request
from datetime import timedelta
from email.message import EmailMessage
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.models import (
    AuditLog,
    Channel,
    ChannelKind,
    Message,
    Project,
    User,
    utcnow,
)

log = logging.getLogger("yaaf.notify")


class Attention(StrEnum):
    """How much of the chairman a notice is asking for, which is what decides
    whether it becomes an email.

    Named rather than boolean because the call site is where the judgement
    actually lives, and a bare `mail=False` at thirty-five call sites would
    have told the next reader nothing about why this one is quiet and that one
    is not.
    """

    #: The project stopped or is about to. Uncapped.
    ALARM = "alarm"
    #: Something is waiting on a decision only the chairman can make. Capped.
    ACTION = "action"
    #: The daily letter itself. Uncapped.
    BRIEF = "brief"
    #: A record of something already done. Never mails.
    FYI = "fyi"


#: The classes that reach the inbox at all.
MAILED = frozenset({Attention.ALARM, Attention.ACTION, Attention.BRIEF})

#: The classes that spend against the daily budget. An alarm means the project
#: is not working; rationing those would be rationing the one message that says
#: so. The brief is one a day by construction and is where the held-back ones
#: are read anyway.
BUDGETED = frozenset({Attention.ACTION})

#: The audit action that records a spend. In the audit log rather than a column
#: because the budget is a cadence, and this repo already keeps nag cadences
#: there (`_nagged_within`) — it wants to be readable as a record either way.
MAIL_SPEND = "chairman.mailed"

#: And the mirror: what the budget held back, so a chairman who wonders why an
#: inbox went quiet can find the answer rather than infer it.
MAIL_HELD = "chairman.mail_held"


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


def _mail_budget(session: Session, project: Project) -> int | None:
    """The project's daily ration of chairman email, or None for unlimited.

    Read from the constitution rather than a module constant because it is a
    rule about what the platform may do to a human, and every one of those in
    this system is his to set and his to turn off. Failure to read it is not a
    licence to flood: an unreadable constitution falls back to the schema
    default, which is the same number a new project gets.
    """
    from yaaf.core.constitution import ConstitutionDoc, effective

    try:
        limit = effective(session, project.id).mail_max_per_day
    except LookupError:
        limit = ConstitutionDoc.model_fields["mail_max_per_day"].default
    return None if limit <= 0 else limit


def _spend_mail_budget(session: Session, project: Project, attention: Attention,
                       subject: str) -> bool:
    """Whether this notice may take an inbox slot today, recording either way.

    The window is the rolling 24 hours rather than the calendar day, because a
    calendar reset is a cliff a busy project walks off at midnight: a chairman
    who has been chased five times by 23:00 would get five more before he woke
    up. Rolling means the ration refills the way he actually reads.

    Held is not dropped. The substance of every ACTION notice is a *wait* —
    scope to accept, a verdict to record, an approval to sign — and the daily
    brief already renders every one of those live, from state, in its "waiting
    on you" section. So the chairman loses the interrupt, never the news; the
    audit row is here so that even the interrupt is recoverable.
    """
    if attention not in BUDGETED:
        return True
    limit = _mail_budget(session, project)
    if limit is None:
        return True
    spent = session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.project_id == project.id,
            AuditLog.action == MAIL_SPEND,
            AuditLog.at >= utcnow() - timedelta(hours=24),
        )
    ).scalar_one()
    allowed = spent < limit
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:notify",
        action=MAIL_SPEND if allowed else MAIL_HELD, entity="project",
        entity_id=project.id,
        detail={"subject": subject[:200], "attention": str(attention), "limit": limit},
    ))
    if not allowed:
        log.info("held chairman mail for %s (%d/day spent): %s",
                 project.slug, limit, subject)
    return allowed


def chairman_notify(session: Session, project: Project, subject: str, body: str,
                    *, attention: Attention, kind: str | None = None,
                    data: dict | None = None) -> None:
    """Send subject/body to the project's chairman via every configured channel.

    `attention` says how much of the chairman is being asked for (see the module
    docstring) and therefore whether this becomes an email. It has no default on
    purpose: the next person to add a notice has to decide which of the four it
    is, and a default would have let them decide by not thinking about it —
    which is how the inbox got here. `tests/selftest_invariants/
    test_chairman_mail.py` refuses a call site that omits it.

    Delivery happens on a fire-and-forget thread: SMTP and webhook I/O carry
    15s timeouts each, and several agent request handlers call this with a DB
    transaction open — a slow mail host must never hold row locks or pin a
    request worker. Everything the thread needs is resolved from the session
    BEFORE it starts; the thread itself never touches the database.

    The chairman alone, not the co-chairs, deliberately for now: the inbox
    ration below is one person's, and splitting or multiplying it is a design
    of its own (docs/AUTH.md, "Later"). Co-chairs read the same decisions on the
    Desk, which lists every project they chair."""
    chairman = session.get(User, project.chairman_user_id)
    log.info("chairman notify [%s] (%s) %s: %s", project.slug, attention, subject, body)
    cfg = settings()
    # Resolved here, not in the thread — and gated here, not in the thread,
    # because the budget is a database read and the thread never touches it.
    #
    # The ration is only spent by a notice that could actually have interrupted
    # him. A deployment with no SMTP host, or a project whose chairman has no
    # address, is not sending anything to ration — charging it would fill the
    # audit log with mail nobody got and then hold back the first notice that
    # someone finally configured a mailbox for.
    address = chairman.email if chairman is not None else None
    email = (address if cfg.smtp_host and address and attention in MAILED
             and _spend_mail_budget(session, project, attention, subject) else None)
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
                _send_webhook(hook, slug, subject, body, secret=secret, project_id=project_id,
                              kind=kind, data=data)
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
                  *, secret: str | None = None, project_id: str | None = None,
                  kind: str | None = None, data: dict | None = None) -> None:
    """POST the notice, signed when the project provisioned a secret.

    The `text` field keeps the Slack-compatible shape the app-wide hook has
    always sent, so an existing receiver is unaffected. `project_id` is added
    for a receiver that has to route: a slug is the chairman's name for the
    project and can be edited, an id cannot. `kind` names the event for a
    receiver that routes on it rather than on the subject's wording, and
    `data` carries the row the notice is about (an `approval`, a
    `deliverable`, a `cycle`) in the same shape the observe API lists it.
    """
    envelope = {"project": project_slug, "subject": subject, "text": f"*{subject}*\n{body}"}
    if project_id:
        envelope["project_id"] = project_id
    if kind:
        envelope["kind"] = kind
    if data:
        envelope.update(data)
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
