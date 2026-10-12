"""Two-phase confirmation, and the attribution that outlives it.

Two rules from docs/MCP_SERVER.md live here because they are the same moment.

**Rule 4 — an irreversible write cannot happen in one call.** The first call
returns a preview and a `confirm_token`; the second spends it. The token is
bound to a hash of the tool *and its exact arguments*, so the second call
cannot be a different write wearing the first one's approval, and it is
single-use with a five-minute life.

The obvious objection is that a model can simply call twice. That is the point:
it cannot do so *without seeing what it is about to do* — the preview is the
turn where a person reading along can stop it, and the argument binding means a
model that changed its mind between calls starts over. What this defends
against is not a determined adversary with the chairman's token; it is the
ordinary failure where a confident model acts on a channel message that told it
to.

**Rule 3 — attribution.** Every write made through this surface is recorded as
machine-mediated, permanently: `user:<id> via mcp:<client>` rather than the
bare `user:<id>` the browser writes. The audit trail keeps the distinction
forever, so "the chairman accepted this" and "the chairman's model client
accepted this" can never be confused in a review six weeks later.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import McpConfirmation, utcnow
from yaaf.core.tokens import hash_token, mint_prefixed
from yaaf.server import acting
from yaaf.server.mcp.auth import Principal, ToolRefusal

#: Long enough to read a preview and decide, short enough that an approval does
#: not sit around waiting to be used for something the chairman has forgotten.
TTL = timedelta(minutes=5)


def actor(principal: Principal) -> str:
    return acting_for(principal).stamp


def acting_for(principal: Principal) -> acting.Actor:
    return acting.person(principal.user_id, f"mcp:{principal.name}")


@contextmanager
def attributed(principal: Principal):
    """Make the admin layer's own records name the machine that drove them.

    A request through the dashboard binds its person on the way in; a tool
    call reaches the same handlers as plain functions and so has to bind its
    own actor — the person, via this client. Rather than duplicating every
    handler to pass an actor, it is ambient for the duration of the call, and
    the handlers stay the single definition of what each verb does.
    """
    with acting.acting(acting_for(principal)):
        yield


def fingerprint(tool: str, arguments: dict) -> str:
    """A hash of what is about to happen, ignoring the confirmation itself."""
    material = {k: v for k, v in sorted(arguments.items()) if k != "confirm"}
    return hashlib.sha256(
        json.dumps([tool, material], sort_keys=True, default=str).encode()).hexdigest()


def gate(session: Session, principal: Principal, tool: str, arguments: dict,
         preview: dict) -> dict | None:
    """Return a preview to show, or None when the write may proceed.

    Called by every confirm-gated tool as its last step before writing.
    """
    supplied = arguments.get("confirm")
    wanted = fingerprint(tool, arguments)
    if not supplied:
        plaintext, token_hash = mint_prefixed("ycf")
        session.add(McpConfirmation(
            id=ids.new_id(ids.MCP_CONFIRMATION),
            client_id=principal.client_id,
            token_hash=token_hash,
            fingerprint=wanted,
            tool=tool,
            expires_at=utcnow() + TTL,
        ))
        return {
            "confirm_required": True,
            "about_to": preview,
            "confirm": plaintext,
            "expires_in_seconds": int(TTL.total_seconds()),
            "note": (
                "Nothing has changed yet. Show the chairman what this would do, and "
                "call again with the same arguments plus this confirm token to do it. "
                "The token is bound to these exact arguments — change any of them and "
                "you get a new preview instead of a write."
            ),
        }

    row = session.execute(
        select(McpConfirmation).where(McpConfirmation.token_hash == hash_token(supplied))
    ).scalar_one_or_none()
    if row is None or row.client_id != principal.client_id:
        raise ToolRefusal("that confirmation token is not one of this client's")
    if row.used_at is not None:
        raise ToolRefusal("that confirmation was already spent; preview again")
    if row.expires_at < utcnow():
        raise ToolRefusal("that confirmation expired; preview again")
    if row.fingerprint != wanted:
        # The whole reason the token carries a fingerprint: an approval for one
        # write must not be spendable on another.
        raise ToolRefusal(
            "these are not the arguments that confirmation was issued for. "
            "Preview the write you actually mean.")
    row.used_at = utcnow()
    return None
