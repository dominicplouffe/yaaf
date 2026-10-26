"""Integrations: MCP servers the administrator connects once, for every project.

A data source belongs to one project. That was right for a warehouse only one
project reads, and wrong for the HubSpot, Notion or Trello the company already
pays for: it had to be connected again in every project, and a project nobody
connected it to never learned it existed — so it rebuilt email campaigns, a
CRM, a task board, from scratch, next to the one already paid for.

An integration is the same server held above the projects. The administrator
sets the address, the sign-in and the allowed tools once; each project decides
to use it or not (`ProjectIntegration`), and for which roles. Using it is opt
in, and it is enforced where a token cannot reach around it:

* its credentials live in the integration's own vault, never as `Secret` rows,
  so no role-scope grant can deliver them to a project that did not choose it;
* a run receives them only through `mount`, which reads the project's own
  `use` rows;
* an OAuth token is renewed under a row lock, because every project shares one
  refresh token and the provider rotates it on use — two leases renewing at
  once would leave one holding a token the provider already revoked.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from yaaf.core import ids
from yaaf.core.models import (
    Integration,
    IntegrationDecision,
    Project,
    ProjectIntegration,
    utcnow,
)
from yaaf.core.naming import env_var_name
from yaaf.server.mcp_oauth import SourceHome, ensure_fresh

log = logging.getLogger("yaaf.integrations")

#: The data source naming rule, because the name becomes the MCP server name.
NAME_PATTERN = "^[a-z0-9-]{2,32}$"

#: The vault key the address is kept under. The token keys come from `key`.
URL_KEY = "url"


class IntegrationError(Exception):
    """A refusal, worded for whoever pressed the button or called the route."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class IntegrationHome(SourceHome):
    """An integration's tokens, in its own vault. `granted` means nothing
    here: nothing in the vault is granted to a role, and a run receives the
    token only through an attachment (`mount`)."""

    noun = "integration"
    project_id = None

    def __init__(self, integration: Integration):
        self.integration = integration

    def key(self, entry: dict, part: str) -> str:
        return part

    def read(self, name: str | None) -> str | None:
        from yaaf.core.crypto import decrypt

        cipher = (self.integration.vault or {}).get(name or "")
        return decrypt(cipher) if cipher else None

    def write(self, entry: dict, name: str, value: str, *, granted: bool) -> str:
        from yaaf.core.crypto import encrypt

        self.integration.vault = {**(self.integration.vault or {}), name: encrypt(value)}
        flag_modified(self.integration, "vault")
        return name

    def delete(self, name: str) -> None:
        vault = dict(self.integration.vault or {})
        vault.pop(name, None)
        self.integration.vault = vault
        flag_modified(self.integration, "vault")

    def persist(self, entry: dict) -> None:
        # The name is the row's own column and roles are a project's choice;
        # neither belongs in the stored entry.
        self.integration.entry = {k: v for k, v in entry.items() if k not in ("name", "roles")}
        flag_modified(self.integration, "entry")


def entry_of(integration: Integration) -> dict:
    """The integration as a data source entry: what the mount, the allowlist
    check and the health page read, in the shape they already read."""
    return {**(integration.entry or {}), "name": integration.name}


# ── reading ─────────────────────────────────────────────────────────────────────


def catalog(session: Session) -> list[Integration]:
    return list(session.execute(select(Integration).order_by(Integration.name)).scalars())


def by_name(session: Session, name: str) -> Integration | None:
    return session.execute(
        select(Integration).where(Integration.name == name)).scalar_one_or_none()


def decisions(session: Session, project_id: str) -> list[tuple[ProjectIntegration, Integration]]:
    """Every integration this project has decided about, use or decline."""
    rows = session.execute(
        select(ProjectIntegration, Integration)
        .join(Integration, Integration.id == ProjectIntegration.integration_id)
        .where(ProjectIntegration.project_id == project_id)
        .order_by(Integration.name)).all()
    return [(row[0], row[1]) for row in rows]


def used(session: Session, project_id: str) -> list[tuple[ProjectIntegration, Integration]]:
    return [(d, i) for d, i in decisions(session, project_id)
            if d.decision == IntegrationDecision.USE]


def projects_using(session: Session, integration_id: str) -> list[Project]:
    return list(session.execute(
        select(Project)
        .join(ProjectIntegration, ProjectIntegration.project_id == Project.id)
        .where(ProjectIntegration.integration_id == integration_id,
               ProjectIntegration.decision == IntegrationDecision.USE)
        .order_by(Project.name)).scalars())


def roster(session: Session, project_id: str) -> set[str]:
    """Every role this project actually runs: its teams' and its CEO's."""
    from yaaf.core.models import Agent, AgentKind, Role, RoleVersion, Team

    teams = select(Team.role_version_id).where(Team.project_id == project_id)
    ceo = select(Agent.role_version_id).where(Agent.project_id == project_id,
                                              Agent.kind == AgentKind.CEO)
    return set(session.execute(
        select(Role.slug).join(RoleVersion, RoleVersion.role_id == Role.id)
        .where(RoleVersion.id.in_(teams.union(ceo)))).scalars())


def source_entry(session: Session, project: Project, name: str) -> dict | None:
    """A source this project reaches under `name` — its own data source, or an
    integration it uses. The two never share a name (`attach` refuses it)."""
    for source in (project.integrations or {}).get("mcp_servers", []):
        if source.get("name") == name:
            return source
    for decision, integration in used(session, project.id):
        if integration.name == name:
            return {**entry_of(integration), "roles": list(decision.roles or [])}
    return None


def available(session: Session, project_id: str) -> list[tuple[Integration, ProjectIntegration | None]]:
    """Integrations this project does not use: never decided, or declined."""
    decided = {i.id: d for d, i in decisions(session, project_id)}
    return [(i, decided.get(i.id)) for i in catalog(session)
            if decided.get(i.id) is None or decided[i.id].decision != IntegrationDecision.USE]


def view(integration: Integration, *, using: list[Project] | None = None) -> dict:
    """What a screen or a tool may see. Never the vault, never a value."""
    entry = integration.entry or {}
    out = {
        "id": integration.id,
        "name": integration.name,
        "description": integration.description or "",
        "covers": list(integration.covers or []),
        "url_host": entry.get("url_host"),
        "url_port": entry.get("url_port"),
        "access": entry.get("access", "inline"),
        "auth": entry.get("auth", "none"),
        "tools": entry.get("tools"),
        "last_check": entry.get("last_check"),
        "oauth": {k: v for k, v in (entry.get("oauth") or {}).items()
                  if k in ("status", "error", "expires_at", "obtained_at", "client_id",
                           "scope")} or None,
        "task_mirror": integration.task_mirror,
        "created_at": integration.created_at.isoformat() if integration.created_at else None,
    }
    if using is not None:
        out["used_by"] = [{"id": p.id, "slug": p.slug, "name": p.name} for p in using]
    return out


def decision_view(decision: ProjectIntegration, integration: Integration) -> dict:
    return {**view(integration), "decision": decision.decision,
            "roles": list(decision.roles or []), "reason": decision.reason,
            "mirror_tasks": bool(decision.mirror_tasks),
            "decided_by": decision.decided_by,
            "decided_at": (decision.updated_at or decision.created_at).isoformat()
            if (decision.updated_at or decision.created_at) else None}


# ── deciding ────────────────────────────────────────────────────────────────────


def _decision_row(session: Session, project_id: str, integration_id: str) -> ProjectIntegration | None:
    return session.execute(
        select(ProjectIntegration).where(ProjectIntegration.project_id == project_id,
                                         ProjectIntegration.integration_id == integration_id)
    ).scalar_one_or_none()


def attach(session: Session, project: Project, integration: Integration, roles: list[str], *,
           decided_by: str, reason: str | None = None,
           mirror_tasks: bool | None = None) -> ProjectIntegration:
    """Use it, for these roles. Saving again is the edit path: it moves the
    roles and keeps the row, so the record of when it was first chosen stays."""
    if not roles:
        raise IntegrationError(
            f"say which roles get {integration.name}'s tools — an integration used by "
            "nobody is a decline, and there is a verb for that")
    for source in (project.integrations or {}).get("mcp_servers", []):
        if source.get("name") == integration.name:
            raise IntegrationError(
                f"this project already has its own data source called {integration.name!r}. "
                "Two servers under one name would mount as one set of tools; remove the "
                "project's source, or promote it to this integration, first.",
                status_code=409)
    if mirror_tasks and not integration.task_mirror:
        raise IntegrationError(
            f"{integration.name} is not set up as a task manager — an administrator "
            "says how tasks are written to it, in Settings → Integrations, first")
    row = _decision_row(session, project.id, integration.id)
    if row is None:
        row = ProjectIntegration(id=ids.new_id(ids.PROJECT_INTEGRATION), project_id=project.id,
                                 integration_id=integration.id, created_at=utcnow())
        session.add(row)
    row.decision = IntegrationDecision.USE
    row.roles = sorted(set(roles))
    row.reason = reason
    if mirror_tasks is not None:
        row.mirror_tasks = bool(mirror_tasks)
    row.decided_by = decided_by[:80]
    row.updated_at = utcnow()
    session.flush()
    return row


def decline(session: Session, project: Project, integration: Integration, *,
            decided_by: str, reason: str | None = None) -> ProjectIntegration:
    """Not for this project, said out loud so nobody asks again by accident.
    Declining one in use takes its tools away from the next run."""
    row = _decision_row(session, project.id, integration.id)
    if row is None:
        row = ProjectIntegration(id=ids.new_id(ids.PROJECT_INTEGRATION), project_id=project.id,
                                 integration_id=integration.id, created_at=utcnow())
        session.add(row)
    row.decision = IntegrationDecision.DECLINE
    row.roles = []
    row.mirror_tasks = False
    row.reason = reason
    row.decided_by = decided_by[:80]
    row.updated_at = utcnow()
    session.flush()
    return row


def forget(session: Session, project_id: str, integration_id: str) -> bool:
    """Back to undecided — the Founder and the form will ask again."""
    row = _decision_row(session, project_id, integration_id)
    if row is None:
        return False
    session.delete(row)
    return True


# ── running ─────────────────────────────────────────────────────────────────────


def env_names(name: str) -> tuple[str, str]:
    """The URL and token variables a run reads, in the data source shape
    (`MCP_<NAME>_URL`, `MCP_<NAME>_TOKEN`) so the sandbox cannot tell an
    integration from a project's own source."""
    return env_var_name(f"mcp-{name}-url"), env_var_name(f"mcp-{name}-token")


def ensure_fresh_locked(session: Session, integration: Integration) -> str | None:
    """The current access token, renewed at most once however many projects
    lease at the same moment.

    The row is locked before the expiry is read, and re-read under the lock,
    so the second lease finds the token the first one just stored instead of
    spending the refresh token a second time. SQLite has no row locks, and one
    writer at a time already serialises it."""
    if (integration.entry or {}).get("auth") != "oauth":
        return None
    locked = session.execute(
        select(Integration).where(Integration.id == integration.id)
        .with_for_update().execution_options(populate_existing=True)).scalar_one()
    home = IntegrationHome(locked)
    entry = entry_of(locked)
    return ensure_fresh(home, entry)


def mount(session: Session, project: Project, role_slug: str | None) -> tuple[list[dict], dict]:
    """The integrations this role's run mounts, as data source specs, and the
    variables they read. Only `use` rows naming this role; nothing else."""
    specs: list[dict] = []
    env: dict = {}
    if not role_slug:
        return specs, env
    for decision, integration in used(session, project.id):
        if role_slug not in (decision.roles or []):
            continue
        entry = integration.entry or {}
        token = ensure_fresh_locked(session, integration) if entry.get("auth") == "oauth" \
            else IntegrationHome(integration).read(entry.get("bearer_secret"))
        url = IntegrationHome(integration).read(URL_KEY)
        if not url:
            log.warning("integration %s has no address; not mounted", integration.name)
            continue
        url_env, token_env = env_names(integration.name)
        spec = {"name": integration.name, "url_env": url_env,
                "access": entry.get("access", "inline")}
        if entry.get("tools") is not None:
            spec["tools"] = list(entry["tools"])
        env[url_env] = url
        if token:
            spec["bearer_env"] = token_env
            env[token_env] = token
        specs.append(spec)
    return specs, env


def slugify(raw: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", (raw or "").lower()).strip("-")[:32]
