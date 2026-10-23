"""One rendering of an approval, shared by every surface that shows one.

The observe listing, the chairman webhook and the janitor's nags all describe the
same row. A receiver that routes on the webhook's `approval` body must be able to
treat it exactly as a listing row, so there is one function and no second shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from yaaf.core.models import Approval

#: "Can I undo it?", said only where the platform knows the answer from the
#: kind alone. A spend is money that left; a capacity dial and a brief are
#: rules he can turn back. Every other kind depends on what was asked, and a
#: guess here would be the platform vouching for something it cannot see.
_UNDO = {
    "spend": "No — a purchase cannot be taken back once it is made.",
    "capacity": "Yes — lower it again any time in Settings → Rules in force.",
    "brief": "Yes — edit the brief in Settings, or confirm a new one.",
}


def approval_undo(kind: str) -> str | None:
    return _UNDO.get(kind)


def approval_row(a: Approval, *, outcome: dict | None = None,
                 held_note: dict | None = None) -> dict:
    return {
        "id": a.id, "kind": a.kind, "title": a.title, "body": a.body,
        "amount_usd": a.amount_usd, "status": a.status,
        "requested_by": a.requested_by_agent_id,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
        "decision_note": a.decision_note,
        # A TOOL_REQUEST's grant names the call the chairman is deciding on, and
        # spent_at is when the granted call was actually made. A client that
        # renders "you approved · applied at" needs both; neither is secret.
        "grant": a.grant,
        "spent_at": a.spent_at.isoformat() if a.spent_at else None,
        "disclosure": a.disclosure,
        # A brief's proposed text — the goal, the standing orders, what is retired.
        "brief": a.brief,
        "capacity": a.capacity,
        "undo": approval_undo(a.kind),
        # What the spent call was observed to do, filed by the agent afterwards.
        "outcome": outcome,
        # His note as held for the CEO's answer, and the answer once given.
        "held_note": held_note,
    }


def seat_row(agent, *, team=None, role_slug: str | None = None) -> dict:
    """One rendering of who a seat is, shared by every surface that names one.

    The context pack, the activity rollup, the observe listing and the roster
    card all describe the same person. They disagreed about it before this
    existed: the pack's roster carried a name and an id and no `status`, so a
    CEO could not see from its own context that one of its seats was paused —
    while `observe.org`, drawing the same roster for the chairman, carried it.

    Activity numbers are spread onto this by the caller that measured them; the
    identity is here so it cannot drift.
    """
    return {
        "agent_id": agent.id,
        "name": agent.name,
        "kind": agent.kind,
        "status": agent.status,
        "team_id": agent.team_id,
        "team": team.name if team is not None else None,
        # Employees inherit their team's pinned version, so the slug is the
        # team's; only the CEO carries one of its own.
        "role_slug": role_slug,
    }


def chairman_time(at: datetime, timezone: str | None) -> str:
    """One rendering of a moment for a person: "Thu 16 Oct, 3:00 pm EDT".

    Every clock he is held to — when a question stops waiting, when an approval
    decides itself — used to reach him as `2026-10-16T19:00+00:00`, or as
    "19:00 UTC" in an email. Both are correct and both make him do arithmetic
    before he can tell whether it is tonight. His constitution already names
    his timezone; the platform is the one that should do the sum.
    """
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    try:
        zone = ZoneInfo(timezone or "UTC")
    except Exception:  # pragma: no cover - the rule validates this on write
        zone = ZoneInfo("UTC")
    local = at.astimezone(zone)
    hour = local.hour % 12 or 12
    half = "am" if local.hour < 12 else "pm"
    return f"{local:%a} {local.day} {local:%b}, {hour}:{local:%M} {half} {local:%Z}"


def requirement_name(req) -> str:
    """What a person calls a requirement: the owner's title when the CEO wrote
    one, the statement when it did not.

    The statement is written for the checker and the key for agents and
    directives; `meta.title` is the one the CEO is told to write "for the person
    who hired you". It was stored, returned on the plan view, and rendered
    nowhere, so every email and screen named the work by its key and its
    checker's sentence instead.
    """
    title = ((req.meta or {}).get("title") or "").strip()
    return title or req.statement
