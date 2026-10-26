"""The shared store: what a project receives from above it (docs/SHARED_STORE.md).

Every reader goes through this module, so "which shared items does this project
receive, and under which names" has one definition. The lease, the health page,
the CEO's credential roster and the collision refusals all ask it. Two
definitions would drift, and the first drift would be a value injected under a
name the refusals did not know was taken.

Two keys gate a shared item. The instance admin's availability says which
projects may use it at all: `shared_with` is "all", or "listed" plus a
`SharedAccess` row the admin wrote. The project chairs' grant says which roles
receive it, in that row's `role_scopes`. Nothing here reads a shared Secret's
own `role_scopes`, which stay empty.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import Secret, SharedAccess, SharedDataSource
from yaaf.core.naming import env_var_name, file_cred_carrier

log = logging.getLogger("yaaf.shared_store")

VARIABLE, DATA_SOURCE = "variable", "data_source"
KINDS = (VARIABLE, DATA_SOURCE)
ALL, LISTED = "all", "listed"


def source_url_name(name: str) -> str:
    """The secret name a shared source's address is injected under. The same
    shape a project source uses, so an agent's MCP_<NAME>_URL means one thing
    wherever the source came from."""
    return f"mcp-{name}-url"


def source_token_name(name: str) -> str:
    return f"mcp-{name}-token"


def variables(session: Session) -> list[Secret]:
    return list(session.execute(
        select(Secret).where(Secret.project_id.is_(None), Secret.shared_with.is_not(None))
        .order_by(Secret.name)
    ).scalars())


def data_sources(session: Session) -> list[SharedDataSource]:
    return list(session.execute(
        select(SharedDataSource).order_by(SharedDataSource.name)).scalars())


def variable(session: Session, item_id: str) -> Secret | None:
    row = session.get(Secret, item_id)
    if row is None or row.project_id is not None or row.shared_with is None:
        return None
    return row


def item(session: Session, kind: str, item_id: str) -> Secret | SharedDataSource | None:
    if kind == VARIABLE:
        return variable(session, item_id)
    if kind == DATA_SOURCE:
        return session.get(SharedDataSource, item_id)
    return None


def access(session: Session, project_id: str, kind: str, item_id: str) -> SharedAccess | None:
    return session.execute(
        select(SharedAccess).where(SharedAccess.project_id == project_id,
                                   SharedAccess.item_kind == kind,
                                   SharedAccess.item_id == item_id)
    ).scalar_one_or_none()


def access_rows(session: Session, kind: str, item_id: str) -> list[SharedAccess]:
    return list(session.execute(
        select(SharedAccess).where(SharedAccess.item_kind == kind,
                                   SharedAccess.item_id == item_id)
        .order_by(SharedAccess.created_at)
    ).scalars())


def label(kind: str, row) -> str:
    """The item in the words a refusal uses."""
    if kind == VARIABLE:
        return f"shared variable {env_var_name(row.name)}"
    return f"shared data source '{row.name}'"


@dataclass
class Held:
    """One shared item as one project sees it."""

    kind: str
    item: Secret | SharedDataSource
    access: SharedAccess | None

    @property
    def roles(self) -> list[str]:
        return list(self.access.role_scopes or []) if self.access else []


def available(session: Session, project_id: str) -> list[Held]:
    """Every shared item this project may use: open to all, or listed for it.
    Received or not."""
    rows = {(a.item_kind, a.item_id): a for a in session.execute(
        select(SharedAccess).where(SharedAccess.project_id == project_id)).scalars()}
    out: list[Held] = []
    for row in variables(session):
        held = rows.get((VARIABLE, row.id))
        if row.shared_with == ALL or held is not None:
            out.append(Held(VARIABLE, row, held))
    for row in data_sources(session):
        held = rows.get((DATA_SOURCE, row.id))
        if row.shared_with == ALL or held is not None:
            out.append(Held(DATA_SOURCE, row, held))
    return out


def received(session: Session, project_id: str) -> list[Held]:
    """The shared items some role on this project actually gets."""
    return [h for h in available(session, project_id) if h.roles]


def env_names(held: Held) -> list[str]:
    """The variables an item puts into a granted sandbox."""
    if held.kind == VARIABLE:
        return [env_var_name(held.item.name)]
    names = [env_var_name(source_url_name(held.item.name))]
    if held.item.bearer_cipher:
        names.append(env_var_name(source_token_name(held.item.name)))
    return names


def received_names(session: Session, project_id: str) -> dict[str, str]:
    """Env var name → the shared item that puts it into this project's runs."""
    out: dict[str, str] = {}
    for held in received(session, project_id):
        for name in env_names(held):
            out[name] = label(held.kind, held.item)
    return out


def received_source(session: Session, project_id: str, name: str) -> Held | None:
    for held in received(session, project_id):
        if held.kind == DATA_SOURCE and held.item.name == name:
            return held
    return None


# ── collisions: refused at every door, never resolved at lease ─────────────────


def project_collision(session: Session, project_id: str, kind: str,
                      row: Secret | SharedDataSource) -> str | None:
    """Why this project cannot receive this item, if its own wiring already
    holds the name. A grant that shadowed a project value, or was shadowed by
    one, would leave one of the two silently invisible to every agent."""
    from yaaf.core.models import Project

    project = session.get(Project, project_id)
    own_env = {env_var_name(s.name): s.name for s in session.execute(
        select(Secret).where(Secret.project_id == project_id, Secret.llm_mode.is_(None))
    ).scalars()}
    if kind == DATA_SOURCE:
        sources = (project.integrations or {}).get("mcp_servers", []) if project else []
        if any(s.get("name") == row.name for s in sources):
            return (f"this project has its own data source '{row.name}' — remove it or "
                    "rename it before using the shared one")
        wanted = env_names(Held(kind, row, None))
    else:
        wanted = [env_var_name(row.name)]
    for name in wanted:
        if name in own_env:
            return (f"this project already holds its own ${name} ({own_env[name]}) — "
                    "delete it before using the shared one, or two values would answer "
                    "to one name")
    return None


def name_taken(session: Session, project_id: str, *, secret_name: str | None = None,
               source_name: str | None = None) -> str | None:
    """The shared item a project-side write would collide with, as a refusal.
    Asked by the project's own credential and data source doors."""
    names = received_names(session, project_id)
    if secret_name is not None:
        hit = names.get(env_var_name(secret_name))
        if hit:
            return (f"${env_var_name(secret_name)} already reaches this project as the "
                    f"{hit} — take it from the teams that have it first, or pick "
                    "another name")
    if source_name is not None:
        held = received_source(session, project_id, source_name)
        if held is not None:
            return (f"'{source_name}' already reaches this project as a shared data "
                    "source — take it from the teams that have it first, or pick "
                    "another name")
    return None


# ── the lease ───────────────────────────────────────────────────────────────────


def role_env(session: Session, project_id: str, role_slug: str, taken: dict) -> dict:
    """Decrypted env for the shared items this role receives on this project.
    `taken` is what the project's own credentials already put there. A name in
    it is skipped, because a project value never loses to a shared one. The
    doors refuse that collision. This is the second line of defence."""
    from yaaf.core.crypto import decrypt

    env: dict = {}

    def put(var: str, value: str, filename: str | None = None) -> None:
        if var in taken or file_cred_carrier(var) in taken or var in env:
            return
        if filename:
            # Same carrier the project path uses: the bytes ride under it and
            # the entrypoint turns the variable itself into a path.
            env[file_cred_carrier(var)] = json.dumps({"filename": filename, "b64": value})
        else:
            env[var] = value

    for held in received(session, project_id):
        if role_slug not in held.roles:
            continue
        try:
            if held.kind == VARIABLE:
                put(env_var_name(held.item.name), decrypt(held.item.ciphertext),
                    held.item.filename)
            else:
                put(env_var_name(source_url_name(held.item.name)),
                    decrypt(held.item.url_cipher))
                if held.item.bearer_cipher:
                    put(env_var_name(source_token_name(held.item.name)),
                        decrypt(held.item.bearer_cipher))
        except Exception:
            log.warning("could not decrypt shared %s %s", held.kind, held.item.id)
    return env


def role_sources(session: Session, project_id: str, role_slug: str) -> list[dict]:
    """The shared sources this role mounts, as the sandbox spec describes one."""
    specs = []
    for held in received(session, project_id):
        if held.kind != DATA_SOURCE or role_slug not in held.roles:
            continue
        entry = held.item.entry or {}
        spec = {"name": held.item.name,
                "url_env": env_var_name(source_url_name(held.item.name)),
                "access": entry.get("access", "inline")}
        if entry.get("tools") is not None:
            spec["tools"] = list(entry["tools"])
        if held.item.bearer_cipher:
            spec["bearer_env"] = env_var_name(source_token_name(held.item.name))
        specs.append(spec)
    return specs
