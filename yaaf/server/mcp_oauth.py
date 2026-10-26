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


class SourceHome:
    """Where one OAuth-connected source keeps its tokens and its entry.

    The ladder above is the same for a project's data source and for an
    integration the administrator connected for every project; only the
    storage differs. A project source keeps its tokens as that project's
    secrets, granted to the roles that mount it; an integration keeps them in
    its own encrypted vault, granted to no role at all, because a run receives
    them only through an attachment. One renewal path for both is what keeps
    the two from drifting on rotation, expiry and needs_reauth.
    """

    #: What the sign-in pages call it.
    noun = "data source"
    #: The project an audit row belongs to, None above every project.
    project_id: str | None = None

    def key(self, entry: dict, part: str) -> str:
        """The name a part ("token", "oauth-refresh", "oauth-client") is stored under."""
        raise NotImplementedError

    def read(self, name: str | None) -> str | None:
        raise NotImplementedError

    def write(self, entry: dict, name: str, value: str, *, granted: bool) -> str:
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError

    def persist(self, entry: dict) -> None:
        raise NotImplementedError


class ProjectHome(SourceHome):
    def __init__(self, session: Session, project: Project):
        self.session, self.project, self.project_id = session, project, project.id

    def key(self, entry: dict, part: str) -> str:
        return f"mcp-{entry.get('name', '')}-{part}"

    def read(self, name: str | None) -> str | None:
        from yaaf.server.api.admin import _project_secret_value

        return _project_secret_value(self.session, self.project.id, name) if name else None

    def write(self, entry: dict, name: str, value: str, *, granted: bool) -> str:
        from yaaf.server.api.admin import _upsert_project_secret

        roles = list(entry.get("roles") or []) if granted else []
        return _upsert_project_secret(self.session, self.project.id, name, value, roles).name

    def delete(self, name: str) -> None:
        from yaaf.server.api.admin import _delete_project_secret

        _delete_project_secret(self.session, self.project.id, name)

    def persist(self, entry: dict) -> None:
        persist_entry(self.project, entry)


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
    """A project data source's current access token (see `ensure_fresh`)."""
    return ensure_fresh(ProjectHome(session, project), entry, margin_s=margin_s,
                        transport=transport)


def ensure_fresh(home: SourceHome, entry: dict, *, margin_s: int = REFRESH_MARGIN_S,
                 transport=None) -> str | None:
    """The access token this source should present right now — refreshed and
    re-stored when it is close to expiry, stale when renewal is impossible,
    None only when the source was never connected. Both callers (the health
    check and the lease path) get the same behavior: a broken renewal marks
    the source needs_reauth and keeps the old token mounted, so the failure
    shows up as a named auth error instead of a silently absent source."""
    if entry.get("auth") != "oauth":
        return None
    oauth = dict(entry.get("oauth") or {})
    name = entry.get("name", "")
    access = home.read(entry.get("bearer_secret"))
    if access and not _expires_soon(oauth.get("expires_at"), margin_s):
        return access

    refresh = home.read(oauth.get("refresh_secret"))
    if not refresh or not oauth.get("token_endpoint") or not oauth.get("client_id"):
        if access is not None:
            # Expiring, and nothing to renew with: the provider issued no
            # refresh token. Only a fresh sign-in can help.
            oauth["status"] = "needs_reauth"
            oauth["error"] = "the access token is expiring and the provider issued no refresh token"
            entry["oauth"] = oauth
            home.persist(entry)
        return access

    client_secret = home.read(oauth.get("client_secret_secret"))
    try:
        tokens = refresh_tokens(oauth["token_endpoint"], refresh, oauth["client_id"],
                                client_secret, oauth.get("resource"), transport=transport)
    except OAuthSetupError as exc:
        log.warning("token refresh failed for %s %s/%s: %s",
                    home.noun, home.project_id, name, exc)
        oauth["status"] = "needs_reauth"
        oauth["error"] = str(exc)
        entry["oauth"] = oauth
        home.persist(entry)
        return access

    entry["oauth"] = oauth
    store(home, entry, tokens)
    return tokens["access_token"]


def store_tokens(session: Session, project: Project, entry: dict, tokens: dict) -> None:
    store(ProjectHome(session, project), entry, tokens)


def store(home: SourceHome, entry: dict, tokens: dict) -> None:
    """The one place freshly-minted OAuth tokens land — the sign-in callback and
    every later refresh both call it, so the access token's role grants, the
    refresh secret's name, and the connected/expires_at/obtained_at bookkeeping
    can never drift between the two paths. The access token reuses the source's
    ordinary token slot (so the sandbox path is unchanged); the refresh token is
    stored ungranted and never injected."""
    oauth = dict(entry.get("oauth") or {})
    entry["bearer_secret"] = home.write(entry, home.key(entry, "token"),
                                        tokens["access_token"], granted=True)
    if tokens.get("refresh_token"):
        # Rotation: the old refresh token died the moment this one was issued.
        refresh_name = oauth.get("refresh_secret") or home.key(entry, "oauth-refresh")
        home.write(entry, refresh_name, tokens["refresh_token"], granted=False)
        oauth["refresh_secret"] = refresh_name
    oauth["status"] = "connected"
    oauth["expires_at"] = tokens.get("expires_at")
    oauth["obtained_at"] = utcnow().isoformat()
    oauth.pop("error", None)
    entry["oauth"] = oauth
    home.persist(entry)


def begin_sign_in(home: SourceHome, entry: dict, redirect_uri: str, *,
                  by_user_id: str | None) -> str:
    """Discover the authorization server, register a client there (or reuse one
    the chairman pasted), and mint the URL his browser must visit. The `state`
    in that URL is single-use and 256 bits — it is the whole credential the
    unauthenticated callback route later redeems, which is why a fresh call
    here voids any earlier half-finished sign-in."""
    import secrets as pysecrets

    from yaaf.core.crypto import encrypt

    url = home.read(entry.get("url_secret"))
    if not url:
        raise OAuthSetupError(f"the {home.noun}'s address is missing — save it again")
    oauth = dict(entry.get("oauth") or {})
    try:
        disc = discover(url)
        # A client we registered is bound to the callback it named. When this
        # install's public origin moves, that client is refused with
        # invalid_redirect forever, so register afresh. A pasted client
        # (no registered_redirect_uri) is the chairman's and is never replaced.
        registered_for = oauth.get("registered_redirect_uri")
        if registered_for and registered_for != redirect_uri:
            oauth.pop("client_id", None)
            if oauth.get("client_secret_secret"):
                home.delete(oauth.pop("client_secret_secret"))
        if not oauth.get("client_id"):
            client_id, client_secret = register_client(disc, redirect_uri)
            oauth["client_id"] = client_id
            oauth["registered_redirect_uri"] = redirect_uri
            if client_secret:
                oauth["client_secret_secret"] = home.write(
                    entry, home.key(entry, "oauth-client"), client_secret, granted=False)
    except OAuthSetupError as exc:
        entry["oauth"] = {**oauth, "error": str(exc)}
        home.persist(entry)
        raise

    state = pysecrets.token_urlsafe(32)
    verifier = pysecrets.token_urlsafe(64)
    oauth.update({
        "status": "pending",
        "authorization_endpoint": disc.authorization_endpoint,
        "token_endpoint": disc.token_endpoint,
        "registration_endpoint": disc.registration_endpoint,
        "resource": disc.resource,
        # The PKCE verifier is a secret with a fifteen-minute life; it still
        # never touches the config JSON in plaintext.
        "pending": {"state": state, "verifier_cipher": encrypt(verifier),
                    "redirect_uri": redirect_uri, "created_at": utcnow().isoformat(),
                    # Who pressed Connect: the callback arrives with no
                    # credential but a state, and its record should name them.
                    "by_user_id": by_user_id},
    })
    oauth.pop("error", None)
    entry["oauth"] = oauth
    home.persist(entry)
    return build_authorize_url(disc, oauth["client_id"], redirect_uri, state, verifier,
                               oauth.get("scope"))
