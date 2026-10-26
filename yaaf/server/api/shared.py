"""The shared store's doors (docs/SHARED_STORE.md).

Two sets of them, for the two keys. The instance admin's own (`/admin/shared`)
store values, rotate and reveal them, and decide which projects may use each
one. A project's chairs (`/admin/projects/{id}/shared`) see what their project
may use and choose which of their teams receive it. Neither set can turn the
other's key: a chair cannot reach an item the admin did not make available, and
the admin's availability grants no team anything.
"""

from __future__ import annotations

import contextlib
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import credential_files, ids, shared_store
from yaaf.core.models import Project, Secret, SharedAccess, SharedDataSource, utcnow
from yaaf.core.naming import env_var_name, is_reserved
from yaaf.server import authority, policy
from yaaf.server.api import admin
from yaaf.server.deps import db
from yaaf.server.identity import signed_in
from yaaf.server.notify import Attention, chairman_notify

router = APIRouter(dependencies=[Depends(signed_in)])

#: The path's spelling of each kind.
PATH_KINDS = {"variables": shared_store.VARIABLE, "data-sources": shared_store.DATA_SOURCE}


def _kind(segment: str) -> str:
    kind = PATH_KINDS.get(segment)
    if kind is None:
        raise HTTPException(status_code=404, detail="unknown shared item")
    return kind


def _item_or_404(session: Session, kind: str, item_id: str):
    row = shared_store.item(session, kind, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown shared item")
    return row


def _team_names(session: Session, project_id: str) -> dict[str, str]:
    """Role slug → the team names a person recognises on this project."""
    from yaaf.core.models import Role, RoleVersion, Team
    from yaaf.server.staffing import team_role_slug

    named: dict[str, list[str]] = {}
    for team in session.execute(select(Team).where(Team.project_id == project_id)
                                .order_by(Team.created_at)).scalars():
        slug = team_role_slug(session, team)
        if slug:
            named.setdefault(slug, []).append(team.name)
    ceo = policy.ceo_of(session, project_id)
    version = session.get(RoleVersion, ceo.role_version_id) if ceo else None
    role = session.get(Role, version.role_id) if version else None
    if role is not None:
        named.setdefault(role.slug, []).append("CEO")
    return {slug: " + ".join(names) for slug, names in named.items()}


def _variable_kind(row: Secret) -> str:
    return "file" if row.filename else (row.visibility or "secret")


def _base_row(kind: str, row) -> dict:
    out = {"id": row.id, "kind": kind, "name": row.name, "shared_with": row.shared_with,
           "updated_at": (row.updated_at or row.created_at).isoformat()
           if (row.updated_at or row.created_at) else None}
    if kind == shared_store.VARIABLE:
        out.update({"env": env_var_name(row.name), "type": _variable_kind(row),
                    "filename": row.filename})
    else:
        entry = row.entry or {}
        out.update({"env": [env_var_name(shared_store.source_url_name(row.name))]
                    + ([env_var_name(shared_store.source_token_name(row.name))]
                       if row.bearer_cipher else []),
                    **{k: entry.get(k) for k in ("url_host", "url_port", "access", "auth",
                                                 "tools", "last_check")}})
    return out


# ── the admin's store ───────────────────────────────────────────────────────────


@router.get("/shared", dependencies=[authority.INSTANCE_ADMIN])
def list_store(session: Session = Depends(db)) -> dict:
    """The whole store, and every project's use of each item. The admin's
    alone: which project uses which credential is not one project's business."""
    from yaaf.core.crypto import decrypt

    def projects_of(kind: str, item_id: str) -> list[dict]:
        out = []
        for access in shared_store.access_rows(session, kind, item_id):
            project = session.get(Project, access.project_id)
            if project is None:
                continue
            teams = _team_names(session, project.id)
            out.append({"project_id": project.id, "slug": project.slug,
                        "name": project.name, "roles": list(access.role_scopes or []),
                        "teams": [teams.get(s, s) for s in access.role_scopes or []]})
        return out

    variables = []
    for row in shared_store.variables(session):
        entry = {**_base_row(shared_store.VARIABLE, row),
                 "projects": projects_of(shared_store.VARIABLE, row.id)}
        # The same two things the project list carries, for the same reader: a
        # plain value whole, and a secret's last four when it is long enough.
        with contextlib.suppress(Exception):
            value = decrypt(row.ciphertext)
            if row.filename:
                with contextlib.suppress(credential_files.BadCredentialFile):
                    entry["size_bytes"] = len(credential_files.decode(value))
            elif (row.visibility or "secret") == "plain":
                entry["value"] = value
            elif len(value) >= 12:
                entry["hint"] = value[-4:]
        variables.append(entry)
    sources = [{**_base_row(shared_store.DATA_SOURCE, row),
                "projects": projects_of(shared_store.DATA_SOURCE, row.id)}
               for row in shared_store.data_sources(session)]
    projects = [{"id": p.id, "slug": p.slug, "name": p.name}
                for p in session.execute(select(Project).order_by(Project.created_at)).scalars()]
    return {"variables": variables, "data_sources": sources, "projects": projects}


class SharedVariableIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    # Omit to change availability or visibility without re-pasting.
    value: str | None = Field(default=None, min_length=1)
    visibility: Literal["secret", "plain"] | None = None
    # Set => a file credential, `value` base64 of its bytes. Fixed at creation.
    filename: str | None = Field(default=None, max_length=120)
    # Omitted keeps it; a new item starts "listed", the narrower of the two,
    # because making a value reachable from every project should be said.
    shared_with: Literal["all", "listed"] | None = None


def _name_problem(session: Session, name: str, existing: Secret | None) -> str | None:
    env = env_var_name(name)
    if not env:
        return "a name needs at least one letter or digit"
    if is_reserved(env):
        return (f"'{name}' would be injected as {env}, and YAAF_* belongs to the platform "
                "inside a sandbox. Pick another name")
    if env.startswith("MCP_"):
        return (f"{env} is how a data source's address and token reach a sandbox — share "
                "the data source itself instead, or pick another name")
    if name.lower().startswith("platform-") or name.lower() == "github":
        return "this name is reserved for a platform connection"
    for row in session.execute(select(Secret).where(Secret.project_id.is_(None),
                                                    Secret.hidden_at.is_(None))).scalars():
        if existing is not None and row.id == existing.id:
            continue
        if env_var_name(row.name) == env:
            return (f"${env} is already taken at platform scope by '{row.name}' — two "
                    "values cannot answer to one name")
    return None


@router.post("/shared/variables", dependencies=[authority.INSTANCE_ADMIN])
def put_variable(body: SharedVariableIn, session: Session = Depends(db)) -> dict:
    """Store or change a shared secret, plain variable or file, by name. The
    project door's rules, held at this door too: shape fixed at creation, a
    file refused as plain, a file's contents refused as a string."""
    from yaaf.core.crypto import encrypt

    existing = session.execute(
        select(Secret).where(Secret.project_id.is_(None), Secret.shared_with.is_not(None),
                             Secret.name == body.name)
    ).scalar_one_or_none()
    problem = _name_problem(session, body.name, existing)
    if problem:
        raise HTTPException(status_code=409 if "taken" in problem else 422, detail=problem)

    value = body.value
    as_file = body.filename is not None or bool(existing and existing.filename)
    filename = existing.filename if existing else None
    if existing and body.filename is not None and not existing.filename:
        raise HTTPException(
            status_code=422,
            detail=f"a credential's shape is fixed at creation — '{body.name}' was stored "
                   "as a string; store a new file credential instead")
    try:
        if as_file and value is not None:
            decoded = credential_files.decode(value)
            filename = credential_files.check_filename(
                (body.filename or "").strip() or filename
                or credential_files.default_filename(env_var_name(body.name), decoded))
        elif as_file and body.filename:
            filename = credential_files.check_filename(body.filename.strip())
    except credential_files.BadCredentialFile as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not as_file and value is not None and credential_files.looks_like_a_file(value):
        raise HTTPException(
            status_code=422,
            detail=f"'{body.name}' looks like the contents of a file, not a value. Store it "
                   "as a file credential and agents receive the path to it")
    if body.visibility == "plain" and as_file:
        raise HTTPException(
            status_code=422,
            detail=f"'{body.name}' is a file credential — it stays secret")

    if existing is not None:
        was = {"shared_with": existing.shared_with, "visibility": existing.visibility}
        actions = []
        if value is not None:
            existing.ciphertext = encrypt(value)
            existing.filename = filename
            actions.append("shared.rotate")
        if body.visibility is not None and body.visibility != (existing.visibility or "secret"):
            existing.visibility = body.visibility
            actions.append("shared.visibility")
        if body.shared_with is not None and body.shared_with != existing.shared_with:
            existing.shared_with = body.shared_with
            actions.append("shared.availability")
        existing.updated_at = utcnow()
        for action in actions or ["shared.save"]:
            admin._audit(session, None, action, "secret", existing.id,
                         {"name": existing.name, "was": was,
                          "shared_with": existing.shared_with,
                          "visibility": existing.visibility})
        return {**_base_row(shared_store.VARIABLE, existing),
                "rotated": value is not None,
                "note": "Projects that receive it get the change on their next run."}
    if value is None:
        raise HTTPException(status_code=422, detail="a new shared variable needs a value")
    row = Secret(id=ids.new_id(ids.SECRET), project_id=None, name=body.name,
                 ciphertext=encrypt(value), role_scopes=[], kind="service",
                 filename=filename, visibility=body.visibility or "secret",
                 shared_with=body.shared_with or shared_store.LISTED)
    session.add(row)
    admin._audit(session, None, "shared.create", "secret", row.id,
                 {"name": row.name, "shared_with": row.shared_with,
                  "visibility": row.visibility, "filename": filename})
    session.flush()
    return {**_base_row(shared_store.VARIABLE, row), "rotated": False}


@router.get("/shared/variables/{item_id}/value", dependencies=[authority.INSTANCE_ADMIN])
def reveal_variable(item_id: str, session: Session = Depends(db)) -> dict:
    """The admin reads a shared value back — one press, one audit row. A
    project's chairs never can: they did not set it, and an eye on their
    screen would hand the admin's value to every project it was shared with."""
    from yaaf.core.crypto import decrypt

    row = _item_or_404(session, shared_store.VARIABLE, item_id)
    admin._audit(session, None, "shared.reveal", "secret", row.id, {"name": row.name})
    value = decrypt(row.ciphertext)
    if row.filename:
        try:
            body = credential_files.decode(value)
        except credential_files.BadCredentialFile:
            raise HTTPException(status_code=422, detail=f"'{row.name}' is marked as a file "
                                "but its stored value is not base64 of one") from None
        try:
            return {"id": row.id, "name": row.name, "filename": row.filename,
                    "value": body.decode()}
        except UnicodeDecodeError:
            return {"id": row.id, "name": row.name, "filename": row.filename,
                    "value": None, "binary": True, "size_bytes": len(body)}
    return {"id": row.id, "name": row.name, "value": value}


def _refuse_while_used(session: Session, kind: str, row) -> None:
    users = [session.get(Project, a.project_id)
             for a in shared_store.access_rows(session, kind, row.id)]
    names = [p.name for p in users if p is not None]
    if names:
        raise HTTPException(
            status_code=409,
            detail=f"{shared_store.label(kind, row)} is still available to "
                   f"{', '.join(names)}. Remove it from each project first, so each "
                   "project's record says it went")


@router.delete("/shared/variables/{item_id}", dependencies=[authority.INSTANCE_ADMIN])
def delete_variable(item_id: str, session: Session = Depends(db)) -> dict:
    row = _item_or_404(session, shared_store.VARIABLE, item_id)
    _refuse_while_used(session, shared_store.VARIABLE, row)
    session.delete(row)
    admin._audit(session, None, "shared.delete", "secret", row.id, {"name": row.name})
    return {"deleted": row.name}


class SharedSourceIn(BaseModel):
    name: str = Field(pattern="^[a-z0-9-]{2,32}$")
    url: str
    # As on a project source: omitted keeps the token, "" removes it.
    bearer: str | None = None
    access: Literal["inline", "bulk"] = "inline"
    tools: list[str] | None = None
    # OAuth is refused, not hidden: see docs/SHARED_STORE.md.
    auth: Literal["none", "bearer", "oauth"] | None = None
    shared_with: Literal["all", "listed"] | None = None


@router.post("/shared/data-sources", dependencies=[authority.INSTANCE_ADMIN])
def put_data_source(body: SharedSourceIn, session: Session = Depends(db)) -> dict:
    """Store or edit a shared MCP server by name, and prove it answers."""
    from yaaf.core.crypto import decrypt, encrypt

    if body.auth == "oauth":
        raise HTTPException(
            status_code=422,
            detail="an OAuth source cannot be shared: its refresh token rotates on use, so "
                   "one sign-in cannot serve several projects. Add it to each project "
                   "that needs it instead")
    parsed = admin._parse_source_url(
        body.url,
        "that is not a URL — a data source needs a full address like "
        "https://host:1234/path (schemes http/https only)")
    url = body.url.strip()
    row = session.execute(select(SharedDataSource).where(
        SharedDataSource.name == body.name)).scalar_one_or_none()
    prior = dict(row.entry or {}) if row is not None else {}

    token: str | None
    if body.bearer:
        token = body.bearer
    elif body.bearer == "" or body.auth == "none":
        token = None
    else:
        token = decrypt(row.bearer_cipher) if row is not None and row.bearer_cipher else None

    entry = {"url_host": parsed.hostname,
             "url_port": parsed.port or (443 if parsed.scheme == "https" else 80),
             "access": body.access, "auth": "bearer" if token else "none"}
    allowlist = body.tools if body.tools is not None else prior.get("tools")
    if allowlist is not None:
        entry["tools"] = sorted({t.strip() for t in allowlist if t.strip()})
    check = admin._check_data_source(url, token)
    entry["last_check"] = {**check, "at": utcnow().isoformat()}

    created = row is None
    if created:
        row = SharedDataSource(id=ids.new_id(ids.SHARED_SOURCE), name=body.name,
                               shared_with=body.shared_with or shared_store.LISTED)
        session.add(row)
    elif body.shared_with is not None:
        row.shared_with = body.shared_with
    row.url_cipher = encrypt(url)
    row.bearer_cipher = encrypt(token) if token else None
    row.entry = entry
    row.updated_at = utcnow()
    session.flush()
    admin._audit(session, None, "shared.source_save", "shared_data_source", row.id,
                 {"name": row.name, "host": entry["url_host"], "access": body.access,
                  "auth": entry["auth"], "shared_with": row.shared_with,
                  "check_ok": check.get("ok"), "created": created})
    return {**_base_row(shared_store.DATA_SOURCE, row), "check": check,
            "note": "Projects that receive it get the change on their next run."}


@router.post("/shared/data-sources/{item_id}/check", dependencies=[authority.INSTANCE_ADMIN])
def check_data_source(item_id: str, session: Session = Depends(db)) -> dict:
    from yaaf.core.crypto import decrypt

    row = _item_or_404(session, shared_store.DATA_SOURCE, item_id)
    check = admin._check_data_source(
        decrypt(row.url_cipher), decrypt(row.bearer_cipher) if row.bearer_cipher else None)
    row.entry = {**(row.entry or {}), "last_check": {**check, "at": utcnow().isoformat()}}
    admin._audit(session, None, "shared.source_check", "shared_data_source", row.id,
                 {"name": row.name, "ok": check.get("ok")})
    return check


@router.delete("/shared/data-sources/{item_id}", dependencies=[authority.INSTANCE_ADMIN])
def delete_data_source(item_id: str, session: Session = Depends(db)) -> dict:
    row = _item_or_404(session, shared_store.DATA_SOURCE, item_id)
    _refuse_while_used(session, shared_store.DATA_SOURCE, row)
    session.delete(row)
    admin._audit(session, None, "shared.source_delete", "shared_data_source", row.id,
                 {"name": row.name})
    return {"deleted": row.name}


class ProjectRef(BaseModel):
    project_id: str


@router.post("/shared/{kind}/{item_id}/projects", dependencies=[authority.INSTANCE_ADMIN])
def make_available(kind: str, item_id: str, body: ProjectRef,
                   session: Session = Depends(db)) -> dict:
    """The admin's key: this project may use this item. No team receives it
    until the project's chairs say which — the admin does not run the project,
    and a grant chosen from outside it is a grant nobody there can explain."""
    kind = _kind(kind)
    row = _item_or_404(session, kind, item_id)
    project = session.get(Project, body.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="unknown project")
    held = shared_store.access(session, project.id, kind, row.id)
    if held is not None:
        return {"project_id": project.id, "roles": list(held.role_scopes or []),
                "created": False}
    problem = shared_store.project_collision(session, project.id, kind, row)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    session.add(SharedAccess(id=ids.new_id(ids.SHARED_ACCESS), project_id=project.id,
                             item_kind=kind, item_id=row.id, role_scopes=[]))
    admin._audit(session, project.id, "shared.available", kind, row.id,
                 {"name": row.name})
    chairman_notify(
        session, project, f"{shared_store.label(kind, row).capitalize()} is available",
        f"The instance admin made the {shared_store.label(kind, row)} available to "
        f"{project.name}. No team has it yet — give it to the teams that need it under "
        "Settings → Secrets & variables.",
        attention=Attention.FYI)
    return {"project_id": project.id, "roles": [], "created": True}


@router.post("/shared/{kind}/{item_id}/revoke", dependencies=[authority.INSTANCE_ADMIN])
def revoke(kind: str, item_id: str, body: ProjectRef, session: Session = Depends(db)) -> dict:
    """Take an item away from a project, and every team on it with it. On the
    project's record and in its chairman's briefing, because a value that
    left a sandbox shows up first as an agent's failure."""
    kind = _kind(kind)
    row = _item_or_404(session, kind, item_id)
    project = session.get(Project, body.project_id)
    held = shared_store.access(session, body.project_id, kind, row.id) if project else None
    if held is None:
        raise HTTPException(status_code=404, detail="that project has no access to it")
    was = list(held.role_scopes or [])
    session.delete(held)
    admin._audit(session, project.id, "shared.revoke", kind, row.id,
                 {"name": row.name, "was": was})
    if was:
        chairman_notify(
            session, project, f"{shared_store.label(kind, row).capitalize()} was withdrawn",
            f"The instance admin withdrew the {shared_store.label(kind, row)} from "
            f"{project.name}. Teams that had it lose it on their next run.",
            attention=Attention.FYI)
    if row.shared_with == shared_store.ALL:
        note = ("It is still open to every project, so this project's chairs can take "
                "it again. Make it listed to keep it away.")
    else:
        note = "Teams that had it lose it on their next run."
    return {"project_id": project.id, "was": was, "note": note}


# ── a project's view, and its chairs' grants ────────────────────────────────────


def project_view(session: Session, project_id: str) -> dict:
    """What this project may use from the store, and who on it receives each.
    Names and shapes only — never a value, a hint, or another project's use."""
    teams = _team_names(session, project_id)
    out: dict[str, list] = {"variables": [], "data_sources": []}
    for held in shared_store.available(session, project_id):
        row = _base_row(held.kind, held.item)
        row.pop("shared_with", None)
        row["roles"] = held.roles
        row["teams"] = [teams.get(s, s) for s in held.roles]
        out["variables" if held.kind == shared_store.VARIABLE else "data_sources"].append(row)
    return out


@router.get("/projects/{project_id}/shared", dependencies=[authority.READ])
def list_project_shared(project_id: str, session: Session = Depends(db)) -> dict:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="unknown project")
    return project_view(session, project_id)


class SharedGrantIn(BaseModel):
    # Empty is legal: the project keeps access and nobody on it receives it.
    roles: list[str] = Field(default_factory=list)


def grant(session: Session, project: Project, kind: str, item_id: str,
          roles: list[str]) -> dict:
    """The chairs' key, shared by the HTTP door, the MCP tool and the CEO's
    delegation, so all three refuse the same things in the same words."""
    row = shared_store.item(session, kind, item_id)
    held = shared_store.access(session, project.id, kind, item_id) if row else None
    if row is None or (held is None and row.shared_with != shared_store.ALL):
        # The same answer as an item that does not exist: a project the admin
        # did not list learns nothing about what the store holds.
        raise HTTPException(status_code=404, detail="unknown shared item")
    roles = list(dict.fromkeys(r for r in roles if r))
    try:
        policy.check_roles_on_project(session, project.id, roles)
    except policy.Refusal as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    if roles:
        problem = shared_store.project_collision(session, project.id, kind, row)
        if problem:
            raise HTTPException(status_code=422, detail=problem)
    if held is None:
        held = SharedAccess(id=ids.new_id(ids.SHARED_ACCESS), project_id=project.id,
                            item_kind=kind, item_id=row.id, role_scopes=[])
        session.add(held)
    was = list(held.role_scopes or [])
    held.role_scopes = roles
    held.updated_at = utcnow()
    admin._audit(session, project.id, "shared.grant", kind, row.id,
                 {"name": row.name, "was": was, "roles": roles})
    return {"id": row.id, "name": row.name, "kind": kind, "roles": roles, "was": was,
            "note": "Effective on each granted role's next run."}


@router.put("/projects/{project_id}/shared/{kind}/{item_id}", dependencies=[authority.STEER])
def grant_shared(project_id: str, kind: str, item_id: str, body: SharedGrantIn,
                 session: Session = Depends(db)) -> dict:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="unknown project")
    return grant(session, project, _kind(kind), item_id, body.roles)
