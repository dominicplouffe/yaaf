"""Signing in, signing out, and the credentials a person holds.

The dashboard signs in with a password and holds a session cookie; the CLI
exchanges the same password for a personal token. Both go through one door —
`identity.authenticate` — so there is one throttle. Everything a person can do
to their *own* credentials lives here; what an instance admin does to other
people's lives under `/admin/users`.

Rule 4 of docs/AUTH.md is the one this file is careful about: **a credential
cannot widen itself.** A personal token can list and revoke tokens but not mint
one, and cannot change a password — both need a browser session, which needed
the password. A leaked token is then bounded by its own expiry instead of being
a way to make a longer-lived one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import User, UserSession, UserToken, utcnow
from yaaf.server import acting, authority, identity
from yaaf.server.deps import db
from yaaf.server.identity import Person, signed_in

router = APIRouter()


class LoginIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=1024)


class TokenExchangeIn(LoginIn):
    name: str = Field(min_length=1, max_length=80)
    days: int = Field(default=identity.TOKEN_DAYS_DEFAULT, ge=1, le=identity.TOKEN_DAYS_MAX)


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    days: int = Field(default=identity.TOKEN_DAYS_DEFAULT, ge=1, le=identity.TOKEN_DAYS_MAX)


class SetupIn(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=1024)


class PasswordIn(BaseModel):
    current: str = Field(max_length=1024)
    new: str = Field(max_length=1024)


def _audit(session: Session, action: str, user: User, detail: dict | None = None) -> None:
    from yaaf.server.api.admin import _audit as audit

    with acting.acting(acting.person(user.id)):
        audit(session, None, action, "user", user.id, detail or {})


def me_payload(session: Session, user: User, via: str) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_admin": bool(user.is_admin),
        "via": via,
    }


def _sole_session(person: Person) -> None:
    if person.via != "session":
        raise HTTPException(
            status_code=403,
            detail="a personal token cannot do this; sign in to the dashboard with your "
                   "password. A token that could mint tokens or change the password "
                   "would outlive its own expiry the moment it leaked.")


# ── in and out ──────────────────────────────────────────────────────────────────


@router.post("/login", dependencies=[authority.PUBLIC])  # the door itself
def login(body: LoginIn, request: Request, response: Response,
          session: Session = Depends(db)) -> dict:
    user = identity.authenticate(session, body.email, body.password)
    plaintext = identity.open_session(session, user, request.headers.get("user-agent"))
    identity.set_session_cookie(response, plaintext)
    _audit(session, "user.sign_in", user)
    return me_payload(session, user, "session")


@router.post("/token", dependencies=[authority.PUBLIC])  # the CLI's door; same throttle
def exchange_for_token(body: TokenExchangeIn, session: Session = Depends(db)) -> dict:
    """`yaaf login`: an email and password for a personal token."""
    user = identity.authenticate(session, body.email, body.password)
    row, plaintext = identity.mint_token(session, user, body.name, days=body.days)
    _audit(session, "user.token_mint", user, {"name": row.name, "via": "password"})
    return {"token": plaintext, **_token_row(row)}


@router.post("/logout", dependencies=[authority.SIGNED_IN])
def logout(response: Response, person: Person = Depends(signed_in),
           session: Session = Depends(db)) -> dict:
    """Sign out whatever credential made this request: the browser's session,
    or — from `yaaf logout` — the personal token itself."""
    now = utcnow()
    if person.via == "session":
        session.get(UserSession, person.session_id).revoked_at = now
        identity.clear_session_cookie(response)
    else:
        session.get(UserToken, person.token_id).revoked_at = now
    return {"signed_out": True}


@router.get("/me", dependencies=[authority.SIGNED_IN])
def me(person: Person = Depends(signed_in), session: Session = Depends(db)) -> dict:
    return me_payload(session, session.get(User, person.id), person.via)


# ── setup links ─────────────────────────────────────────────────────────────────


@router.get("/setup", dependencies=[authority.PUBLIC])  # credentialed by the link
def setup_info(token: str, session: Session = Depends(db)) -> dict:
    """Who a setup link is for, so the page can say so before asking for a
    password. Nothing is spent by reading it."""
    row = identity.read_setup_link(session, token)
    if row is None:
        raise HTTPException(status_code=404,
                            detail="this link has been used, has expired, or was replaced")
    user = session.get(User, row.user_id)
    return {"email": user.email, "display_name": user.display_name, "purpose": row.purpose}


@router.post("/setup", dependencies=[authority.PUBLIC])  # credentialed by the link
def setup(body: SetupIn, request: Request, response: Response,
          session: Session = Depends(db)) -> dict:
    user = identity.redeem_setup_link(session, body.token, body.password)
    plaintext = identity.open_session(session, user, request.headers.get("user-agent"))
    identity.set_session_cookie(response, plaintext)
    _audit(session, "user.password_set", user, {"via": "setup_link"})
    return me_payload(session, user, "session")


@router.post("/password", dependencies=[authority.SIGNED_IN])
def change_password(body: PasswordIn, person: Person = Depends(signed_in),
                    session: Session = Depends(db)) -> dict:
    _sole_session(person)
    user = identity.authenticate(session, person.email, body.current)
    try:
        identity.set_password(user, body.new)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Every other browser is signed out; this one stays, because it is the one
    # that just proved it knew the old password.
    identity.revoke_sessions(session, user.id, keep=person.session_id)
    _audit(session, "user.password_set", user, {"via": "password"})
    return {"changed": True}


# ── personal tokens ─────────────────────────────────────────────────────────────


def _token_row(row: UserToken) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat(),
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }


@router.get("/tokens", dependencies=[authority.SIGNED_IN])
def list_tokens(person: Person = Depends(signed_in),
                session: Session = Depends(db)) -> list[dict]:
    rows = session.execute(
        select(UserToken).where(UserToken.user_id == person.id)
        .order_by(UserToken.created_at.desc())
    ).scalars()
    return [_token_row(r) for r in rows]


@router.post("/tokens", dependencies=[authority.SIGNED_IN])
def mint_token(body: TokenIn, person: Person = Depends(signed_in),
               session: Session = Depends(db)) -> dict:
    _sole_session(person)
    user = session.get(User, person.id)
    row, plaintext = identity.mint_token(session, user, body.name, days=body.days)
    _audit(session, "user.token_mint", user, {"name": row.name, "via": "session"})
    return {"token": plaintext, **_token_row(row)}


@router.delete("/tokens/{token_id}", dependencies=[authority.SIGNED_IN])
def revoke_token(token_id: str, person: Person = Depends(signed_in),
                 session: Session = Depends(db)) -> dict:
    row = session.get(UserToken, token_id)
    # Somebody else's token is refused in the words of one that does not exist.
    if row is None or row.user_id != person.id:
        raise HTTPException(status_code=404, detail="no such token")
    if row.revoked_at is None:
        row.revoked_at = utcnow()
    return _token_row(row)


# ── sessions ────────────────────────────────────────────────────────────────────


@router.get("/sessions", dependencies=[authority.SIGNED_IN])
def list_sessions(person: Person = Depends(signed_in),
                  session: Session = Depends(db)) -> list[dict]:
    now = utcnow()
    rows = session.execute(
        select(UserSession).where(UserSession.user_id == person.id,
                                  UserSession.revoked_at.is_(None),
                                  UserSession.expires_at > now)
        .order_by(UserSession.last_seen_at.desc())
    ).scalars()
    return [{
        "id": r.id,
        "user_agent": r.user_agent,
        "created_at": r.created_at.isoformat(),
        "last_seen_at": r.last_seen_at.isoformat(),
        "current": r.id == person.session_id,
    } for r in rows if r.last_seen_at + identity.SESSION_IDLE >= now]


@router.delete("/sessions/{session_id}", dependencies=[authority.SIGNED_IN])
def revoke_session(session_id: str, person: Person = Depends(signed_in),
                   session: Session = Depends(db)) -> dict:
    row = session.get(UserSession, session_id)
    if row is None or row.user_id != person.id:
        raise HTTPException(status_code=404, detail="no such session")
    if row.revoked_at is None:
        row.revoked_at = utcnow()
    return {"revoked": True, "current": row.id == person.session_id}
