"""People: how a request proves it is somebody, and who.

`deps.py` used to have two principals and the first was a static bearer that
authenticated a *client* — whoever held `YAAF_ADMIN_TOKEN` was "admin", and
every write was recorded as the project's chairman. This module is what
replaced it (docs/AUTH.md): a person signs in with a password and holds either
a browser session or a personal token, and the request is bound to them.

`login.py` was the first slice of it — argon2id against the `password_hash`
column, sized to be absorbed here — and keeps only the consent-screen cookie.

Every credential below is a random value stored as a SHA-256 hash and checked
against its row on each use, the shape run and MCP tokens already had. None is
self-validating, because each has to die the moment somebody revokes it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode, urlparse

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.models import (
    LoginFailure,
    McpClient,
    McpToken,
    User,
    UserSession,
    UserSetupLink,
    UserToken,
    utcnow,
)
from yaaf.core.tokens import hash_token, mint_prefixed
from yaaf.server import acting
from yaaf.server.deps import db

#: The browser's credential. HttpOnly, so a script running in the dashboard can
#: use it and cannot read it — the property localStorage never had.
COOKIE = "yaaf_session"
TOKEN_PREFIX = "ypt"

#: A signed-in browser lasts two weeks at most and three days untouched. Long
#: enough that a chairman checking in on a Monday is not signed out by the
#: weekend; short enough that a forgotten laptop stops being a key.
SESSION_TTL = timedelta(days=14)
SESSION_IDLE = timedelta(days=3)
#: last_seen_at is a write. Once a minute is plenty for an idle clock measured
#: in days, and keeps the Desk's six polls a minute from writing six rows.
SEEN_EVERY = timedelta(minutes=1)

TOKEN_DAYS_DEFAULT = 30
TOKEN_DAYS_MAX = 365

SETUP_TTL = timedelta(hours=72)

THROTTLE_WINDOW = timedelta(minutes=15)
THROTTLE_MAX = 10

MIN_PASSWORD = 12

person_bearer = HTTPBearer(auto_error=False, scheme_name="PersonalToken",
                           description="A personal token (ypt_…) from `yaaf login`")


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def _email_hash(email: str) -> str:
    return hashlib.sha256(normalise_email(email).encode()).hexdigest()


def find_user(session: Session, email: str) -> User | None:
    return session.execute(
        select(User).where(User.email == normalise_email(email))
    ).scalar_one_or_none()


# ── passwords ───────────────────────────────────────────────────────────────────


def set_password(user: User, password: str) -> None:
    from argon2 import PasswordHasher

    if len(password or "") < MIN_PASSWORD:
        # Refused here rather than advised in a docstring: this password stands
        # between the network and everything its owner chairs.
        raise ValueError(f"a password must be at least {MIN_PASSWORD} characters")
    user.password_hash = PasswordHasher().hash(password)


def check_password(session: Session, email: str, password: str) -> User | None:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerificationError, VerifyMismatchError

    user = find_user(session, email)
    if user is None or not user.password_hash or user.deactivated_at is not None:
        # Same answer as a wrong password, and the same work: the sign-in page
        # is not an oracle for which addresses exist here.
        PasswordHasher().hash(password or "")
        return None
    try:
        PasswordHasher().verify(user.password_hash, password or "")
    except (VerifyMismatchError, VerificationError):
        return None
    return user


def authenticate(session: Session, email: str, password: str) -> User:
    """The one door a password goes through: the dashboard, the CLI and the
    consent screen all call this, so there is one throttle and not three."""
    key = _email_hash(email)
    since = utcnow() - THROTTLE_WINDOW
    recent = session.execute(
        select(func.count(LoginFailure.id)).where(LoginFailure.email_hash == key,
                                                  LoginFailure.at >= since)
    ).scalar_one()
    if recent >= THROTTLE_MAX:
        raise HTTPException(
            status_code=429,
            detail=(f"too many wrong passwords for this address; try again in "
                    f"{int(THROTTLE_WINDOW.total_seconds() // 60)} minutes"),
        )
    user = check_password(session, email, password)
    if user is None:
        session.add(LoginFailure(email_hash=key, at=utcnow()))
        # Housekeeping on the write path: nothing older than the window is ever
        # read, and nothing else needs to remember a stranger's guesses.
        session.execute(delete(LoginFailure).where(LoginFailure.at < since))
        # Committed now, because the 401 below rolls the request back — and a
        # throttle that forgot every failure it counted would never fire.
        session.commit()
        raise HTTPException(status_code=401, detail="that email and password did not match")
    session.execute(delete(LoginFailure).where(LoginFailure.email_hash == key))
    return user


# ── sessions ────────────────────────────────────────────────────────────────────


def open_session(session: Session, user: User, user_agent: str | None = None) -> str:
    plaintext, token_hash = mint_prefixed("yss")
    now = utcnow()
    session.add(UserSession(
        id=ids.new_id(ids.USER_SESSION), user_id=user.id, token_hash=token_hash,
        user_agent=(user_agent or "")[:255] or None, created_at=now, last_seen_at=now,
        expires_at=now + SESSION_TTL,
    ))
    return plaintext


def read_session(session: Session, plaintext: str | None) -> UserSession | None:
    if not plaintext:
        return None
    row = session.execute(
        select(UserSession).where(UserSession.token_hash == hash_token(plaintext))
    ).scalar_one_or_none()
    now = utcnow()
    if (row is None or row.revoked_at is not None or row.expires_at < now
            or row.last_seen_at + SESSION_IDLE < now):
        return None
    if now - row.last_seen_at > SEEN_EVERY:
        _touch(UserSession, row.id, "last_seen_at", now)
    return row


def _touch(model, row_id: str, column: str, at) -> None:
    """Move a last-used clock in a transaction of its own.

    Not in the request's: on SQLite a write takes the database lock until the
    transaction ends, so a request that stamped its credential on the way in
    held the lock for its whole life — and a reader polling the Founder
    starved the very turn it was waiting for. Committed at once, the request
    that follows is a read again.
    """
    from yaaf.core.db import session_factory

    with session_factory()() as own:
        own.execute(update(model).where(model.id == row_id).values({column: at}))
        own.commit()


def revoke_sessions(session: Session, user_id: str, *, keep: str | None = None) -> None:
    query = update(UserSession).where(UserSession.user_id == user_id,
                                      UserSession.revoked_at.is_(None))
    if keep is not None:
        query = query.where(UserSession.id != keep)
    session.execute(query.values(revoked_at=utcnow()))


def cookie_secure() -> bool:
    return (settings().public_base_url or "").startswith("https")


def set_session_cookie(response, plaintext: str) -> None:
    response.set_cookie(COOKIE, plaintext, httponly=True, samesite="lax",
                        secure=cookie_secure(), max_age=int(SESSION_TTL.total_seconds()),
                        path="/")


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax",
                           secure=cookie_secure())


# ── personal tokens ─────────────────────────────────────────────────────────────


def mint_token(session: Session, user: User, name: str, *, days: int | None = None,
               minutes: int | None = None) -> tuple[UserToken, str]:
    """A personal token. `minutes` exists for the deploy scripts, which need a
    credential for one library sync and nothing after it."""
    if minutes is not None:
        lifetime = timedelta(minutes=max(1, minutes))
    else:
        days = TOKEN_DAYS_DEFAULT if days is None else days
        if not 1 <= days <= TOKEN_DAYS_MAX:
            raise ValueError(f"a personal token lives 1 to {TOKEN_DAYS_MAX} days; "
                             "one that never expires is not on offer")
        lifetime = timedelta(days=days)
    plaintext, token_hash = mint_prefixed(TOKEN_PREFIX)
    row = UserToken(id=ids.new_id(ids.USER_TOKEN), user_id=user.id, name=name[:80],
                    token_hash=token_hash, expires_at=utcnow() + lifetime)
    session.add(row)
    return row, plaintext


def read_token(session: Session, plaintext: str) -> UserToken | None:
    row = session.execute(
        select(UserToken).where(UserToken.token_hash == hash_token(plaintext))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None or row.expires_at < utcnow():
        return None
    return row


# ── setup links ─────────────────────────────────────────────────────────────────


def issue_setup_link(session: Session, user: User, purpose: str,
                     created_by: str | None = None) -> str:
    """A fresh single-use link, revoking any this person still holds unused —
    so "send me another one" never leaves two working links in two inboxes."""
    now = utcnow()
    session.execute(
        update(UserSetupLink)
        .where(UserSetupLink.user_id == user.id, UserSetupLink.used_at.is_(None),
               UserSetupLink.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    plaintext, token_hash = mint_prefixed("ysl")
    session.add(UserSetupLink(
        id=ids.new_id(ids.SETUP_LINK), user_id=user.id, token_hash=token_hash,
        purpose=purpose, created_by_user_id=created_by, created_at=now,
        expires_at=now + SETUP_TTL,
    ))
    return plaintext


def setup_path(plaintext: str) -> str:
    return f"/app/#/setup?{urlencode({'token': plaintext})}"


def setup_url(plaintext: str, base: str | None = None) -> str:
    """The link to hand on. Prefers the browser-facing origin; without one it
    falls back to the API's own, which inside compose is `http://api:8080` — a
    name only the containers can resolve — so a caller that knows better (the
    dashboard knows its own origin, the deploy script knows the port it
    published) passes `base` or uses `setup_path`."""
    origin = (base or settings().public_base_url or settings().api_base_url).rstrip("/")
    return f"{origin}{setup_path(plaintext)}"


def read_setup_link(session: Session, plaintext: str | None) -> UserSetupLink | None:
    if not plaintext:
        return None
    row = session.execute(
        select(UserSetupLink).where(UserSetupLink.token_hash == hash_token(plaintext))
    ).scalar_one_or_none()
    if (row is None or row.used_at is not None or row.revoked_at is not None
            or row.expires_at < utcnow()):
        return None
    user = session.get(User, row.user_id)
    if user is None or user.deactivated_at is not None:
        return None
    return row


def redeem_setup_link(session: Session, plaintext: str, password: str) -> User:
    row = read_setup_link(session, plaintext)
    if row is None:
        raise HTTPException(status_code=404,
                            detail="this link has been used, has expired, or was replaced")
    user = session.get(User, row.user_id)
    try:
        set_password(user, password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row.used_at = utcnow()
    # A new password is a new start: every browser signed in on the old one is
    # signed out, which is what a reset after a stolen laptop has to mean.
    revoke_sessions(session, user.id)
    return user


# ── leaving ─────────────────────────────────────────────────────────────────────


def revoke_everything(session: Session, user: User) -> None:
    """Every credential this person holds stops working at once: sessions,
    personal tokens, model-client grants and unused setup links."""
    now = utcnow()
    revoke_sessions(session, user.id)
    session.execute(update(UserToken).where(UserToken.user_id == user.id,
                                            UserToken.revoked_at.is_(None))
                    .values(revoked_at=now))
    clients = select(McpClient.id).where(McpClient.user_id == user.id)
    session.execute(update(McpToken).where(McpToken.client_id.in_(clients),
                                           McpToken.revoked_at.is_(None))
                    .values(revoked_at=now))
    session.execute(update(McpClient).where(McpClient.user_id == user.id,
                                            McpClient.revoked_at.is_(None))
                    .values(revoked_at=now))
    session.execute(update(UserSetupLink).where(UserSetupLink.user_id == user.id,
                                                UserSetupLink.used_at.is_(None),
                                                UserSetupLink.revoked_at.is_(None))
                    .values(revoked_at=now))


# ── the principal ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Person:
    id: str
    email: str
    display_name: str
    is_admin: bool
    #: "session" (a browser) or "token" (a terminal or a script).
    via: str
    session_id: str | None = None
    token_id: str | None = None
    token_name: str | None = None

    @property
    def actor(self) -> acting.Actor:
        return acting.person(self.id, f"token:{self.token_name}" if self.via == "token" else None)


def _person(user: User, via: str, **extra) -> Person:
    return Person(id=user.id, email=user.email, display_name=user.display_name,
                  is_admin=bool(user.is_admin), via=via, **extra)


_WRONG_DOOR = {
    "ymt": "an MCP client token reaches /mcp and nothing else",
    "yrt": "a run token reaches /agent and nothing else",
}


def _refuse_bearer(token: str) -> HTTPException:
    """A 401 that says what the credential was, because the commonest one to
    arrive here for a while will be the retired admin token in a shell profile,
    and "unknown token" teaches nothing."""
    prefix = token.split("_", 1)[0]
    detail = _WRONG_DOOR.get(prefix) or (
        "not a personal token. The admin token was retired (docs/AUTH.md): sign in "
        "at /app, or run `yaaf login` for a personal token")
    return HTTPException(status_code=401, detail=detail)


def origin_allowed(origin: str | None, host: str | None) -> bool:
    """Whether a browser's Origin (or Referer) names this install: the host the
    request arrived on, or one of the origins it is configured to answer on."""
    if not origin:
        return False
    allowed = {host or ""}
    for configured in (settings().public_base_url, settings().api_base_url):
        if configured:
            allowed.add(urlparse(configured).netloc)
    return urlparse(origin).netloc in allowed - {""}


def check_origin(request: Request) -> None:
    """A write carried only by the browser's ambient cookie must say it came
    from this install's own pages.

    SameSite=Lax already keeps the cookie off a cross-site form post; this is
    the half that also covers a sibling origin on the same site, and a browser
    old enough not to honour SameSite. Bearer requests are exempt — a header
    is not something another page can make the browser send.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin_allowed(origin, request.headers.get("host")):
        raise HTTPException(
            status_code=403,
            detail="a browser write has to come from this install's own pages")


def person_auth(
    request: Request,
    session: Session = Depends(db),
    credentials: HTTPAuthorizationCredentials | None = Depends(person_bearer),
) -> Person:
    """Who this request is, from a personal token or a session cookie.

    A bearer wins when both are present: it is the credential somebody chose to
    send, and the cookie may just be a browser that happens to be signed in.
    """
    if credentials is not None and credentials.credentials:
        token = credentials.credentials.strip()
        if not token.startswith(f"{TOKEN_PREFIX}_"):
            raise _refuse_bearer(token)
        row = read_token(session, token)
        user = session.get(User, row.user_id) if row is not None else None
        if row is None or user is None or user.deactivated_at is not None:
            raise HTTPException(status_code=401,
                                detail="this personal token was revoked or has expired")
        now = utcnow()
        if row.last_used_at is None or now - row.last_used_at > SEEN_EVERY:
            _touch(UserToken, row.id, "last_used_at", now)
        return _person(user, "token", token_id=row.id, token_name=row.name)

    row = read_session(session, request.cookies.get(COOKIE))
    user = session.get(User, row.user_id) if row is not None else None
    if row is None or user is None or user.deactivated_at is not None:
        raise HTTPException(status_code=401, detail="sign in first")
    check_origin(request)
    return _person(user, "session", session_id=row.id)


def socket_refusal(session: Session, cookie: str | None, origin: str | None,
                   host: str | None) -> str | None:
    """Why a live-update socket may not open, or None when it may.

    The socket's own rules, because it is not a request FastAPI resolves
    dependencies for: the session cookie, and an Origin naming this install —
    the check CORS would have made for a fetch and never makes for a socket.
    """
    if not origin_allowed(origin, host):
        return "a live socket has to be opened by this install's own pages"
    row = read_session(session, cookie)
    user = session.get(User, row.user_id) if row is not None else None
    if row is None or user is None or user.deactivated_at is not None:
        return "sign in first"
    return None


async def signed_in(person: Person = Depends(person_auth)) -> Person:
    """`person_auth`, plus binding the actor for the rest of the request.

    Async on purpose: a contextvar set in a sync dependency is set inside the
    threadpool worker that ran it and is gone before the handler starts. Set
    here, on the request's own task, it is copied into the worker that runs a
    sync handler — and into nothing outside this request.
    """
    acting.bind(person.actor)
    return person
