"""OAuth for a shared data source: one sign-in, every project it is granted to.

The protocol is `yaaf.server.mcp_oauth`'s, step for step: discovery, client
registration, the authorization-code flow with PKCE, refresh. What differs is
where the result lives. A project source keeps its tokens as that project's
secrets. A shared source keeps them encrypted on its own `shared_data_sources`
row, so there is exactly one access token and one refresh token however many
projects mount the source.

One copy is what makes sharing sound. Providers rotate refresh tokens on use,
so two copies of one connection would invalidate each other on the first
refresh. One copy, renewed under a row lock, is renewed once and read by
everybody. `ensure_fresh` is the only code that touches the token endpoint for
a shared source.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from yaaf.core import shared_store
from yaaf.core.models import SharedDataSource, utcnow
from yaaf.server import mcp_oauth

log = logging.getLogger("yaaf.shared_oauth")


def is_oauth(row: SharedDataSource) -> bool:
    return (row.entry or {}).get("auth") == "oauth"


def persist(row: SharedDataSource, entry: dict) -> None:
    """Write the entry back. flag_modified because an entry edited in place
    compares equal to the loaded one, and the flush would skip it."""
    row.entry = entry
    flag_modified(row, "entry")


def _decrypt(cipher: str | None) -> str | None:
    from yaaf.core.crypto import decrypt

    if not cipher:
        return None
    try:
        return decrypt(cipher)
    except Exception:
        log.warning("a shared data source's OAuth value failed to decrypt")
        return None


def store_tokens(row: SharedDataSource, tokens: dict) -> None:
    """Where freshly minted tokens land, from the sign-in and from every
    refresh. The access token goes in `bearer_cipher`, so the lease injects it
    exactly as it injects a pasted token."""
    from yaaf.core.crypto import encrypt

    entry = dict(row.entry or {})
    oauth = dict(entry.get("oauth") or {})
    row.bearer_cipher = encrypt(tokens["access_token"])
    if tokens.get("refresh_token"):
        # Rotation: the old refresh token died the moment this one was issued.
        oauth["refresh_cipher"] = encrypt(tokens["refresh_token"])
    oauth["status"] = "connected"
    oauth["expires_at"] = tokens.get("expires_at")
    oauth["obtained_at"] = utcnow().isoformat()
    oauth.pop("error", None)
    oauth.pop("pending", None)
    entry["oauth"] = oauth
    row.updated_at = utcnow()
    persist(row, entry)


def ensure_fresh(session: Session, row: SharedDataSource, *,
                 margin_s: int = mcp_oauth.REFRESH_MARGIN_S, transport=None) -> str | None:
    """The access token this source should present now. Renewed when it is
    close to expiry, stale when renewal is impossible, None only when it was
    never connected. A failed renewal marks it needs_reauth and keeps the old
    token mounted, as a project source does.

    The row is re-read under a lock first. Two leases on two projects can ask
    at the same moment, and the second must find the first one's new token
    rather than spend the refresh token the first one just rotated."""
    if not is_oauth(row):
        return None
    row = session.execute(
        select(SharedDataSource).where(SharedDataSource.id == row.id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one()
    entry = dict(row.entry or {})
    oauth = dict(entry.get("oauth") or {})
    access = _decrypt(row.bearer_cipher)
    if access and not mcp_oauth._expires_soon(oauth.get("expires_at"), margin_s):
        return access

    refresh = _decrypt(oauth.get("refresh_cipher"))
    if not refresh or not oauth.get("token_endpoint") or not oauth.get("client_id"):
        if access is not None:
            oauth["status"] = "needs_reauth"
            oauth["error"] = ("the access token is expiring and the provider issued no "
                              "refresh token")
            entry["oauth"] = oauth
            persist(row, entry)
        return access
    try:
        tokens = mcp_oauth.refresh_tokens(
            oauth["token_endpoint"], refresh, oauth["client_id"],
            _decrypt(oauth.get("client_secret_cipher")), oauth.get("resource"),
            transport=transport)
    except mcp_oauth.OAuthSetupError as exc:
        log.warning("token refresh failed for shared data source %s: %s", row.name, exc)
        oauth["status"] = "needs_reauth"
        oauth["error"] = str(exc)
        entry["oauth"] = oauth
        persist(row, entry)
        return access
    store_tokens(row, tokens)
    return tokens["access_token"]


def refresh_for_role(session: Session, project_id: str, role_slug: str) -> None:
    """Renew every shared OAuth source this role is about to mount."""
    for held in shared_store.received(session, project_id):
        if held.kind == shared_store.DATA_SOURCE and role_slug in held.roles \
                and is_oauth(held.item):
            ensure_fresh(session, held.item)


def find_pending(session: Session, state: str) -> SharedDataSource | None:
    """The shared source whose unfinished sign-in minted this state."""
    if not state:
        return None
    for row in shared_store.data_sources(session):
        if ((row.entry or {}).get("oauth") or {}).get("pending", {}).get("state") == state:
            return row
    return None
