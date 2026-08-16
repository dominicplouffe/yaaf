"""Chairman/admin API: project lifecycle, org setup, approvals, amendments.

Everything here is authenticated with the admin token (full user auth is the
UI milestone). Chairman powers are deliberately narrow at runtime: speak in the
boardroom, decide approvals, extend a hypothesis once, amend the constitution
through the cooling-off, pause/stop things, unfreeze threads, spot-audit. The
chairman never edits tasks, posts to team rooms, or touches memory — that
discipline is the product.
"""

from __future__ import annotations

import secrets as pysecrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.constitution import ConstitutionDoc, create_initial, effective, propose_amendment
from yaaf.core.db import notify
from yaaf.core.models import (
    Agent,
    AgentKind,
    Amendment,
    Approval,
    ApprovalStatus,
    AuditLog,
    Channel,
    ChannelKind,
    Evidence,
    Hypothesis,
    HypothesisStatus,
    Message,
    Notice,
    NoticeKind,
    Project,
    ProjectMember,
    ProjectStatus,
    Role,
    RoleVersion,
    Run,
    RunQueue,
    RunStatus,
    Secret,
    Task,
    TaskStatus,
    Team,
    User,
    VerificationStatus,
    utcnow,
)
from yaaf.server import policy
from yaaf.server.api.agent import WAKE_CHANNEL
from yaaf.server.deps import admin_auth, db

router = APIRouter(dependencies=[Depends(admin_auth)])


def _audit(session: Session, project_id: str | None, action: str, entity: str,
           entity_id: str | None, detail: dict | None = None) -> None:
    session.add(
        AuditLog(
            id=ids.new_id(ids.AUDIT),
            project_id=project_id,
            actor="user:admin",
            action=action,
            entity=entity,
            entity_id=entity_id,
            detail=detail or {},
        )
    )


# ── users & roles ───────────────────────────────────────────────────────────────


class UserIn(BaseModel):
    email: str
    display_name: str


@router.post("/users")
def create_user(body: UserIn, session: Session = Depends(db)) -> dict:
    existing = session.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
    if existing:
        return {"id": existing.id, "existing": True}
    user = User(id=ids.new_id(ids.USER), email=body.email, display_name=body.display_name)
    session.add(user)
    return {"id": user.id, "existing": False}


class RoleIn(BaseModel):
    slug: str = Field(pattern="^[a-z0-9-]{2,64}$")
    name: str
    is_ceo_role: bool = False
    description: str
    system_prompt: str
    model: str = "claude-sonnet-5"
    effort: str = "high"
    tool_permissions: dict = Field(default_factory=dict)
    secret_scopes: list[str] = Field(default_factory=list)


@router.post("/roles")
def create_role(body: RoleIn, session: Session = Depends(db)) -> dict:
    # Roles never own MCP servers. A server line frozen into a role template
    # follows every project that pins the role — the ghost that broke a live
    # project which had never heard of the source it "declared". Data sources
    # are project-owned and assigned to roles (Settings → Data sources).
    if "mcp_servers" in body.tool_permissions:
        raise HTTPException(
            status_code=422,
            detail="roles do not own MCP servers — add a Data Source on the "
                   "project (Settings → Data sources) and assign it to this role",
        )
    role = session.execute(select(Role).where(Role.slug == body.slug)).scalar_one_or_none()
    if role is None:
        role = Role(
            id=ids.new_id(ids.ROLE), slug=body.slug, name=body.name, is_ceo_role=body.is_ceo_role
        )
        session.add(role)
        session.flush()
        version = 1
    else:
        latest = session.execute(
            select(RoleVersion)
            .where(RoleVersion.role_id == role.id)
            .order_by(RoleVersion.version.desc())
            .limit(1)
        ).scalar_one_or_none()
        version = (latest.version if latest else 0) + 1
    row = RoleVersion(
        id=ids.new_id(ids.ROLE_VERSION),
        role_id=role.id,
        version=version,
        description=body.description,
        system_prompt=body.system_prompt,
        model=body.model,
        effort=body.effort,
        tool_permissions=body.tool_permissions,
        secret_scopes=body.secret_scopes,
    )
    session.add(row)
    return {"role_id": role.id, "role_version_id": row.id, "version": version}


@router.get("/roles/{slug}")
def get_role(slug: str, session: Session = Depends(db)) -> dict:
    role = session.execute(select(Role).where(Role.slug == slug)).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="unknown role")
    latest = session.execute(
        select(RoleVersion)
        .where(RoleVersion.role_id == role.id)
        .order_by(RoleVersion.version.desc())
        .limit(1)
    ).scalar_one()
    return {
        "slug": role.slug,
        "name": role.name,
        "is_ceo_role": role.is_ceo_role,
        "version": latest.version,
        "description": latest.description,
        "system_prompt": latest.system_prompt,
        "model": latest.model,
        "effort": latest.effort,
        "tool_permissions": latest.tool_permissions,
        "secret_scopes": latest.secret_scopes,
    }


@router.get("/roles")
def list_roles(session: Session = Depends(db)) -> list[dict]:
    out = []
    for role in session.execute(select(Role)).scalars():
        versions = list(
            session.execute(
                select(RoleVersion)
                .where(RoleVersion.role_id == role.id)
                .order_by(RoleVersion.version.desc())
            ).scalars()
        )
        out.append(
            {
                "id": role.id,
                "slug": role.slug,
                "name": role.name,
                "is_ceo_role": role.is_ceo_role,
                "latest_version": versions[0].version if versions else 0,
                "model": versions[0].model if versions else None,
            }
        )
    return out


# ── project creation ────────────────────────────────────────────────────────────


class TeamSpec(BaseModel):
    role_slug: str
    name: str
    # Agent display names. Empty is only legal in solo mode (the CEO is the
    # team); create_project/add_team enforce that below.
    employees: list[str] = Field(default_factory=list)
    lead: str | None = None  # employee name; defaults to first when >1


class ProjectCreate(BaseModel):
    slug: str = Field(pattern="^[a-z0-9-]{2,64}$")
    name: str
    goal: str = Field(min_length=10)
    chairman_email: str
    billing_mode: str = Field(default="api_key", pattern="^(api_key|subscription)$")
    controlled_domains: list[str] = Field(default_factory=list)
    constitution: dict = Field(default_factory=dict)  # overrides onto defaults
    config: dict = Field(default_factory=dict)
    ceo_role_slug: str = "ceo"
    ceo_name: str = "CEO"
    teams: list[TeamSpec] = Field(default_factory=list)
    # Solo mode (the M2 single-agent arm): the CEO is placed as the sole member
    # — hence derived lead — of the only team, so one brain plans AND executes
    # under byte-identical policy. Requires exactly one team with no employees.
    solo: bool = False


def _latest_role_version(session: Session, slug: str) -> RoleVersion:
    role = session.execute(select(Role).where(Role.slug == slug)).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=422, detail=f"unknown role slug '{slug}'")
    return session.execute(
        select(RoleVersion)
        .where(RoleVersion.role_id == role.id)
        .order_by(RoleVersion.version.desc())
        .limit(1)
    ).scalar_one()


@router.post("/projects")
def create_project(body: ProjectCreate, session: Session = Depends(db)) -> dict:
    chairman = session.execute(
        select(User).where(User.email == body.chairman_email)
    ).scalar_one_or_none()
    if chairman is None:
        raise HTTPException(status_code=422, detail="chairman user does not exist; create it first")
    if session.execute(select(Project).where(Project.slug == body.slug)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="project slug already exists")
    if body.solo and (len(body.teams) != 1 or body.teams[0].employees):
        raise HTTPException(
            status_code=422,
            detail="solo mode takes exactly one team with no employees — the CEO is the team",
        )
    if not body.solo and any(not spec.employees for spec in body.teams):
        raise HTTPException(status_code=422, detail="every team needs at least one employee")

    ceo_role_version = _latest_role_version(session, body.ceo_role_slug)

    project = Project(
        id=ids.new_id(ids.PROJECT),
        slug=body.slug,
        name=body.name,
        goal=body.goal,
        chairman_user_id=chairman.id,
        billing_mode=body.billing_mode,
        controlled_domains=body.controlled_domains,
        config=body.config,
    )
    session.add(project)
    session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=chairman.id, role="chairman"))

    # The constitution freezes at creation. Overrides are validated against the schema.
    doc = ConstitutionDoc.model_validate({**ConstitutionDoc().model_dump(), **body.constitution})
    create_initial(session, project.id, doc)

    ceo = Agent(
        id=ids.new_id(ids.AGENT),
        project_id=project.id,
        team_id=None,
        kind=AgentKind.CEO,
        name=body.ceo_name,
        role_version_id=ceo_role_version.id,
        model_override=None,
    )
    session.add(ceo)
    session.add(Channel(id=ids.new_id(ids.CHANNEL), project_id=project.id,
                        kind=ChannelKind.BOARDROOM))
    session.add(Channel(id=ids.new_id(ids.CHANNEL), project_id=project.id,
                        kind=ChannelKind.LEADERSHIP))

    team_out = []
    for spec in body.teams:
        role_version = _latest_role_version(session, spec.role_slug)
        team = Team(
            id=ids.new_id(ids.TEAM),
            project_id=project.id,
            role_version_id=role_version.id,
            name=spec.name,
        )
        session.add(team)
        session.flush()
        agents = []
        for employee_name in spec.employees:
            agent = Agent(
                id=ids.new_id(ids.AGENT),
                project_id=project.id,
                team_id=team.id,
                kind=AgentKind.EMPLOYEE,
                name=employee_name,
            )
            session.add(agent)
            agents.append(agent)
        session.flush()
        if len(agents) > 1:
            lead_name = spec.lead or spec.employees[0]
            lead = next((a for a in agents if a.name == lead_name), agents[0])
            team.lead_agent_id = lead.id
        session.add(Channel(id=ids.new_id(ids.CHANNEL), project_id=project.id,
                            kind=ChannelKind.TEAM, team_id=team.id))
        if body.solo:
            ceo.team_id = team.id
            agents.append(ceo)
        team_out.append({"id": team.id, "name": team.name,
                         "agents": [{"id": a.id, "name": a.name} for a in agents]})

    _audit(session, project.id, "project.create", "project", project.id)
    return {"id": project.id, "ceo_agent_id": ceo.id, "teams": team_out,
            "constitution": doc.model_dump(), "status": project.status}


# Config keys that are this install's plumbing, not the project's design:
# Slack wiring, webhook signing secrets, experiment bookkeeping. Everything
# else in config (runtime, cadences, data sources…) travels with the bundle.
_NON_PORTABLE_CONFIG_KEYS = frozenset(
    {"slack", "github_webhook_secret", "webhook_secret", "experiment"}
)


@router.get("/projects/{project_id}/export")
def export_project(project_id: str, session: Session = Depends(db)) -> dict:
    """The project's design as a portable setup file. The `project` section is
    a ready ProjectCreate body (the importer supplies slug and chairman); the
    role definitions and credential *names* ride along so another install can
    rebuild the org. Secret values never leave — they are encrypted with this
    deployment's master key — so an import asks for them again. The New
    Project page accepts this file."""
    project = _get_project(session, project_id)
    ceo = session.execute(
        select(Agent).where(Agent.project_id == project.id, Agent.kind == AgentKind.CEO)
    ).scalar_one()

    roles: dict[str, dict] = {}

    def record_role(role_version_id: str) -> str:
        """Capture the pinned version's content — what this project actually
        runs, not whatever the library has moved on to — and return the slug."""
        version = session.get(RoleVersion, role_version_id)
        role = session.get(Role, version.role_id)
        roles.setdefault(role.slug, {
            "slug": role.slug,
            "name": role.name,
            "is_ceo_role": role.is_ceo_role,
            "description": version.description,
            "system_prompt": version.system_prompt,
            "model": version.model,
            "effort": version.effort,
            # Roles no longer own MCP servers; strip the key from any old
            # pinned version so importing the role never 422s.
            "tool_permissions": {
                k: v for k, v in (version.tool_permissions or {}).items()
                if k != "mcp_servers"
            },
            "secret_scopes": version.secret_scopes,
        })
        return role.slug

    ceo_role_slug = record_role(ceo.role_version_id)

    teams = []
    for team in session.execute(
        select(Team).where(Team.project_id == project.id).order_by(Team.created_at)
    ).scalars():
        role_slug = record_role(team.role_version_id)
        members = list(session.execute(
            select(Agent)
            .where(Agent.team_id == team.id, Agent.kind == AgentKind.EMPLOYEE)
            .order_by(Agent.created_at)
        ).scalars())
        teams.append({
            "role_slug": role_slug,
            "name": team.name,
            "employees": [a.name for a in members],
            "lead": next((a.name for a in members if a.id == team.lead_agent_id), None),
        })

    config = {k: v for k, v in (project.config or {}).items()
              if k not in _NON_PORTABLE_CONFIG_KEYS}
    if "mcp_servers" in config:
        config["mcp_servers"] = [
            {k: v for k, v in entry.items() if k != "last_check"}
            for entry in config["mcp_servers"]
        ]

    # Billing credentials stay out entirely: their values cannot travel, and
    # the twin connects its own in Settings (the page already insists).
    secret_names = [
        {"name": s.name, "role_scopes": s.role_scopes, "kind": s.kind}
        for s in session.execute(
            select(Secret)
            .where(Secret.project_id == project.id, Secret.kind == "service")
            .order_by(Secret.created_at)
        ).scalars()
    ]

    _audit(session, project.id, "project.export", "project", project.id)
    return {
        "yaaf_bundle": 1,
        "kind": "project_setup",
        "source_slug": project.slug,
        "exported_at": utcnow().isoformat(),
        "project": {
            "slug": project.slug,
            "name": project.name,
            "goal": project.goal,
            "billing_mode": project.billing_mode,
            "controlled_domains": project.controlled_domains,
            "constitution": effective(session, project.id).model_dump(),
            "config": config,
            "ceo_role_slug": ceo_role_slug,
            "ceo_name": ceo.name,
            "teams": teams,
            "solo": ceo.team_id is not None,
        },
        "roles": list(roles.values()),
        "secrets": secret_names,
    }


# ── lifecycle: the kill switches ────────────────────────────────────────────────


def _get_project(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="unknown project")
    return project


def _revoke_running(session: Session, project_id: str) -> int:
    """Kill switch: revoke tokens of live runs and drop queued work."""
    n = 0
    for run in session.execute(
        select(Run).where(Run.project_id == project_id, Run.status == RunStatus.RUNNING)
    ).scalars():
        run.token_revoked = True
        n += 1
    for queued in session.execute(
        select(RunQueue).where(RunQueue.project_id == project_id)
    ).scalars():
        session.delete(queued)
    return n


@router.post("/projects/{project_id}/teams")
def add_team(project_id: str, body: TeamSpec, session: Session = Depends(db)) -> dict:
    """Add a team to an existing project: agents, derived lead, team channel."""
    if not body.employees:
        raise HTTPException(status_code=422, detail="a team needs at least one employee")
    project = _get_project(session, project_id)
    role_version = _latest_role_version(session, body.role_slug)
    team = Team(
        id=ids.new_id(ids.TEAM),
        project_id=project.id,
        role_version_id=role_version.id,
        name=body.name,
    )
    session.add(team)
    session.flush()
    agents = []
    for employee_name in body.employees:
        agent = Agent(
            id=ids.new_id(ids.AGENT),
            project_id=project.id,
            team_id=team.id,
            kind=AgentKind.EMPLOYEE,
            name=employee_name,
        )
        session.add(agent)
        agents.append(agent)
    session.flush()
    if len(agents) > 1:
        lead_name = body.lead or body.employees[0]
        lead = next((a for a in agents if a.name == lead_name), agents[0])
        team.lead_agent_id = lead.id
    session.add(Channel(id=ids.new_id(ids.CHANNEL), project_id=project.id,
                        kind=ChannelKind.TEAM, team_id=team.id))
    _audit(session, project.id, "team.add", "team", team.id,
           {"role_slug": body.role_slug, "employees": body.employees})
    notify(session, WAKE_CHANNEL, project.id)
    return {"id": team.id, "name": team.name,
            "agents": [{"id": a.id, "name": a.name} for a in agents]}


# ── data sources (MCP servers) ──────────────────────────────────────────────────


class DataSourceIn(BaseModel):
    name: str = Field(pattern="^[a-z0-9-]{2,32}$")
    url: str
    bearer: str | None = None
    roles: list[str] = Field(min_length=1)


def _upsert_project_secret(session: Session, project_id: str, name: str, value: str,
                           role_scopes: list[str]) -> Secret:
    from yaaf.core.crypto import encrypt

    existing = session.execute(
        select(Secret).where(Secret.project_id == project_id, Secret.name == name)
    ).scalar_one_or_none()
    if existing:
        existing.ciphertext = encrypt(value)
        existing.role_scopes = role_scopes
        return existing
    row = Secret(id=ids.new_id(ids.SECRET), project_id=project_id, name=name,
                 ciphertext=encrypt(value), role_scopes=role_scopes, kind="service")
    session.add(row)
    return row


def _parse_source_url(raw: str, detail: str):
    """One rule for what counts as a data source address, wherever the address
    arrives from — the Data Sources form or a bare secret write. 422s with the
    caller's own wording, because the two doors read very differently."""
    from urllib.parse import urlparse

    parsed = urlparse((raw or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=422, detail=detail)
    return parsed


def _data_source_using(session: Session, project_id: str | None, secret_name: str) -> str | None:
    """The data source that treats this secret as its address, if any. The
    sandbox hands that value to the MCP client verbatim, so a bare hostname or
    a mispasted token is not a secret with a typo — it is a data source that
    will never mount. Returns the source's name so the refusal can say which."""
    if not project_id:
        return None
    project = session.get(Project, project_id)
    if project is None:
        return None
    for source in (project.config or {}).get("mcp_servers", []):
        if source.get("url_secret") == secret_name:
            return source.get("name")
    return None


def _check_data_source(url: str, bearer: str | None) -> dict:
    """Reachability, not correctness: any HTTP answer means the address is
    real; a connection failure gets a plain-language reason."""
    import httpx

    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    try:
        response = httpx.get(url, headers=headers, timeout=10, follow_redirects=True)
        return {"ok": True, "detail": f"reachable (HTTP {response.status_code})"}
    except httpx.ConnectError:
        return {"ok": False, "detail": "nothing is listening at that address — check "
                                       "host and port, and that the server is running"}
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"could not connect: {exc.__class__.__name__}"}


@router.post("/projects/{project_id}/mcp_servers")
def add_data_source(project_id: str, body: DataSourceIn, session: Session = Depends(db)) -> dict:
    """One form, everything wired: validates the URL at the door, stores it
    (and any token) as role-granted secrets, records the mapping in project
    config, and preflights reachability. Egress and the proxy follow
    automatically; no role edit, no repin, no CLI."""
    project = _get_project(session, project_id)
    parsed = _parse_source_url(
        body.url,
        "that is not a URL — a data source needs a full address like "
        "https://host:1234/path (schemes http/https only)",
    )
    for slug in body.roles:
        _latest_role_version(session, slug)  # 422s on unknown role

    url_secret = _upsert_project_secret(
        session, project.id, f"mcp-{body.name}-url", body.url.strip(), body.roles)
    entry = {
        "name": body.name,
        "url_secret": url_secret.name,
        "roles": body.roles,
        # Host/port recorded in config (chairman-visible) so egress derivation
        # never needs to decrypt anything.
        "url_host": parsed.hostname,
        "url_port": parsed.port or (443 if parsed.scheme == "https" else 80),
    }
    if body.bearer:
        token_secret = _upsert_project_secret(
            session, project.id, f"mcp-{body.name}-token", body.bearer, body.roles)
        entry["bearer_secret"] = token_secret.name

    check = _check_data_source(body.url.strip(), body.bearer)
    entry["last_check"] = {**check, "at": utcnow().isoformat()}

    servers = [s for s in (project.config or {}).get("mcp_servers", [])
               if s.get("name") != body.name]
    project.config = {**(project.config or {}), "mcp_servers": [*servers, entry]}
    _audit(session, project.id, "mcp_server.save", "project", project.id,
           {"name": body.name, "roles": body.roles, "host": entry["url_host"],
            "check_ok": check["ok"]})
    return {"name": body.name, "check": check,
            "note": "Egress updates and the proxy reloads automatically within ~15s; "
                    "effective on each granted role's next run."}


@router.delete("/projects/{project_id}/mcp_servers/{name}")
def delete_data_source(project_id: str, name: str, session: Session = Depends(db)) -> dict:
    project = _get_project(session, project_id)
    servers = (project.config or {}).get("mcp_servers", [])
    entry = next((s for s in servers if s.get("name") == name), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown data source")
    for secret_name in (entry.get("url_secret"), entry.get("bearer_secret")):
        if secret_name:
            row = session.execute(
                select(Secret).where(Secret.project_id == project.id,
                                     Secret.name == secret_name)
            ).scalar_one_or_none()
            if row is not None:
                session.delete(row)
    project.config = {**project.config,
                      "mcp_servers": [s for s in servers if s.get("name") != name]}
    _audit(session, project.id, "mcp_server.delete", "project", project.id, {"name": name})
    return {"deleted": name}


@router.post("/projects/{project_id}/mcp_servers/{name}/check")
def check_data_source(project_id: str, name: str, session: Session = Depends(db)) -> dict:
    from yaaf.core.crypto import decrypt

    project = _get_project(session, project_id)
    servers = (project.config or {}).get("mcp_servers", [])
    entry = next((s for s in servers if s.get("name") == name), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown data source")

    def value_of(secret_name: str | None) -> str | None:
        if not secret_name:
            return None
        row = session.execute(
            select(Secret).where(Secret.project_id == project.id,
                                 Secret.name == secret_name)
        ).scalar_one_or_none()
        return decrypt(row.ciphertext) if row else None

    url = value_of(entry.get("url_secret"))
    if not url:
        raise HTTPException(status_code=422, detail="the data source's URL secret is missing")
    check = _check_data_source(url, value_of(entry.get("bearer_secret")))
    entry["last_check"] = {**check, "at": utcnow().isoformat()}
    project.config = {**project.config, "mcp_servers": servers}
    _audit(session, project.id, "mcp_server.check", "project", project.id,
           {"name": name, "ok": check["ok"]})
    return check


@router.get("/egress/config")
def egress_config(session: Session = Depends(db)) -> dict:
    """The generated per-role squid config, served for `yaaf egress-sync` to
    write on the compose host (where deploy/proxy/ actually lives)."""
    from yaaf.server.egress import build

    try:
        assignments, files = build(session)
    except RuntimeError as exc:  # port collision — actionable, not a 500
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"assignments": assignments, "files": files}


@router.post("/teams/{team_id}/repin")
def repin_team(team_id: str, session: Session = Depends(db)) -> dict:
    """Adopt the latest version of the team's role. Teams pin their role
    version at creation so an edited prompt never changes running agents
    behind the chairman's back; this is the chairman explicitly adopting it —
    the golden evals are the gate on the edit itself."""
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="unknown team")
    current = session.get(RoleVersion, team.role_version_id)
    role = session.get(Role, current.role_id)
    latest = _latest_role_version(session, role.slug)
    if latest.id == team.role_version_id:
        return {"team_id": team.id, "changed": False, "version": latest.version}
    team.role_version_id = latest.id
    _audit(session, team.project_id, "team.repin", "team", team.id,
           {"role": role.slug, "from_version": current.version, "to_version": latest.version})
    return {"team_id": team.id, "changed": True, "version": latest.version}


class ProjectPatch(BaseModel):
    billing_mode: str | None = Field(default=None, pattern="^(api_key|subscription)$")
    billing_secret_id: str | None = None
    controlled_domains: list[str] | None = None
    # Merged into the existing config (mutable configuration only — the
    # constitution changes exclusively through amendments).
    config: dict | None = None


def _billing_shape_error(mode: str, plaintext: str) -> str | None:
    """Subscription tokens come from `claude setup-token` and start sk-ant-oat;
    API keys start sk-ant-api. A mismatched pair fails every future run with a
    baffling 'Not logged in' — refuse it while the human is still looking."""
    is_oauth = plaintext.startswith("sk-ant-oat")
    if mode == "subscription" and not is_oauth:
        return ("that credential is not a subscription token (sk-ant-oat…). Run "
                "`claude setup-token` and paste the result — or switch to API-key mode.")
    if mode == "api_key" and is_oauth:
        return ("that credential is a subscription OAuth token, not an API key "
                "(sk-ant-api…). Paste a key from the Anthropic console — or switch "
                "to subscription mode.")
    return None


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch, session: Session = Depends(db)) -> dict:
    project = _get_project(session, project_id)
    changed: dict = {}
    if body.billing_mode is not None:
        project.billing_mode = body.billing_mode
        changed["billing_mode"] = body.billing_mode
    if body.billing_secret_id is not None:
        secret = session.get(Secret, body.billing_secret_id)
        if secret is None or (secret.project_id not in (None, project.id)):
            raise HTTPException(status_code=422, detail="unknown secret for this project")
        if secret.kind != "billing":
            raise HTTPException(
                status_code=422,
                detail=f"'{secret.name}' is a service credential, not a billing one — "
                       "agents cannot pay for Claude with it. Connect a billing "
                       "credential (kind='billing') instead.",
            )
        project.billing_secret_id = body.billing_secret_id
        changed["billing_secret_id"] = body.billing_secret_id
    # Validate the (mode, credential) pair whenever either side moved.
    if (body.billing_mode is not None or body.billing_secret_id is not None) \
            and project.billing_secret_id:
        from yaaf.core.crypto import decrypt

        attached = session.get(Secret, project.billing_secret_id)
        shape_error = _billing_shape_error(project.billing_mode, decrypt(attached.ciphertext))
        if shape_error:
            raise HTTPException(status_code=422, detail=shape_error)
    if body.controlled_domains is not None:
        project.controlled_domains = body.controlled_domains
        changed["controlled_domains"] = body.controlled_domains
    if body.config is not None:
        project.config = {**(project.config or {}), **body.config}
        changed["config"] = body.config
    _audit(session, project.id, "project.patch", "project", project.id, changed)
    return {"id": project.id, "billing_mode": project.billing_mode,
            "billing_secret_id": project.billing_secret_id,
            "controlled_domains": project.controlled_domains, "config": project.config}


@router.post("/projects/{project_id}/billing_check")
def billing_check(project_id: str, session: Session = Depends(db)) -> dict:
    """Preflight the active billing credential without burning an agent run.
    API keys are verified against the live API (a free models-list call);
    subscription tokens can only be shape-checked here — they authenticate
    through the Claude Code CLI, which exists only inside sandboxes."""
    import httpx

    from yaaf.core.crypto import decrypt

    project = _get_project(session, project_id)
    if not project.billing_secret_id:
        raise HTTPException(status_code=422, detail="no billing credential attached")
    secret = session.get(Secret, project.billing_secret_id)
    plaintext = decrypt(secret.ciphertext)
    shape_error = _billing_shape_error(project.billing_mode, plaintext)
    result: dict
    if shape_error:
        result = {"ok": False, "detail": shape_error}
    elif project.billing_mode == "api_key":
        try:
            response = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": plaintext, "anthropic-version": "2023-06-01"},
                timeout=15,
            )
            if response.status_code == 200:
                result = {"ok": True, "detail": "API key verified against the live API."}
            elif response.status_code in (401, 403):
                result = {"ok": False,
                          "detail": "the API rejected this key (revoked or mistyped) — "
                                    "paste a fresh one from the Anthropic console"}
            else:
                result = {"ok": False,
                          "detail": f"unexpected API response ({response.status_code}); "
                                    "try again shortly"}
        except httpx.HTTPError as exc:
            result = {"ok": False, "detail": f"could not reach the API: {exc.__class__.__name__}"}
    else:
        result = {"ok": True,
                  "detail": "token has the right shape (sk-ant-oat…); full validation "
                            "happens on the first run — expired tokens surface there"}
    _audit(session, project.id, "billing.check", "project", project.id,
           {"ok": result["ok"]})
    return result


@router.post("/projects/{project_id}/start")
def start_project(project_id: str, session: Session = Depends(db)) -> dict:
    project = _get_project(session, project_id)
    if project.status == ProjectStatus.STOPPED:
        raise HTTPException(status_code=409, detail="a stopped project is terminal")
    project.status = ProjectStatus.RUNNING
    project.status_reason = None
    _audit(session, project.id, "project.start", "project", project.id)
    notify(session, WAKE_CHANNEL, project.id)
    return {"id": project.id, "status": project.status}


class LifecycleReason(BaseModel):
    reason: str = Field(default="", max_length=2000)


@router.post("/projects/{project_id}/pause")
def pause_project(
    project_id: str, body: LifecycleReason, session: Session = Depends(db)
) -> dict:
    project = _get_project(session, project_id)
    project.status = ProjectStatus.PAUSED
    project.status_reason = body.reason or "paused by chairman"
    revoked = _revoke_running(session, project.id)
    _audit(session, project.id, "project.pause", "project", project.id,
           {"revoked_runs": revoked})
    return {"id": project.id, "status": project.status, "revoked_runs": revoked}


@router.post("/projects/{project_id}/stop")
def stop_project(
    project_id: str, body: LifecycleReason, session: Session = Depends(db)
) -> dict:
    project = _get_project(session, project_id)
    project.status = ProjectStatus.STOPPED
    project.status_reason = body.reason or "stopped by chairman"
    revoked = _revoke_running(session, project.id)
    _audit(session, project.id, "project.stop", "project", project.id,
           {"revoked_runs": revoked})
    return {"id": project.id, "status": project.status, "revoked_runs": revoked}


class AgentPause(BaseModel):
    paused: bool


@router.post("/agents/{agent_id}/pause")
def pause_agent(agent_id: str, body: AgentPause, session: Session = Depends(db)) -> dict:
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="unknown agent")
    agent.status = "paused" if body.paused else "active"
    if body.paused:
        for run in session.execute(
            select(Run).where(Run.agent_id == agent.id, Run.status == RunStatus.RUNNING)
        ).scalars():
            run.token_revoked = True
    _audit(session, agent.project_id, "agent.pause", "agent", agent.id,
           {"paused": body.paused})
    return {"id": agent.id, "status": agent.status}


# ── boardroom ───────────────────────────────────────────────────────────────────


class BoardroomPost(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
    thread_id: str | None = None


@router.post("/projects/{project_id}/boardroom")
def boardroom_post(
    project_id: str, body: BoardroomPost, session: Session = Depends(db)
) -> dict:
    project = _get_project(session, project_id)
    channel = session.execute(
        select(Channel).where(
            Channel.project_id == project.id, Channel.kind == ChannelKind.BOARDROOM
        )
    ).scalar_one()
    message = Message(
        id=ids.new_id(ids.MESSAGE),
        project_id=project.id,
        channel_id=channel.id,
        sender_user_id=project.chairman_user_id,
        thread_id=body.thread_id,
        body=body.body,
    )
    session.add(message)
    notify(session, WAKE_CHANNEL, project.id)
    return {"id": message.id}


# ── approvals, hypotheses, amendments, threads ─────────────────────────────────


class ApprovalDecision(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=4000)


@router.post("/approvals/{approval_id}/decide")
def decide_approval(
    approval_id: str, body: ApprovalDecision, session: Session = Depends(db)
) -> dict:
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="unknown approval")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"approval already {approval.status}")
    approval.status = ApprovalStatus.APPROVED if body.approve else ApprovalStatus.DENIED
    approval.decided_at = utcnow()
    approval.decision_note = body.note
    project = session.get(Project, approval.project_id)
    approval.decided_by_user_id = project.chairman_user_id
    session.add(
        Notice(
            id=ids.new_id(ids.NOTICE),
            project_id=approval.project_id,
            agent_id=approval.requested_by_agent_id,
            kind=NoticeKind.APPROVAL_DECIDED,
            body=(
                f"Approval '{approval.title}' was "
                f"{'APPROVED' if body.approve else 'DENIED'}"
                + (f": {body.note}" if body.note else ".")
            ),
        )
    )
    _audit(session, approval.project_id, "approval.decide", "approval", approval.id,
           {"approve": body.approve})
    notify(session, WAKE_CHANNEL, approval.project_id)
    return {"id": approval.id, "status": approval.status}


class HypothesisExtend(BaseModel):
    new_kill_date: datetime
    reason: str = Field(min_length=10)


@router.post("/hypotheses/{hypothesis_id}/extend")
def extend_hypothesis(
    hypothesis_id: str, body: HypothesisExtend, session: Session = Depends(db)
) -> dict:
    row = session.get(Hypothesis, hypothesis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown hypothesis")
    if row.extended_once:
        raise HTTPException(
            status_code=409,
            detail="a hypothesis is extended at most once; after that it dies or is replaced",
        )
    if body.new_kill_date <= utcnow():
        raise HTTPException(status_code=422, detail="new_kill_date must be in the future")
    row.kill_date = body.new_kill_date
    row.status = HypothesisStatus.ACTIVE
    row.extended_once = True
    row.extension_reason = body.reason
    # Unblock tasks the expiry froze.
    for task in session.execute(
        select(Task).where(Task.hypothesis_id == row.id, Task.status == "blocked")
    ).scalars():
        if task.blocked_reason == "hypothesis_expired":
            task.status = "ready"
            task.blocked_reason = None
            policy.record_task_event(session, task, "ready", "user:chairman",
                                     note="hypothesis extended", from_status="blocked")
    _audit(session, row.project_id, "hypothesis.extend", "hypothesis", row.id,
           {"new_kill_date": body.new_kill_date.isoformat(), "reason": body.reason})
    notify(session, WAKE_CHANNEL, row.project_id)
    return {"id": row.id, "kill_date": row.kill_date.isoformat(), "extended_once": True}


class AmendmentIn(BaseModel):
    reason: str = Field(min_length=10)
    changes: dict


@router.post("/projects/{project_id}/amendments")
def amend_constitution(
    project_id: str, body: AmendmentIn, session: Session = Depends(db)
) -> dict:
    project = _get_project(session, project_id)
    try:
        amendments = propose_amendment(
            session, project.id, project.chairman_user_id, body.reason, body.changes
        )
    except Exception as exc:  # invalid overlay
        raise HTTPException(status_code=422, detail=f"invalid amendment: {exc}") from exc
    if not amendments:
        raise HTTPException(status_code=422, detail="the amendment changes nothing")
    applied_now = [a for a in amendments if a.applied_constitution_id is not None]
    pending = [a for a in amendments if a.applied_constitution_id is None]
    for amendment in amendments:
        _audit(session, project.id, "constitution.amend", "amendment", amendment.id,
               {"changes": amendment.changes,
                "effective_at": amendment.effective_at.isoformat(),
                "applied_now": amendment in applied_now})
    # Budget amendments applied in this call are disclosed in this call too —
    # the janitor only discloses what its own apply pass lands.
    ceo = policy.ceo_of(session, project.id)
    for amendment in applied_now:
        if ceo is not None:
            session.add(Notice(
                id=ids.new_id(ids.NOTICE), project_id=project.id, agent_id=ceo.id,
                kind=NoticeKind.CONSTITUTION_AMENDED,
                body=(f"The chairman amended the constitution (reason: {amendment.reason}). "
                      f"Changes now in force: {amendment.changes}. Plan accordingly."),
            ))
    note_parts = []
    if applied_now:
        note_parts.append("Budget changes are in force NOW.")
    if pending:
        note_parts.append("The other changes take effect after the 72h cooling-off.")
    return {
        "amendments": [
            {"id": a.id, "effective_at": a.effective_at.isoformat(),
             "applied_now": a in applied_now}
            for a in amendments
        ],
        "note": " ".join(note_parts) + " Everything is disclosed to the CEO.",
    }


def _amendment_status(a: Amendment) -> str:
    if a.applied_constitution_id:
        return "applied"
    if a.cancelled_at:
        return "cancelled"
    return "pending"


@router.get("/projects/{project_id}/amendments")
def list_amendments(project_id: str, session: Session = Depends(db)) -> list[dict]:
    project = _get_project(session, project_id)
    rows = session.execute(
        select(Amendment)
        .where(Amendment.project_id == project.id)
        .order_by(Amendment.created_at.desc())
    ).scalars()
    return [
        {
            "id": a.id,
            "reason": a.reason,
            "changes": a.changes,
            "status": _amendment_status(a),
            "proposed_at": a.created_at.isoformat(),
            "effective_at": a.effective_at.isoformat(),
            "disclosed": a.disclosed_at is not None,
        }
        for a in rows
    ]


@router.post("/amendments/{amendment_id}/cancel")
def cancel_amendment(amendment_id: str, session: Session = Depends(db)) -> dict:
    """The cooling-off period is for reconsidering; this is the reconsidering."""
    row = session.get(Amendment, amendment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown amendment")
    if _amendment_status(row) != "pending":
        raise HTTPException(
            status_code=409, detail=f"amendment is {_amendment_status(row)}, not pending"
        )
    row.cancelled_at = utcnow()
    _audit(session, row.project_id, "constitution.amendment_cancel", "amendment", row.id)
    return {"id": row.id, "status": "cancelled"}


@router.post("/threads/{message_id}/unfreeze")
def unfreeze_thread(message_id: str, session: Session = Depends(db)) -> dict:
    root = session.get(Message, message_id)
    if root is None:
        raise HTTPException(status_code=404, detail="unknown message")
    root.frozen_at = None
    _audit(session, root.project_id, "thread.unfreeze", "message", root.id)
    return {"thread_id": root.id, "frozen": False}


# ── evidence spot-audit & secrets ──────────────────────────────────────────────


class EvidenceAudit(BaseModel):
    ok: bool
    note: str = Field(default="", max_length=4000)


def _notice_ceo(session: Session, project_id: str, kind: str, body: str) -> None:
    ceo = policy.ceo_of(session, project_id)
    if ceo is not None:
        session.add(
            Notice(id=ids.new_id(ids.NOTICE), project_id=project_id, agent_id=ceo.id,
                   kind=kind, body=body)
        )


@router.post("/evidence/{evidence_id}/audit")
def audit_evidence(
    evidence_id: str, body: EvidenceAudit, session: Session = Depends(db)
) -> dict:
    row = session.get(Evidence, evidence_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown evidence")
    row.verification = VerificationStatus.VERIFIED if body.ok else VerificationStatus.FAILED
    row.verifier_notes = ((row.verifier_notes or "") + f"\n[chairman audit] {body.note}").strip()
    _audit(session, row.project_id, "evidence.audit", "evidence", row.id, {"ok": body.ok})
    if not body.ok:
        _notice_ceo(
            session, row.project_id, NoticeKind.AUDIT_FAILED,
            f"A chairman spot-audit FAILED evidence '{row.description[:120]}' "
            f"(by {row.created_by_agent_id or 'connector'}). That agent's evidence stream "
            "is suspect: re-verify its recent claims before trusting them in planning.",
        )
    return {"id": row.id, "verification": row.verification}


class TaskAudit(BaseModel):
    ok: bool
    note: str = Field(default="", max_length=4000)


@router.post("/tasks/{task_id}/audit")
def audit_task(task_id: str, body: TaskAudit, session: Session = Depends(db)) -> dict:
    """Completions join the spot-audit sample alongside evidence rows: LLM
    reviewers approve nearly everything, so the chairman's verdict is recorded
    on the task itself."""
    row = session.get(Task, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown task")
    if row.status != TaskStatus.DONE:
        raise HTTPException(status_code=409, detail="only completed tasks can be audited")
    row.audit_status = "passed" if body.ok else "failed"
    row.audit_note = body.note or None
    _audit(session, row.project_id, "task.audit", "task", row.id, {"ok": body.ok})
    if not body.ok:
        _notice_ceo(
            session, row.project_id, NoticeKind.AUDIT_FAILED,
            f"A chairman spot-audit FAILED the completion of '{row.title[:120]}' "
            f"(completed by {row.claimed_by_agent_id}). Its claimed result does not hold; "
            "treat that agent's completions as suspect and re-check what depended on this.",
        )
    return {"id": row.id, "audit_status": row.audit_status}


class SecretIn(BaseModel):
    project_id: str | None = None
    name: str = Field(min_length=2, max_length=120)
    # Omit value to update role grants only — no re-pasting to change access.
    value: str | None = Field(default=None, min_length=1)
    role_scopes: list[str] = Field(default_factory=list)
    # Fixed at creation; omit on update. "billing" credentials refuse role
    # grants — they are injected everywhere by the billing path, so a grant
    # would hand agents the raw model credential under a second name.
    kind: str | None = Field(default=None, pattern="^(service|billing)$")


@router.get("/secrets")
def list_secrets(project_id: str | None = None, session: Session = Depends(db)) -> list[dict]:
    """Names and scopes only — values are write-only, decrypted solely at
    run-injection time."""
    stmt = select(Secret)
    if project_id is not None:
        stmt = stmt.where(Secret.project_id == project_id)
    return [
        {"id": s.id, "name": s.name, "project_id": s.project_id,
         "role_scopes": s.role_scopes, "kind": s.kind}
        for s in session.execute(stmt.order_by(Secret.created_at)).scalars()
    ]


@router.post("/projects/{project_id}/webhook_secrets")
def provision_webhook_secrets(project_id: str, session: Session = Depends(db)) -> dict:
    """Generate the signing secrets for the inbound evidence connectors. The
    secrets live only in project.config — never in a role scope — so no run
    ever holds them, which is what makes webhook evidence unforgeable by
    agents."""
    project = _get_project(session, project_id)
    github_secret = pysecrets.token_hex(32)
    generic_secret = pysecrets.token_hex(32)
    # Merge, never clobber: config also carries cadences, Slack mapping, etc.
    project.config = {
        **(project.config or {}),
        "github_webhook_secret": github_secret,
        "webhook_secret": generic_secret,
    }
    _audit(session, project.id, "webhook_secrets.provision", "project", project.id,
           {"keys": ["github_webhook_secret", "webhook_secret"]})
    return {
        "github_webhook_secret": github_secret,
        "webhook_secret": generic_secret,
        "note": "Shown once: point your senders at these now. Recovering them "
                "later means regenerating, which invalidates the old ones.",
    }


class GithubAttach(BaseModel):
    # A bare name ("widget") creates a private repo under the platform token's
    # user; "owner/repo" attaches an existing one after verifying access.
    repo: str = Field(min_length=1, max_length=200, pattern=r"^[\w.-]+(/[\w.-]+)?$")


@router.post("/projects/{project_id}/github")
def attach_github(project_id: str, body: GithubAttach, session: Session = Depends(db)) -> dict:
    """Provision the project's private GitHub workspace repo. The platform
    token does the creating and the runner's one-time clone; it never enters a
    sandbox — agents push with their own role-scoped secret."""
    from yaaf.server import github

    project = _get_project(session, project_id)
    full_name = github.provision_repo(session, project, body.repo)
    project.github_repo = full_name
    _audit(session, project.id, "project.github_attach", "project", project.id,
           {"github_repo": full_name})
    return {"github_repo": full_name}


@router.post("/secrets")
def put_secret(body: SecretIn, session: Session = Depends(db)) -> dict:
    from yaaf.core.crypto import encrypt

    # Most secrets are opaque strings nobody here can judge. A data source's
    # address is the exception: the runner refuses to mount anything that is
    # not a full URL, and that refusal only reaches the chairman inside an
    # agent's run summary, hours later. Judge it at the door instead — this is
    # the door an imported setup file comes through, credential by credential.
    value = body.value
    source = _data_source_using(session, body.project_id, body.name)
    if source is not None and value is not None:
        _parse_source_url(
            value,
            f"'{body.name}' is the address of data source '{source}' — it needs a full URL "
            "like https://host:3845/mcp, not a bare hostname or a token",
        )
        value = value.strip()  # a pasted trailing newline breaks the mount too

    existing = session.execute(
        select(Secret).where(Secret.project_id == body.project_id, Secret.name == body.name)
    ).scalar_one_or_none()
    if existing:
        if body.kind is not None and body.kind != existing.kind:
            raise HTTPException(
                status_code=422,
                detail="a secret's kind is fixed at creation — store a new secret instead",
            )
        if existing.kind == "billing" and body.role_scopes:
            raise HTTPException(
                status_code=422,
                detail="billing credentials cannot be granted to roles: the billing path "
                       "already injects them into every run — a grant would hand agents "
                       "the raw model credential",
            )
        rotated = False
        if value is not None:
            existing.ciphertext = encrypt(value)
            rotated = True
        existing.role_scopes = body.role_scopes
        _audit(session, body.project_id, "secret.rotate" if rotated else "secret.rescope",
               "secret", existing.id, {"role_scopes": body.role_scopes})
        return {"id": existing.id, "rotated": rotated, "role_scopes": existing.role_scopes}
    if value is None:
        raise HTTPException(status_code=422, detail="a new secret needs a value")
    kind = body.kind or "service"
    if kind == "billing" and body.role_scopes:
        raise HTTPException(
            status_code=422,
            detail="billing credentials cannot be granted to roles: the billing path "
                   "already injects them into every run",
        )
    row = Secret(
        id=ids.new_id(ids.SECRET),
        project_id=body.project_id,
        name=body.name,
        ciphertext=encrypt(value),
        role_scopes=body.role_scopes,
        kind=kind,
    )
    session.add(row)
    _audit(session, body.project_id, "secret.create", "secret", row.id,
           {"role_scopes": body.role_scopes, "kind": kind})
    return {"id": row.id, "rotated": False, "role_scopes": row.role_scopes, "kind": kind}
