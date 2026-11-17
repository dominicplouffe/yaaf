"""Library sources: hiring roles and projects from a registry.

A library source is a registry index (registry/SPEC.md in yaaf-website): one
`index.json` that lists listings, each version with the sha256 of its file. The
official one is the yaaf marketplace; a company can run its own; an empty
`YAAF_LIBRARY_SOURCES` turns the whole feature off and yaaf works as it always
did, from the library on this install.

Three rules hold here and are what the attacks in
tests/selftest_invariants/test_library_*.py try to break:

* **Nothing is installed that does not hash to what the index says.** A file
  changed after review, or served by somebody in between, is refused.
* **Nothing removed is installed.** A listing whose status is `removed` is
  refused, whatever version is asked for.
* **Nothing is installed over a role it did not come from.** A role arriving
  with a slug this install already uses becomes a version of that role only if
  both came from the same source; otherwise it is installed alongside, under a
  new local name. Identical content is not a clash and installs nothing.

Fetching uses `safefetch`, so an index cannot point the platform at its own
network. A registry on disk is named by an absolute path or a file:// URL.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import Role, RoleVersion

#: How long a fetched index is trusted before it is fetched again. Short enough
#: that a removal reaches every install within the hour, long enough that a
#: Founder turn searching three times fetches once.
INDEX_TTL_S = 600.0
#: A listing version is a role or a setup file; anything larger is not one.
MAX_FILE_BYTES = 1_000_000
MAX_INDEX_BYTES = 8_000_000
LISTING_ID = re.compile(r"^@([a-z0-9][a-z0-9-]{0,38})/([a-z0-9-]{2,64})$")
SLUG = re.compile(r"^[a-z0-9-]{2,64}$")

#: The fields that decide what a seat runs, compared to tell an identical role
#: from a different one that happens to share a slug.
_CONTENT_FIELDS = ("description", "system_prompt", "effort", "harness",
                   "tool_permissions", "secret_scopes")


class LibraryRefusal(Exception):
    """A request the library will not carry out, in words for the chairman."""

    def __init__(self, text: str, status: int = 422):
        super().__init__(text)
        self.text = text
        self.status = status


@dataclass
class _Cached:
    fetched_at: float
    index: dict | None
    error: str | None


_cache: dict[str, _Cached] = {}
_lock = threading.Lock()


def configured_sources() -> list[str]:
    from yaaf.core.config import settings

    raw = settings().library_sources or ""
    return [s.strip() for s in raw.split(",") if s.strip()]


def _is_local(url: str) -> bool:
    return url.startswith("file://") or url.startswith("/")


def _local_path(url: str) -> Path:
    return Path(urlparse(url).path if url.startswith("file://") else url)


def _read(url: str, max_bytes: int) -> bytes:
    if _is_local(url):
        path = _local_path(url)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise LibraryRefusal(f"cannot read {path}: {exc.strerror}", status=502) from None
        if len(data) > max_bytes:
            raise LibraryRefusal(f"{path.name} is larger than a listing can be")
        return data
    if urlparse(url).scheme != "https":
        raise LibraryRefusal(f"{url} is not https. A library source is fetched over https, "
                             "or read from a path on this machine.")
    from yaaf.core import safefetch

    try:
        status, body = safefetch.fetch(url, max_bytes=max_bytes, user_agent="yaaf-library/1")
    except safefetch.UnsafeURL as exc:
        raise LibraryRefusal(str(exc)) from None
    except Exception as exc:  # network: the caller says the source is unreachable
        raise LibraryRefusal(f"could not reach {urlparse(url).hostname}: {type(exc).__name__}",
                             status=502) from None
    if status != 200:
        raise LibraryRefusal(f"{url} answered {status}", status=502)
    return body


def load_index(url: str, *, refresh: bool = False) -> dict:
    """The index at `url`, from cache when fresh. Raises LibraryRefusal."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(url)
    if hit and not refresh and now - hit.fetched_at < INDEX_TTL_S:
        if hit.error:
            raise LibraryRefusal(hit.error, status=502)
        return hit.index  # type: ignore[return-value]
    try:
        index = json.loads(_read(url, MAX_INDEX_BYTES))
        if not isinstance(index, dict) or not isinstance(index.get("listings"), list):
            raise LibraryRefusal(f"{url} is not a library index")
        spec = (index.get("registry") or {}).get("spec")
        if spec != 1:
            raise LibraryRefusal(f"{url} uses registry spec {spec!r}; this yaaf reads spec 1")
    except LibraryRefusal as exc:
        with _lock:
            _cache[url] = _Cached(now, None, exc.text)
        raise
    except ValueError:
        text = f"{url} is not valid JSON"
        with _lock:
            _cache[url] = _Cached(now, None, text)
        raise LibraryRefusal(text, status=502) from None
    with _lock:
        _cache[url] = _Cached(now, index, None)
    return index


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _resolve(index_url: str, rel: str) -> str:
    """A path from the index, resolved against the index's own location and
    refused if it climbs out of it."""
    if not isinstance(rel, str) or rel.startswith("/") or "://" in rel or ".." in rel.split("/"):
        raise LibraryRefusal("the index names a file outside itself")
    if _is_local(index_url):
        return str(_local_path(index_url).parent / rel)
    return urljoin(index_url, rel)


@dataclass
class Fetched:
    source: str
    listing: dict
    version: dict
    body: dict
    sha256: str
    provenance: dict = field(default_factory=dict)


def find_listing(source: str, listing_id: str) -> dict:
    if not LISTING_ID.match(listing_id or ""):
        raise LibraryRefusal(f"{listing_id!r} is not a listing id like @handle/slug")
    index = load_index(source)
    for listing in index["listings"]:
        if isinstance(listing, dict) and listing.get("id") == listing_id:
            return listing
    raise LibraryRefusal(f"{listing_id} is not in {source}", status=404)


def fetch_version(source: str, listing_id: str, version: int | None = None) -> Fetched:
    """Download one listing version and prove it is the one the index lists."""
    if source not in configured_sources():
        raise LibraryRefusal(f"{source} is not one of this install's library sources", status=403)
    listing = find_listing(source, listing_id)
    if listing.get("status") == "removed":
        raise LibraryRefusal(f"{listing_id} was removed from {source}: "
                             f"{listing.get('status_reason') or 'no reason given'}", status=410)
    versions = [v for v in listing.get("versions") or [] if isinstance(v, dict)]
    wanted = version or listing.get("latest")
    entry = next((v for v in versions if v.get("version") == wanted), None)
    if entry is None:
        raise LibraryRefusal(f"{listing_id} has no version {wanted}", status=404)
    raw = _read(_resolve(source, entry.get("path", "")), MAX_FILE_BYTES)
    digest = hashlib.sha256(raw).hexdigest()
    if not isinstance(entry.get("sha256"), str) or digest != entry["sha256"].lower():
        raise LibraryRefusal(
            f"{listing_id} v{wanted} does not match what {source} lists for it (sha256 "
            f"{digest[:12]}… against {str(entry.get('sha256'))[:12]}…). It was changed after it "
            f"was reviewed, or by something between here and the registry, so it is not installed.",
            status=409)
    try:
        body = json.loads(raw)
    except ValueError:
        raise LibraryRefusal(f"{listing_id} v{wanted} is not valid JSON") from None
    if body.get("id") != listing_id or body.get("version") != wanted:
        raise LibraryRefusal(f"{listing_id} v{wanted} says it is {body.get('id')} v{body.get('version')}")
    if body.get("kind") != listing.get("kind"):
        raise LibraryRefusal(f"{listing_id} v{wanted} is a {body.get('kind')}, listed as a {listing.get('kind')}")
    return Fetched(source=source, listing=listing, version=entry, body=body, sha256=digest,
                   provenance={"source": listing_id, "source_index": source,
                               "source_version": wanted, "content_sha256": digest})


def listing_status(role: Role) -> dict | None:
    """What the source says about an installed role today: its status, why,
    and its latest version. None for a role written here, a source this
    install no longer reads, or one that cannot be reached — an unreachable
    registry degrades to "no news", never to a refusal."""
    if not role.source or not role.source_index or role.source_index not in configured_sources():
        return None
    try:
        listing = find_listing(role.source_index, role.source)
    except LibraryRefusal:
        return None
    return {"status": listing.get("status") or "active", "reason": listing.get("status_reason"),
            "latest": listing.get("latest"), "tier": listing.get("tier") or "community"}


# ── installing ─────────────────────────────────────────────────────────────────


def _normalised(definition: dict) -> dict:
    from yaaf.runtimes.catalog import DEFAULT_EFFORT
    from yaaf.runtimes.harnesses import canonical as canonical_harness

    return {
        "description": definition.get("description") or "",
        "system_prompt": definition.get("system_prompt") or "",
        "effort": definition.get("effort") or DEFAULT_EFFORT,
        "harness": canonical_harness(definition.get("harness")),
        "tool_permissions": {k: v for k, v in (definition.get("tool_permissions") or {}).items()
                             if k != "mcp_servers"},
        "secret_scopes": list(definition.get("secret_scopes") or []),
    }


def same_content(version: RoleVersion, definition: dict) -> bool:
    mine = _normalised({f: getattr(version, f) for f in _CONTENT_FIELDS})
    return mine == _normalised(definition)


def _latest(session: Session, role: Role) -> RoleVersion | None:
    return session.execute(
        select(RoleVersion).where(RoleVersion.role_id == role.id)
        .order_by(RoleVersion.version.desc()).limit(1)).scalar_one_or_none()


def _candidates(slug: str, source: str | None):
    yield slug
    handle = LISTING_ID.match(source).group(1) if source and LISTING_ID.match(source) else None
    if handle and handle != "yaaf":
        candidate = f"{slug}-{handle}"[:64]
        if SLUG.match(candidate):
            yield candidate
    for n in range(2, 100):
        yield f"{slug[:60]}-{n}"


def install_role(session: Session, definition: dict, provenance: dict | None = None) -> dict:
    """Put a role definition in the library without touching any role it did
    not come from. Returns {slug, version, action, renamed_from}; `action` is
    `created`, `updated` (a new version of the same source's role) or
    `reused` (this install already has exactly this role)."""
    from yaaf.server.api.admin import Provenance, RoleIn, stamp_role

    slug = definition.get("slug")
    if not isinstance(slug, str) or not SLUG.match(slug):
        raise LibraryRefusal(f"{slug!r} is not a role slug")
    source = (provenance or {}).get("source")
    for candidate in _candidates(slug, source):
        role = session.execute(select(Role).where(Role.slug == candidate)).scalar_one_or_none()
        if role is None:
            action = "created"
        else:
            latest = _latest(session, role)
            if latest is not None and same_content(latest, definition):
                return {"slug": candidate, "version": latest.version, "action": "reused",
                        "renamed_from": slug if candidate != slug else None}
            if not (source and role.source == source):
                continue  # somebody else's role under this name: try the next name
            action = "updated"
        body = RoleIn.model_validate({**definition, "slug": candidate})
        stamped = stamp_role(session, body, Provenance(
            source=source, source_index=(provenance or {}).get("source_index"),
            source_version=(provenance or {}).get("source_version"),
            content_sha256=(provenance or {}).get("content_sha256")) if source else None)
        return {"slug": candidate, "version": stamped["version"], "action": action,
                "renamed_from": slug if candidate != slug else None}
    raise LibraryRefusal(f"no free name for {slug}")


def verify_carried(definition: dict) -> dict | None:
    """A role carried in a setup file that says it came from a listing: prove
    it, by fetching that listing version and comparing. Returns the provenance
    to install it with, or None for a role that claims no source."""
    claimed = definition.get("provenance")
    if not claimed:
        return None
    if not isinstance(claimed, dict):
        raise LibraryRefusal(f"role {definition.get('slug')}: provenance must be an object")
    fetched = fetch_version(str(claimed.get("source_index")), str(claimed.get("source")),
                            claimed.get("source_version"))
    if fetched.listing.get("kind") == "role":
        listed = [fetched.body.get("role") or {}]
    else:
        listed = list((fetched.body.get("bundle") or {}).get("roles") or [])
    match = next((r for r in listed if _normalised(r) == _normalised(definition)), None)
    if match is None:
        raise LibraryRefusal(
            f"role {definition.get('slug')} says it is {claimed.get('source')} "
            f"v{claimed.get('source_version')}, but its content is not what that listing holds. "
            "A role is installed as listed or not at all.", status=409)
    return fetched.provenance if fetched.listing.get("kind") == "role" else None


# ── searching ──────────────────────────────────────────────────────────────────


def _matches(text_fields: list[str], query: str | None) -> bool:
    if not query:
        return True
    words = [w for w in re.split(r"\W+", query.lower()) if len(w) > 1]
    blob = " ".join(t for t in text_fields if t).lower()
    return all(w in blob for w in words) if words else True


def search(session: Session, *, query: str | None = None, kind: str | None = None,
           category: str | None = None, domain: str | None = None,
           limit: int = 20) -> dict:
    """Listings from every configured source, best first, each saying whether
    this install already has it. Never raises for an unreachable source: the
    library on this install keeps working, and the source is reported."""
    installed = {(r.source or ""): r.slug for r in session.execute(
        select(Role).where(Role.source.is_not(None))).scalars()}
    results: list[dict] = []
    status: list[dict] = []
    for source in configured_sources():
        try:
            index = load_index(source)
        except LibraryRefusal as exc:
            status.append({"url": source, "ok": False, "error": exc.text})
            continue
        status.append({"url": source, "ok": True,
                       "name": (index.get("registry") or {}).get("name") or source,
                       "listings": len(index["listings"])})
        for listing in index["listings"]:
            if not isinstance(listing, dict) or listing.get("status") == "removed":
                continue
            if kind and listing.get("kind") != kind:
                continue
            if category and listing.get("category") != category:
                continue
            if domain and domain not in (listing.get("domains") or []):
                continue
            latest = next((v for v in listing.get("versions") or []
                           if v.get("version") == listing.get("latest")), {}) or {}
            if not _matches([listing.get("id", ""), listing.get("name", ""),
                             listing.get("summary", ""), " ".join(listing.get("domains") or []),
                             " ".join((listing.get("details") or {}).values())], query):
                continue
            results.append({
                "id": listing.get("id"),
                "kind": listing.get("kind"),
                "name": listing.get("name"),
                "summary": listing.get("summary"),
                "category": listing.get("category"),
                "domains": listing.get("domains") or [],
                "tier": listing.get("tier") or "community",
                "status": listing.get("status") or "active",
                "status_reason": listing.get("status_reason"),
                "latest": listing.get("latest"),
                "installs": listing.get("installs") or 0,
                "can_do": latest.get("can_do") or [],
                "needs_credentials": latest.get("keys") or [],
                "required_credentials": latest.get("required_keys") or [],
                "data_sources": latest.get("data_sources") or [],
                "inputs": latest.get("inputs") or [],
                "roles": latest.get("roles") or [],
                "has_receipt": bool(latest.get("has_receipt")),
                "details": listing.get("details") or {},
                "published_at": latest.get("published_at"),
                "source": source,
                "installed_as": installed.get(listing.get("id") or ""),
            })
    tier_rank = {"official": 0, "community": 1}
    results.sort(key=lambda r: (r["status"] != "active", tier_rank.get(r["tier"], 2),
                                -int(r["installs"] or 0), r["name"] or ""))
    return {"results": results[:max(1, min(limit, 100))], "count": len(results), "sources": status}


def draft_from_project(fetched: Fetched) -> dict:
    """A project listing as a draft the New Project page and the Founder fill
    in: its setup file with every carried role marked with where it came from,
    plus what it needs and the blanks to ask for. Installs nothing."""
    body = fetched.body
    bundle = json.loads(json.dumps(body.get("bundle") or {}))
    uses = {u.get("slug"): u for u in body.get("uses") or [] if isinstance(u, dict)}
    for role in bundle.get("roles") or []:
        use = uses.get(role.get("slug"))
        if use:
            role["provenance"] = {"source": use.get("id"), "source_version": use.get("version"),
                                  "source_index": fetched.source}
    bundle["listing"] = {"id": fetched.listing.get("id"), "version": fetched.version.get("version"),
                         "source": fetched.source, "content_sha256": fetched.sha256,
                         "name": fetched.listing.get("name"), "summary": fetched.listing.get("summary")}
    bundle["needs"] = body.get("needs") or {}
    bundle["inputs"] = body.get("inputs") or []
    if body.get("receipt"):
        bundle["receipt"] = body["receipt"]
    return bundle


BLANK = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def blanks_in(value) -> set[str]:
    """Every `{{blank}}` left anywhere in a value."""
    if isinstance(value, str):
        return set(BLANK.findall(value))
    if isinstance(value, dict):
        return set().union(*(blanks_in(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(blanks_in(v) for v in value)) if value else set()
    return set()


def fill_blanks(value, answers: dict[str, str]):
    """Write the installer's answers into a draft, once."""
    if isinstance(value, str):
        return BLANK.sub(lambda m: str(answers.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: fill_blanks(v, answers) for k, v in value.items()}
    if isinstance(value, list):
        return [fill_blanks(v, answers) for v in value]
    return value


# ── what a project from a listing still needs ──────────────────────────────────


def listing_of(project) -> dict | None:
    """The listing a project was started from, as stored at creation."""
    value = (project.integrations or {}).get("listing")
    return value if isinstance(value, dict) else None


def missing_needs(session: Session, project) -> list[dict]:
    """The keys and data sources a project's listing marks required and the
    project does not have yet, each named the way the listing names it."""
    from yaaf.core.models import Secret

    listing = listing_of(project)
    needs = (listing or {}).get("needs") or {}
    held = {name for name in session.execute(
        select(Secret.name).where(Secret.project_id == project.id)).scalars()}
    sources = {s.get("name") for s in (project.integrations or {}).get("mcp_servers") or []}
    out = []
    for key in needs.get("keys") or []:
        if isinstance(key, dict) and key.get("required") and key.get("name") not in held:
            out.append({"kind": "key", "name": key.get("name"), "label": key.get("label") or key.get("name"),
                        "help": key.get("help")})
    for source in needs.get("data_sources") or []:
        if isinstance(source, dict) and source.get("required") and source.get("name") not in sources:
            out.append({"kind": "data_source", "name": source.get("name"),
                        "label": source.get("serves") or source.get("name"), "help": None})
    return out


def clean_listing(value: dict | None) -> dict | None:
    """What a project keeps of the listing it came from: which one, and what
    it needs. Nothing else a client sends is stored."""
    if not isinstance(value, dict) or not isinstance(value.get("id"), str):
        return None
    needs = value.get("needs") if isinstance(value.get("needs"), dict) else {}
    return {
        "id": value["id"][:200],
        "version": value.get("version") if isinstance(value.get("version"), int) else None,
        "source": str(value.get("source") or "")[:500] or None,
        "needs": {
            "keys": [{k: key.get(k) for k in ("name", "label", "format", "help", "required", "roles")}
                     for key in needs.get("keys") or [] if isinstance(key, dict)][:20],
            "data_sources": [{k: src.get(k) for k in ("name", "serves", "address", "tools", "required")}
                             for src in needs.get("data_sources") or [] if isinstance(src, dict)][:20],
            "mailbox": bool(needs.get("mailbox")),
            "github_repo": bool(needs.get("github_repo")),
        },
    }
