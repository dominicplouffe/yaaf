"""/admin/library: search, preview and install from library sources.

Reads are open to anyone signed in, because choosing a hire is a chairman's
job. Installing a role writes the install-wide library, so it is an instance
admin's, as POST /admin/roles is. A project listing installs nothing here: it
comes back as a draft for the New Project page or the Founder, and its roles
are installed by POST /admin/projects when the chairman creates it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from yaaf.server import authority, library
from yaaf.server.deps import db
from yaaf.server.identity import signed_in

router = APIRouter(dependencies=[Depends(signed_in)])


def _refused(exc: library.LibraryRefusal) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.text)


@router.get("/sources", dependencies=[authority.SIGNED_IN])
def list_sources() -> dict:
    """Which library sources this install reads, and whether each answers."""
    out = []
    for url in library.configured_sources():
        try:
            index = library.load_index(url)
            out.append({"url": url, "ok": True,
                        "name": (index.get("registry") or {}).get("name") or url,
                        "listings": len(index["listings"]),
                        "generated_at": index.get("generated_at"),
                        "publish": (index.get("registry") or {}).get("publish")})
        except library.LibraryRefusal as exc:
            out.append({"url": url, "ok": False, "error": exc.text})
    return {"enabled": bool(out), "sources": out}


@router.get("/search", dependencies=[authority.SIGNED_IN])
def search(q: str | None = None, kind: str | None = None, category: str | None = None,
           domain: str | None = None, limit: int = 20,
           session: Session = Depends(db)) -> dict:
    if kind not in (None, "role", "project"):
        raise HTTPException(status_code=422, detail="kind is role or project")
    return library.search(session, query=q, kind=kind, category=category, domain=domain,
                          limit=limit)


@router.get("/preview", dependencies=[authority.SIGNED_IN])
def preview(source: str, id: str, version: int | None = None) -> dict:
    """One listing version, checked against its index, for the install screen:
    the whole prompt, what it can do, and what it needs. Installs nothing."""
    try:
        fetched = library.fetch_version(source, id, version)
    except library.LibraryRefusal as exc:
        raise _refused(exc) from None
    body = fetched.body
    if body.get("kind") == "role":
        roles = [body.get("role") or {}]
    else:
        roles = list((body.get("bundle") or {}).get("roles") or [])
    return {
        "listing": {k: v for k, v in fetched.listing.items() if k != "versions"},
        "versions": [{k: v.get(k) for k in ("version", "published_at", "changes")}
                     for v in fetched.listing.get("versions") or []],
        "version": fetched.version.get("version"),
        "sha256": fetched.sha256,
        "changes": body.get("changes"),
        "roles": [{"slug": r.get("slug"), "name": r.get("name"),
                   "description": r.get("description"),
                   "system_prompt": r.get("system_prompt"),
                   "tools": sorted(t for t, on in (r.get("tool_permissions") or {}).items() if on),
                   "secret_scopes": r.get("secret_scopes") or []} for r in roles],
        "needs": body.get("needs") or {},
        "inputs": body.get("inputs") or [],
        "goal": ((body.get("bundle") or {}).get("project") or {}).get("goal"),
        "receipt": body.get("receipt"),
    }


@router.get("/updates", dependencies=[authority.SIGNED_IN])
def updates(session: Session = Depends(db)) -> dict:
    """Every role installed from a source, beside what that source says now:
    a newer version to install, or a removal and its reason."""
    from sqlalchemy import select

    from yaaf.core.models import Role, RoleVersion

    out = []
    for role in session.execute(select(Role).where(Role.source.is_not(None))
                                .order_by(Role.slug)).scalars():
        installed = session.execute(
            select(RoleVersion.source_version).where(RoleVersion.role_id == role.id)
            .order_by(RoleVersion.version.desc()).limit(1)).scalar_one_or_none()
        status = library.listing_status(role)
        out.append({"slug": role.slug, "name": role.name, "source": role.source,
                    "source_index": role.source_index, "installed_version": installed,
                    "latest_version": (status or {}).get("latest"),
                    "status": (status or {}).get("status"),
                    "reason": (status or {}).get("reason"),
                    "update": bool(status and status.get("latest") and installed
                                   and status["latest"] > installed
                                   and status.get("status") != "removed")})
    return {"roles": out}


class InstallIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=500)
    id: str = Field(min_length=3, max_length=120)
    version: int | None = Field(default=None, ge=1)


@router.post("/install", dependencies=[authority.INSTANCE_ADMIN])
def install(body: InstallIn, session: Session = Depends(db)) -> dict:
    """Install a role listing into this install's library."""
    try:
        fetched = library.fetch_version(body.source, body.id, body.version)
        if fetched.body.get("kind") != "role":
            raise library.LibraryRefusal(
                f"{body.id} is a project. Start it from New Project or the Founder; its roles "
                "are added when you create it.")
        result = library.install_role(session, fetched.body.get("role") or {}, fetched.provenance)
    except library.LibraryRefusal as exc:
        raise _refused(exc) from None
    from yaaf.server.api.admin import _audit

    _audit(session, None, "library.install", "role", result["slug"],
           {"listing": body.id, "version": fetched.version.get("version"),
            "source": body.source, "sha256": fetched.sha256, "action": result["action"]})
    return {**result, "listing": body.id, "listing_version": fetched.version.get("version")}


class DraftIn(InstallIn):
    pass


@router.post("/draft", dependencies=[authority.SIGNED_IN])
def draft(body: DraftIn) -> dict:
    """A project listing as a setup-file draft, blanks and all. Creates nothing."""
    try:
        fetched = library.fetch_version(body.source, body.id, body.version)
    except library.LibraryRefusal as exc:
        raise _refused(exc) from None
    if fetched.body.get("kind") != "project":
        raise HTTPException(status_code=422, detail=f"{body.id} is a role. Install it from the Roles page.")
    return library.draft_from_project(fetched)
