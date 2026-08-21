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

import logging
from contextlib import suppress
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import Project, utcnow
from yaaf.server import mcp_oauth
from yaaf.server.deps import db

log = logging.getLogger("yaaf.oauth")

router = APIRouter()


def _page(title: str, detail: str, status_code: int = 200) -> HTMLResponse:
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


def _find_pending(session: Session, state: str) -> tuple[Project, dict] | None:
    """The one (project, source entry) whose pending sign-in minted this
    state. A linear scan over projects is fine: pending flows are rare,
    short-lived, and this route only runs when a human finishes one."""
    if not state:
        return None
    for project in session.execute(select(Project)).scalars():
        for entry in (project.integrations or {}).get("mcp_servers", []):
            pending = (entry.get("oauth") or {}).get("pending") or {}
            if pending.get("state") == state:
                return project, entry
    return None


@router.get("/mcp/callback")
def mcp_oauth_callback(state: str = "", code: str = "", error: str = "",
                       error_description: str = "",
                       session: Session = Depends(db)) -> HTMLResponse:
    from yaaf.core.crypto import decrypt
    from yaaf.server.api.admin import _audit, _oauth_checked, _upsert_project_secret

    found = _find_pending(session, state)
    if found is None:
        return _page("Sign-in link not recognized",
                     "This link was already used, or belongs to a sign-in that was "
                     "restarted. Press Connect again in the project's Settings.", 400)
    project, entry = found
    name = entry.get("name", "")
    oauth = dict(entry.get("oauth") or {})
    pending = dict(oauth.pop("pending", {}) or {})

    def persist(status: str, error_detail: str | None = None) -> None:
        oauth["status"] = status
        if error_detail:
            oauth["error"] = error_detail
        else:
            oauth.pop("error", None)
        entry["oauth"] = oauth
        mcp_oauth.persist_entry(project, entry)

    started = None
    with suppress(ValueError):
        started = datetime.fromisoformat(pending.get("created_at", ""))
    if started is None or utcnow() - started > timedelta(seconds=mcp_oauth.PENDING_TTL_S):
        persist("needs_auth", "the sign-in link expired before it was completed")
        return _page("Sign-in link expired",
                     "This sign-in took longer than fifteen minutes. Press Connect "
                     "again in the project's Settings for a fresh one.", 400)

    if error:
        detail = error_description or error
        persist("needs_auth", f"the provider reported: {detail}")
        _audit(session, project.id, "mcp_server.oauth_denied", "project", project.id,
               {"name": name, "error": error})
        return _page("Sign-in not completed",
                     f"The provider reported: {detail}. Press Connect in Settings "
                     "to try again.", 400)

    client_secret = None
    if oauth.get("client_secret_secret"):
        from yaaf.server.api.admin import _project_secret_value

        client_secret = _project_secret_value(session, project.id,
                                              oauth["client_secret_secret"])
    try:
        tokens = mcp_oauth.exchange_code(
            oauth["token_endpoint"], code, decrypt(pending["verifier_cipher"]),
            oauth["client_id"], client_secret, pending["redirect_uri"],
            oauth.get("resource"))
    except (mcp_oauth.OAuthSetupError, KeyError) as exc:
        log.warning("OAuth code exchange failed for %s/%s: %s", project.id, name, exc)
        persist("needs_auth", f"the token exchange failed: {exc}")
        return _page("Sign-in failed",
                     f"The provider would not exchange the sign-in for a token: {exc}. "
                     "Press Connect in Settings to try again.", 400)

    token_row = _upsert_project_secret(
        session, project.id, f"mcp-{name}-token", tokens["access_token"],
        list(entry.get("roles") or []))
    entry["bearer_secret"] = token_row.name
    if tokens.get("refresh_token"):
        refresh_row = _upsert_project_secret(
            session, project.id, f"mcp-{name}-oauth-refresh", tokens["refresh_token"], [])
        oauth["refresh_secret"] = refresh_row.name
    oauth["expires_at"] = tokens.get("expires_at")
    oauth["obtained_at"] = utcnow().isoformat()
    persist("connected")

    # Prove the connection while the chairman is still looking: the tab they
    # return to should already show a green check or a named failure.
    url = None
    from yaaf.server.api.admin import _project_secret_value

    if entry.get("url_secret"):
        url = _project_secret_value(session, project.id, entry["url_secret"])
    if url:
        check = _oauth_checked(url, tokens["access_token"], entry)
        entry["last_check"] = {**check, "at": utcnow().isoformat()}
        mcp_oauth.persist_entry(project, entry)

    _audit(session, project.id, "mcp_server.oauth_connect", "project", project.id,
           {"name": name, "expires_at": oauth.get("expires_at"),
            "refresh_token": bool(tokens.get("refresh_token"))})
    return _page("Connected",
                 f"The data source '{name}' is signed in. You can close this tab "
                 "and return to yaaf.")
