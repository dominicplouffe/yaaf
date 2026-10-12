"""The other end of the ladder: yaaf as an OAuth 2.1 authorization server.

`yaaf/server/mcp_oauth.py` walks this ladder as a *client*, to reach hosted MCP
data sources. This serves it, because claude.ai's connector offers no field for
a bearer header — OAuth is not a nicety here, it is the only door into the
browser, and the browser is a requirement.

Two roles in one process, kept legible by keeping them in one file:

- **Resource server** — `/mcp` validates access tokens, and an unauthenticated
  request answers 401 with the RFC 9728 pointer that starts discovery.
- **Authorization server** — registration (RFC 7591), the chairman's consent
  screen, and the token endpoint (OAuth 2.1 with PKCE and RFC 8707 audiences).

The rules that are not negotiable, each enforced below rather than described:
PKCE `S256` required, exact `redirect_uri` match, single-use codes with a
60-second life, refresh rotation that treats a replayed token as theft, and an
audience bound to this install — a token minted for another resource is refused
here even when it is otherwise perfectly valid.
"""

from __future__ import annotations

import base64
import hashlib
import html
import secrets as pysecrets
from datetime import timedelta
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.models import (
    AuditLog,
    McpClient,
    McpToken,
    OAuthClient,
    OAuthCode,
    User,
    utcnow,
)
from yaaf.core.tokens import hash_token, mint_prefixed
from yaaf.server import identity, login
from yaaf.server.mcp.auth import SCOPES, base_url, canonical_resource, mcp_db

#: An access token outlives a conversation, not an afternoon.
ACCESS_TTL = timedelta(hours=1)
#: A code outlives the redirect that carries it and nothing more.
CODE_TTL = timedelta(seconds=60)
#: Registration is unauthenticated by design; this is the other wall around it.
MAX_CLIENTS = 200

well_known = APIRouter()
router = APIRouter()


def unroutable_base_url() -> str | None:
    """The advertised origin, when it is one no outside client can reach.

    Every endpoint in both discovery documents is built from `base_url()`, and
    with `YAAF_PUBLIC_BASE_URL` unset that falls back to `YAAF_API_BASE_URL` —
    which in a container deployment is an internal name like `http://api:8080`.
    Nothing fails here when it does: the documents serve 200, and the connector
    that reads them fails somewhere we never see, on the first endpoint it
    tries. That is registration, so the only symptom the chairman is given is a
    client reporting it could not register, pointing at the one file that is
    fine.

    So say it at startup instead. Returns the offending origin, or None when
    the origin is at least plausibly public.
    """
    if settings().public_base_url:
        return None
    origin = base_url()
    host = (urlparse(origin).hostname or "").lower()
    # A bare label ("api", "web") is a container alias; loopback is a dev
    # default. Anything dotted is a hostname the outside world could resolve,
    # and this is a warning, not a gate — we do not guess further than that.
    if "." in host and host not in ("127.0.0.1", "0.0.0.0"):
        return None
    return origin


def challenge_ok(verifier: str, challenge: str) -> bool:
    digest = hashlib.sha256(verifier.encode()).digest()
    return pysecrets.compare_digest(
        base64.urlsafe_b64encode(digest).decode().rstrip("="), challenge)


# ── discovery ───────────────────────────────────────────────────────────────────
#
# Both documents are served unauthenticated, because a client that cannot read
# them cannot discover how to authenticate — which is the whole point of them.


def _protected_resource() -> dict:
    return {
        "resource": canonical_resource(),
        "authorization_servers": [base_url()],
        "scopes_supported": list(SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_documentation": "https://github.com/dominicplouffe/yaaf",
    }


@well_known.get("/.well-known/oauth-protected-resource")
def protected_resource() -> dict:
    return _protected_resource()


@well_known.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource_at_path() -> dict:
    """The path-inserted form. Our own client tries this one first, and so do
    others — a server that serves only the bare form is discovered by luck."""
    return _protected_resource()


@well_known.get("/.well-known/oauth-authorization-server")
def authorization_server() -> dict:
    root = base_url()
    return {
        "issuer": root,
        "authorization_endpoint": f"{root}/oauth/authorize",
        "token_endpoint": f"{root}/oauth/token",
        "registration_endpoint": f"{root}/oauth/register",
        "revocation_endpoint": f"{root}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        # S256 alone. Advertising "plain" invites a downgrade for no gain, and
        # every client that matters has done S256 for years.
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported": list(SCOPES),
    }


@well_known.get("/.well-known/oauth-authorization-server/mcp")
def authorization_server_at_path() -> dict:
    return authorization_server()


# ── registration ────────────────────────────────────────────────────────────────


def _redirect_allowed(uri: str) -> bool:
    parsed = urlparse(uri)
    if parsed.scheme not in ("https", "http"):
        return False
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
        return False
    allowed = {h.strip().lower() for h in settings().mcp_redirect_hosts.split(",") if h.strip()}
    host = (parsed.hostname or "").lower()
    return any(host == entry or host.endswith(f".{entry}") for entry in allowed)


@router.post("/register")
def register(body: dict, session: Session = Depends(mcp_db)) -> JSONResponse:
    """RFC 7591. Registering says only "this software exists"; it grants nothing.

    Anyone who can reach this endpoint can create a row, which is what the spec
    intends — so the limits are on what a row may *say*: a redirect target
    outside the allowlist is refused, and the table has a ceiling.
    """
    uris = [u for u in (body.get("redirect_uris") or []) if isinstance(u, str)]
    if not uris:
        return _oauth_error("invalid_redirect_uri", "redirect_uris is required", 400)
    bad = [u for u in uris if not _redirect_allowed(u)]
    if bad:
        return _oauth_error(
            "invalid_redirect_uri",
            f"redirect host not allowed here: {', '.join(bad)}. "
            "Set YAAF_MCP_REDIRECT_HOSTS if this client is expected.", 400)
    registered = session.execute(select(func.count(OAuthClient.id))).scalar_one()
    if registered >= MAX_CLIENTS:
        return _oauth_error("invalid_request", "too many registered clients", 429)

    client_id = f"yaafc_{pysecrets.token_urlsafe(18)}"
    secret = None
    row = OAuthClient(
        id=ids.new_id(ids.OAUTH_CLIENT),
        client_id=client_id,
        name=str(body.get("client_name") or "unnamed client")[:120],
        redirect_uris=uris,
        source="dcr",
    )
    # Public clients are the norm — PKCE carries the proof and there is nothing
    # to keep secret in a desktop app. A client that asks for a secret gets one.
    if body.get("token_endpoint_auth_method") == "client_secret_post":
        secret = pysecrets.token_urlsafe(32)
        row.client_secret_hash = hash_token(secret)
    session.add(row)
    payload = {
        "client_id": client_id,
        "client_name": row.name,
        "redirect_uris": uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_method": "client_secret_post" if secret else "none",
    }
    if secret:
        payload["client_secret"] = secret
    return JSONResponse(status_code=201, content=payload)


# ── the consent screen ──────────────────────────────────────────────────────────


@router.get("/authorize")
def authorize(request: Request, session: Session = Depends(mcp_db)) -> HTMLResponse:
    params = dict(request.query_params)
    problem = _validate_authorize(session, params)
    if problem:
        # Rendered, never redirected: an error page cannot be turned into an
        # open redirect, and a bad request here means we do not yet trust the
        # address we were told to send the browser to.
        return _page("Cannot continue", f"<p>{html.escape(problem)}</p>", status=400)

    user = login.read(session, request.cookies.get(login.COOKIE))
    if user is None:
        return _page("Sign in to yaaf", _login_form(params))
    return _page("Approve this client", _consent_form(session, user, params))


@router.post("/authorize", response_model=None)
async def authorize_submit(
    request: Request,
    session: Session = Depends(mcp_db),
) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    action = str(form.get("action") or "")
    email = str(form.get("email") or "")
    password = str(form.get("password") or "")
    params = {k: v for k, v in form.items()
              if k not in ("action", "email", "password", "scope_grant")}
    problem = _validate_authorize(session, params)
    if problem:
        return _page("Cannot continue", f"<p>{html.escape(problem)}</p>", status=400)

    if action == "login":
        # The same door as the dashboard's, throttle included: a consent screen
        # that took unlimited guesses would be the cheap way in.
        try:
            user = identity.authenticate(session, email, password)
        except HTTPException as exc:
            message = ("Too many wrong passwords for this address. Wait a few minutes."
                       if exc.status_code == 429 else "That email and password did not match.")
            return _page("Sign in to yaaf", f'<p class="bad">{message}</p>'
                         + _login_form(params), status=exc.status_code)
        response = _page("Approve this client", _consent_form(session, user, params))
        response.set_cookie(login.COOKIE, login.issue(user), httponly=True, samesite="lax",
                            secure=base_url().startswith("https"), max_age=login.TTL_S,
                            path="/oauth")
        return response

    user = login.read(session, request.cookies.get(login.COOKIE))
    if user is None:
        return _page("Sign in to yaaf", _login_form(params), status=401)
    if action != "approve":
        return _redirect_back(params, {"error": "access_denied"})

    granted = [s for s in form.getlist("scope_grant") if s in SCOPES]
    if not granted:
        return _page("Nothing to approve",
                     "<p>No scope was ticked, so there is nothing to grant.</p>", status=400)

    code, code_hash = mint_prefixed("yac")
    session.add(OAuthCode(
        id=ids.new_id(ids.OAUTH_CODE),
        code_hash=code_hash,
        oauth_client_id=params["client_id"],
        user_id=user.id,
        scopes=sorted(set(granted)),
        code_challenge=params["code_challenge"],
        redirect_uri=params["redirect_uri"],
        resource=params.get("resource") or canonical_resource(),
        expires_at=utcnow() + CODE_TTL,
    ))
    return _redirect_back(params, {"code": code})


def _validate_authorize(session: Session, params: dict) -> str | None:
    client_id = params.get("client_id")
    client = session.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    ).scalar_one_or_none() if client_id else None
    if client is None:
        return "unknown client_id — register the client first."
    redirect_uri = params.get("redirect_uri", "")
    # Exact match, not prefix: "starts with" is how a redirect allowlist leaks.
    if redirect_uri not in (client.redirect_uris or []):
        return "redirect_uri does not match this client's registration."
    if params.get("response_type") != "code":
        return "only response_type=code is supported."
    if params.get("code_challenge_method") != "S256" or not params.get("code_challenge"):
        return "PKCE with code_challenge_method=S256 is required."
    resource = params.get("resource")
    if resource and resource.rstrip("/") != canonical_resource().rstrip("/"):
        return f"this server only issues tokens for {canonical_resource()}."
    return None


def _hidden(params: dict) -> str:
    return "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(str(v))}">'
        for k, v in params.items())


def _login_form(params: dict) -> str:
    return f"""
    <form method="post" action="/oauth/authorize">
      {_hidden(params)}
      <input type="hidden" name="action" value="login">
      <label>Email<input name="email" type="email" autocomplete="username" required></label>
      <label>Password<input name="password" type="password"
             autocomplete="current-password" required></label>
      <button type="submit">Sign in</button>
    </form>
    <p class="muted">Your yaaf account — the same email and password you sign
    in to the dashboard with.</p>
    """


def _consent_form(session: Session, user: User, params: dict) -> str:
    client = session.execute(
        select(OAuthClient).where(OAuthClient.client_id == params["client_id"])
    ).scalar_one()
    asked = [s for s in (params.get("scope") or "read").split() if s in SCOPES] or ["read"]
    host = urlparse(params["redirect_uri"]).netloc

    scope_rows = "".join(
        f'<label class="row"><input type="checkbox" name="scope_grant" value="{s}"'
        f'{" checked" if s in asked else ""}> <b>{s}</b> — {html.escape(_describe(s))}</label>'
        for s in SCOPES)

    return f"""
    <p><b>{html.escape(client.name)}</b> is asking to act for
       <b>{html.escape(user.email)}</b>, and will be sent back to
       <code>{html.escape(host)}</code>.</p>
    <form method="post" action="/oauth/authorize">
      {_hidden(params)}
      <h3>What it may do</h3>{scope_rows}
      <p class="muted">On every project you are a member of, including ones
      you join later — reading any of them, deciding only where you chair or
      co-chair. There is no project list to keep in step here. Untick anything
      this client does not need. Every write it makes is recorded as
      machine-mediated, and you can revoke it at any time with
      <code>yaaf mcp-token revoke</code>.</p>
      <button type="submit" name="action" value="approve">Approve</button>
      <button type="submit" name="action" value="deny" class="plain">Deny</button>
    </form>
    """


def _describe(scope: str) -> str:
    return {
        "read": "see every project you are on: its plan, spend, work and proof",
        "steer": "speak to the CEO, decide approvals, accept or cut scope, sign dates",
        "create": "create new projects and clone existing ones",
        "configure": "change settings, teams, data sources and credentials",
    }[scope]


def _redirect_back(params: dict, extra: dict) -> RedirectResponse:
    query = {**extra}
    if params.get("state"):
        query["state"] = params["state"]
    joiner = "&" if "?" in params["redirect_uri"] else "?"
    return RedirectResponse(f"{params['redirect_uri']}{joiner}{urlencode(query)}",
                            status_code=302)


# ── token ───────────────────────────────────────────────────────────────────────


@router.post("/token")
def token(
    grant_type: str = Form(""),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    code_verifier: str = Form(""),
    refresh_token: str = Form(""),
    resource: str = Form(""),
    session: Session = Depends(mcp_db),
) -> JSONResponse:
    client = session.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    ).scalar_one_or_none()
    if client is None:
        return _oauth_error("invalid_client", "unknown client_id")
    if client.client_secret_hash and not pysecrets.compare_digest(
            client.client_secret_hash, hash_token(client_secret or "")):
        return _oauth_error("invalid_client", "bad client_secret")

    if grant_type == "authorization_code":
        return _exchange_code(session, client, code, redirect_uri, code_verifier, resource)
    if grant_type == "refresh_token":
        return _refresh(session, client, refresh_token)
    return _oauth_error("unsupported_grant_type", f"{grant_type!r} is not supported")


def _exchange_code(session: Session, client: OAuthClient, code: str, redirect_uri: str,
                   verifier: str, resource: str) -> JSONResponse:
    row = session.execute(
        select(OAuthCode).where(OAuthCode.code_hash == hash_token(code or ""))
    ).scalar_one_or_none()
    if row is None or row.oauth_client_id != client.client_id:
        return _oauth_error("invalid_grant", "unknown code")
    if row.used_at is not None:
        return _oauth_error("invalid_grant", "this code was already redeemed")
    if row.expires_at < utcnow():
        return _oauth_error("invalid_grant", "this code expired")
    if row.redirect_uri != redirect_uri:
        return _oauth_error("invalid_grant", "redirect_uri does not match the request")
    if not verifier or not challenge_ok(verifier, row.code_challenge):
        return _oauth_error("invalid_grant", "PKCE verification failed")
    if resource and resource.rstrip("/") != row.resource.rstrip("/"):
        return _oauth_error("invalid_target", "resource does not match the one consented to")

    row.used_at = utcnow()
    grant = McpClient(
        id=ids.new_id(ids.MCP_CLIENT),
        name=client.name[:80],
        user_id=row.user_id,
        # Exactly what the consent screen granted. Widening at the token
        # endpoint is the classic escalation, so nothing here reads a scope
        # parameter — the code carries the answer.
        scopes=list(row.scopes),
        oauth_client_id=client.id,
    )
    session.add(grant)
    session.flush()
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=None,
        actor=f"user:{row.user_id} via mcp:{grant.name}",
        action="mcp_client.consent", entity="mcp_client", entity_id=grant.id,
        detail={"scopes": grant.scopes, "oauth_client": client.client_id},
    ))
    return _issue_pair(session, grant, row.resource)


def _refresh(session: Session, client: OAuthClient, presented: str) -> JSONResponse:
    row = session.execute(
        select(McpToken).where(McpToken.token_hash == hash_token(presented or ""),
                               McpToken.kind == "refresh")
    ).scalar_one_or_none()
    if row is None:
        return _oauth_error("invalid_grant", "unknown refresh token")
    grant = session.get(McpClient, row.client_id)
    if grant is None or grant.revoked_at is not None:
        return _oauth_error("invalid_grant", "this grant was revoked")
    if row.rotated_to is not None or row.revoked_at is not None:
        # A rotated refresh token presented again is either a replay or a
        # theft, and the two are indistinguishable from here. Assume the worse
        # one and take the whole grant down.
        grant.revoked_at = utcnow()
        for token_row in session.execute(
                select(McpToken).where(McpToken.client_id == grant.id)).scalars():
            token_row.revoked_at = token_row.revoked_at or utcnow()
        session.add(AuditLog(
            id=ids.new_id(ids.AUDIT), project_id=None,
            actor=f"user:{grant.user_id} via mcp:{grant.name}",
            action="mcp_client.refresh_replayed", entity="mcp_client", entity_id=grant.id,
            detail={"revoked": True},
        ))
        return _oauth_error("invalid_grant",
                            "this refresh token was already used; the grant has been revoked")
    return _issue_pair(session, grant, row.resource, rotating=row)


def _issue_pair(session: Session, grant: McpClient, resource: str,
                rotating: McpToken | None = None) -> JSONResponse:
    access, access_hash = mint_prefixed("ymt")
    refresh, refresh_hash = mint_prefixed("ymr")
    access_row = McpToken(id=ids.new_id(ids.MCP_TOKEN), client_id=grant.id, kind="access",
                          token_hash=access_hash, resource=resource,
                          expires_at=utcnow() + ACCESS_TTL)
    refresh_row = McpToken(id=ids.new_id(ids.MCP_TOKEN), client_id=grant.id, kind="refresh",
                           token_hash=refresh_hash, resource=resource)
    session.add_all([access_row, refresh_row])
    session.flush()
    if rotating is not None:
        rotating.rotated_to = refresh_row.id
        rotating.revoked_at = utcnow()
    return JSONResponse({
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": int(ACCESS_TTL.total_seconds()),
        "refresh_token": refresh,
        "scope": " ".join(grant.scopes or []),
    })


@router.post("/revoke")
def revoke(token: str = Form(""), session: Session = Depends(mcp_db)) -> dict:
    """RFC 7009. Always 200, by spec — a revocation endpoint that says which
    tokens exist is an oracle."""
    row = session.execute(
        select(McpToken).where(McpToken.token_hash == hash_token(token or ""))
    ).scalar_one_or_none()
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()
    return {}


# ── presentation ────────────────────────────────────────────────────────────────


def _oauth_error(code: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": code, "error_description": description})


STYLE = """
body{font:15px/1.5 system-ui,sans-serif;max-width:34rem;margin:3rem auto;padding:0 1.25rem;
color:#111}h1{font-size:1.2rem}h3{font-size:.95rem;margin:1.4rem 0 .4rem}
label.row{display:block;padding:.3rem 0}input[type=email],input[type=password]{display:block;
width:100%;padding:.5rem;margin:.25rem 0 .9rem;border:1px solid #bbb;border-radius:4px}
button{padding:.5rem 1rem;border:0;border-radius:4px;background:#111;color:#fff;
font-size:.95rem;cursor:pointer;margin-right:.5rem}
button.plain{background:#fff;color:#666;border:1px solid #ccc}
.muted{color:#666;font-size:.87rem}.bad{color:#a00}code{background:#f2f2f2;padding:.1rem .3rem}
"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(status_code=status, content=(
        f"<!doctype html><meta charset=utf-8><title>{html.escape(title)} — yaaf</title>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<style>{STYLE}</style><h1>{html.escape(title)}</h1>{body}"))
