"""Ask Anthropic what is left on a subscription plan, when somebody asks for it.

The plan meter is harvested from every agent run — the SDK volunteers the
windows on the token doing the spending, and `plan_usage` files them. That is
the source, and this does not change it. What it cannot cover is a credential
nothing is running on, which is exactly what a full window produces: the runs
stop, so the reports stop, so the number a chairman needs most is the one that
cannot arrive on its own.

This is the door out of that, and it is a **button, not a timer**. The platform
already had the timer — a poller, once a minute per credential, from every open
browser tab — and earned a `429 rate_limit_error` with a `Retry-After` in tens
of minutes. Nothing here runs on a clock. One call happens when a person presses
refresh on a credential, and their press is the whole of the demand: an install
nobody is looking at makes no requests at all, which is a property no interval
can offer.

What is kept from that lesson, because a button on a URL is still a URL:

- **A floor.** One call per token every fifteen seconds, however many tabs are
  pressing. Inside it the answer is the stored row, which is what the reader
  wanted anyway.
- **One call per token**, keyed by fingerprint, because one subscription
  routinely sits behind several credential rows sharing one meter — so three
  rows refreshed together are one question.
- **A throttle is obeyed, not fought.** A `429` quiets that one token for as
  long as Anthropic asked, and touches no other token.
- **A failed probe never costs a reading.** The stored row stays; a probe that
  cannot answer leaves the last answer standing, and `plan_usage.stored` is
  already honest about a window that has rolled over since.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from yaaf.core import plan_usage

log = logging.getLogger("yaaf.plan_probe")

#: The endpoint Claude Code's own `/usage` reads. Undocumented, and the reason
#: this is a fallback rather than the source: it is Anthropic's to change, and
#: when it does the harvest keeps working.
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
#: The beta the CLI sends with an OAuth bearer; without it the token is refused.
OAUTH_BETA = "oauth-2025-04-20"
TIMEOUT_S = 10.0

#: The floor under a probe. The refresh button is a URL, and a URL is pressed by
#: however many tabs are open — six presses inside fifteen seconds are one call
#: and five re-reads of the row, which is the whole difference between a button
#: and the poller this platform deleted. Per token, not per caller or per
#: credential row: the meter belongs to the subscription.
MIN_INTERVAL_S = 15.0
#: What to wait when Anthropic throttles us and does not say for how long, and
#: the longest we will honour if it does. A meter is worth no retry storm.
DEFAULT_BACKOFF_S = 900.0
MAX_BACKOFF_S = 3600.0
_lock = threading.Lock()
#: fingerprint → monotonic deadline before which this token must not be asked.
_quiet_until: dict[str, float] = {}
#: fingerprint → monotonic time of the last attempt, for the floor above.
_last_attempt: dict[str, float] = {}


def _percent_scale(values: list[Any]) -> bool:
    """Is this payload speaking percentages rather than fractions?

    It matters at exactly one value. Anthropic's OAuth usage endpoint reports
    `37.0` for 37% where the SDK reports `0.37`, and a lone `1.0` is 1% on one
    convention and a full window — the one that stops every run — on the other.
    One payload speaks one convention, so the whole payload settles it: if
    anything in it is at or above 1, all of it is percentages.
    """
    for value in values:
        try:
            if float(value) >= 1:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _share(value: Any, percent_scale: bool) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return min(1.0, number / 100.0 if percent_scale else number)


def _resets_at(value: Any) -> Any:
    """`resets_at` as `plan_usage.record` wants it: epoch seconds, or a string.

    Milliseconds are folded down. The SDK path sends seconds and nothing here
    may turn a 2026 reset into the year 58000 — a reset time in the far future
    is a window that never rolls over, which is the bug this meter just had,
    pointed the other way.
    """
    if isinstance(value, (int, float)) and value > 1e12:
        return value / 1000.0
    return value


def windows_from(payload: dict) -> dict | None:
    """The usage payload as a plan reading, or None if it names no window.

    Two places carry numbers. The flat buckets (`five_hour`, `seven_day`,
    `seven_day_oauth_apps`) are the account's own windows. The `limits` array is
    the only place a *model-scoped* allowance appears — the legacy flat keys for
    those (`seven_day_opus` and friends) stayed behind at null, so reading
    buckets alone silently drops a window the account is really spending
    against. Both are read, and a scoped entry brings its own label because
    "Opus, this week" is not a key anything else would know how to name.
    """
    flat = {key: payload.get(key) for key in
            ("five_hour", "seven_day", "seven_day_oauth_apps", "overage")}
    entries = [e for e in (payload.get("limits") or []) if isinstance(e, dict)]
    raw = [b.get("utilization") for b in flat.values() if isinstance(b, dict)]
    raw += [e.get("percent") for e in entries]
    percent_scale = _percent_scale(raw)

    windows = []
    for key, bucket in flat.items():
        if not isinstance(bucket, dict):
            continue
        share = _share(bucket.get("utilization"), percent_scale)
        if share is None:
            continue
        windows.append({"key": key, "utilization": share,
                        "resets_at": _resets_at(bucket.get("resets_at"))})

    seen: set[str] = set()
    for entry in entries:
        scope = entry.get("scope")
        model = scope.get("model") if isinstance(scope, dict) else None
        if not isinstance(model, dict):
            continue
        name = str(model.get("display_name") or model.get("id") or "").strip()
        kind = str(entry.get("kind") or "").strip()
        share = _share(entry.get("percent"), percent_scale)
        if not name or share is None:
            continue
        key = f"scoped:{name}:{kind}".lower().replace(" ", "_")
        if key in seen:
            continue
        seen.add(key)
        windows.append({"key": key, "label": f"{name}, {_window_word(kind)}",
                        "utilization": share,
                        "resets_at": _resets_at(entry.get("resets_at"))})

    if not windows:
        return None
    return {"status": payload.get("status"), "windows": windows}


def _window_word(kind: str) -> str:
    """The period a scoped entry covers, settled here rather than read off a
    model name. "Opus 5 (1M context)" carries a "1M" that reads as a window if
    anything downstream goes looking for one in free text."""
    text = kind.lower()
    if "month" in text:
        return "this month"
    if "week" in text or "day" in text:
        return "this week"
    if "hour" in text or "session" in text:
        return "this session"
    return "current window"


def _retry_after(value: str | None) -> float:
    try:
        return max(0.0, min(MAX_BACKOFF_S, float(value)))
    except (TypeError, ValueError):
        return DEFAULT_BACKOFF_S


def probe(token: str) -> dict | None:
    """One call. The plan reading Anthropic returned, or None with a reason logged.

    Never raises: this runs inside a request thread, and a meter is not worth
    a 500 on the screen that was trying to show it.
    """
    digest = plan_usage.fingerprint(token)
    try:
        response = httpx.get(USAGE_URL, timeout=TIMEOUT_S, headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": OAUTH_BETA,
            "Accept": "application/json"})
    except Exception as exc:
        # No server was reached at all — no route, no DNS, a timeout. That is
        # not a throttle and does not earn one: nothing here retries on its own,
        # so the next attempt is already somebody deciding to press again.
        log.info("plan meter probe could not reach Anthropic (%s)", type(exc).__name__)
        return None

    if response.status_code == 429:
        wait = _retry_after(response.headers.get("retry-after"))
        with _lock:
            _quiet_until[digest] = time.monotonic() + wait
        log.warning("plan meter throttled for token %s; quiet for %ss", digest[:8], round(wait))
        return None
    if response.status_code >= 400:
        # A real answer, and the wrong one. A revoked or expired token answers
        # this way every time, so it earns the same quiet as a throttle rather
        # than a call per press. It is not silenced for good: rotating the
        # credential is how this gets fixed, and the probe after that has to be
        # allowed to succeed.
        with _lock:
            _quiet_until[digest] = time.monotonic() + DEFAULT_BACKOFF_S
        log.warning("plan meter probe returned %s for token %s",
                    response.status_code, digest[:8])
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return windows_from(payload) if isinstance(payload, dict) else None


def due(token: str) -> bool:
    """May this token be asked right now? A throttle and the floor are the two
    reasons it may not, and both are answered from memory rather than the wire."""
    digest = plan_usage.fingerprint(token)
    now = time.monotonic()
    with _lock:
        if now < _quiet_until.get(digest, 0.0):
            return False
        if now - _last_attempt.get(digest, -1e9) < MIN_INTERVAL_S:
            return False
    return True


def refresh(session, token: str) -> bool:
    """Probe this token and file what comes back. True if a reading was stored.

    The caller's transaction is used and not committed here: the request path
    that calls this is already inside one.
    """
    if not due(token):
        return False
    with _lock:
        _last_attempt[plan_usage.fingerprint(token)] = time.monotonic()
    plan = probe(token)
    if plan is None:
        return False
    plan_usage.record(session, token, plan)
    return True
