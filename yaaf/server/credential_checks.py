"""What yaaf knows about the services a chairman promises access to, and how
it checks a credential works.

Two kinds of knowledge, kept apart on purpose because they are trusted
differently:

* **The catalog** — services a person has written an entry for: the exact
  fields, where to find each one, and a test that was checked by hand. Its
  result is called *verified*.
* **A suggested check** — for every other service, the Founder (a language
  model) proposes the fields and one read-only call while the project is
  being created. The call is shown to the chairman in full and runs only when
  he presses Run, and its result is called *suggested*, never verified. A
  confident wrong test is worse than an honest "not tested", so the model
  never runs anything on its own.

Every check only reads. The suggested one is also fenced in code rather than
by trust: GET or HEAD, https, an address that resolves to the public
internet, no redirects, a short timeout and a small read — the server is
calling an address a model wrote down, and it must not be talked into
calling anything inside the network it runs on.
"""

from __future__ import annotations

import base64
import contextlib
import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

TIMEOUT_S = 10.0
#: The most of a response a suggested check reads.
READ_CAP = 64 * 1024


@dataclass
class Field:
    key: str
    label: str
    help: str
    secret: bool = True
    file: bool = False

    def view(self, origin: str) -> dict:
        return {"key": self.key, "label": self.label, "help": self.help,
                "secret": self.secret, "file": self.file, "origin": origin}


@dataclass
class Entry:
    service: str
    label: str
    fields: list[Field]
    aliases: tuple[str, ...] = ()
    test: object = None  # (values: dict[str, str]) -> Result
    roles_hint: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class Result:
    ok: bool
    summary: str
    fix: list[str] = field(default_factory=list)

    def view(self, kind: str) -> dict:
        return {"ok": self.ok, "summary": self.summary, "fix": self.fix, "kind": kind}


# ── WordPress ─────────────────────────────────────────────────────────────────


def _wordpress(values: dict[str, str]) -> Result:
    site = (values.get("WORDPRESS_SITE") or "").strip().rstrip("/")
    user = (values.get("WORDPRESS_USERNAME") or "").strip()
    password = (values.get("WORDPRESS_APP_PASSWORD") or "").strip()
    if not site.startswith(("https://", "http://")):
        site = f"https://{site}"
    host = urlparse(site).hostname or site
    try:
        response = httpx.get(f"{site}/wp-json/wp/v2/users/me", params={"context": "edit"},
                             auth=(user, password), timeout=TIMEOUT_S, follow_redirects=True)
    except httpx.HTTPError as exc:
        return Result(False, f"Couldn't reach {host} from yaaf's server ({exc.__class__.__name__}).",
                      ["Check the site address.",
                       "If the site blocks servers it doesn't know, allow yaaf's server's address."])
    if response.status_code in (401, 403):
        return Result(False, "WordPress refused this username and application password.",
                      ["Check the username: it's the one you sign in with, not your display name.",
                       "Make a new application password in WordPress › Users › Profile › "
                       "Application passwords. Your normal login password doesn't work here."])
    if response.status_code == 404:
        return Result(False, f"{host} doesn't answer at /wp-json/, so its REST API is off or blocked.",
                      ["Turn the REST API back on, or allow it in your security plugin."])
    if response.status_code != 200:
        return Result(False, f"{host} answered {response.status_code} instead of signing in.")
    try:
        me = response.json()
    except ValueError:
        return Result(False, f"{host} answered, but not like a WordPress site.")
    roles = ", ".join(r.capitalize() for r in me.get("roles") or []) or "no role"
    who = me.get("username") or me.get("slug") or user
    if not (me.get("capabilities") or {}).get("publish_posts"):
        return Result(False, f"Signed in to {host} as {who} ({roles}), but this account can't publish posts.",
                      ["In WordPress › Users, make this account an Author, Editor or Administrator."])
    return Result(True, f"Signed in to {host} as {who} ({roles}). It can publish and edit posts.")


# ── Google Analytics ──────────────────────────────────────────────────────────


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _google_token(account: dict, scope: str) -> str:
    """An access token for a service account, signed here: one JWT, one POST."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    now = int(time.time())
    token_uri = account.get("token_uri") or "https://oauth2.googleapis.com/token"
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(json.dumps({"iss": account["client_email"], "scope": scope, "aud": token_uri,
                              "iat": now, "exp": now + 3600}).encode())
    key = serialization.load_pem_private_key(account["private_key"].encode(), password=None)
    signature = key.sign(f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    response = httpx.post(token_uri, timeout=TIMEOUT_S, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": f"{header}.{claims}.{_b64(signature)}"})
    if response.status_code != 200:
        raise PermissionError(response.text[:300])
    return response.json()["access_token"]


def _google_analytics(values: dict[str, str]) -> Result:
    prop = re.sub(r"\D", "", values.get("GA_PROPERTY_ID") or "")
    raw = values.get("GOOGLE_SERVICE_ACCOUNT") or ""
    try:
        account = json.loads(raw)
        email = account["client_email"]
        account["private_key"]  # noqa: B018 — present, or it isn't a key file
    except (ValueError, KeyError, TypeError):
        return Result(False, "That file isn't a Google service-account key.",
                      ["In Google Cloud › IAM › Service accounts, open the account, then "
                       "Keys › Add key › JSON, and use the file it downloads."])
    if not prop:
        return Result(False, "The property ID is missing.",
                      ["Find it in Google Analytics › Admin › Property details: 9 or so digits."])
    try:
        token = _google_token(account, "https://www.googleapis.com/auth/analytics.readonly")
    except PermissionError:
        return Result(False, "Google refused this key file. It may have been deleted or revoked.",
                      ["Make a new JSON key for the service account and use that file."])
    except (httpx.HTTPError, ValueError) as exc:
        return Result(False, f"Couldn't sign in to Google ({exc.__class__.__name__}).")
    try:
        response = httpx.post(
            f"https://analyticsdata.googleapis.com/v1beta/properties/{prop}:runReport",
            headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT_S,
            json={"dateRanges": [{"startDate": "yesterday", "endDate": "yesterday"}],
                  "metrics": [{"name": "screenPageViews"}]})
    except httpx.HTTPError as exc:
        return Result(False, f"Couldn't reach Google Analytics ({exc.__class__.__name__}).")
    text = response.text
    if response.status_code == 403 and ("SERVICE_DISABLED" in text or "has not been used" in text):
        return Result(False, "The Google Analytics Data API is turned off for this key's Google Cloud project.",
                      ["In Google Cloud, open APIs & Services › Library, find "
                       "\"Google Analytics Data API\" and press Enable, then test again."])
    if response.status_code == 403:
        return Result(False, f"Google says this service account can't see property {prop}.",
                      ["In Google Analytics, open Admin › Property access management.",
                       f"Add {email} as a Viewer.",
                       "Come back and test again."])
    if response.status_code in (400, 404):
        return Result(False, f"Google doesn't know a property {prop}.",
                      ["Check the number in Google Analytics › Admin › Property details."])
    if response.status_code != 200:
        return Result(False, f"Google Analytics answered {response.status_code}.")
    rows = response.json().get("rows") or []
    views = rows[0]["metricValues"][0]["value"] if rows else "0"
    return Result(True, f"Read yesterday from property {prop}: {int(views):,} page views. Reading works.")


CATALOG: dict[str, Entry] = {
    "wordpress": Entry(
        "wordpress", "WordPress",
        [Field("WORDPRESS_SITE", "Site address", "Your site's address, like https://example.com.",
               secret=False),
         Field("WORDPRESS_USERNAME", "WordPress username", "The account that publishes.",
               secret=False),
         Field("WORDPRESS_APP_PASSWORD", "Application password",
               "Make one in WordPress › Users › Profile › Application passwords. It's not your "
               "login password, and you can revoke it there any time.")],
        aliases=("wordpress", "wp", "wordpresscom"), test=_wordpress),
    "google_analytics": Entry(
        "google_analytics", "Google Analytics",
        [Field("GA_PROPERTY_ID", "Property ID",
               "9 or so digits, from Google Analytics › Admin › Property details.", secret=False),
         Field("GOOGLE_SERVICE_ACCOUNT", "Service account key",
               "The .json key file of a Google Cloud service account.", file=True)],
        aliases=("googleanalytics", "ga", "ga4", "googleanalytics4", "analytics"),
        test=_google_analytics),
}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def known(service: str) -> Entry | None:
    """The catalog entry for a service named any common way, or None."""
    wanted = _norm(service)
    for entry in CATALOG.values():
        if wanted == _norm(entry.service) or wanted in entry.aliases:
            return entry
    return None


def slug(service: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (service or "").lower()).strip("_")[:64] or "service"


def env_key(name: str) -> str:
    """A field key as the variable the agents will see."""
    return re.sub(r"[^A-Z0-9]+", "_", (name or "").upper()).strip("_")[:100]


# ── the suggested check ───────────────────────────────────────────────────────


class Refused(ValueError):
    """A suggested call that is not allowed to run, with the reason."""


def _public(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise Refused(f"{host} doesn't resolve") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast or address.is_unspecified):
            raise Refused(f"{host} points inside a private network, so yaaf won't call it")


def _fill(text: str, values: dict[str, str]) -> str:
    return re.sub(r"\{([A-Z0-9_]+)\}", lambda m: values.get(m.group(1), m.group(0)), text)


def shown(check: dict, values: dict[str, str]) -> dict:
    """The call as the chairman sees it before pressing Run: the address with
    its non-secret values filled in, and every secret masked."""
    def show(text: str) -> str:
        # Plain values are filled in; anything still a placeholder is a secret
        # (or not given yet) and is masked, never spelled out.
        return re.sub(r"\{[A-Z0-9_]+\}", "••••••••••••", _fill(text, values))

    return {"method": (check.get("method") or "GET").upper(), "url": show(check.get("url", "")),
            "headers": {k: show(v) for k, v in (check.get("headers") or {}).items()},
            "reads": check.get("reads") or ""}


def validate(check: dict | None) -> dict | None:
    """A suggested check as stored: refused at the door if it could change
    anything or reach anything but the public internet over https."""
    if not check:
        return None
    method = (check.get("method") or "GET").upper()
    if method not in ("GET", "HEAD"):
        raise Refused("a suggested check may only read (GET or HEAD)")
    url = str(check.get("url") or "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or "{" in parsed.hostname:
        raise Refused("a suggested check must be an https address on a fixed host")
    headers = {str(k): str(v) for k, v in (check.get("headers") or {}).items()}
    return {"method": method, "url": url, "headers": headers,
            "reads": str(check.get("reads") or "")[:240]}


def _scalars(data: object, limit: int = 3) -> list[str]:
    """A few plain facts from a JSON answer, so "it answered" says something."""
    out: list[str] = []

    def walk(node: object, depth: int) -> None:
        if len(out) >= limit or depth > 3:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, (str, int, float)) and not isinstance(value, bool) \
                        and key.lower() in ("name", "title", "username", "email", "id",
                                            "subscriber_count", "count", "total", "plan"):
                    out.append(f"{key}: {str(value)[:60]}")
                    if len(out) >= limit:
                        return
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list) and node:
            walk(node[0], depth + 1)

    walk(data, 0)
    return out


def run_suggested(check: dict, values: dict[str, str]) -> Result:
    """Run a stored suggested check, fenced. Never follows a redirect."""
    try:
        clean = validate(check)
        assert clean is not None
        url = _fill(clean["url"], values)
        host = urlparse(url).hostname or ""
        if host != urlparse(clean["url"]).hostname:
            raise Refused("the address changed when the values were filled in")
        _public(host)
    except Refused as exc:
        return Result(False, f"yaaf won't run this check: {exc}.")
    headers = {k: _fill(v, values) for k, v in clean["headers"].items()}
    try:
        with httpx.Client(timeout=TIMEOUT_S, follow_redirects=False) as client, \
                client.stream(clean["method"], url, headers=headers) as response:
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) >= READ_CAP:
                    break
            status = response.status_code
    except httpx.HTTPError as exc:
        return Result(False, f"Couldn't reach {host} ({exc.__class__.__name__}).")
    if status in (401, 403):
        return Result(False, f"{host} refused this key ({status}).",
                      ["Check the values against the service's docs, then run the check again."])
    if not 200 <= status < 300:
        return Result(False, f"{host} answered {status}.")
    facts: list[str] = []
    with contextlib.suppress(ValueError):
        facts = _scalars(json.loads(body.decode(errors="replace")))
    said = f" It returned {'; '.join(facts)}." if facts else ""
    return Result(True, f"{host} accepted the key ({status}).{said} Not a verified test: yaaf "
                        "doesn't know this service yet.")


def test(service: str, values: dict[str, str], check: dict | None) -> tuple[dict, str]:
    """Run the right check: (result view, kind). A catalog service gets its
    verified test; anything else runs its suggested check, if it has one."""
    entry = known(service)
    if entry is not None and entry.test is not None:
        return entry.test(values).view("verified"), "verified"
    if check:
        return run_suggested(check, values).view("suggested"), "suggested"
    return Result(False, "There's no check for this service.").view("none"), "none"
