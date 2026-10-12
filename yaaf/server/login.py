"""The consent screen's own short sign-in.

This module was the first slice of people auth — argon2id against the
`password_hash` column, and a signed cookie scoped to the OAuth consent flow —
and was sized to be absorbed by the real thing. It has been: passwords,
sessions and the throttle live in `yaaf.server.identity` (docs/AUTH.md).

What stays here is the cookie the consent screen sets after it asks for the
password, which deliberately is *not* the dashboard session. Handing a model a
credential is the moment to prove it is still you, so a browser that has been
signed in to the dashboard for a week still types the password here, and what
that buys lasts thirty minutes and reaches `/oauth` alone.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from sqlalchemy.orm import Session

from yaaf.core.config import settings
from yaaf.core.models import User

COOKIE = "yaaf_consent"
#: Long enough to read a consent screen and think about it, short enough that a
#: shared machine does not stay signed in to the chairman's authority.
TTL_S = 30 * 60


def _key() -> bytes:
    """The cookie signing key, derived from the master key.

    Derived rather than reused: a cookie signature and an envelope-encryption
    key should never be the same bytes, even when they come from one secret.
    """
    return hashlib.sha256(f"consent-cookie:{settings().master_key}".encode()).digest()


def issue(user: User) -> str:
    payload = {"user": user.id, "exp": int(time.time()) + TTL_S}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{raw}.{hmac.new(_key(), raw.encode(), hashlib.sha256).hexdigest()}"


def read(session: Session, cookie: str | None) -> User | None:
    if not cookie or "." not in cookie:
        return None
    raw, signature = cookie.rsplit(".", 1)
    expected = hmac.new(_key(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, TypeError):
        return None
    if payload.get("exp", 0) < time.time():
        return None
    user = session.get(User, payload.get("user"))
    # A signature outlives the account it was issued to; the row does not.
    return user if user is not None and user.deactivated_at is None else None
