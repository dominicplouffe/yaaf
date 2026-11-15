"""/admin/publish: a project or role, from this install to a library source.

Publishing puts a design in public under a person's name, so it is the
chairman's (a project) or an instance admin's (a role), and the steps are:
draft, generalize, sign in with GitHub, submit. `submit` re-runs every check
on the server — the shape a registry accepts, and that nothing of the
publisher's is in it — whatever the page or the Founder sent.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import Project, Publication, Role, Secret, utcnow
from yaaf.server import acting, authority, library, publish, publish_github
from yaaf.server.deps import db
from yaaf.server.identity import signed_in

router = APIRouter(dependencies=[Depends(signed_in)])

#: How often a publication's pull request is asked about when someone looks.
REFRESH_AFTER = timedelta(minutes=5)


def _require_project(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="unknown project")
    authority.require_here(session, project, authority.Level.CHAIR)
    return project


def _require_role(session: Session, slug: str) -> Role:
    me = authority.acting_person(session)
    if me is None or not getattr(me, "is_admin", False):
        raise HTTPException(status_code=403, detail="only an instance admin publishes a role")
    role = session.execute(select(Role).where(Role.slug == slug)).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="unknown role")
    return role


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Data sources whose address is a public server everyone uses (a vendor's
    #: hosted MCP server): their address travels. Every other one stays behind.
    public_sources: list[str] = Field(default_factory=list)
    include_receipt: bool = True


@router.post("/projects/{project_id}/draft", dependencies=[authority.CHAIR])
def project_draft(project_id: str, body: DraftIn | None = None,
                  session: Session = Depends(db)) -> dict:
    project = _require_project(session, project_id)
    body = body or DraftIn()
    draft = publish.project_draft(session, project, public_sources=set(body.public_sources),
                                  include_receipt=body.include_receipt)
    private = publish.project_private(session, project)
    return {"draft": draft, "private": sorted({what for what, _ in private}),
            "leaks": publish.leaks(draft, private)}


@router.post("/roles/{slug}/draft", dependencies=[authority.INSTANCE_ADMIN])
def role_draft(slug: str, session: Session = Depends(db)) -> dict:
    role = _require_role(session, slug)
    draft = publish.role_draft(session, role)
    private = publish.install_private(session)
    return {"draft": draft, "private": sorted({what for what, _ in private}),
            "leaks": publish.leaks(draft, private)}


class GeneralizeIn(BaseModel):
    draft: dict


def _generalize(session: Session, draft: dict, private: list[tuple[str, str]]) -> dict:
    from yaaf.core import platform
    from yaaf.founder.publish import generalize

    if not platform.has_credential(session):
        raise HTTPException(status_code=409, detail=(
            "the Founder has no key to think with. Add one under Settings → Founder, or write "
            "the listing yourself and submit it."))
    env, model = platform.founder_env(session), platform.founder_model(session)
    try:
        # A sync route runs on a worker thread with no loop of its own, so the
        # one model call gets a loop of its own here.
        return asyncio.run(generalize(draft, private=private, env=env, model=model))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=(
            f"the Founder could not prepare it ({type(exc).__name__}). Try again, or edit the "
            "listing by hand.")) from None


@router.post("/projects/{project_id}/generalize", dependencies=[authority.CHAIR])
def project_generalize(project_id: str, body: GeneralizeIn, session: Session = Depends(db)) -> dict:
    project = _require_project(session, project_id)
    return _generalize(session, body.draft, publish.project_private(session, project))


@router.post("/roles/{slug}/generalize", dependencies=[authority.INSTANCE_ADMIN])
def role_generalize(slug: str, body: GeneralizeIn, session: Session = Depends(db)) -> dict:
    _require_role(session, slug)
    return _generalize(session, body.draft, publish.install_private(session))


# ── GitHub ─────────────────────────────────────────────────────────────────────

def _token_name(user_id: str) -> str:
    return f"github-publish:{user_id}"


def _token_row(session: Session, user_id: str) -> Secret | None:
    return session.execute(select(Secret).where(
        Secret.project_id.is_(None), Secret.name == _token_name(user_id),
        Secret.kind == "publish")).scalar_one_or_none()


def _token(session: Session) -> str | None:
    from yaaf.core.crypto import decrypt

    row = _token_row(session, acting.user_id() or "")
    return decrypt(row.ciphertext) if row else None


def publish_target() -> dict:
    """Where submissions go: the first library source that takes them."""
    for source in library.configured_sources():
        try:
            index = library.load_index(source)
        except library.LibraryRefusal:
            continue
        target = (index.get("registry") or {}).get("publish") or {}
        if target.get("method") == "github-pr" and target.get("repo"):
            return {"source": source, "repo": target["repo"], "client_id": target.get("client_id")}
    raise HTTPException(status_code=409, detail=(
        "none of this install's library sources takes submissions from yaaf. The marketplace "
        "says where in its index, and an install with no library source cannot publish."))


@router.get("/github", dependencies=[authority.SIGNED_IN])
def github_status(session: Session = Depends(db)) -> dict:
    token = _token(session)
    login = None
    if token:
        try:
            login = publish_github.login_of(token)
        except publish_github.GitHubError:
            login = None
    try:
        target = publish_target()
    except HTTPException as exc:
        return {"connected": bool(login), "login": login, "ready": False, "why": exc.detail}
    ready = bool(target.get("client_id"))
    return {"connected": bool(login), "login": login, "repo": target["repo"], "ready": ready,
            "why": None if ready else "the marketplace has not set up GitHub sign-in yet"}


@router.post("/github/start", dependencies=[authority.SIGNED_IN])
def github_start() -> dict:
    target = publish_target()
    if not target.get("client_id"):
        raise HTTPException(status_code=409, detail="the marketplace has not set up GitHub sign-in yet")
    try:
        return publish_github.start_device(target["client_id"])
    except publish_github.GitHubError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.text) from None


class PollIn(BaseModel):
    device_code: str = Field(min_length=4, max_length=200)


@router.post("/github/poll", dependencies=[authority.SIGNED_IN])
def github_poll(body: PollIn, session: Session = Depends(db)) -> dict:
    from yaaf.core.crypto import encrypt

    target = publish_target()
    result = publish_github.poll_device(target["client_id"], body.device_code)
    if result["state"] != "done":
        return {"state": result["state"]}
    token = result["token"]
    login = publish_github.login_of(token)
    user_id = acting.user_id()
    row = _token_row(session, user_id or "")
    if row is None:
        row = Secret(id=ids.new_id(ids.SECRET), project_id=None, name=_token_name(user_id or ""),
                     kind="publish", role_scopes=[], ciphertext=encrypt(token))
        session.add(row)
    else:
        row.ciphertext = encrypt(token)
    from yaaf.server.api.admin import _audit

    _audit(session, None, "publish.github_connected", "user", user_id, {"login": login})
    return {"state": "done", "login": login}


@router.delete("/github", dependencies=[authority.SIGNED_IN])
def github_forget(session: Session = Depends(db)) -> dict:
    row = _token_row(session, acting.user_id() or "")
    if row is not None:
        session.delete(row)
    return {"connected": False}


# ── submitting ─────────────────────────────────────────────────────────────────

class SubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    role_slug: str | None = None
    draft: dict


@router.post("/submit", dependencies=[authority.IN_HANDLER])
def submit(body: SubmitIn, session: Session = Depends(db)) -> dict:
    """Check the listing once more, then open its pull request.

    Refused (422) while anything of the publisher's is in it or the registry
    would refuse its shape. The refusal names each place, never the value."""
    if bool(body.project_id) == bool(body.role_slug):
        raise HTTPException(status_code=422, detail="name the project or the role being published")
    if body.project_id:
        project = _require_project(session, body.project_id)
        private = publish.project_private(session, project)
        kind = "project"
    else:
        _require_role(session, str(body.role_slug))
        private = publish.install_private(session)
        kind = "role"
    draft = body.draft
    if not isinstance(draft.get("listing"), dict) or not isinstance(draft.get("version"), dict) \
            or draft["listing"].get("kind") != kind:
        raise HTTPException(status_code=422, detail=f"that is not a {kind} listing")
    problems = publish.shape_problems(draft)
    found = publish.leaks(draft, private)
    if problems or found:
        raise HTTPException(status_code=422, detail={
            "code": "not_publishable", "problems": problems, "leaks": found,
            "text": "; ".join(problems + [f"{f['what']} is in {f['where']}" for f in found])})
    target = publish_target()
    token = _token(session)
    if not token:
        raise HTTPException(status_code=409, detail="sign in with GitHub first")
    try:
        login = publish_github.login_of(token)
        n = publish_github.next_version(token, target["repo"], login, draft["slug"])
        today = utcnow().date().isoformat()
        listing, version, raw = publish.finalise(draft, login, n, today)
        base = f"listings/{login.lower()}/{draft['slug']}"
        files = {f"{base}/listing.json": (json.dumps(listing, indent=2, ensure_ascii=False) + "\n").encode(),
                 f"{base}/versions/{n}.json": raw}
        title = f"{'Publish' if n == 1 else 'Update'} {listing['id']} v{n}"
        pr_body = (f"**{listing.get('name')}**: {listing.get('summary')}\n\n"
                   f"{version.get('changes')}\n\n_Opened from yaaf._")
        pr = publish_github.submit(token, target["repo"], branch=f"publish/{draft['slug']}-v{n}",
                                   files=files, title=title, body=pr_body)
    except publish_github.GitHubError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.text) from None
    row = Publication(id=ids.new_id(ids.PUBLICATION), kind=kind, project_id=body.project_id,
                      role_slug=body.role_slug, listing_id=listing["id"], version=n,
                      content_sha256=publish.sha256(raw), repo=target["repo"],
                      pr_number=pr["number"], pr_url=pr["url"], state="submitted",
                      submitted_by=acting.user_id(), checked_at=utcnow())
    session.add(row)
    from yaaf.server.api.admin import _audit

    _audit(session, body.project_id, "publish.submit", kind, body.project_id or body.role_slug,
           {"listing": listing["id"], "version": n, "pr": pr["url"], "sha256": row.content_sha256})
    return _row(row)


def _row(row: Publication) -> dict:
    return {"id": row.id, "kind": row.kind, "listing_id": row.listing_id, "version": row.version,
            "state": row.state, "review_note": row.review_note, "pr_url": row.pr_url,
            "submitted_at": row.created_at.isoformat() if row.created_at else None}


@router.get("/publications", dependencies=[authority.IN_HANDLER])
def publications(project_id: str | None = None, role_slug: str | None = None,
                 session: Session = Depends(db)) -> list[dict]:
    """What was submitted from here, with what the moderators last said."""
    query = select(Publication).order_by(Publication.created_at.desc())
    if project_id:
        project = session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="unknown project")
        authority.require_here(session, project, authority.Level.READ)
        query = query.where(Publication.project_id == project_id)
    elif role_slug:
        query = query.where(Publication.role_slug == role_slug)
    else:
        raise HTTPException(status_code=422, detail="name a project or a role")
    rows = list(session.execute(query).scalars())
    token = _token(session)
    now = utcnow()
    for row in rows:
        stale = row.checked_at is None or now - (row.checked_at if row.checked_at.tzinfo
                                                 else row.checked_at.replace(tzinfo=UTC)) > REFRESH_AFTER
        if row.state in ("submitted", "changes_requested") and row.pr_number and stale:
            try:
                state = publish_github.review_state(token, row.repo, row.pr_number)
                row.state, row.review_note = state["state"], state["note"]
            except publish_github.GitHubError:
                pass
            row.checked_at = now
    return [_row(r) for r in rows]

