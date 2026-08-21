"""OAuth 2.1 client for MCP data sources (MCP authorization spec, 2025-06-18).

Hosted MCP servers (Notion, Linear, Atlassian, ...) do not hand out static
bearer tokens: they are OAuth resource servers. Connecting one means walking
the whole ladder — RFC 9728 protected-resource metadata to find the
authorization server, RFC 8414 metadata to find its endpoints, RFC 7591
dynamic client registration where offered, then the authorization-code flow
with PKCE and an RFC 8707 `resource` parameter, and refresh-token renewal
ever after.

All of that happens here, on the control plane. The sandbox never learns any
of it: the access token is stored under the source's ordinary
`mcp-<name>-token` secret, so lease-time injection and the in-sandbox MCP
client keep working byte-for-byte as they do for a pasted bearer token.
`ensure_fresh_token` is the single renewal seam — the health check and the
run/chat lease paths both call it, and nothing else touches the token
endpoint.

Raw httpx throughout, with the same `transport=` test seam as
`_check_data_source`; production passes nothing.
"""

from __future__ import annotations

import base64
import hashlib
import json as jsonlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse

from sqlalchemy.orm import Session

from yaaf.core.models import Project, utcnow

log = logging.getLogger("yaaf.mcp_oauth")

# Renew this long before the recorded expiry. Half an hour covers the check
# round-trip and the gap between leasing a run and the agent's first tool
# call; a token that lives less than the margin is simply refreshed every
# lease, which is correct if noisy.
REFRESH_MARGIN_S = 1800

# A sign-in the chairman abandoned should not stay redeemable for days.
PENDING_TTL_S = 15 * 60

_TIMEOUT = 10


class OAuthSetupError(Exception):
    """Raised with a chairman-facing sentence — every message here is shown
    verbatim in the Settings UI, so say what happened and what to press."""


@dataclass
class Discovery:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    scopes_supported: list[str] = field(default_factory=list)
    # The canonical resource URL, echoed back to the authorization server as
    # the RFC 8707 `resource` parameter so the token it mints is audience-bound
    # to this MCP server and no other.
    resource: str = ""


def _client(transport=None):
    import httpx

    return httpx.Client(timeout=_TIMEOUT, follow_redirects=True, transport=transport)


def _get_json(client, url: str) -> dict | None:
    """A metadata document, or None — discovery walks ordered fallbacks, so a
    404 (or a page of HTML where JSON should be) is a step, not a failure."""
    import httpx

    try:
        response = client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    try:
        data = jsonlib.loads(response.text or "")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _well_known(base_url: str, suffix: str) -> list[str]:
    """RFC 8414/9728 well-known candidates for a URL that may carry a path:
    the path-inserted form first (the spec's rule for path-ful issuers), then
    the bare-origin form (what most servers actually serve)."""
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = (parsed.path or "").rstrip("/")
    candidates = []
    if path:
        candidates.append(f"{origin}/.well-known/{suffix}{path}")
    candidates.append(f"{origin}/.well-known/{suffix}")
    return candidates


def _resource_metadata_url(mcp_url: str, transport=None) -> str | None:
    """Ask the server itself: an unauthenticated MCP handshake at an OAuth
    server comes back 401 with a WWW-Authenticate header naming its RFC 9728
    resource-metadata document. Servers that skip the header fall through to
    the well-known guesses."""
    import httpx

    try:
        with _client(transport) as client:
            response = client.post(mcp_url, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "yaaf", "version": "1.0"}},
            }, headers={"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"})
    except httpx.HTTPError as exc:
        raise OAuthSetupError(
            "nothing answered at that address — check host, port and path "
            f"({exc.__class__.__name__})"
        ) from exc
    challenge = response.headers.get("www-authenticate", "")
    match = re.search(r'resource_metadata="([^"]+)"', challenge)
    return match.group(1) if match else None


def discover(mcp_url: str, transport=None) -> Discovery:
    """From an MCP URL to a usable authorization server, or an
    OAuthSetupError that says exactly which rung of the ladder was missing."""
    metadata_url = _resource_metadata_url(mcp_url, transport)
    with _client(transport) as client:
        resource_meta = None
        for candidate in ([metadata_url] if metadata_url else []) + \
                _well_known(mcp_url, "oauth-protected-resource"):
            resource_meta = _get_json(client, candidate)
            if resource_meta is not None:
                break

        issuer = mcp_url
        resource = mcp_url
        scopes: list[str] = []
        if resource_meta:
            servers = resource_meta.get("authorization_servers") or []
            if servers:
                issuer = servers[0]
            resource = resource_meta.get("resource") or mcp_url
            scopes = list(resource_meta.get("scopes_supported") or [])
        # No resource metadata at all: some servers are their own
        # authorization server and publish only RFC 8414 metadata at their
        # origin. Try that before giving up.

        auth_meta = None
        for suffix in ("oauth-authorization-server", "openid-configuration"):
            for candidate in _well_known(issuer, suffix):
                auth_meta = _get_json(client, candidate)
                if auth_meta and auth_meta.get("authorization_endpoint") \
                        and auth_meta.get("token_endpoint"):
                    break
                auth_meta = None
            if auth_meta:
                break

    if not auth_meta:
        raise OAuthSetupError(
            "the server did not publish OAuth discovery metadata — it may not "
            "support OAuth at all, or it may want a plain bearer token instead"
        )
    return Discovery(
        authorization_endpoint=auth_meta["authorization_endpoint"],
        token_endpoint=auth_meta["token_endpoint"],
        registration_endpoint=auth_meta.get("registration_endpoint"),
        scopes_supported=scopes or list(auth_meta.get("scopes_supported") or []),
        resource=resource,
    )


def register_client(disc: Discovery, redirect_uri: str,
                    transport=None) -> tuple[str, str | None]:
    """RFC 7591 dynamic registration as a public client (PKCE carries the
    proof; no secret to keep). Some servers issue a client_secret anyway —
    keep it and use it, refusing it would be pedantry."""
    import httpx

    if not disc.registration_endpoint:
        raise OAuthSetupError(
            "this server does not offer automatic client registration — paste "
            "a client ID (and secret, if it issued one) from the provider's "
            "developer console"
        )
    try:
        with _client(transport) as client:
            response = client.post(disc.registration_endpoint, json={
                "client_name": "yaaf",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            }, headers={"Content-Type": "application/json"})
    except httpx.HTTPError as exc:
        raise OAuthSetupError(
            f"could not reach the registration endpoint ({exc.__class__.__name__})"
        ) from exc
    if response.status_code >= 400:
        raise OAuthSetupError(
            "the authorization server refused to register yaaf as a client "
            f"(HTTP {response.status_code}) — paste a client ID from the "
            "provider's developer console instead"
        )
    try:
        registered = jsonlib.loads(response.text or "")
    except ValueError:
        registered = {}
    client_id = registered.get("client_id")
    if not client_id:
        raise OAuthSetupError(
            "the authorization server's registration reply had no client_id — "
            "paste one from the provider's developer console instead"
        )
    return client_id, registered.get("client_secret") or None


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorize_url(disc: Discovery, client_id: str, redirect_uri: str,
                        state: str, verifier: str, scope: str | None) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "resource": disc.resource,
    }
    effective_scope = scope if scope is not None else " ".join(disc.scopes_supported)
    if effective_scope:
        params["scope"] = effective_scope
    separator = "&" if "?" in disc.authorization_endpoint else "?"
    return f"{disc.authorization_endpoint}{separator}{urlencode(params)}"


def _token_request(token_endpoint: str, form: dict, client_id: str,
                   client_secret: str | None, resource: str | None,
                   transport=None) -> dict:
    """One shape for both grants: form-encoded POST, HTTP Basic when the
    client is confidential, client_id in the body when it is public. Returns
    {"access_token", "refresh_token"|None, "expires_at"|None} or raises
    OAuthSetupError carrying the server's own error words."""
    import httpx

    body = dict(form)
    if resource:
        body["resource"] = resource
    auth = None
    if client_secret:
        auth = (client_id, client_secret)
    else:
        body["client_id"] = client_id
    try:
        with _client(transport) as client:
            response = client.post(
                token_endpoint, data=body, auth=auth,
                headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise OAuthSetupError(
            f"could not reach the token endpoint ({exc.__class__.__name__})"
        ) from exc
    try:
        payload = jsonlib.loads(response.text or "")
    except ValueError:
        payload = {}
    if response.status_code >= 400 or "access_token" not in payload:
        error = payload.get("error", f"HTTP {response.status_code}")
        description = payload.get("error_description", "")
        raise OAuthSetupError(
            f"the authorization server refused ({error}"
            f"{': ' + description if description else ''})"
        )
    expires_at = None
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        expires_at = (utcnow() + timedelta(seconds=int(expires_in))).isoformat()
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token") or None,
        "expires_at": expires_at,
        "scope": payload.get("scope") or None,
    }


def exchange_code(token_endpoint: str, code: str, verifier: str, client_id: str,
                  client_secret: str | None, redirect_uri: str,
                  resource: str | None, transport=None) -> dict:
    return _token_request(token_endpoint, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }, client_id, client_secret, resource, transport)


def refresh_tokens(token_endpoint: str, refresh_token: str, client_id: str,
                   client_secret: str | None, resource: str | None,
                   transport=None) -> dict:
    return _token_request(token_endpoint, {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }, client_id, client_secret, resource, transport)


def _expires_soon(expires_at: str | None, margin_s: int) -> bool:
    if not expires_at:
        return False  # the server never said; assume the token outlives us
    try:
        deadline = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    if deadline.tzinfo is None:
        from datetime import UTC

        deadline = deadline.replace(tzinfo=UTC)
    return deadline - utcnow() < timedelta(seconds=margin_s)


def persist_entry(project: Project, entry: dict) -> None:
    """Write the (mutated) source entry back so it actually reaches the
    database. Reassigning the attribute is not enough on its own: `entry` is
    usually the loaded dict edited in place, so the "new" value compares equal
    to the (also-mutated) loaded value and SQLAlchemy's flush skips the UPDATE.
    flag_modified forces it."""
    from sqlalchemy.orm.attributes import flag_modified

    servers = [entry if s.get("name") == entry.get("name") else s
               for s in (project.integrations or {}).get("mcp_servers", [])]
    project.integrations = {**(project.integrations or {}), "mcp_servers": servers}
    flag_modified(project, "integrations")


def ensure_fresh_token(session: Session, project: Project, entry: dict, *,
                       margin_s: int = REFRESH_MARGIN_S, transport=None) -> str | None:
    """The access token this source should present right now — refreshed and
    re-stored when it is close to expiry, stale when renewal is impossible,
    None only when the source was never connected. Both callers (the health
    check and the lease path) get the same behavior: a broken renewal marks
    the source needs_reauth and keeps the old token mounted, so the failure
    shows up as a named auth error instead of a silently absent source."""
    from yaaf.server.api.admin import _project_secret_value

    if entry.get("auth") != "oauth":
        return None
    oauth = dict(entry.get("oauth") or {})
    name = entry.get("name", "")
    access = None
    if entry.get("bearer_secret"):
        access = _project_secret_value(session, project.id, entry["bearer_secret"])
    if access and not _expires_soon(oauth.get("expires_at"), margin_s):
        return access

    refresh_name = oauth.get("refresh_secret")
    refresh = _project_secret_value(session, project.id, refresh_name) if refresh_name else None
    if not refresh or not oauth.get("token_endpoint") or not oauth.get("client_id"):
        if access is not None:
            # Expiring, and nothing to renew with: the provider issued no
            # refresh token. Only a fresh sign-in can help.
            oauth["status"] = "needs_reauth"
            oauth["error"] = "the access token is expiring and the provider issued no refresh token"
            entry["oauth"] = oauth
            persist_entry(project, entry)
        return access

    client_secret = None
    if oauth.get("client_secret_secret"):
        client_secret = _project_secret_value(session, project.id, oauth["client_secret_secret"])
    try:
        tokens = refresh_tokens(oauth["token_endpoint"], refresh, oauth["client_id"],
                                client_secret, oauth.get("resource"), transport=transport)
    except OAuthSetupError as exc:
        log.warning("token refresh failed for data source %s/%s: %s",
                    project.id, name, exc)
        oauth["status"] = "needs_reauth"
        oauth["error"] = str(exc)
        entry["oauth"] = oauth
        persist_entry(project, entry)
        return access

    entry["oauth"] = oauth
    store_tokens(session, project, entry, tokens)
    return tokens["access_token"]


def store_tokens(session: Session, project: Project, entry: dict, tokens: dict) -> None:
    """The one place freshly-minted OAuth tokens land — the sign-in callback and
    every later refresh both call it, so the access token's role grants, the
    refresh secret's name, and the connected/expires_at/obtained_at bookkeeping
    can never drift between the two paths. The access token reuses the source's
    ordinary mcp-<name>-token secret (so the sandbox path is unchanged); the
    refresh token is stored ungranted (role_scopes=[]) and never injected."""
    from yaaf.server.api.admin import _upsert_project_secret

    name = entry.get("name", "")
    oauth = dict(entry.get("oauth") or {})
    token_secret = _upsert_project_secret(
        session, project.id, f"mcp-{name}-token", tokens["access_token"],
        list(entry.get("roles") or []))
    entry["bearer_secret"] = token_secret.name
    if tokens.get("refresh_token"):
        # Rotation: the old refresh token died the moment this one was issued.
        refresh_name = oauth.get("refresh_secret") or f"mcp-{name}-oauth-refresh"
        _upsert_project_secret(session, project.id, refresh_name,
                               tokens["refresh_token"], [])
        oauth["refresh_secret"] = refresh_name
    oauth["status"] = "connected"
    oauth["expires_at"] = tokens.get("expires_at")
    oauth["obtained_at"] = utcnow().isoformat()
    oauth.pop("error", None)
    entry["oauth"] = oauth
    persist_entry(project, entry)
