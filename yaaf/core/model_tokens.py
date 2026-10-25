"""Short-lived model tokens minted from a key file (docs/PROVIDERS.md §4).

Vertex AI is paid with a Google Cloud service account. Its key file is a
long-lived credential that can mint tokens for anything the account may do, so
it never enters a sandbox, a Founder subprocess or a log: the control plane
mints an hour-long access token from it, hands *that* to the run, and the run
asks the agent API for a fresh one when its own nears expiry
(`POST /agent/model-token`). A run that outlives an hour — the boardroom chat
does — keeps working; a run whose token leaks leaks an hour.

Minted tokens are cached in this process by the key's fingerprint, so a busy
project mints once an hour rather than once a run.
"""

from __future__ import annotations

import base64
import json
import threading
import time

import httpx

from yaaf.core.plan_usage import fingerprint

SCOPE = "https://www.googleapis.com/auth/cloud-platform"
#: A token with less than this left is minted again rather than handed out.
MARGIN_S = 600
LIFETIME_S = 3600

_cache: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


class MintError(RuntimeError):
    """The key file could not be turned into a token. `kind` is "auth" when
    Google refused it (a deleted key, a disabled account) and "transient"
    when Google could not be reached."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def assertion(account: dict, now: float | None = None) -> str:
    """The signed JWT a service account trades for an access token (RFC 7523)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    issued = int(now if now is not None else time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    if account.get("private_key_id"):
        header["kid"] = account["private_key_id"]
    claims = {"iss": account["client_email"], "scope": SCOPE,
              "aud": account.get("token_uri") or "https://oauth2.googleapis.com/token",
              "iat": issued, "exp": issued + LIFETIME_S}
    signing_input = (f"{_b64(json.dumps(header, separators=(',', ':')).encode())}."
                     f"{_b64(json.dumps(claims, separators=(',', ':')).encode())}")
    key = serialization.load_pem_private_key(account["private_key"].encode(), password=None)
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64(signature)}"


def google_token(key_file: str, *, transport: httpx.BaseTransport | None = None
                 ) -> tuple[str, float]:
    """(access token, expiry as a UNIX time) for a service account key file."""
    digest = fingerprint(key_file)
    with _lock:
        cached = _cache.get(digest)
        if cached and cached[1] - time.time() > MARGIN_S:
            return cached
    try:
        account = json.loads(key_file)
        signed = assertion(account)
    except (ValueError, KeyError, TypeError) as exc:
        raise MintError("auth", f"the service account key could not be read: {exc}") from exc
    try:
        with httpx.Client(transport=transport, timeout=30) as http:
            response = http.post(account.get("token_uri") or "https://oauth2.googleapis.com/token",
                                 data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                       "assertion": signed})
    except httpx.HTTPError as exc:
        raise MintError("transient", f"could not reach Google to mint a token: {exc!r}") from exc
    if response.status_code >= 400:
        kind = "transient" if response.status_code >= 500 or response.status_code == 429 \
            else "auth"
        raise MintError(kind, f"Google refused the service account ({response.status_code}): "
                              f"{response.text[:300]}")
    body = response.json()
    token = (body["access_token"], time.time() + float(body.get("expires_in") or LIFETIME_S))
    with _lock:
        _cache[digest] = token
    return token


def mint(provider_name: str, key_file: str) -> tuple[str, float]:
    """A token for a provider whose credential mints them. One provider does
    today; the function is the seam the next one plugs into."""
    if provider_name == "vertex":
        return google_token(key_file)
    raise MintError("auth", f"{provider_name} does not mint tokens")


def forget() -> None:
    """Drop every cached token — a credential rotated, or a test."""
    with _lock:
        _cache.clear()
