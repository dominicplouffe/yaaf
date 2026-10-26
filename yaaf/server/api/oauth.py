"""The OAuth redirect landing for MCP data sources.

Unauthenticated by bearer-token design, like the webhooks router: this URL is
where an external authorization server sends the chairman's *browser* back
after consent, and a redirect cannot carry the admin token. The credential is
the `state` parameter — 256 bits, single-use, expiring in fifteen minutes,
minted by the admin-authed start endpoint and matched here against the one
pending sign-in that knows it. Redeeming it also requires the authorization
code the provider just issued, and the code exchange is bound by PKCE to a
verifier that never left this server encrypted. A guessed or replayed state
gets a 400 page and nothing else.

Responses are HTML, not JSON: the audience is a person in a browser tab the
provider opened, not the SPA.
"""

from __future__ import annotations

import html
import logging
from contextlib import suppress
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import Project, utcnow
from yaaf.server import acting, mcp_oauth
from yaaf.server.deps import db

log = logging.getLogger("yaaf.oauth")

router = APIRouter()


def _page(title: str, detail: str, status_code: int = 200) -> HTMLResponse:
    # title and detail can carry provider-controlled text (an OAuth error
    # description, an exchange-failure message) on this unauthenticated route,
    # so every interpolated value is HTML-escaped — an authorization server
    # must not be able to script the dashboard origin in the chairman's tab.
    title = html.escape(title)
    detail = html.escape(detail)
    body = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>yaaf — {title}</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #111417; color: #e6e8ea;
         display: grid; place-items: center; min-height: 100vh; margin: 0; }}
  main {{ max-width: 26rem; padding: 2rem; text-align: center; }}
  h1 {{ font-size: 1.2rem; }}
  p {{ color: #9aa3ab; line-height: 1.5; }}
  a {{ color: #7ab8ff; }}
</style></head>
<body><main><h1>{title}</h1><p>{detail}</p>
<p><a href="/app/">Back to yaaf</a></p></main></body></html>"""
    return HTMLResponse(content=body, status_code=status_code)


def _find_pending(session: Session, state: str) -> tuple[mcp_oauth.SourceHome, dict] | None:
    """The one source whose pending sign-in minted this state — a project's
    data source, or an integration connected for every project. A linear scan
    is fine: pending flows are rare, short-lived, and this route only runs
    when a human finishes one."""
    from yaaf.core.models import Integration
    from yaaf.server.integrations import IntegrationHome, entry_of

    if not state:
        return None
    for project in session.execute(select(Project)).scalars():
        for entry in (project.integrations or {}).get("mcp_servers", []):
            pending = (entry.get("oauth") or {}).get("pending") or {}
            if pending.get("state") == state:
                return mcp_oauth.ProjectHome(session, project), entry
    for integration in session.execute(select(Integration)).scalars():
        pending = ((integration.entry or {}).get("oauth") or {}).get("pending") or {}
        if pending.get("state") == state:
            return IntegrationHome(integration), entry_of(integration)
    return None


@router.get("/mcp/callback")
def mcp_oauth_callback(state: str = "", code: str = "", error: str = "",
                       error_description: str = "",
                       session: Session = Depends(db)) -> HTMLResponse:
    from yaaf.core.crypto import decrypt
    from yaaf.server.api.admin import _audit, _oauth_checked

    # This route is unauthenticated; a real callback always carries a state and
    # either a code or an error. Reject the malformed shape before the pending
    # scan, so junk traffic cannot make each request cost a projects table load.
    if not state or not (code or error):
        return _page("Sign-in link not recognized",
                     "This is not a valid sign-in link. Press Connect again in the "
                     "project's Settings.", 400)

    found = _find_pending(session, state)
    if found is None:
        return _page("Sign-in link not recognized",
                     "This link was already used, or belongs to a sign-in that was "
                     "restarted. Press Connect again in the project's Settings.", 400)
    home, entry = found
    name = entry.get("name", "")
    where = ("the project's Settings" if home.project_id
             else "Settings → Integrations")
    oauth = dict(entry.get("oauth") or {})
    pending = dict(oauth.pop("pending", {}) or {})
    # The person who pressed Connect, completing their own sign-in in their own
    # browser. A state minted before that was recorded names the callback.
    starter = pending.get("by_user_id")
    acting.bind(acting.person(starter) if starter
                else acting.Actor("system", "oauth-callback"))

    def persist(status: str, error_detail: str | None = None) -> None:
        oauth["status"] = status
        if error_detail:
            oauth["error"] = error_detail
        else:
            oauth.pop("error", None)
        entry["oauth"] = oauth
        home.persist(entry)

    started = None
    with suppress(ValueError):
        started = datetime.fromisoformat(pending.get("created_at", ""))
    if started is None or utcnow() - started > timedelta(seconds=mcp_oauth.PENDING_TTL_S):
        persist("needs_auth", "the sign-in link expired before it was completed")
        return _page("Sign-in link expired",
                     "This sign-in took longer than fifteen minutes. Press Connect "
                     f"again in {where} for a fresh one.", 400)

    if error:
        detail = error_description or error
        persist("needs_auth", f"the provider reported: {detail}")
        _audit(session, home.project_id, f"{_subject(home)}.oauth_denied",
               _entity(home), home.project_id or name, {"name": name, "error": error})
        return _page("Sign-in not completed",
                     f"The provider reported: {detail}. Press Connect in {where} "
                     "to try again.", 400)

    client_secret = home.read(oauth.get("client_secret_secret"))
    try:
        tokens = mcp_oauth.exchange_code(
            oauth["token_endpoint"], code, decrypt(pending["verifier_cipher"]),
            oauth["client_id"], client_secret, pending["redirect_uri"],
            oauth.get("resource"))
    except (mcp_oauth.OAuthSetupError, KeyError) as exc:
        log.warning("OAuth code exchange failed for %s %s/%s: %s",
                    home.noun, home.project_id, name, exc)
        persist("needs_auth", f"the token exchange failed: {exc}")
        return _page("Sign-in failed",
                     f"The provider would not exchange the sign-in for a token: {exc}. "
                     f"Press Connect in {where} to try again.", 400)

    # One writer for freshly-minted tokens, shared with the refresh path, so
    # the two can never disagree on secret names, role grants, or status.
    entry["oauth"] = oauth
    mcp_oauth.store(home, entry, tokens)
    oauth = entry["oauth"]

    # Prove the connection while the chairman is still looking: the tab they
    # return to should already show a green check or a named failure.
    url = home.read(entry.get("url_secret"))
    if url:
        check = _oauth_checked(url, tokens["access_token"], entry)
        entry["last_check"] = {**check, "at": utcnow().isoformat()}
        home.persist(entry)

    _audit(session, home.project_id, f"{_subject(home)}.oauth_connect", _entity(home),
           home.project_id or name,
           {"name": name, "expires_at": oauth.get("expires_at"),
            "refresh_token": bool(tokens.get("refresh_token"))})
    return _page("Connected",
                 f"The {home.noun} '{name}' is signed in. You can close this tab "
                 "and return to yaaf.")


def _subject(home: mcp_oauth.SourceHome) -> str:
    """The audit verb's prefix, kept as it always was for a project source."""
    return "mcp_server" if home.project_id else "integration"


def _entity(home: mcp_oauth.SourceHome) -> str:
    return "project" if home.project_id else "integration"
