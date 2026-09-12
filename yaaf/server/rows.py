"""One rendering of an approval, shared by every surface that shows one.

The observe listing, the chairman webhook and the janitor's nags all describe the
same row. A receiver that routes on the webhook's `approval` body must be able to
treat it exactly as a listing row, so there is one function and no second shape.
"""

from __future__ import annotations

from yaaf.core.models import Approval


def approval_row(a: Approval, *, outcome: dict | None = None) -> dict:
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
        # What the spent call was observed to do, filed by the agent afterwards.
        "outcome": outcome,
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
