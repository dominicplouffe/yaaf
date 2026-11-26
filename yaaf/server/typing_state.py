"""Ephemeral "someone is typing" state, in-memory only.

Typing is pure presence: it means nothing a second after it stops, so it never
touches the database. The API process (which also hosts the dispatcher and
janitor threads) keeps one expiring flag per channel; losing the process loses
nothing but a few seconds of animation. The SPA polls it alongside messages —
same degrade-to-polling philosophy as the WebSocket stamp.

A flag carries what the writer is doing ("searching the web") and when the
streak began, so a six-minute reply reads as six minutes of work, not as a
room that went quiet. The streak survives every refresh and status change and
ends only when the flag is cleared or lapses.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime

_MAX_SECONDS = 120.0


@dataclass
class _Flag:
    until: float
    since: datetime
    status: str | None


_lock = threading.Lock()
_flags: dict[str, _Flag] = {}


def set_typing(channel_id: str, seconds: float, status: str | None = None) -> None:
    """Raise or refresh the flag; 0 clears it. A status replaces the last one;
    None keeps it, so a bare refresh never blanks what the room shows."""
    seconds = max(0.0, min(float(seconds), _MAX_SECONDS))
    now = time.monotonic()
    with _lock:
        if seconds == 0.0:
            _flags.pop(channel_id, None)
            return
        flag = _flags.get(channel_id)
        if flag is None or flag.until <= now:
            flag = _flags[channel_id] = _Flag(until=0.0, since=datetime.now(UTC), status=None)
        flag.until = now + seconds
        if status is not None:
            flag.status = status


def state(channel_id: str) -> _Flag | None:
    now = time.monotonic()
    with _lock:
        flag = _flags.get(channel_id)
        if flag is None:
            return None
        if flag.until <= now:
            del _flags[channel_id]
            return None
        return _Flag(flag.until, flag.since, flag.status)


def is_typing(channel_id: str) -> bool:
    return state(channel_id) is not None
