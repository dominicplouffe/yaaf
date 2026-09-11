"""The plan meter behind a subscription credential — told to us, not asked for.

A Claude subscription is metered by Anthropic against a five-hour session
window and a seven-day window: the bars a subscriber sees under Settings →
Usage in claude.ai and under `/usage` in Claude Code. A project on a
subscription credential spends against exactly those windows, so they are
worth a screen.

**This module makes no network calls** — it is the store and the reader, and
what writes it is described below. It used to do the asking itself, once a
minute per credential from every open browser tab, and got what anything
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

The cost of that trade is a credential nothing is running on: it reports
nothing, so it shows nothing, and the case a chairman most needs this screen
for is the one where the runs have already stopped. The door out of that is a
button — `yaaf.server.plan_probe`, one floored, throttle-aware call for one
subscription, and only when somebody presses for it — which files what it gets
through `record` below, exactly as a run does. Two writers, one row, one shape.
Which one last wrote a reading is not a distinction any screen needs to draw:
both are Anthropic answering about the same account, and the screen says "as
of" either way. Nothing here runs on a timer, which is the part worth keeping
from the poller: an install nobody is looking at makes no requests at all.

That trade shipped with a belief attached — "a meter that only moves when you
spend cannot go stale in a way that matters" — and the belief is wrong in the
one case the meter exists for. A window at 100% is precisely the window that
stops the runs, and a stopped run files no reading, so the number freezes at
full and the screen goes on saying "runs on this credential fail until it
resets at 3:22pm" at seven in the evening, forever. The reading did not age; it
outlived the window it describes. So every read here checks each window's own
`resets_at` against the clock and marks the ones that have turned over: that
number measured a window which no longer exists, and the honest thing to show
is the rollover, not a percentage nobody has measured since.
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
    "seven_day_oauth_apps": "This week, API apps",
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


def _has_turned_over(resets_at: Any, now: datetime) -> bool:
    """Has this window's clock run out since a run last reported it?

    A window with no reset time never turns over: an unlabelled shape the SDK
    grew is better shown as it was reported than quietly retired on a guess.
    So is one whose reset time this cannot read — the value comes back out of a
    JSON column written by an older shape of this module, and the failure mode
    of guessing wrong is hiding the bar that is about to stop every run on the
    credential.
    """
    if not isinstance(resets_at, str) or not resets_at:
        return False
    try:
        when = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when <= now


def record(session, token: str, plan: dict) -> None:
    """File what Anthropic said about the plan behind a credential.

    Called on the completion path of every run that carried a reading — so the
    meter is maintained by the spending itself — and by the refresh button, for
    the credentials no run is covering. Last writer wins: every caller is
    reading the same account, and the latest answer is the only one worth
    keeping.

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
        # A caller may name its own window. The SDK's keys are all in the table
        # above, but a model-scoped allowance arrives from the usage endpoint as
        # a display name and a period — "Opus 5, this week" — which no lookup
        # here could have known to write down.
        label = entry.get("label") or WINDOW_LABELS.get(
            key, key.replace("_", " ").capitalize())
        windows.append({"key": key, "label": label, "utilization": share,
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
    said — about a window that is still open. A window whose `resets_at` has
    passed is a different thing entirely, and comes back flagged `expired`: the
    number is still the last thing Anthropic said, but what it was said about
    is gone, and a caller that draws it as the current state of the plan is
    telling its reader the opposite of the truth.

    Nothing is invented in its place. The share of a window that has just
    turned over is almost certainly near zero and it is emphatically not "0%
    used" until a run says so, so the flag says the window rolled over and the
    screens say the same. When every window in a reading has turned over, the
    reading as a whole no longer describes the plan and the answer is
    `available: False` — which is what every caller already renders as a
    sentence rather than a bar, including the credential menu the Founder picks
    from, where a frozen 100% is an argument against a credential that is in
    fact wide open.
    """
    from yaaf.core.models import PlanReading

    row = session.get(PlanReading, fingerprint(token))
    if row is None or not row.windows:
        return None
    now = datetime.now(UTC)
    windows = [{**w, "expired": _has_turned_over(w.get("resets_at"), now)}
               for w in row.windows]
    answer = {"windows": windows, "status": row.status,
              "fetched_at": row.seen_at.isoformat() if row.seen_at else _now_iso()}
    if all(w["expired"] for w in windows):
        return {"available": False,
                "reason": "every window in the last reading has reset since a run "
                          "reported it — the new numbers arrive with this "
                          "credential's next agent run",
                **answer}
    return {"available": True, "reason": None, **answer}
