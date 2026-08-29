"""Authenticating a person in a browser — the smallest version that is real.

`deps.py` says it plainly: full user auth arrives with the UI milestone. There
is one static admin token, and it authenticates a *client*, not a person. OAuth
cannot be built on that: `/authorize` has to know who is sitting at the
keyboard before it can ask them to approve anything.

So this is deliberately the minimum that is not a pretence — argon2id against
the `password_hash` column the users table has carried unused since the schema
was written, and a signed cookie scoped to the consent flow alone. It does not
convert the SPA to cookie auth; that stays the UI milestone's job, and this
module is sized to be absorbed by it rather than argued with.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.config import settings
from yaaf.core.models import User

COOKIE = "yaaf_consent"
#: Long enough to read a consent screen and think about it, short enough that a
#: shared machine does not stay signed in to the chairman's authority.
TTL_S = 30 * 60


def set_password(session: Session, email: str, password: str) -> User:
    from argon2 import PasswordHasher

    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        raise LookupError(f"no user {email!r}")
    if len(password) < 12:
        # Refused here rather than advised in a docstring: this password stands
        # between the internet and the chairman's authority.
        raise ValueError("a chairman's password must be at least 12 characters")
    user.password_hash = PasswordHasher().hash(password)
    return user


def check_password(session: Session, email: str, password: str) -> User | None:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerificationError, VerifyMismatchError

    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not user.password_hash:
        # Same answer as a wrong password, and the same work: the login page is
        # not an oracle for which addresses exist here.
        PasswordHasher().hash(password)
        return None
    try:
        PasswordHasher().verify(user.password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return None
    return user


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
    return session.get(User, payload.get("user"))
