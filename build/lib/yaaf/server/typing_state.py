"""Ephemeral "someone is typing" state, in-memory only.

Typing is pure presence: it means nothing a second after it stops, so it never
touches the database. The API process (which also hosts the dispatcher and
janitor threads) keeps one expiring flag per channel; losing the process loses
nothing but a few seconds of animation. The SPA polls it alongside messages —
same degrade-to-polling philosophy as the WebSocket stamp.
"""

from __future__ import annotations

import threading
import time

_MAX_SECONDS = 120.0

_lock = threading.Lock()
_until: dict[str, float] = {}


def set_typing(channel_id: str, seconds: float) -> None:
    seconds = max(0.0, min(float(seconds), _MAX_SECONDS))
    with _lock:
        if seconds == 0.0:
            _until.pop(channel_id, None)
        else:
            _until[channel_id] = time.monotonic() + seconds


def is_typing(channel_id: str) -> bool:
    now = time.monotonic()
    with _lock:
        expiry = _until.get(channel_id)
        if expiry is None:
            return False
        if expiry <= now:
            del _until[channel_id]
            return False
        return True
