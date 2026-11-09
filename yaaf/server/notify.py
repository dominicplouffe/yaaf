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
and that — filtered by his `mail_when` rule — is what decides the inbox:

* `Attention.ALARM` — the project stopped, or is about to. Never capped when it
  mails, because the failure mode of a missed alarm is a project sitting dead
  for a week. Whether it mails at all is his: the default `mail_when` is the
  CEO waiting on him and nothing else, because that is what he asked for.
* `Attention.ASK` — the chairman's CEO is waiting on him: a decision, a
  question, a need, a heads-up he asked for. Spends against the daily budget,
  and — when it names the row it waits on — is held for `mail_grace_minutes`
  first, so a request he answers in the app never becomes mail at all.
* `Attention.ACTION` — something the *platform* is waiting on him for: a status
  meeting, a verdict, a delivery ready to close. Spends against the budget.
* `Attention.BRIEF` — the daily letter. Always mails, when he set a time for it. It is the digest that makes
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
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from enum import StrEnum
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.inbox import (  # noqa: F401 — re-exported: the mail links these rows
    commitment_item,
    frozen_item,
    frozen_mail_item,
    scope_item,
    signoff_item,
    verdict_item,
)
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
    #: His CEO is waiting on him. Capped, and held for the grace period first.
    ASK = "ask"
    #: The platform is waiting on him. Capped.
    ACTION = "action"
    #: The daily letter itself. Uncapped.
    BRIEF = "brief"
    #: A record of something already done. Never mails.
    FYI = "fyi"


#: The classes that reach the inbox at all.
MAILED = frozenset({Attention.ALARM, Attention.ASK, Attention.ACTION, Attention.BRIEF})

#: The classes that spend against the daily budget. An alarm means the project
#: is not working; rationing those would be rationing the one message that says
#: so. The brief is one a day by construction and is where the held-back ones
#: are read anyway.
BUDGETED = frozenset({Attention.ASK, Attention.ACTION})

#: The audit action that records a spend. In the audit log rather than a column
#: because the budget is a cadence, and this repo already keeps nag cadences
#: there (`_nagged_within`) — it wants to be readable as a record either way.
MAIL_SPEND = "chairman.mailed"

#: And the mirror: what the budget held back, so a chairman who wonders why an
#: inbox went quiet can find the answer rather than infer it.
MAIL_HELD = "chairman.mail_held"

#: An ASK waiting out `mail_grace_minutes`, holding the mail it will become. In
#: the audit log for the same reason as the spend: it is a cadence, and "we
#: were going to tell him at 10:40 unless he answered first" is a record.
MAIL_GRACE = "chairman.mail_grace"

#: How a grace hold ended — sent, answered in time, or no longer wanted — keyed
#: on the hold's own id, so each hold settles exactly once.
MAIL_GRACE_SETTLED = "chairman.mail_grace_settled"

#: How long a hold is looked at. Longer than any approval or ask can stay open
#: by default; a hold older than this is one nobody will ever want mailed.
GRACE_LOOKBACK = timedelta(days=30)


#: `Message.slack_ts` for a boardroom line whose Slack delivery belongs to the
#: deliverable push: that push carries the file itself, so mirroring the line
#: as text too would announce the same deliverable twice in one channel.
SLACK_VIA_DELIVERABLE = "via-deliverable"


def inbox_url(item_key: str, tab: str | None = None) -> str | None:
    """The one Inbox row, as a link — when this install knows its
    browser-facing origin. Never the API's own origin: inside compose that is
    `http://api:8080`, a link nobody's browser can open. `tab` for a row that
    is not on Decide (a heads-up is noted on Acknowledge)."""
    origin = (settings().public_base_url or "").rstrip("/")
    on = f"tab={quote(tab, safe='')}&" if tab else ""
    return f"{origin}/app/#/inbox?{on}item={quote(item_key, safe='')}" if origin else None


# The Inbox's keys for the rows that are not one database row are spelled once,
# in yaaf.core.inbox — cheap to import, for `yaaf signal` — and re-exported
# here for the mail that links them.


def inbox_link(item_key: str, label: str) -> str:
    """`project_link` for a thing waiting on him: a last line opening that one
    Inbox row, or nothing when this install has no browser-facing origin.

    It opens the signed-in dashboard and decides nothing by being opened. A
    link that approved on a GET would be a decision anybody holding the email —
    a forward, a mail scanner prefetching links — could take for him.
    """
    url = inbox_url(item_key)
    return f"\n\n{label}: {url}" if url else ""


def project_link(project_id: str, page: str = "", label: str = "Open it") -> str:
    """A last line linking an alarm to the page that fixes it, or nothing when
    this install has no browser-facing origin. An alarm that names the fix
    ("reconnect billing in Settings") still made the reader find the project,
    then the tab, then the card — on the morning it stopped."""
    origin = (settings().public_base_url or "").rstrip("/")
    if not origin:
        return ""
    return f"\n\n{label}: {origin}/app/#/projects/{project_id}{'/' + page if page else ''}"


def inbox_line(item_key: str) -> str:
    """Where to decide it, as the last line of a notice."""
    url = inbox_url(item_key)
    return f"Decide it in your Inbox: {url}" if url else "Decide it in your Inbox."


def boardroom_line(session: Session, project_id: str, body: str,
                   *, slack_ts: str | None = None) -> None:
    """A platform-authored boardroom line: no sender, so it spends no reporting
    cadence, mirrors to Slack through the bridge, and — because the dispatcher
    skips sender-less lines — never wakes an agent. The one channel that reaches
    the chairman wherever he is, for free.

    Email is not that channel. A chairman who is mid-conversation in the
    boardroom does not see an SMTP notice, and the one thing the platform had to
    tell him about a finished project went only there.

    `slack_ts` pre-stamps the line for the one case where another path owns its
    Slack delivery (:data:`SLACK_VIA_DELIVERABLE`).
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
        slack_ts=slack_ts,
    ))


def _rules(session: Session, project: Project):
    """The project's constitution, or the schema defaults when it has none.

    Failure to read it is not a licence to flood: an unreadable constitution
    falls back to what a new project gets, which is the same answer every rule
    in this module would have given."""
    from yaaf.core.constitution import ConstitutionDoc, effective

    try:
        return effective(session, project.id)
    except LookupError:
        return ConstitutionDoc()


def wants_mail(session: Session, project: Project, attention: Attention) -> bool:
    """Whether the chairman's `mail_when` lets this class into his inbox.

    The brief is outside it: it has its own switch, and a chairman who set a
    time for it already said yes to that email."""
    from yaaf.core.constitution import MAIL_WHEN

    if attention is Attention.BRIEF:
        return True
    if attention not in MAILED:
        return False
    return str(attention) in MAIL_WHEN.get(_rules(session, project).mail_when, frozenset())


def _mail_budget(session: Session, project: Project) -> int | None:
    """The project's daily ration of chairman email, or None for unlimited.

    Read from the constitution rather than a module constant because it is a
    rule about what the platform may do to a human, and every one of those in
    this system is his to set and his to turn off.
    """
    limit = _rules(session, project).mail_max_per_day
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


# ── the grace period: answered in the app, never mailed ────────────────────────

#: The rows an ASK can wait on, and how each says it is still waiting. Three
#: shapes, because the CEO has three ways to need him: an approval he decides,
#: a question he answers, a heads-up he marks noted. Spelled once, here, so the
#: hold and the janitor that settles it cannot disagree about "still open".
def _open_row(session: Session, kind: str, row_id: str):
    """The row an ASK waits on, if it is still waiting; else None."""
    from yaaf.core.models import Approval, ApprovalStatus, Ask, AskStatus, HeadsUp

    if kind == "approval":
        row = session.get(Approval, row_id)
        return row if row is not None and row.status == ApprovalStatus.PENDING else None
    if kind == "ask":
        row = session.get(Ask, row_id)
        return row if row is not None and row.status == AskStatus.OPEN else None
    if kind == "heads_up":
        row = session.get(HeadsUp, row_id)
        return row if row is not None and row.acknowledged_at is None else None
    raise ValueError(f"an ask cannot wait on a {kind!r}")


def _inbox_key(kind: str, row_id: str) -> str:
    """The Inbox row's key, as web/src/pages/Inbox.tsx spells it."""
    return f"hu-{row_id}" if kind == "heads_up" else row_id


def _snoozed(session: Session, project: Project, kind: str, row_id: str) -> bool:
    """He snoozed it: he has seen it and said "not now", which is the one
    thing the email was going to make sure of."""
    from yaaf.core.models import Snooze

    return session.execute(
        select(func.count(Snooze.id)).where(
            Snooze.user_id == project.chairman_user_id,
            Snooze.item_key == _inbox_key(kind, row_id))
    ).scalar_one() > 0


def _held(session: Session, project: Project, row_id: str) -> bool:
    return session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.project_id == project.id,
            AuditLog.action == MAIL_GRACE,
            AuditLog.entity_id == row_id)
    ).scalar_one() > 0


def _grace_ends(session: Session, project: Project, row) -> datetime:
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return created + timedelta(minutes=_rules(session, project).mail_grace_minutes)


def send_mail_after_grace(session: Session, project: Project) -> int:
    """Mail every held ASK whose grace ran out with the thing still open.

    The other half of `chairman_notify`'s hold. Run by the janitor, so the
    clock is the janitor's tick — a minute or so late is the price of not
    keeping a timer per request. Every hold settles exactly once, and says
    how, so "why did I never get mail about that?" has an answer on record.
    """
    cfg = settings()
    now = utcnow()
    holds = session.execute(
        select(AuditLog).where(
            AuditLog.project_id == project.id,
            AuditLog.action == MAIL_GRACE,
            AuditLog.at >= now - GRACE_LOOKBACK,
        ).order_by(AuditLog.at)
    ).scalars().all()
    if not holds:
        return 0
    settled = set(session.execute(
        select(AuditLog.entity_id).where(
            AuditLog.project_id == project.id,
            AuditLog.action == MAIL_GRACE_SETTLED,
            AuditLog.entity_id.in_([h.id for h in holds]),
        )
    ).scalars())
    chairman = session.get(User, project.chairman_user_id)
    sent = 0
    for hold in holds:
        if hold.id in settled:
            continue
        kind, row_id = hold.entity, hold.entity_id
        row = _open_row(session, kind, row_id)
        if row is None:
            outcome = "answered"
        elif _snoozed(session, project, kind, row_id):
            outcome = "snoozed"
        elif now < _grace_ends(session, project, row):
            continue
        elif not (cfg.smtp_host and chairman is not None and chairman.email
                  and wants_mail(session, project, Attention.ASK)):
            outcome = "not wanted"
        elif not _spend_mail_budget(session, project, Attention.ASK, hold.detail["subject"]):
            outcome = "over the daily limit"
        else:
            _deliver(project.slug, email=chairman.email,
                     subject=hold.detail["subject"], body=hold.detail["body"])
            outcome = "sent"
            sent += 1
        session.add(AuditLog(
            id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:notify",
            action=MAIL_GRACE_SETTLED, entity="audit", entity_id=hold.id,
            detail={"outcome": outcome, "subject": hold.detail["subject"][:200],
                    "waits_on": {"kind": kind, "id": row_id}},
        ))
    return sent


def chairman_notify(session: Session, project: Project, subject: str, body: str,
                    *, attention: Attention, kind: str | None = None,
                    data: dict | None = None,
                    waits_on: tuple[str, str] | None = None) -> None:
    """Send subject/body to the project's chairman via every configured channel.

    `attention` says how much of the chairman is being asked for (see the module
    docstring) and therefore whether this becomes an email. It has no default on
    purpose: the next person to add a notice has to decide which of the four it
    is, and a default would have let them decide by not thinking about it —
    which is how the inbox got here. `tests/selftest_invariants/
    test_chairman_mail.py` refuses a call site that omits it.

    `waits_on` names the row an ASK is waiting on — ("approval", id), ("ask",
    id) or ("heads_up", id). With it, the email waits out the chairman's
    `mail_grace_minutes` from the moment that row was filed, and is sent only if
    the row is still open then (`send_mail_after_grace`). A chase about a row
    filed days ago is past its grace already and goes at once. The webhook is
    never held: a machine receiver is not the party being spared.

    Delivery happens on a fire-and-forget thread: SMTP and webhook I/O carry
    15s timeouts each, and several agent request handlers call this with a DB
    transaction open — a slow mail host must never hold row locks or pin a
    request worker. Everything the thread needs is resolved from the session
    BEFORE it starts; the thread itself never touches the database.

    The chairman alone, not the co-chairs, deliberately for now: the inbox
    ration below is one person's, and splitting or multiplying it is a design
    of its own. Co-chairs read the same decisions on the
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
    email = None
    if cfg.smtp_host and address and wants_mail(session, project, attention):
        row = (_open_row(session, *waits_on)
               if attention is Attention.ASK and waits_on else None)
        if row is not None and utcnow() < _grace_ends(session, project, row):
            # One hold per row. A short-fused approval can draw its expiry
            # chase inside the grace too, and two holds would be two emails
            # about one decision the moment the grace ran out.
            if not _held(session, project, waits_on[1]):
                session.add(AuditLog(
                    id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:notify",
                    action=MAIL_GRACE, entity=waits_on[0], entity_id=waits_on[1],
                    detail={"subject": f"[yaaf/{project.slug}] {subject}", "body": body},
                ))
        elif _spend_mail_budget(session, project, attention, subject):
            email = address
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
    _deliver(slug, email=email, subject=f"[yaaf/{slug}] {subject}", body=body,
             hook=hook, hook_subject=subject, secret=secret, project_id=project.id,
             kind=kind, data=data)


def _deliver(slug: str, *, email: str | None, subject: str, body: str,
             hook: str | None = None, hook_subject: str | None = None,
             secret: str | None = None, project_id: str | None = None,
             kind: str | None = None, data: dict | None = None) -> None:
    """Both transports, on a thread that never touches the database."""
    cfg = settings()

    def _run() -> None:
        try:
            if cfg.smtp_host and email:
                _send_email(email, subject, body)
            if hook:
                _send_webhook(hook, slug, hook_subject or subject, body, secret=secret,
                              project_id=project_id, kind=kind, data=data)
        except Exception:  # pragma: no cover - best-effort by contract
            log.warning("chairman notify delivery failed for %s", slug, exc_info=True)

    import threading

    threading.Thread(target=_run, daemon=True).start()


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
