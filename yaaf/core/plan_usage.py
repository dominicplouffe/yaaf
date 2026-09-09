"""The plan meter behind a subscription credential — told to us, not asked for.

A Claude subscription is metered by Anthropic against a five-hour session
window and a seven-day window: the bars a subscriber sees under Settings →
Usage in claude.ai and under `/usage` in Claude Code. A project on a
subscription credential spends against exactly those windows, so they are
worth a screen.

**This module makes no network calls.** It used to. There is no documented API
for those bars, so it read the undocumented `GET /api/oauth/usage` with the
stored token, once a minute per credential, forever — and got what anything
polling an interactive endpoint at a hundred times human rate eventually gets:
`429 rate_limit_error`, with a `Retry-After` measured in tens of minutes. The
meter stayed dark for hours while the credential itself was perfectly healthy
and hundreds of agent runs went through on it untouched.

The fix was not a better backoff. It was noticing that **the answer already
arrives on its own**. The Claude Agent SDK — which every yaaf run already uses,
in the sandbox, on that very token — emits a `RateLimitEvent` carrying the same
`five_hour` / `seven_day` windows, with utilizations and reset times. On the
install that found this there were 23,817 of them sitting unread in our own
trace files, beside a poller being rate-limited asking Anthropic the same
question.

So the meter is a by-product of work already done: `sandbox_entry` keeps the
last event of a run, the run result carries it home, the daemon files it here
against the token, and screens read a local row. Nothing to rate-limit, nothing
to time out, no undocumented endpoint to break, and the number refreshes
exactly when it moves — when the credential is actually used.

The cost of that trade, stated plainly: a credential nothing has run on yet has
no reading, and a quiet credential's reading ages. Both are honest — a meter
that only moves when you spend cannot go stale in a way that matters — and the
screen says "as of" rather than implying live.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

#: The windows worth a bar. Keys not listed still render, under their own name:
#: the set is Anthropic's to grow, and an unlabelled window beats a missing one
#: on a screen whose whole job is "what is about to stop my runs".
WINDOW_LABELS = {
    "five_hour": "Current session",
    "seven_day": "This week, all models",
    "seven_day_opus": "This week, Opus",
    "seven_day_sonnet": "This week, Sonnet",
    "overage": "Overage",
}


def fingerprint(token: str) -> str:
    """The identity of a token, without the token.

    Readings are filed under this rather than under a credential id because one
    subscription routinely sits behind several credential rows — the migration
    that lifted keys above projects made one row per historical key — and all
    of them share one meter. A digest, not the value: this is a database key,
    and a key is one traceback away from being printed.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _fraction(value: Any) -> float | None:
    """A share of the window, 0.0-1.0. The CLI multiplies `utilization` by 100
    to print it, so it is a fraction; a value above 1 can only be a percentage
    already, and is folded back rather than drawn as 5100%."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1.0:
        number = number / 100.0
    return max(0.0, number)


def _reset_iso(value: Any) -> str | None:
    """`resets_at` as ISO-8601, whether the wire sends epoch seconds or a string."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC).isoformat()
    return str(value)


def record(session, token: str, plan: dict) -> None:
    """File what a run was told about the plan behind its own credential.

    Called on the completion path of every run that carried a reading, so the
    meter is maintained by the spending itself. Last writer wins: every run on
    this token is reading the same account, and the latest answer is the only
    one worth keeping.

    Tolerant on purpose. The shape is the SDK's and may grow, so a window this
    cannot make a number of is skipped rather than raised on — a meter is never
    worth failing a finished run over.
    """
    from yaaf.core.models import PlanReading, utcnow

    windows = []
    for entry in plan.get("windows") or []:
        key = entry.get("key")
        share = _fraction(entry.get("utilization"))
        if not key or share is None:
            continue
        windows.append({"key": key,
                        "label": WINDOW_LABELS.get(key, key.replace("_", " ").capitalize()),
                        "utilization": share,
                        "resets_at": _reset_iso(entry.get("resets_at"))})
    if not windows:
        return
    row = session.get(PlanReading, fingerprint(token))
    if row is None:
        row = PlanReading(token_fingerprint=fingerprint(token))
        session.add(row)
    row.windows = windows
    row.status = plan.get("status")
    row.seen_at = utcnow()


def stored(session, token: str) -> dict | None:
    """The last harvested reading for this token, or None if nothing has run.

    A plain row read: no cache, because there is no call to save, and no TTL,
    because a reading nothing has superseded is still the last thing Anthropic
    said.
    """
    from yaaf.core.models import PlanReading

    row = session.get(PlanReading, fingerprint(token))
    if row is None or not row.windows:
        return None
    return {"available": True, "reason": None, "windows": list(row.windows),
            "status": row.status,
            "fetched_at": row.seen_at.isoformat() if row.seen_at else _now_iso()}
