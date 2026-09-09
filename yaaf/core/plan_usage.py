"""The plan meter behind a subscription credential.

A Claude subscription token is metered by Anthropic against a five-hour
session window and a seven-day window — the bars a subscriber sees under
Settings → Usage in claude.ai and under `/usage` in Claude Code. There is no
documented API for those bars. Claude Code reads them from
`GET /api/oauth/usage` with the same OAuth token it runs on, and a project on a
subscription credential runs on exactly that token, so the meter it draws is
the meter its agents spend.

Two rules follow from "undocumented":

- **It is a reading, never a rule.** The token cap in a project's constitution
  is enforced from yaaf's own ledger; this module only reports what Anthropic
  says is left. An endpoint that vanishes takes a number off a screen, not a
  refusal out of the platform.
- **It is cached and it degrades.** One reading per *token* per minute — the
  unit Anthropic rate-limits, which is not the unit this platform stores, since
  one subscription can sit behind many credential rows — and every failure is a
  sentence the screen can show rather than an error page.

API keys have no plan meter — each call is billed in dollars — so the reading
for one says so instead of pretending to measure anything.
"""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import UTC, datetime
from typing import Any

import httpx

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
#: The beta the CLI sends with an OAuth bearer; without it the token is refused.
OAUTH_BETA = "oauth-2025-04-20"
CACHE_TTL_S = 60
TIMEOUT_S = 8

#: The windows worth a bar, in the order a reader wants them: the one that
#: resets soonest first. Keys not listed here are ignored rather than shown
#: under a label nobody chose.
WINDOW_LABELS = {
    "five_hour": "Current session",
    "seven_day": "This week, all models",
    "seven_day_opus": "This week, Opus",
    "seven_day_sonnet": "This week, Sonnet",
}

#: Readings, and the one fetch in flight, keyed by **token** rather than by
#: credential — because that is what Anthropic rate-limits.
#:
#: Keying by credential id was right until credentials moved above projects.
#: That migration made one row per historical key, so a single subscription
#: routinely sits behind several rows; on the install that found this, 33 rows
#: were 6 tokens, and the 11 the screen was polling were 4. Each row had its
#: own cache entry and its own gate, so one subscription was asked two or three
#: times a minute by what Anthropic sees as one subscriber hammering an
#: undocumented endpoint. It answered 429 — and because the limit is on the
#: account, every row sharing that token went unavailable together, which reads
#: on screen as "the meter is broken" rather than "you asked three times".
#:
#: The key is a digest, not the token: this is a process-global dict, and a
#: dict whose keys are credential values is one traceback away from printing
#: them.
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()
_inflight: dict[str, threading.Lock] = {}
#: Which token a credential was last seen holding, so `cached` can find a
#: reading without being handed the value (its callers deliberately have none).
_token_of: dict[str, str] = {}


def fingerprint(token: str) -> str:
    """The cache key for a token. One-way, and never returned to a caller."""
    return hashlib.sha256(token.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _fraction(value: Any) -> float | None:
    """A share of the window, 0.0–1.0. The CLI multiplies `utilization` by 100
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


def normalize(payload: dict) -> list[dict]:
    """The windows a screen draws, from whatever the wire sent.

    Tolerant on purpose: the shape is unpublished, so a key this does not
    recognise is skipped, not raised on. A window is a dict with a
    `utilization`; a per-model list (entries naming `scope.model.display_name`
    and a `percent`) becomes one window per model, the way `/usage` draws the
    row for the model you use most.
    """
    windows: list[dict] = []
    for key, label in WINDOW_LABELS.items():
        entry = payload.get(key)
        if not isinstance(entry, dict):
            continue
        share = _fraction(entry.get("utilization"))
        if share is None:
            continue
        windows.append({"key": key, "label": label, "utilization": share,
                        "resets_at": _reset_iso(entry.get("resets_at"))})
    for key, value in payload.items():
        if not isinstance(value, list):
            continue
        for entry in value:
            if not isinstance(entry, dict):
                continue
            name = (((entry.get("scope") or {}).get("model") or {}).get("display_name"))
            share = _fraction(entry.get("percent", entry.get("utilization")))
            if not name or share is None:
                continue
            windows.append({"key": f"{key}:{name}".lower().replace(" ", "_"),
                            "label": f"This week, {name}", "utilization": share,
                            "resets_at": _reset_iso(entry.get("resets_at"))})
    return windows


def fetch(token: str) -> dict:
    """One reading, straight from Anthropic. Never cached here; see `reading`."""
    try:
        response = httpx.get(
            USAGE_URL,
            headers={"Authorization": f"Bearer {token}", "anthropic-beta": OAUTH_BETA,
                     "Content-Type": "application/json"},
            timeout=TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        return _unavailable(f"could not reach Anthropic ({exc.__class__.__name__})")
    if response.status_code in (401, 403):
        return _unavailable("Anthropic rejected this token — it may be expired or revoked; "
                            "rotate it in Settings")
    if response.status_code == 429:
        return _unavailable("Anthropic is throttling the meter itself; try again in a minute")
    if response.status_code != 200:
        return _unavailable(f"unexpected response from Anthropic ({response.status_code})")
    try:
        payload = response.json()
    except ValueError:
        return _unavailable("Anthropic returned something other than a usage reading")
    if not isinstance(payload, dict):
        return _unavailable("Anthropic returned something other than a usage reading")
    windows = normalize(payload)
    if not windows:
        return _unavailable("Anthropic's reading had no usage windows in it — the format "
                            "may have changed")
    return {"available": True, "reason": None, "windows": windows,
            "fetched_at": _now_iso()}


def _unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason, "windows": [], "fetched_at": _now_iso()}


def reading(credential_id: str, mode: str | None, token: str, *, refresh: bool = False) -> dict:
    """The cached reading for a credential, fetching when stale or forced.

    `cached` on the result says whether the screen is looking at a minute-old
    number; the refresh button is how a reader who just watched a run finish
    gets a fresh one.
    """
    if mode != "subscription":
        return {"available": False,
                "reason": "API keys are metered per call in dollars, not against a plan — "
                          "the monthly budget below is the meter that applies",
                "windows": [], "fetched_at": _now_iso(), "cached": False}
    key = fingerprint(token)
    asked_at = time.monotonic()

    def usable(hit: tuple[float, dict] | None) -> bool:
        if hit is None:
            return False
        if refresh:
            # A forced refresh wants a reading newer than the moment it asked
            # — not necessarily one of its own. Three rows on one subscription
            # refreshed together used to mean three calls; now the first one
            # asks and the others accept its answer, which is the same number
            # they would have got.
            return hit[0] > asked_at
        return time.monotonic() - hit[0] < CACHE_TTL_S

    with _lock:
        _token_of[credential_id] = key
        hit = _cache.get(key)
        if usable(hit):
            return {**hit[1], "cached": True}
        gate = _inflight.setdefault(key, threading.Lock())
    with gate:
        with _lock:
            hit = _cache.get(key)
            if usable(hit):
                return {**hit[1], "cached": True}
        fresh = fetch(token)
        with _lock:
            # Stamped after the fetch, so a slow answer is not already half expired.
            _cache[key] = (time.monotonic(), fresh)
    return {**fresh, "cached": False}


def cached(credential_id: str) -> dict | None:
    """The last reading if a screen took one in the last minute, else None.
    For callers — a draft, a tool preview — that want the number if it is
    lying around and must not spend a network call to get it.

    A credential nobody has read yet has no token on file, so this says None
    rather than decrypting one to find out: the whole promise of this function
    is that it costs nothing."""
    with _lock:
        key = _token_of.get(credential_id)
        hit = _cache.get(key) if key else None
    if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    return None


def forget(credential_id: str) -> None:
    """After a rotation this credential holds a different token, so forget
    which one it was and let the next read fingerprint it again.

    The *reading* stays. It is filed under the old token, and any other
    credential still holding that token still has exactly that meter — a
    rotation here is not news about somebody else's subscription. An entry
    nothing points at any more expires on its own within the minute."""
    with _lock:
        _token_of.pop(credential_id, None)
