"""Inbound evidence connectors: webhooks that produce platform-attested rows.

Most external evidence should arrive here, not through an agent's pen. These
endpoints carry no bearer token by design — GitHub and other senders cannot
hold yaaf credentials — but every post must carry an HMAC-SHA256 of its raw
body under a secret the chairman provisioned. An agent can never forge
platform_attested evidence this way because the webhook secret is never
injected into a run.

The Slack endpoint below is the one non-evidence inbound: the chairman
speaking in the mapped boardroom channel becomes a boardroom bus message
(see yaaf.server.slack.bridge for the config shape). Same signing discipline,
Slack's v0 scheme instead of a bare body HMAC.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import (
    AuditLog,
    Evidence,
    EvidenceKind,
    EvidenceProvenance,
    Message,
    Project,
    VerificationStatus,
    utcnow,
)
from yaaf.server.deps import db
from yaaf.server.slack.bridge import (
    post_boardroom_from_chairman,
    project_secret,
    verify_slack_signature,
)

router = APIRouter()

# Event -> the action that means "the outside world did something new".
# None means the event carries no action worth filtering (a fork fires once).
_HANDLED_GITHUB_EVENTS: dict[str, set[str] | None] = {
    "star": {"created"},
    "watch": {"started"},
    "fork": None,
    "issues": {"opened"},
    "pull_request": {"opened"},
    "issue_comment": {"created"},
}

# Where each event's html_url lives; anything else falls back to the repository.
_SOURCE_OBJECT = {
    "issues": "issue",
    "pull_request": "pull_request",
    "issue_comment": "comment",
    "fork": "forkee",
}


def _require_signature(raw: bytes, secret: str | None, provided: str | None,
                       prefix: str = "") -> None:
    """403 unless `provided` is the HMAC-SHA256 hex of the raw body under the
    project's webhook secret, computed exactly as the sender would."""
    if not secret:
        raise HTTPException(
            status_code=403,
            detail="This project has no webhook secret provisioned; the chairman "
                   "generates one at POST /admin/projects/{id}/webhook_secrets first.",
        )
    if not provided:
        raise HTTPException(
            status_code=403,
            detail="Missing signature header; an unsigned post is not evidence.",
        )
    expected = prefix + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided.strip()):
        raise HTTPException(
            status_code=403,
            detail="Signature does not match the body; a forged or corrupted "
                   "delivery attests nothing.",
        )


def _get_project(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="unknown project")
    return project


def _attest(session: Session, project: Project, description: str,
            source_url: str | None, occurred_at: datetime) -> Evidence:
    row = Evidence(
        id=ids.new_id(ids.EVIDENCE),
        project_id=project.id,
        created_by_agent_id=None,  # null is the connector's mark: no agent wrote this
        kind=EvidenceKind.EXTERNAL,
        provenance=EvidenceProvenance.PLATFORM_ATTESTED,
        description=description,
        source_url=source_url,
        # Attested rows skip the verifier: the signature already proved the
        # sender, and the platform — not an agent — wrote the row.
        verification=VerificationStatus.NOT_APPLICABLE,
        occurred_at=occurred_at,
    )
    session.add(row)
    return row


def _seen_delivery(session: Session, project_id: str, delivery: str) -> bool:
    """Dedupe by scanning the project's webhook audit rows in Python: JSON
    containment queries differ between SQLite and Postgres, and one project's
    webhook volume is thousands of rows, not millions — a filtered scan is
    fine at this scale, and a deliveries table earns its place only when it
    stops being true."""
    details = session.execute(
        select(AuditLog.detail).where(
            AuditLog.project_id == project_id, AuditLog.action == "webhook.github"
        )
    ).scalars()
    return any(detail.get("delivery") == delivery for detail in details)


@router.post("/github/{project_id}")
async def github_webhook(
    project_id: str, request: Request, session: Session = Depends(db)
) -> dict:
    project = _get_project(session, project_id)
    raw = await request.body()
    _require_signature(
        raw,
        (project.config or {}).get("github_webhook_secret"),
        request.headers.get("X-Hub-Signature-256"),
        prefix="sha256=",
    )
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="body is not JSON") from exc

    event = request.headers.get("X-GitHub-Event", "")
    if event not in _HANDLED_GITHUB_EVENTS:
        return {"ignored": f"unhandled event '{event}'"}
    actions = _HANDLED_GITHUB_EVENTS[event]
    action = payload.get("action")
    if actions is not None and action not in actions:
        return {"ignored": f"unhandled action '{action}'"}

    login = ((payload.get("sender") or {}).get("login") or "").strip()
    if not login:
        raise HTTPException(status_code=422, detail="payload names no sender.login")
    # The project's own account starring itself is a diary entry, not the
    # outside world acting — controlled accounts can never produce external
    # evidence, exactly like controlled domains in the verifier.
    if f"github:{login.lower()}" in {str(d).lower() for d in project.controlled_domains or []}:
        return {"ignored": "controlled account"}

    # GitHub retries deliveries; the delivery id is the idempotency key.
    delivery = request.headers.get("X-GitHub-Delivery")
    if delivery and _seen_delivery(session, project.id, delivery):
        return {"ignored": "duplicate delivery"}

    repo = (payload.get("repository") or {}).get("full_name") or "unknown repo"
    source = payload.get(_SOURCE_OBJECT.get(event, ""), None) or payload.get("repository") or {}
    row = _attest(
        session,
        project,
        description=f"github {event} by {login} on {repo}",
        source_url=source.get("html_url"),
        occurred_at=utcnow(),
    )
    session.add(
        AuditLog(
            id=ids.new_id(ids.AUDIT),
            project_id=project.id,
            actor="system:webhook",
            action="webhook.github",
            entity="evidence",
            entity_id=row.id,
            detail={"delivery": delivery, "event": event, "sender": login},
        )
    )
    return {"evidence_id": row.id}


# ── slack: the chairman's inbound path ──────────────────────────────────────────


def _slack_ignored(session: Session, project: Project, reason: str,
                   event: dict | None = None) -> dict:
    """Acknowledge-and-drop, on the record. Slack retries every non-2xx
    delivery, so refusing here would buy nothing but a retry storm — the drop
    still lands in the audit log with its reason."""
    detail: dict = {"reason": reason}
    if event:
        detail |= {"channel": event.get("channel"), "user": event.get("user")}
    session.add(
        AuditLog(
            id=ids.new_id(ids.AUDIT),
            project_id=project.id,
            actor="system:webhook",
            action="webhook.slack_ignored",
            entity="project",
            entity_id=project.id,
            detail=detail,
        )
    )
    return {"ignored": reason}


@router.post("/slack/{project_id}")
async def slack_webhook(
    project_id: str, request: Request, session: Session = Depends(db)
) -> dict:
    """Slack events in; only one thing comes out: the chairman, in the mapped
    boardroom channel, becomes a boardroom bus message — identical to posting
    through the admin API. Every other event is dropped. The bus stays
    canonical: Slack never writes anything the chairman could not."""
    project = _get_project(session, project_id)
    slack_cfg = (project.config or {}).get("slack") or {}
    raw = await request.body()
    signing_secret = project_secret(session, project.id, slack_cfg.get("signing_secret"))
    if signing_secret is None:
        raise HTTPException(
            status_code=403,
            detail="This project has no Slack signing secret configured; store one via "
                   "POST /admin/secrets and name it in project.config['slack'] first.",
        )
    if not verify_slack_signature(
        signing_secret,
        request.headers.get("X-Slack-Request-Timestamp"),
        raw,
        request.headers.get("X-Slack-Signature"),
    ):
        raise HTTPException(
            status_code=403,
            detail="Slack signature does not match the body; a forged or replayed "
                   "delivery speaks for no one.",
        )
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="body is not JSON") from exc

    # Slack's handshake when the events URL is (re)pointed here.
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}
    if payload.get("type") != "event_callback":
        return _slack_ignored(session, project, f"unhandled type '{payload.get('type')}'")

    event = payload.get("event") or {}
    if event.get("type") != "message":
        return _slack_ignored(session, project, f"unhandled event '{event.get('type')}'")
    if event.get("bot_id") or event.get("subtype"):
        # The mirror's own posts come straight back as bot-message events;
        # writing them to the bus would echo forever. Not audited: one row per
        # mirrored message would double the log and say nothing.
        return {"ignored": "bot or non-user message"}

    channel_map = slack_cfg.get("channel_map") or {}
    if event.get("channel") != channel_map.get("boardroom"):
        # Slack may deliver from any channel the bot sits in; only the
        # boardroom is an inbound door, and only for the chairman — everyone
        # else already has their channel: the bus.
        return _slack_ignored(session, project, "not the mapped boardroom channel", event)
    if not event.get("user") or event.get("user") != slack_cfg.get("chairman_slack_user"):
        return _slack_ignored(session, project, "not the chairman's Slack user", event)
    text = (event.get("text") or "").strip()
    if not text:
        return _slack_ignored(session, project, "empty message", event)

    # Slack redelivers when we answer slowly; the event ts is the idempotency key.
    ts = event.get("ts")
    if ts is not None and session.execute(
        select(Message.id).where(Message.project_id == project.id, Message.slack_ts == ts)
    ).first() is not None:
        return _slack_ignored(session, project, "duplicate delivery", event)

    message = post_boardroom_from_chairman(session, project, text, slack_ts=ts)
    session.add(
        AuditLog(
            id=ids.new_id(ids.AUDIT),
            project_id=project.id,
            actor="system:webhook",
            action="webhook.slack",
            entity="message",
            entity_id=message.id,
            detail={"ts": ts},
        )
    )
    return {"message_id": message.id}


class GenericEvidenceIn(BaseModel):
    """What a chairman-configured connector posts: a described observation."""

    description: str = Field(min_length=10)
    source_url: str | None = None
    occurred_at: datetime | None = None


@router.post("/generic/{project_id}")
async def generic_webhook(
    project_id: str, request: Request, session: Session = Depends(db)
) -> dict:
    project = _get_project(session, project_id)
    raw = await request.body()
    # Signature before validation: an unsigned caller learns nothing about the
    # schema, and only a signed body is worth parsing at all.
    _require_signature(
        raw,
        (project.config or {}).get("webhook_secret"),
        request.headers.get("X-Yaaf-Signature"),
    )
    try:
        body = GenericEvidenceIn.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    row = _attest(
        session,
        project,
        description=f"connector: {body.description}",
        source_url=body.source_url,
        occurred_at=body.occurred_at or utcnow(),
    )
    session.add(
        AuditLog(
            id=ids.new_id(ids.AUDIT),
            project_id=project.id,
            actor="system:webhook",
            action="webhook.generic",
            entity="evidence",
            entity_id=row.id,
            detail={"source_url": body.source_url},
        )
    )
    return {"evidence_id": row.id}
