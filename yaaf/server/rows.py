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
