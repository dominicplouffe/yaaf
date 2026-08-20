"""Chairman/admin API: project lifecycle, org setup, approvals, amendments.

Everything here is authenticated with the admin token (full user auth is the
UI milestone). Chairman powers are deliberately narrow at runtime: speak in the
boardroom, decide approvals, extend a hypothesis once, amend the constitution
(recorded and disclosed), pause/stop things, unfreeze threads, spot-audit. The
chairman never edits tasks, posts to team rooms, or touches memory — that
discipline is the product.
"""

from __future__ import annotations

import logging
import secrets as pysecrets
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.constitution import (
    GROUP_THEMES,
    THEME_ORDER,
    ConstitutionDoc,
    create_initial,
    effective,
    effective_version,
    propose_amendment,
)
from yaaf.core.db import notify
from yaaf.core.models import (
    Agent,
    AgentKind,
    Amendment,
    Approval,
    ApprovalStatus,
    AuditLog,
    AuditSample,
    AuditSampleStatus,
    Channel,
    ChannelKind,
    Commitment,
    CommitmentStatus,
    Cycle,
    Evidence,
    Hypothesis,
    HypothesisStatus,
    Message,
    Notice,
    NoticeKind,
    Project,
    ProjectMember,
    ProjectMode,
    ProjectStatus,
    Requirement,
    RequirementEdge,
    RequirementStatus,
    Review,
    ReviewStatus,
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

log = logging.getLogger("yaaf.admin")

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
        latest = versions[0] if versions else None
        # Blast radius: every team pinned to any version of this role. Editing
        # a role is only scary when you can't see who runs it.
        usage = [
            {
                "project_name": project_name,
                "team_name": team_name,
                "pinned_version": pinned,
            }
            for team_name, project_name, pinned in session.execute(
                select(Team.name, Project.name, RoleVersion.version)
                .join(RoleVersion, RoleVersion.id == Team.role_version_id)
                .join(Project, Project.id == Team.project_id)
                .where(RoleVersion.role_id == role.id)
                .order_by(Project.name, Team.name)
            )
        ]
        out.append(
            {
                "id": role.id,
                "slug": role.slug,
                "name": role.name,
                "is_ceo_role": role.is_ceo_role,
                "latest_version": latest.version if latest else 0,
                "model": latest.model if latest else None,
                "effort": latest.effort if latest else None,
                "description": latest.description if latest else "",
                "tools": sorted(
                    k for k, v in (latest.tool_permissions or {}).items()
                    if v is True
                ) if latest else [],
                "usage": usage,
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
    # Explore chases a goal with no fixed end; Deliver ships one thing and
    # finishes; Operate is a Deliver that starts again on a rhythm. Outside
    # Explore a task must serve a requirement, and the project starts owing a
    # plan.
    mode: str = Field(default="explore", pattern="^(explore|deliver|operate)$")
    controlled_domains: list[str] = Field(default_factory=list)
    constitution: dict = Field(default_factory=dict)  # overrides onto defaults
    integrations: dict = Field(default_factory=dict)
    ceo_role_slug: str = "ceo"
    ceo_name: str = "CEO"
    teams: list[TeamSpec] = Field(default_factory=list)
    # Solo mode (the M2 single-agent arm): the CEO is placed as the sole member
    # — hence derived lead — of the only team, so one brain plans AND executes
    # under byte-identical policy. Requires exactly one team with no employees.
    solo: bool = False
    # Explore only: a bounded window on open scope ("one sale in 60 days").
    # Deliver has commitments and Operate has cycles, so it is refused there.
    deadline: datetime | None = None


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
    deadline = body.deadline
    if deadline is not None:
        if body.mode != "explore":
            raise HTTPException(
                status_code=422,
                detail="a bare deadline is Explore's shape — open scope, bounded time. "
                       "Deliver promises through a commitment; Operate runs on cycles.",
            )
        if deadline.tzinfo is None:
            from datetime import UTC
            deadline = deadline.replace(tzinfo=UTC)
        if deadline <= utcnow():
            raise HTTPException(status_code=422, detail="the deadline is already past")

    ceo_role_version = _latest_role_version(session, body.ceo_role_slug)

    # The org shape as a recorded fact: solo (one brain), ceo_team (flat —
    # every employee is a one-person team the CEO talks to directly), or
    # ceo_leads_workers (any team big enough to have a real lead).
    topology = ("solo" if body.solo
                else "ceo_leads_workers" if any(len(t.employees) > 1 for t in body.teams)
                else "ceo_team")
    project = Project(
        id=ids.new_id(ids.PROJECT),
        slug=body.slug,
        name=body.name,
        goal=body.goal,
        chairman_user_id=chairman.id,
        billing_mode=body.billing_mode,
        mode=body.mode,
        controlled_domains=body.controlled_domains,
        integrations=body.integrations,
        deadline=deadline,
        topology=topology,
    )
    session.add(project)
    session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=chairman.id, role="chairman"))

    # The constitution freezes at creation. Overrides are validated against the schema.
    doc = ConstitutionDoc.model_validate({**ConstitutionDoc().model_dump(), **body.constitution})
    create_initial(session, project.id, doc)

    # Webhook secrets exist from birth. The platform_attested path was never
    # the missing piece — provisioning was: 315 of 315 evidence rows in the
    # audit said an agent claimed it, because no project ever had a secret.
    # Generated here (unless the caller brought their own, e.g. a clone), and
    # shown once in the response with the endpoint URL beside each.
    integrations = dict(project.integrations or {})
    integrations.setdefault("github_webhook_secret", pysecrets.token_hex(32))
    integrations.setdefault("webhook_secret", pysecrets.token_hex(32))
    project.integrations = integrations

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
            "constitution": doc.model_dump(), "status": project.status,
            "webhooks": {
                "github": {"url": f"/webhooks/github/{project.id}",
                           "secret": integrations["github_webhook_secret"]},
                "generic": {"url": f"/webhooks/generic/{project.id}",
                            "secret": integrations["webhook_secret"]},
                "note": ("Point senders at these now — evidence arriving signed with "
                         "them is platform-attested, which no agent can forge. The "
                         "secrets never enter a sandbox."),
            }}


# Config keys that are this install's plumbing, not the project's design:
# Slack wiring, webhook signing secrets, experiment bookkeeping. Everything
# else in integrations (runtime, data sources…) travels with the bundle.
_NON_PORTABLE_INTEGRATION_KEYS = frozenset(
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

    integrations = {k: v for k, v in (project.integrations or {}).items()
                    if k not in _NON_PORTABLE_INTEGRATION_KEYS}
    if "mcp_servers" in integrations:
        integrations["mcp_servers"] = [
            {k: v for k, v in entry.items() if k != "last_check"}
            for entry in integrations["mcp_servers"]
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
            "mode": project.mode,
            "controlled_domains": project.controlled_domains,
            "constitution": effective(session, project.id).model_dump(),
            "integrations": integrations,
            "ceo_role_slug": ceo_role_slug,
            "ceo_name": ceo.name,
            "teams": teams,
            "solo": ceo.team_id is not None,
        },
        "roles": list(roles.values()),
        "secrets": secret_names,
    }


# Config a twin must not inherit: this project's own webhook signing keys and
# its experiment bookkeeping. Slack is handled separately below — its tokens
# travel, its room bindings do not.
_NON_CLONABLE_INTEGRATION_KEYS = frozenset({"github_webhook_secret", "webhook_secret", "experiment"})


class ProjectCloneIn(BaseModel):
    slug: str = Field(pattern="^[a-z0-9-]{2,64}$")
    name: str | None = None  # defaults to the source's name plus " (clone)"
    # The usual reason to duplicate a company is to point it at the next thing.
    # Omit to keep the source's mandate; the machinery is what's being reused.
    goal: str | None = Field(default=None, min_length=10)


@router.post("/projects/{project_id}/clone")
def clone_project(project_id: str, body: ProjectCloneIn, session: Session = Depends(db)) -> dict:
    """A running twin on this install — the counterpart to export, which makes
    a portable *design*. An export cannot carry a secret off the deployment
    that encrypted it, so it re-asks for every credential by hand, and a twin
    is one mistyped address away from agents hunting for tools that never
    mounted. A clone never leaves that deployment, so it copies what the export
    could not: credential values verbatim (ciphertext, no round trip through
    plaintext), and the exact role versions the source has pinned rather than
    whatever the library has drifted to since.

    What it deliberately does not copy is the source's *history* — runs, tasks,
    bus, memory, artifacts, workspace, amendment log — and its claim on shared
    outside rooms: Slack tokens come along, the channel map does not, because
    two companies posting into one room is nobody's idea of a twin.
    """
    source = _get_project(session, project_id)
    if session.execute(select(Project).where(Project.slug == body.slug)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="project slug already exists")

    integrations = {k: v for k, v in (source.integrations or {}).items()
                    if k not in _NON_CLONABLE_INTEGRATION_KEYS}
    slack_rooms_dropped = bool((integrations.get("slack") or {}).get("channel_map"))
    if "slack" in integrations:
        # The bridge is inert without a channel map, so the twin mirrors
        # nowhere until the chairman maps rooms — with the tokens already there.
        integrations["slack"] = {**integrations["slack"], "channel_map": {}}

    # Not inheriting the source's signing keys is right — they are its own, and
    # sharing them would let either company's senders attest evidence for the
    # other. Leaving the twin with *none* is not: github_repo and the controlled
    # domains do come across, so the twin looks wired to GitHub while no inbound
    # event could ever be attested. Every other birth mints these (see
    # create_project); a twin is no less born. The values are readable any time
    # from the twin's own Settings, so nothing here has to be shown once.
    integrations["github_webhook_secret"] = pysecrets.token_hex(32)
    integrations["webhook_secret"] = pysecrets.token_hex(32)

    # A twin of a Deliver company that comes back as an Explore one is not a
    # twin: mode decides whether the company has requirements at all, and it is
    # frozen at creation — so getting it wrong here cannot be corrected later,
    # only deleted and redone. It defaults to EXPLORE on the model, which is
    # what an omission silently produced.
    #
    # The deadline is Explore's window, and copying one verbatim can hand the
    # twin a date that has already passed — a company the janitor closes at
    # birth. A future window carries; a spent one is reported, not inherited.
    deadline_expired = (source.deadline is not None and source.deadline <= utcnow())
    project = Project(
        id=ids.new_id(ids.PROJECT),
        slug=body.slug,
        name=body.name or f"{source.name} (clone)",
        goal=body.goal or source.goal,
        chairman_user_id=source.chairman_user_id,
        mode=source.mode,
        # The org shape, for the same reason mode is here: the twin rebuilds the
        # source's teams and people exactly, so the source's recorded shape is
        # true of it by construction — but omitting it let the model default
        # ("ceo_leads_workers") overwrite that truth, and a solo twin came back
        # claiming a hierarchy it does not have. This is the fact the topology
        # experiment compares, and it is frozen at creation like the rest.
        topology=source.topology,
        deadline=None if deadline_expired else source.deadline,
        billing_mode=source.billing_mode,
        controlled_domains=list(source.controlled_domains or []),
        github_repo=source.github_repo,
        integrations=integrations,
    )
    session.add(project)
    session.flush()
    for member in session.execute(
        select(ProjectMember).where(ProjectMember.project_id == source.id)
    ).scalars():
        session.add(ProjectMember(project_id=project.id, user_id=member.user_id,
                                  role=member.role))

    # The rulebook the source is governed by *now*, frozen fresh at version 1:
    # the twin inherits the rules, not the argument that produced them.
    create_initial(session, project.id, effective(session, source.id))

    # Ciphertext is copied as-is — same install, same master key, so nothing is
    # ever decrypted to move it. This is the line an export cannot cross, and
    # the reason a clone needs no credential re-entry at all.
    ciphertexts: dict[str, str] = {}
    secret_ids: dict[str, str] = {}
    for row in session.execute(
        select(Secret).where(Secret.project_id == source.id).order_by(Secret.created_at)
    ).scalars():
        copy = Secret(id=ids.new_id(ids.SECRET), project_id=project.id, name=row.name,
                      ciphertext=row.ciphertext, role_scopes=list(row.role_scopes or []),
                      kind=row.kind)
        session.add(copy)
        ciphertexts[row.name] = row.ciphertext
        secret_ids[row.id] = copy.id
    if source.billing_secret_id:
        project.billing_secret_id = secret_ids.get(source.billing_secret_id)

    source_ceo = session.execute(
        select(Agent).where(Agent.project_id == source.id, Agent.kind == AgentKind.CEO)
    ).scalar_one()
    ceo = Agent(id=ids.new_id(ids.AGENT), project_id=project.id, team_id=None,
                kind=AgentKind.CEO, name=source_ceo.name,
                role_version_id=source_ceo.role_version_id,
                model_override=source_ceo.model_override)
    session.add(ceo)
    session.add(Channel(id=ids.new_id(ids.CHANNEL), project_id=project.id,
                        kind=ChannelKind.BOARDROOM))
    session.add(Channel(id=ids.new_id(ids.CHANNEL), project_id=project.id,
                        kind=ChannelKind.LEADERSHIP))

    team_out = []
    for source_team in session.execute(
        select(Team).where(Team.project_id == source.id).order_by(Team.created_at)
    ).scalars():
        # The source's pinned version, not _latest_role_version: a twin that
        # silently upgrades its roles is not running the same company.
        team = Team(id=ids.new_id(ids.TEAM), project_id=project.id,
                    role_version_id=source_team.role_version_id, name=source_team.name)
        session.add(team)
        agents = []
        for source_agent in session.execute(
            select(Agent)
            .where(Agent.team_id == source_team.id, Agent.kind == AgentKind.EMPLOYEE)
            .order_by(Agent.created_at)
        ).scalars():
            agent = Agent(id=ids.new_id(ids.AGENT), project_id=project.id, team_id=team.id,
                          kind=AgentKind.EMPLOYEE, name=source_agent.name,
                          role_version_id=source_agent.role_version_id,
                          status=source_agent.status,
                          model_override=source_agent.model_override)
            session.add(agent)
            agents.append(agent)
            if source_agent.id == source_team.lead_agent_id:
                team.lead_agent_id = agent.id
        session.add(Channel(id=ids.new_id(ids.CHANNEL), project_id=project.id,
                            kind=ChannelKind.TEAM, team_id=team.id))
        if source_ceo.team_id == source_team.id:  # solo: the CEO is the team
            ceo.team_id = team.id
            agents.append(ceo)
        team_out.append({"id": team.id, "name": team.name,
                         "agents": [{"id": a.id, "name": a.name} for a in agents]})

    # Prove the twin's data sources here, on its own copied credentials. A
    # source that cannot answer is exactly what a clone exists to rule out —
    # and the source project's stale verdict would be a lie about this one.
    checked = []
    for entry in integrations.get("mcp_servers") or []:
        from yaaf.core.crypto import decrypt

        entry = {k: v for k, v in entry.items() if k != "last_check"}
        url_cipher = ciphertexts.get(entry.get("url_secret") or "")
        if not url_cipher:
            check = {"ok": False, "detail": "the data source's URL secret did not come across"}
        else:
            token_cipher = ciphertexts.get(entry.get("bearer_secret") or "")
            check = _check_data_source(decrypt(url_cipher),
                                       decrypt(token_cipher) if token_cipher else None)
        entry["last_check"] = {**check, "at": utcnow().isoformat()}
        checked.append(entry)
    if checked:
        project.integrations = {**integrations, "mcp_servers": checked}

    _audit(session, source.id, "project.clone", "project", project.id, {"slug": body.slug})
    _audit(session, project.id, "project.create", "project", project.id,
           {"cloned_from": source.id, "cloned_from_slug": source.slug})
    return {
        "id": project.id,
        "slug": project.slug,
        "name": project.name,
        "ceo_agent_id": ceo.id,
        "teams": team_out,
        "status": project.status,
        "mode": project.mode,
        "copied": {
            "secrets": len(ciphertexts),
            "data_sources": [{"name": e["name"], "check": e["last_check"]} for e in checked],
            "slack_rooms_dropped": slack_rooms_dropped,
            "deadline_dropped": deadline_expired,
        },
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
    # inline: mounted as mcp__<name>__* tools, so results land in the model's
    # context — right for small lookups, ruinous for exports. bulk: not mounted
    # at all; reachable only through data_fetch, which writes the result to a
    # workspace file. Marking a source bulk is enforcement, not advice — the
    # expensive path stops existing for it. Default inline: a source that was
    # already connected keeps behaving exactly as it did.
    access: Literal["inline", "bulk"] = "inline"


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


def _project_secret(session: Session, project_id: str, name: str) -> Secret | None:
    return session.execute(
        select(Secret).where(Secret.project_id == project_id, Secret.name == name)
    ).scalar_one_or_none()


def _project_secret_value(session: Session, project_id: str, name: str) -> str | None:
    from yaaf.core.crypto import decrypt

    row = _project_secret(session, project_id, name)
    return decrypt(row.ciphertext) if row is not None else None


def _delete_project_secret(session: Session, project_id: str, name: str) -> None:
    row = _project_secret(session, project_id, name)
    if row is not None:
        session.delete(row)


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
    for source in (project.integrations or {}).get("mcp_servers", []):
        if source.get("url_secret") == secret_name:
            return source.get("name")
    return None


def _data_source_owning(session: Session, project_id: str | None, secret_name: str) -> str | None:
    """The data source this secret belongs to, address *or* token. Wider than
    `_data_source_using`, which asks the narrower question 'is this an address
    that must parse as a URL'. Deleting either half orphans the source, so both
    are refused."""
    if not project_id:
        return None
    project = session.get(Project, project_id)
    if project is None:
        return None
    for source in (project.integrations or {}).get("mcp_servers", []):
        if secret_name in (source.get("url_secret"), source.get("bearer_secret")):
            return source.get("name")
    return None


def _sse_or_json(response) -> dict | None:
    """An MCP server answers a POST with either JSON or a one-event SSE
    stream, depending on what it felt like negotiating. Both carry the same
    JSON-RPC envelope."""
    import json as jsonlib

    body = response.text or ""
    if "text/event-stream" in response.headers.get("content-type", ""):
        for line in body.splitlines():
            if line.startswith("data:"):
                return jsonlib.loads(line[5:].strip())
        return None
    return jsonlib.loads(body) if body.strip() else None


def _check_data_source(url: str, bearer: str | None, transport=None) -> dict:
    """Would an agent actually get tools here? The old answer to that was a GET
    and a shrug: any HTTP status counted as success, so a healthy MCP endpoint
    — which answers GET with 405, because it wants POST — reported "reachable
    (HTTP 405)" and taught chairmen to read a passing check as noise. It also
    proved nothing. A wrong path, a rejected token and a server that is not MCP
    at all all sailed through it identically.

    So speak MCP instead: initialize, then tools/list, and say which tools came
    back. `transport` is a seam for tests; production passes nothing.
    """
    import httpx

    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    def rpc(client, payload: dict, session_id: str | None):
        sent = {**headers, **({"Mcp-Session-Id": session_id} if session_id else {})}
        return client.post(url, json=payload, headers=sent)

    try:
        with httpx.Client(timeout=10, follow_redirects=True, transport=transport) as client:
            opened = rpc(client, {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "yaaf", "version": "1.0"}},
            }, None)
            if opened.status_code in (401, 403):
                return {"ok": False, "detail": f"the server refused the credential (HTTP "
                                               f"{opened.status_code}) — check the bearer token"}
            if opened.status_code == 404:
                return {"ok": False, "detail": "no MCP endpoint at that path (HTTP 404) — the "
                                               "address needs the server's own path, often /mcp"}
            if opened.status_code >= 400:
                return {"ok": False, "detail": f"something is listening, but it did not accept an "
                                               f"MCP handshake (HTTP {opened.status_code})"}
            try:
                envelope = _sse_or_json(opened) or {}
            except ValueError:
                return {"ok": False, "detail": "that address answered, but not with MCP — it "
                                               "looks like an ordinary web page or API"}
            if "error" in envelope:
                message = (envelope["error"] or {}).get("message", "unknown error")
                return {"ok": False, "detail": f"the server refused the handshake: {message}"}

            info = (envelope.get("result") or {}).get("serverInfo") or {}
            named = " ".join(str(p) for p in (info.get("name"), info.get("version")) if p)
            session_id = opened.headers.get("mcp-session-id")
            rpc(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id)

            listed = rpc(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                                  "params": {}}, session_id)
            tools = []
            if listed.status_code < 400:
                try:
                    tools = ((_sse_or_json(listed) or {}).get("result") or {}).get("tools") or []
                except ValueError:
                    tools = []
    except httpx.ConnectError:
        return {"ok": False, "detail": "nothing is listening at that address — check "
                                       "host and port, and that the server is running"}
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"could not connect: {exc.__class__.__name__}"}

    who = f"MCP server{f' {named}' if named else ''}"
    if not tools:
        # The handshake is real, so the address and credential are right — but
        # a source that offers an agent nothing is not a working data source.
        return {"ok": False, "detail": f"{who} answered the handshake but offers no tools — "
                                       "agents would mount it and find nothing to call"}
    shown = ", ".join(t.get("name", "?") for t in tools[:4])
    more = f", +{len(tools) - 4} more" if len(tools) > 4 else ""
    return {"ok": True, "detail": f"{who} ready — {len(tools)} tool(s): {shown}{more}"}


@router.post("/projects/{project_id}/mcp_servers")
def add_data_source(project_id: str, body: DataSourceIn, session: Session = Depends(db)) -> dict:
    """One form, everything wired: validates the URL at the door, stores it
    (and any token) as role-granted secrets, records the mapping in project
    config, and preflights reachability. Egress and the proxy follow
    automatically; no role edit, no repin, no CLI.

    Saving under an existing name is the edit path, so `bearer` reads as:
    omitted = keep the current token, `""` = remove it, anything else = rotate.
    A URL-only edit that dropped the auth header would fail hours later inside
    an agent run, which is exactly the class of failure this form exists to
    prevent."""
    project = _get_project(session, project_id)
    parsed = _parse_source_url(
        body.url,
        "that is not a URL — a data source needs a full address like "
        "https://host:1234/path (schemes http/https only)",
    )
    for slug in body.roles:
        _latest_role_version(session, slug)  # 422s on unknown role

    servers = (project.integrations or {}).get("mcp_servers", [])
    prior = next((s for s in servers if s.get("name") == body.name), None)

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
        "access": body.access,
    }
    # Three cases, because an edit that changes only the URL must not silently
    # disarm the auth header. Absent (None) keeps whatever the source already
    # had; empty string is the explicit "drop the token"; anything else rotates.
    token = body.bearer or None
    held = prior.get("bearer_secret") if prior else None
    if body.bearer:
        token_secret = _upsert_project_secret(
            session, project.id, f"mcp-{body.name}-token", body.bearer, body.roles)
        entry["bearer_secret"] = token_secret.name
    elif body.bearer == "" and held:
        _delete_project_secret(session, project.id, held)
    elif body.bearer is None and held:
        carried = _project_secret_value(session, project.id, held)
        if carried is not None:
            # Re-store it so the carried token tracks this save's role grants.
            _upsert_project_secret(session, project.id, held, carried, body.roles)
            entry["bearer_secret"] = held
            token = carried

    check = _check_data_source(body.url.strip(), token)
    entry["last_check"] = {**check, "at": utcnow().isoformat()}

    servers = [s for s in servers if s.get("name") != body.name]
    project.integrations = {**(project.integrations or {}), "mcp_servers": [*servers, entry]}
    _audit(session, project.id, "mcp_server.save", "project", project.id,
           {"name": body.name, "roles": body.roles, "host": entry["url_host"],
            "access": body.access, "check_ok": check["ok"]})
    return {"name": body.name, "check": check,
            "note": "Egress updates and the proxy reloads automatically within ~15s; "
                    "effective on each granted role's next run."}


@router.delete("/projects/{project_id}/mcp_servers/{name}")
def delete_data_source(project_id: str, name: str, session: Session = Depends(db)) -> dict:
    project = _get_project(session, project_id)
    servers = (project.integrations or {}).get("mcp_servers", [])
    entry = next((s for s in servers if s.get("name") == name), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown data source")
    for secret_name in (entry.get("url_secret"), entry.get("bearer_secret")):
        if secret_name:
            _delete_project_secret(session, project.id, secret_name)
    project.integrations = {**project.integrations,
                      "mcp_servers": [s for s in servers if s.get("name") != name]}
    _audit(session, project.id, "mcp_server.delete", "project", project.id, {"name": name})
    return {"deleted": name}


@router.post("/projects/{project_id}/mcp_servers/{name}/check")
def check_data_source(project_id: str, name: str, session: Session = Depends(db)) -> dict:
    project = _get_project(session, project_id)
    servers = (project.integrations or {}).get("mcp_servers", [])
    entry = next((s for s in servers if s.get("name") == name), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown data source")

    def value_of(secret_name: str | None) -> str | None:
        if not secret_name:
            return None
        return _project_secret_value(session, project.id, secret_name)

    url = value_of(entry.get("url_secret"))
    if not url:
        raise HTTPException(status_code=422, detail="the data source's URL secret is missing")
    check = _check_data_source(url, value_of(entry.get("bearer_secret")))
    entry["last_check"] = {**check, "at": utcnow().isoformat()}
    project.integrations = {**project.integrations, "mcp_servers": servers}
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
    name: str | None = Field(default=None, min_length=1, max_length=200)
    # The chairman's mandate, not a constitutional rule: it redirects the
    # company rather than constraining it, and needs no written reason.
    goal: str | None = Field(default=None, min_length=10)
    billing_mode: str | None = Field(default=None, pattern="^(api_key|subscription)$")
    billing_secret_id: str | None = None
    controlled_domains: list[str] | None = None
    # Merged into the existing integrations (outward wiring only — every
    # limit lives in the constitution and changes only through amendments).
    integrations: dict | None = None
    # Explore only. Set a new window, or clear it with clear_deadline.
    deadline: datetime | None = None
    clear_deadline: bool = False


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
    if body.name is not None:
        project.name = body.name.strip()
        changed["name"] = project.name
    if body.goal is not None and body.goal.strip() != project.goal:
        # The mandate moved. Agents read it live from the next context pack, so
        # the change lands on its own — but landing silently is how a CEO ends
        # up defending a plan built for the old one. Tell it, in its own words.
        was, project.goal = project.goal, body.goal.strip()
        changed["goal"] = project.goal
        _notice_ceo(
            session, project.id, NoticeKind.GOAL_CHANGED,
            f"The chairman changed this company's goal.\n\nIT IS NOW: {project.goal}\n\n"
            f"IT WAS: {was}\n\nEverything already in flight — open tasks, bets, deliverables "
            "— was planned against the old goal. Re-read the backlog against this one before "
            "continuing, and close or re-aim whatever no longer serves it.",
        )
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
    if body.integrations is not None:
        # A limit written here would be stored and never read — the silent
        # failure that made `ceo_heartbeat_hours` invisible to the settings UI,
        # to the amendment log and to the CEO it governed. Refuse it and say
        # where it goes instead.
        rules = set(ConstitutionDoc.model_fields) & set(body.integrations)
        if rules:
            raise HTTPException(
                status_code=422,
                detail=(f"{', '.join(sorted(rules))} "
                        f"{'is a constitution rule' if len(rules) == 1 else 'are constitution rules'}, "
                        "not wiring. Change it with POST "
                        f"/admin/projects/{project.id}/amendments so it is validated, "
                        "audited with your reason, and disclosed to the CEO."),
            )
        project.integrations = {**(project.integrations or {}), **body.integrations}
        changed["integrations"] = body.integrations
    if body.deadline is not None or body.clear_deadline:
        if project.mode != ProjectMode.EXPLORE:
            raise HTTPException(
                status_code=422,
                detail="a bare deadline is Explore's shape. Deliver promises through a "
                       "commitment; Operate runs on cycles.",
            )
        if body.clear_deadline:
            project.deadline = None
            changed["deadline"] = None
        else:
            deadline = body.deadline
            if deadline.tzinfo is None:
                from datetime import UTC
                deadline = deadline.replace(tzinfo=UTC)
            project.deadline = deadline
            changed["deadline"] = deadline.isoformat()
        _notice_ceo(
            session, project.id, NoticeKind.GOAL_CHANGED,
            "The chairman "
            + ("cleared the deadline — the window is open-ended again."
               if body.clear_deadline else
               f"set the window: {project.deadline:%Y-%m-%d}. Open scope, bounded "
               "time — what exists by then is what this project will be judged on."),
        )
    _audit(session, project.id, "project.patch", "project", project.id, changed)
    return {"id": project.id, "name": project.name, "goal": project.goal,
            "billing_mode": project.billing_mode,
            "billing_secret_id": project.billing_secret_id,
            "controlled_domains": project.controlled_domains, "integrations": project.integrations,
            "changed": changed}


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
    if project.status in (ProjectStatus.STOPPED, ProjectStatus.COMPLETED):
        raise HTTPException(status_code=409,
                            detail=f"a {project.status} project is terminal")
    project.status = ProjectStatus.RUNNING
    project.status_reason = None
    # A Deliver or Operate project starts owing a plan. The chairman's words
    # were that the CEO's first task is the plan; R-00 is that obligation as a
    # row, and until it closes no task may be created.
    if policy.requires_requirements(project) and \
            policy.plan_requirement(session, project.id) is None:
        policy.create_plan_requirement(session, project)
    # An Operate project always has exactly one open cycle while running.
    # Opened here rather than waiting a janitor tick, so the first proposal
    # lands inside it; on a resume after a long pause this is also what makes
    # it ONE new cycle, never one per missed boundary.
    if project.mode == ProjectMode.OPERATE and \
            policy.current_cycle(session, project.id) is None:
        last = session.execute(
            select(func.max(Cycle.ordinal)).where(Cycle.project_id == project.id)
        ).scalar_one() or 0
        session.add(Cycle(id=ids.new_id(ids.CYCLE), project_id=project.id,
                          ordinal=last + 1))
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


@router.post("/projects/{project_id}/complete")
def complete_project(project_id: str, session: Session = Depends(db)) -> dict:
    """The chairman confirms a delivery. One of the three gates he chose to be
    asked at — the janitor only detects readiness and emails; this button
    decides. Refused with the same outstanding list the janitor checks, so
    the email and the refusal can never disagree.

    The receipt's load-bearing row is "failed once before": every requirement
    that ate dead runs on its way to true says so, because a delivery that
    looked effortless in the receipt would teach the wrong lesson about what
    it cost.
    """
    from yaaf.server.janitor import delivery_outstanding

    project = _get_project(session, project_id)
    if project.mode != ProjectMode.DELIVER:
        raise HTTPException(
            status_code=422,
            detail="only a Deliver project completes: Explore is judged and stopped, "
                   "Operate finishes and starts again.",
        )
    if project.status == ProjectStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="already completed")
    outstanding = delivery_outstanding(session, project)
    if outstanding:
        raise HTTPException(
            status_code=409,
            detail="not everything is true yet — " + "; ".join(outstanding),
        )

    scope = policy.scope_requirements(session, project.id)
    accepted = [r for r in scope if r.status != RequirementStatus.PROPOSED]
    rows = []
    for r in accepted:
        actual = policy.runs_spent_on(session, r.id)
        rows.append({
            "key": r.key, "statement": r.statement[:300], "status": r.status,
            "est_runs": r.est_runs, "actual_runs": actual,
            "failure_load": r.failure_load,
            "failed_before": r.failure_load > 0,
        })
    runs_total, cost_total = session.execute(
        select(func.count(Run.id), func.coalesce(func.sum(Run.cost_usd), 0.0))
        .where(Run.project_id == project.id)
    ).one()
    commitment = session.execute(
        select(Commitment).where(Commitment.project_id == project.id)
        .order_by(Commitment.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    now = utcnow()
    receipt = {
        "requirements": rows,
        "met": len([r for r in rows if r["status"] == RequirementStatus.MET]),
        "cut": len([r for r in rows if r["status"] == RequirementStatus.CUT]),
        "runs_total": int(runs_total),
        "cost_usd": round(float(cost_total), 2),
        "duration_days": (now - project.created_at).days,
        "commitment": ({
            "deliver_by": commitment.deliver_by.isoformat(),
            "met_at": commitment.met_at.isoformat() if commitment.met_at else None,
            "kept": bool(commitment.met_at and commitment.met_at <= commitment.deliver_by),
        } if commitment is not None else None),
        "completed_at": now.isoformat(),
    }
    project.status = ProjectStatus.COMPLETED
    project.status_reason = f"delivered: {receipt['met']} requirement(s) true"
    revoked = _revoke_running(session, project.id)
    for queued in session.execute(
        select(RunQueue).where(RunQueue.project_id == project.id)
    ).scalars():
        session.delete(queued)

    lines = []
    for r in rows:
        history = (f"met after {r['failure_load']} failed run(s)" if
                   r["status"] == RequirementStatus.MET and r["failed_before"]
                   else "met clean" if r["status"] == RequirementStatus.MET
                   else f"cut ({r['failure_load']} failed run(s) first)"
                   if r["failed_before"] else "cut")
        est = f", est {r['est_runs']} vs {r['actual_runs']} actual" if r["est_runs"] else ""
        lines.append(f"{r['key']} — {history}{est}")
    from yaaf.core.models import Decision

    session.add(Decision(
        id=ids.new_id(ids.DECISION),
        project_id=project.id,
        by_user_id=project.chairman_user_id,
        subject=f"delivery receipt: {receipt['met']} true, {receipt['cut']} cut",
        body=("Confirmed complete by the chairman.\n\n" + "\n".join(lines)
              + f"\n\nTotals: {receipt['runs_total']} runs, "
                f"${receipt['cost_usd']:,.2f}, {receipt['duration_days']} days."
              + (f"\nCommitment {'KEPT' if receipt['commitment']['kept'] else 'missed'}: "
                 f"promised {receipt['commitment']['deliver_by'][:10]}."
                 if receipt["commitment"] else "")),
    ))
    _audit(session, project.id, "project.complete", "project", project.id, receipt)
    return {"id": project.id, "status": project.status, "receipt": receipt,
            "revoked_runs": revoked}


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



@router.get("/constitution/schema")
def constitution_schema() -> list[dict]:
    """Every rule the constitution holds, described well enough to render an
    editor from. Derived from the model itself, so a field added to
    ConstitutionDoc is editable the moment it exists — the settings page used to
    carry its own list of six, and the rules a stuck company actually needs to
    change were not on it."""
    schema = ConstitutionDoc.model_json_schema()
    fields = []
    for key, spec in schema["properties"].items():
        # `str | None` fields arrive as anyOf; the real type is the non-null arm.
        arms = spec.get("anyOf") or [spec]
        typed = next((a for a in arms if a.get("type") != "null"), arms[0])
        group = spec.get("group", "Other")
        theme = GROUP_THEMES.get(group, "Other")
        fields.append({
            "key": key,
            "label": spec.get("title") or key.replace("_", " "),
            "help": spec.get("description", ""),
            "group": group,
            # The tab this rule sits under. Groups are the taxonomy; themes are
            # the navigation, and both are decided here so the editor never
            # keeps a list of its own to go stale. The rank rides along because
            # declaration order puts work discipline first, and the tab a stuck
            # company opens is Money.
            "theme": theme,
            "theme_rank": THEME_ORDER.index(theme) if theme in THEME_ORDER else len(THEME_ORDER),
            "type": typed.get("type", "string"),
            "minimum": typed.get("minimum", typed.get("exclusiveMinimum")),
            "maximum": typed.get("maximum", typed.get("exclusiveMaximum")),
            "choices": spec.get("choices"),
            # What the rule would be if nobody had touched it — the editor
            # marks anything that differs, so "what is this company actually
            # running under" is a glance instead of a diff against the docs.
            "default": spec.get("default"),
        })
    return fields


@router.post("/projects/{project_id}/amendments")
def amend_constitution(
    project_id: str, body: AmendmentIn, session: Session = Depends(db)
) -> dict:
    project = _get_project(session, project_id)
    try:
        amendments = propose_amendment(
            session, project.id, project.chairman_user_id, body.reason, body.changes,
        )
    except Exception as exc:  # invalid overlay
        raise HTTPException(status_code=422, detail=f"invalid amendment: {exc}") from exc
    if not amendments:
        raise HTTPException(status_code=422, detail="the amendment changes nothing")
    applied_now = amendments  # nothing waits; the split existed for the cooling-off
    for amendment in amendments:
        _audit(session, project.id, "constitution.amend", "amendment", amendment.id,
               {"changes": amendment.changes,
                "effective_at": amendment.effective_at.isoformat(),
                "applied_now": True})
        # The audit row is now the whole accountability story, so it is also
        # worth an operator log line: the rulebook moving is not a routine edit.
        log.warning("constitution amended: project=%s changes=%s reason=%s",
                    project.slug, amendment.changes, amendment.reason)
    # Disclosed in this call, since it is in force in this call.
    ceo = policy.ceo_of(session, project.id)
    for amendment in applied_now:
        if ceo is not None:
            session.add(Notice(
                id=ids.new_id(ids.NOTICE), project_id=project.id, agent_id=ceo.id,
                kind=NoticeKind.CONSTITUTION_AMENDED,
                body=(f"The chairman amended the constitution (reason: {amendment.reason}). "
                      f"Changes now in force: {amendment.changes}. Plan accordingly."),
            ))
    # Name the fields rather than a category: a note that lists what you just
    # changed cannot go stale the way a note about which bucket it fell in did.
    changed_now = sorted({k for a in applied_now for k in a.changes})
    note_parts = [f"In force NOW: {', '.join(changed_now)}."]
    return {
        "amendments": [
            {"id": a.id, "effective_at": a.effective_at.isoformat(), "applied_now": True}
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
    """Cancels an amendment that has not landed yet. New amendments take effect
    the moment they are saved, so nothing reaches this any more — it stays for
    the ones written while the cooling-off still existed, which are the only
    rows that can still be pending. To undo a change that is already in force,
    amend it back."""
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


class AuditSampleDecision(BaseModel):
    ok: bool
    note: str = Field(default="", max_length=4000)


@router.post("/audit_samples/{sample_id}/decide")
def decide_audit_sample(
    sample_id: str, body: AuditSampleDecision, session: Session = Depends(db)
) -> dict:
    """The chairman's verdict on a drawn sample. Failing one lands on the
    task's audit fields and in the CEO's next pack — the same disclosure path
    as every other governance event. Passing one is recorded and life goes on.
    An expired sample takes no verdict: the moment passed, and pretending
    otherwise would make the coverage line lie."""
    row = session.get(AuditSample, sample_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown audit sample")
    if row.status != AuditSampleStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"this sample is {row.status}; only a pending one takes a verdict.",
        )
    task = session.get(Task, row.task_id)
    row.status = AuditSampleStatus.PASSED if body.ok else AuditSampleStatus.FAILED
    row.decided_at = utcnow()
    row.note = body.note or None
    if task is not None:
        task.audit_status = "passed" if body.ok else "failed"
        task.audit_note = body.note or None
        if not body.ok:
            _notice_ceo(
                session, row.project_id, NoticeKind.AUDIT_FAILED,
                f"The chairman audited a sampled close and it FAILED: "
                f"'{task.title[:200]}'. His note: {body.note[:1000] or '(none)'}\n"
                "The task stays done — sampling never reopens work by itself — but "
                "this is on the record, it will be on the next review, and a pattern "
                "of failed samples is how a company loses the benefit of the doubt.",
            )
    _audit(session, row.project_id, "audit_sample.decide", "audit_sample", row.id,
           {"task_id": row.task_id, "ok": body.ok})
    return {"id": row.id, "status": row.status}


# ── requirements: the chairman owns scope ───────────────────────────────────────


@router.get("/projects/{project_id}/requirements")
def list_requirements(project_id: str, session: Session = Depends(db)) -> list[dict]:
    project = _get_project(session, project_id)
    edges = list(session.execute(
        select(RequirementEdge).where(RequirementEdge.project_id == project.id)
    ).scalars())
    blocked_by: dict[str, list[str]] = {}
    for edge in edges:
        blocked_by.setdefault(edge.to_requirement_id, []).append(edge.from_requirement_id)
    rows = session.execute(
        select(Requirement)
        .where(Requirement.project_id == project.id)
        .order_by(Requirement.stage, Requirement.key)
    ).scalars()
    return [
        {
            "id": r.id, "key": r.key, "statement": r.statement, "status": r.status,
            "stage": r.stage, "acceptance_test": r.acceptance_test,
            "est_runs": r.est_runs, "actual_runs": r.actual_runs,
            "failure_load": r.failure_load,
            "depends_on": blocked_by.get(r.id, []),
            "proposed_by_agent_id": r.proposed_by_agent_id,
            "accepted_at": r.accepted_at.isoformat() if r.accepted_at else None,
            "met_at": r.met_at.isoformat() if r.met_at else None,
            "check_output": r.check_output,
            "cut_reason": r.cut_reason,
        }
        for r in rows
    ]


class RequirementDecision(BaseModel):
    stage: int | None = Field(default=None, ge=1, le=50)
    note: str = Field(default="", max_length=4000)


@router.post("/requirements/{requirement_id}/accept")
def accept_requirement(
    requirement_id: str, body: RequirementDecision, session: Session = Depends(db)
) -> dict:
    """Scope becomes scope here and nowhere else.

    The CEO proposes; only the chairman accepts. That split is the whole reason
    the forecast means anything: remaining ÷ velocity is a number about the
    CEO's work, and a CEO that could accept its own requirements would own both
    sides of its own scoreboard.
    """
    row = session.get(Requirement, requirement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown requirement")
    project = _get_project(session, row.project_id)
    try:
        policy.check_requirement_accept(row)
    except policy.Refusal as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    from_status = row.status
    row.status = RequirementStatus.PENDING
    row.accepted_by_user_id = project.chairman_user_id
    row.accepted_at = utcnow()
    if body.stage is not None:
        row.stage = body.stage
    policy.record_requirement_event(session, row, RequirementStatus.PENDING,
                                    f"user:{project.chairman_user_id}",
                                    note=body.note or None, from_status=from_status)
    _audit(session, row.project_id, "requirement.accept", "requirement", row.id,
           {"key": row.key, "stage": row.stage})
    _notice_ceo(
        session, row.project_id, NoticeKind.GOAL_CHANGED,
        f"The chairman ACCEPTED {row.key} into scope: {row.statement[:400]}\n"
        f"Stage {row.stage}. It is now something you may create work for, and it counts "
        "toward what has to be true before this project is finished.",
    )
    return {"id": row.id, "key": row.key, "status": row.status, "stage": row.stage}


class RequirementCut(BaseModel):
    reason: str = Field(min_length=5, max_length=4000)


@router.post("/requirements/{requirement_id}/cut")
def cut_requirement(
    requirement_id: str, body: RequirementCut, session: Session = Depends(db)
) -> dict:
    """Cutting is how a date is met when the work will not fit. It is the
    chairman's lever, never the CEO's — the party being measured does not get
    to shrink the thing it is measured against."""
    row = session.get(Requirement, requirement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown requirement")
    project = _get_project(session, row.project_id)
    try:
        policy.check_requirement_cut(row)
    except policy.Refusal as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    from_status = row.status
    row.status = RequirementStatus.CUT
    row.cut_reason = body.reason
    row.cut_at = utcnow()
    policy.record_requirement_event(session, row, RequirementStatus.CUT,
                                    f"user:{project.chairman_user_id}",
                                    note=body.reason, from_status=from_status)
    _audit(session, row.project_id, "requirement.cut", "requirement", row.id,
           {"key": row.key, "reason": body.reason})
    _notice_ceo(
        session, row.project_id, NoticeKind.GOAL_CHANGED,
        f"The chairman CUT {row.key} from scope: {row.statement[:300]}\n"
        f"His reason: {body.reason[:1000]}\nStop work on it. Anything that depended on it "
        "is now unblocked — a cut requirement counts as settled, not as pending forever.",
    )
    return {"id": row.id, "key": row.key, "status": row.status}


class RequirementVerdict(BaseModel):
    ok: bool
    note: str = Field(default="", max_length=4000)


@router.post("/requirements/{requirement_id}/verdict")
def requirement_verdict(
    requirement_id: str, body: RequirementVerdict, session: Session = Depends(db)
) -> dict:
    """The chairman's answer on an *observable* acceptance test.

    A command test is run by the platform and needs nobody. An observable one
    names something a script cannot check — "the memo reads well" — so somebody
    has to look. That somebody is never the agent whose work it judges, which
    is why this route is here and not in the agent API.
    """
    row = session.get(Requirement, requirement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown requirement")
    if row.status != RequirementStatus.CHECKING:
        raise HTTPException(
            status_code=409,
            detail=f"{row.key} is {row.status}; only a requirement awaiting your verdict "
                   "can take one.",
        )
    project = _get_project(session, row.project_id)
    actor = f"user:{project.chairman_user_id}"
    from_status = row.status
    if body.ok:
        row.status = RequirementStatus.MET
        row.met_at = utcnow()
        row.check_output = body.note or "chairman confirmed the observable"
        policy.record_requirement_event(session, row, RequirementStatus.MET, actor,
                                        note=body.note or None, from_status=from_status)
    else:
        row.status = RequirementStatus.IN_PROGRESS
        row.failure_load += 1
        policy.record_requirement_event(session, row, RequirementStatus.IN_PROGRESS, actor,
                                        note=body.note or "chairman rejected the observable",
                                        from_status=from_status)
        _notice_ceo(
            session, row.project_id, NoticeKind.AUDIT_FAILED,
            f"The chairman looked at {row.key} and it does NOT hold: "
            f"{body.note[:1000] or '(no note)'}\nIt is back in progress. Read what he said "
            "before creating more work for it.",
        )
    _audit(session, row.project_id, "requirement.verdict", "requirement", row.id,
           {"key": row.key, "ok": body.ok})
    return {"id": row.id, "key": row.key, "status": row.status}


# ── the topology experiment: one variable, three arms, four numbers ─────────────

TOPOLOGIES = ("solo", "ceo_team", "ceo_leads_workers")


class ExperimentRequirementIn(BaseModel):
    statement: str = Field(min_length=10, max_length=2000)
    acceptance_test: dict
    stage: int = Field(default=1, ge=1, le=50)
    est_runs: int = Field(ge=1, le=10_000)


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    slug_base: str = Field(pattern="^[a-z0-9-]{2,48}$")
    goal: str = Field(min_length=10)
    chairman_email: str
    requirements: list[ExperimentRequirementIn] = Field(min_length=1, max_length=30)
    topologies: list[str] = Field(
        default_factory=lambda: list(TOPOLOGIES), min_length=1, max_length=3)
    ceo_role_slug: str = "ceo"
    worker_role_slug: str
    # Employees per arm, CEO excluded. Solo ignores it; ceo_team gets this
    # many one-person teams; ceo_leads_workers one team of this many.
    team_size: int = Field(default=2, ge=1, le=6)
    constitution: dict = Field(default_factory=dict)
    billing_mode: str = Field(default="api_key", pattern="^(api_key|subscription)$")
    start: bool = True


def _arm_teams(topology: str, worker_role: str, team_size: int) -> tuple[list[TeamSpec], bool]:
    """The three shapes, and only these three — identical but for this."""
    if topology == "solo":
        return [TeamSpec(role_slug=worker_role, name="Solo", employees=[])], True
    if topology == "ceo_team":
        return [
            TeamSpec(role_slug=worker_role, name=f"Member {i}",
                     employees=[f"Member {i}"])
            for i in range(1, team_size + 1)
        ], False
    return [TeamSpec(
        role_slug=worker_role, name="Team",
        employees=[f"Worker {i}" for i in range(1, team_size + 1)],
        lead="Worker 1",
    )], False


@router.post("/experiments")
def create_experiment(body: ExperimentCreate, session: Session = Depends(db)) -> dict:
    """One goal, one chairman-authored requirement set, one constitution — N
    projects differing in exactly one variable. The set and the constitution
    freeze on the experiment row, so "the arms were identical" is a checkable
    fact rather than a claim about setup care. Scope is chairman-authored and
    accepted at birth in every arm: the CEOs plan HOW, never WHAT, which is
    what keeps the denominator identical across topologies."""
    from yaaf.core.models import Experiment, ExperimentArm

    unknown = sorted(set(body.topologies) - set(TOPOLOGIES))
    if unknown or len(set(body.topologies)) != len(body.topologies):
        raise HTTPException(
            status_code=422,
            detail=f"topologies must be distinct picks from {', '.join(TOPOLOGIES)}")
    if "ceo_leads_workers" in body.topologies and body.team_size < 2:
        raise HTTPException(
            status_code=422,
            detail="ceo_leads_workers needs team_size >= 2 — a lead with no workers "
                   "is just ceo_team wearing a hat")
    try:
        doc = ConstitutionDoc.model_validate(
            {**ConstitutionDoc().model_dump(), **body.constitution})
        for req in body.requirements:
            policy.validate_success_test(doc, req.acceptance_test)
    except policy.Refusal as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    chairman = session.execute(
        select(User).where(User.email == body.chairman_email)).scalar_one_or_none()
    if chairman is None:
        raise HTTPException(status_code=422, detail="chairman user does not exist")

    experiment = Experiment(
        id=ids.new_id(ids.EXPERIMENT),
        name=body.name,
        goal=body.goal,
        requirement_set=[r.model_dump() for r in body.requirements],
        constitution=doc.model_dump(),
        chairman_user_id=chairman.id,
    )
    session.add(experiment)
    session.flush()

    arms_out = []
    for topology in body.topologies:
        teams, solo = _arm_teams(topology, body.worker_role_slug, body.team_size)
        created = create_project(ProjectCreate(
            slug=f"{body.slug_base}-{topology.replace('_', '-')}",
            name=f"{body.name} — {topology}",
            goal=body.goal,
            chairman_email=body.chairman_email,
            billing_mode=body.billing_mode,
            mode="deliver",
            constitution=body.constitution,
            integrations={"experiment": {"id": experiment.id, "topology": topology}},
            ceo_role_slug=body.ceo_role_slug,
            teams=teams,
            solo=solo,
        ), session)
        for req in body.requirements:
            add_requirement(created["id"], ChairmanRequirementIn(
                statement=req.statement,
                acceptance_test=req.acceptance_test,
                stage=req.stage,
                est_runs=req.est_runs,
                note=f"experiment '{body.name}': identical scope in every arm",
            ), session)
        if body.start:
            start_project(created["id"], session)
        session.add(ExperimentArm(
            id=ids.new_id(ids.EXPERIMENT_ARM),
            experiment_id=experiment.id,
            project_id=created["id"],
            topology=topology,
        ))
        arms_out.append({"topology": topology, "project_id": created["id"],
                         "slug": f"{body.slug_base}-{topology.replace('_', '-')}"})

    _audit(session, None, "experiment.create", "experiment", experiment.id,
           {"name": body.name, "topologies": body.topologies,
            "requirements": len(body.requirements)})
    return {"id": experiment.id, "name": experiment.name, "arms": arms_out,
            "note": ("Arms run concurrently under identical constitutions and "
                     "identical chairman-authored scope. Compared on cost per "
                     "requirement met, wall-clock to first attested evidence, share "
                     "of spend producing nothing, and words per delivered byte — "
                     "the report is on the Experiments page.")}


class ExperimentClose(BaseModel):
    reason: str = Field(default="", max_length=2000)


@router.post("/experiments/{experiment_id}/close")
def close_experiment(
    experiment_id: str, body: ExperimentClose, session: Session = Depends(db)
) -> dict:
    """Stop every still-running arm and freeze the reading. The report stays
    readable forever — closing ends the spend, not the record."""
    from yaaf.core.models import Experiment, ExperimentArm

    experiment = session.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="unknown experiment")
    if experiment.status != "running":
        raise HTTPException(status_code=409, detail=f"already {experiment.status}")
    stopped = 0
    for arm in session.execute(
        select(ExperimentArm).where(ExperimentArm.experiment_id == experiment.id)
    ).scalars():
        project = session.get(Project, arm.project_id)
        if project is not None and not ProjectStatus(project.status).is_terminal:
            project.status = ProjectStatus.STOPPED
            project.status_reason = (
                "experiment closed" + (f": {body.reason}" if body.reason else ""))
            _revoke_running(session, project.id)
            stopped += 1
    experiment.status = "closed"
    experiment.closed_at = utcnow()
    _audit(session, None, "experiment.close", "experiment", experiment.id,
           {"reason": body.reason, "arms_stopped": stopped})
    return {"id": experiment.id, "status": experiment.status, "arms_stopped": stopped}


# ── the review: six levers, each recorded with a reason ─────────────────────────


class ChairmanRequirementIn(BaseModel):
    statement: str = Field(min_length=10, max_length=2000)
    acceptance_test: dict
    stage: int = Field(default=1, ge=1, le=50)
    est_runs: int = Field(ge=1, le=10_000)
    note: str = Field(default="", max_length=4000)


@router.post("/projects/{project_id}/requirements")
def add_requirement(
    project_id: str, body: ChairmanRequirementIn, session: Session = Depends(db)
) -> dict:
    """The ADD lever, also usable outside a review: chairman-authored scope,
    accepted at birth because acceptance is his act and this is him acting.
    The last design only let him delete — 'alter requirements' includes
    adding one. His adds carry est_runs like everyone else's: the feasibility
    arithmetic does not care whose idea the work was."""
    project = _get_project(session, project_id)
    if not policy.requires_requirements(project):
        raise HTTPException(
            status_code=422,
            detail="An Explore project has no requirements, by design — its shape is "
                   "bets and evidence.",
        )
    try:
        policy.validate_success_test(effective(session, project.id), body.acceptance_test)
    except policy.Refusal as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    cycle = policy.current_cycle(session, project.id)
    requirement = Requirement(
        id=ids.new_id(ids.REQUIREMENT),
        project_id=project.id,
        cycle_id=cycle.id if cycle is not None else None,
        key=policy.next_requirement_key(session, project.id),
        statement=body.statement.strip(),
        acceptance_test=body.acceptance_test,
        status=RequirementStatus.PENDING,
        stage=body.stage,
        est_runs=body.est_runs,
        accepted_by_user_id=project.chairman_user_id,
        accepted_at=utcnow(),
    )
    session.add(requirement)
    session.flush()
    policy.record_requirement_event(session, requirement, RequirementStatus.PENDING,
                                    f"user:{project.chairman_user_id}",
                                    note=body.note or "chairman-authored",
                                    from_status=None)
    _audit(session, project.id, "requirement.add", "requirement", requirement.id,
           {"key": requirement.key, "stage": requirement.stage,
            "est_runs": requirement.est_runs})
    _notice_ceo(
        session, project.id, NoticeKind.GOAL_CHANGED,
        f"The chairman ADDED {requirement.key} to scope himself: "
        f"{requirement.statement[:400]}\nStage {requirement.stage}, estimated "
        f"{requirement.est_runs} runs. It is accepted — plan for it. If it breaks the "
        "arithmetic, the plan gate will say so and that conversation is his to have "
        "with himself; yours is to sequence it honestly.",
    )
    return {"id": requirement.id, "key": requirement.key,
            "status": requirement.status, "stage": requirement.stage}


class RequirementReword(BaseModel):
    statement: str = Field(min_length=10, max_length=2000)
    reason: str = Field(min_length=5, max_length=4000)


@router.post("/requirements/{requirement_id}/reword")
def reword_requirement(
    requirement_id: str, body: RequirementReword, session: Session = Depends(db)
) -> dict:
    """The REWORD lever: the statement moves, the identity does not — key,
    estimate, history and failure load stay put, so a reword can never be
    used to launder a struggling requirement into a fresh-looking one."""
    row = session.get(Requirement, requirement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown requirement")
    if row.status in policy.CLOSED_REQUIREMENT_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"{row.key} is {row.status}; settled scope is history. Add a new "
                   "requirement instead of editing what already happened.",
        )
    project = _get_project(session, row.project_id)
    old = row.statement
    row.statement = body.statement.strip()
    policy.record_requirement_event(
        session, row, row.status, f"user:{project.chairman_user_id}",
        note=f"reworded: {body.reason[:500]}", from_status=row.status)
    _audit(session, project.id, "requirement.reword", "requirement", row.id,
           {"key": row.key, "reason": body.reason})
    _notice_ceo(
        session, project.id, NoticeKind.GOAL_CHANGED,
        f"The chairman REWORDED {row.key}.\nWas: {old[:300]}\nIs now: "
        f"{row.statement[:300]}\nHis reason: {body.reason[:500]}\nThe key, estimate "
        "and history are unchanged — read the new wording before working it.",
    )
    return {"id": row.id, "key": row.key, "statement": row.statement}


class ReviewLeverIn(BaseModel):
    kind: str = Field(pattern="^(cut|add|reword|move_date|raise_budget|accept_slip)$")
    reason: str = Field(min_length=5, max_length=4000)
    requirement_id: str | None = None
    statement: str | None = None
    acceptance_test: dict | None = None
    stage: int = Field(default=1, ge=1, le=50)
    est_runs: int | None = Field(default=None, ge=1, le=10_000)
    deliver_by: datetime | None = None
    amount: float | None = Field(default=None, gt=0)


def _lever_needs(body: ReviewLeverIn, *fields: str) -> None:
    missing = [f for f in fields if getattr(body, f) in (None, "")]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"the {body.kind} lever needs {', '.join(missing)}",
        )


@router.post("/reviews/{review_id}/lever")
def pull_review_lever(
    review_id: str, body: ReviewLeverIn, session: Session = Depends(db)
) -> dict:
    """One lever, pulled: the action happens through the SAME routes it would
    outside a review — cut, add, reword, move, amend — and the pull is
    recorded on the review with the chairman's reason, so acting and
    accounting cannot drift apart. accept_slip is the sixth: an explicit
    'the date stands and I know it will slip', which is a decision, not an
    absence of one."""
    review = session.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="unknown review")
    if review.status != ReviewStatus.OPEN:
        raise HTTPException(
            status_code=409,
            detail=f"review #{review.number} is {review.status}; the next meeting is "
                   "the place for new decisions.",
        )
    project = _get_project(session, review.project_id)

    if body.kind == "cut":
        _lever_needs(body, "requirement_id")
        result = cut_requirement(body.requirement_id,
                                 RequirementCut(reason=body.reason), session)
        detail = f"cut {result['key']}"
    elif body.kind == "add":
        _lever_needs(body, "statement", "acceptance_test", "est_runs")
        result = add_requirement(
            project.id,
            ChairmanRequirementIn(statement=body.statement,
                                  acceptance_test=body.acceptance_test,
                                  stage=body.stage, est_runs=body.est_runs,
                                  note=body.reason),
            session)
        detail = f"added {result['key']} (stage {body.stage}, {body.est_runs} runs)"
    elif body.kind == "reword":
        _lever_needs(body, "requirement_id", "statement")
        result = reword_requirement(
            body.requirement_id,
            RequirementReword(statement=body.statement, reason=body.reason), session)
        detail = f"reworded {result['key']}"
    elif body.kind == "move_date":
        _lever_needs(body, "deliver_by")
        commitment = policy.live_commitment(session, project.id)
        if commitment is None:
            raise HTTPException(status_code=409,
                                detail="no live commitment — there is no date to move")
        result = move_commitment(
            commitment.id,
            CommitmentMove(deliver_by=body.deliver_by, reason=body.reason), session)
        detail = f"moved the date to {result['deliver_by'][:10]}"
    elif body.kind == "raise_budget":
        _lever_needs(body, "amount")
        changes = ({"monthly_usd_cap": body.amount}
                   if project.billing_mode == "api_key"
                   else {"monthly_token_cap": int(body.amount)})
        amend_constitution(project.id,
                           AmendmentIn(reason=body.reason, changes=changes), session)
        detail = f"raised the monthly cap to {body.amount:,.0f}"
    else:  # accept_slip
        detail = "accepted the slip"
        _notice_ceo(
            session, project.id, NoticeKind.GOAL_CHANGED,
            f"At review #{review.number} the chairman ACCEPTED THE SLIP: "
            f"{body.reason[:800]}\nThe date stands; if scope is frozen it stays "
            "frozen. This is him choosing lateness over churn — deliver.",
        )

    review.decisions = [*(review.decisions or []),
                        {"kind": body.kind, "reason": body.reason,
                         "detail": detail, "at": utcnow().isoformat()}]
    _audit(session, project.id, "review.lever", "review", review.id,
           {"number": review.number, "kind": body.kind, "detail": detail})
    return {"review": review.number, "recorded": detail,
            "decisions": len(review.decisions)}


class ReviewClose(BaseModel):
    note: str = Field(default="", max_length=4000)


@router.post("/reviews/{review_id}/close")
def close_review(
    review_id: str, body: ReviewClose, session: Session = Depends(db)
) -> dict:
    """The meeting ends. Closing with zero decisions is itself a decision —
    'no change' on the record beats an open meeting aging into a hole — and
    everything decided is disclosed to the CEO in one summary notice."""
    review = session.get(Review, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="unknown review")
    if review.status != ReviewStatus.OPEN:
        raise HTTPException(status_code=409,
                            detail=f"review #{review.number} is already {review.status}")
    project = _get_project(session, review.project_id)
    review.status = ReviewStatus.CLOSED
    review.closed_at = utcnow()
    review.closed_by_user_id = project.chairman_user_id
    decisions = review.decisions or []
    summary = ("; ".join(f"{d['kind']}: {d['detail']} — {d['reason'][:120]}"
                         for d in decisions)
               if decisions else "no changes — the plan stands as it is")
    # The learning loop: on an Operate project the review writes the cycle's
    # narrative — platform-composed from the frozen numbers, the CEO's own
    # words, and the chairman's decisions. The next cycle's pack carries the
    # last three of these verbatim.
    if project.mode == ProjectMode.OPERATE:
        cycle = policy.current_cycle(session, project.id)
        if cycle is not None:
            numbers = review.numbers or {}
            block = (
                f"[review #{review.number}, {utcnow():%Y-%m-%d}] "
                f"pace {numbers.get('velocity', {}).get('per_week_3w')}/wk; "
                f"{numbers.get('scope', {}).get('met_since_last_review')} met since "
                f"last review; decisions: {summary}."
                + (f"\nThe CEO's variance narrative: {review.narrative}"
                   if review.narrative else "\nNo variance narrative was filed.")
            )
            cycle.narrative = ((cycle.narrative + "\n\n") if cycle.narrative else "") + block
    _notice_ceo(
        session, project.id, NoticeKind.GOAL_CHANGED,
        f"Review #{review.number} is closed. Decisions: {summary}."
        + (f" His closing note: {body.note[:500]}" if body.note else "")
        + " Each will be shown at the next review beside what it bought.",
    )
    _audit(session, project.id, "review.close", "review", review.id,
           {"number": review.number, "decisions": len(decisions)})
    return {"id": review.id, "number": review.number, "status": review.status,
            "decisions": decisions}


# ── the commitment: the chairman owns the date ──────────────────────────────────


def _get_commitment(session: Session, commitment_id: str) -> Commitment:
    row = session.get(Commitment, commitment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown commitment")
    return row


def _freeze_commitment_basis(session: Session, project: Project, row: Commitment,
                             cons: ConstitutionDoc) -> None:
    """Record what the promise was made under, and project from it fresh. The
    janitor's three re-validation triggers diff against exactly this."""
    from yaaf.server.velocity import commitment_projection

    scope_keys = policy.commitment_scope_keys(session, project.id)
    scope = [r for r in policy.scope_requirements(session, project.id)
             if r.key in set(scope_keys)]
    row.constitution_version = effective_version(session, project.id)
    row.scope_snapshot = scope_keys
    projection = commitment_projection(session, project, cons)
    row.projected_done_at = (projection["projected_done_at"]
                             if projection["known"] else None)
    row.projection = {
        "constitution_version": row.constitution_version,
        "scope_keys": scope_keys,
        "failure_load": sum(r.failure_load for r in scope),
        "method": projection.get("method"),
        "note": projection.get("basis") or projection.get("why"),
    }


class CommitmentAccept(BaseModel):
    # The chairman's own date, if he wants one other than the projection the
    # CEO proposed. His to write — the constraint runs on agents, not on him.
    deliver_by: datetime | None = None
    note: str = Field(default="", max_length=4000)


@router.post("/commitments/{commitment_id}/accept")
def accept_commitment(
    commitment_id: str, body: CommitmentAccept, session: Session = Depends(db)
) -> dict:
    """The promise becomes a promise here and nowhere else.

    Acceptance freezes two things — the constitution version and the scope
    keys in force — so that later drift is detectable rather than deniable.
    From this moment the janitor recomputes the projection continuously and
    walks the status itself; nobody, including the chairman, edits it by hand.
    """
    row = _get_commitment(session, commitment_id)
    project = _get_project(session, row.project_id)
    try:
        policy.check_commitment_accept(row)
    except policy.Refusal as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    if body.deliver_by is not None:
        deliver_by = body.deliver_by
        if deliver_by.tzinfo is None:
            from datetime import UTC
            deliver_by = deliver_by.replace(tzinfo=UTC)
        if deliver_by <= utcnow():
            raise HTTPException(
                status_code=422,
                detail="deliver_by is in the past — a promise that starts breached "
                       "is not a promise.",
            )
        row.deliver_by = deliver_by
    cons = effective(session, project.id)
    row.status = CommitmentStatus.ACCEPTED
    row.accepted_by_user_id = project.chairman_user_id
    row.accepted_at = utcnow()
    _freeze_commitment_basis(session, project, row, cons)
    _audit(session, project.id, "commitment.accept", "commitment", row.id,
           {"deliver_by": row.deliver_by.isoformat(),
            "constitution_version": row.constitution_version,
            "scope": row.scope_snapshot})
    _notice_ceo(
        session, project.id, NoticeKind.COMMITMENT_ACCEPTED,
        f"The chairman ACCEPTED the commitment: {len(row.scope_snapshot)} "
        f"requirement(s) by {row.deliver_by:%Y-%m-%d}."
        + (f" His note: {body.note[:500]}" if body.note else "")
        + " From here the platform recomputes the projection continuously. If it "
          "slips past the date, new scope freezes and he is emailed — the way to "
          "keep the promise is to finish things, not to say reassuring words about "
          "them.",
    )
    return {"id": row.id, "status": row.status,
            "deliver_by": row.deliver_by.isoformat(),
            "constitution_version": row.constitution_version,
            "scope_snapshot": row.scope_snapshot}


class CommitmentDecline(BaseModel):
    reason: str = Field(min_length=5, max_length=4000)


@router.post("/commitments/{commitment_id}/decline")
def decline_commitment(
    commitment_id: str, body: CommitmentDecline, session: Session = Depends(db)
) -> dict:
    """A proposal the chairman does not want is removed, with his reason on the
    record and in front of the CEO. Only proposals — a live promise is not
    something to disappear; move its date or cut its scope instead."""
    row = _get_commitment(session, commitment_id)
    if row.status != CommitmentStatus.PROPOSED:
        raise HTTPException(
            status_code=409,
            detail=f"This commitment is {row.status}; only a proposal can be declined. "
                   "A live promise is changed by moving the date or cutting scope, "
                   "never by deleting it.",
        )
    project = _get_project(session, row.project_id)
    _audit(session, project.id, "commitment.decline", "commitment", row.id,
           {"reason": body.reason, "deliver_by": row.deliver_by.isoformat()})
    _notice_ceo(
        session, project.id, NoticeKind.GOAL_CHANGED,
        f"The chairman DECLINED the proposed commitment ({row.deliver_by:%Y-%m-%d}). "
        f"His reason: {body.reason[:1000]}\nNothing binds. Keep working the plan; "
        "propose again when the arithmetic has changed enough to answer him.",
    )
    session.delete(row)
    return {"declined": commitment_id}


class CommitmentMove(BaseModel):
    deliver_by: datetime
    reason: str = Field(min_length=5, max_length=4000)


@router.post("/commitments/{commitment_id}/move")
def move_commitment(
    commitment_id: str, body: CommitmentMove, session: Session = Depends(db)
) -> dict:
    """The chairman moves the date — the release valve on a promise that
    stopped holding, and one of the six levers the stage-7 review will offer.
    Recorded with his reason, disclosed to the CEO, re-projected on the spot
    so at_risk/breached lift (or persist) immediately rather than on the next
    janitor tick."""
    row = _get_commitment(session, commitment_id)
    if row.status not in policy.LIVE_COMMITMENT_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"This commitment is {row.status}; only a live one has a date to move.",
        )
    project = _get_project(session, row.project_id)
    deliver_by = body.deliver_by
    if deliver_by.tzinfo is None:
        from datetime import UTC
        deliver_by = deliver_by.replace(tzinfo=UTC)
    old = row.deliver_by
    row.deliver_by = deliver_by
    _audit(session, project.id, "commitment.move", "commitment", row.id,
           {"from": old.isoformat(), "to": deliver_by.isoformat(),
            "reason": body.reason})
    _notice_ceo(
        session, project.id, NoticeKind.GOAL_CHANGED,
        f"The chairman MOVED the commitment date: {old:%Y-%m-%d} → "
        f"{deliver_by:%Y-%m-%d}. His reason: {body.reason[:1000]}\nThe projection is "
        "being re-checked against the new date; a freeze lifts only if the "
        "arithmetic now fits inside it.",
    )
    # Re-walk the status now, through the same producer the janitor uses.
    from yaaf.server import janitor

    janitor.check_commitments(session, project, effective(session, project.id))
    return {"id": row.id, "status": row.status,
            "deliver_by": row.deliver_by.isoformat()}


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
    """Names and scopes only. The list is polled every few seconds, so values
    never ride it — reading one is a deliberate act, and has its own endpoint."""
    stmt = select(Secret)
    if project_id is not None:
        stmt = stmt.where(Secret.project_id == project_id)
    return [
        {"id": s.id, "name": s.name, "project_id": s.project_id,
         "role_scopes": s.role_scopes, "kind": s.kind}
        for s in session.execute(stmt.order_by(Secret.created_at)).scalars()
    ]


@router.get("/secrets/{secret_id}/value")
def reveal_secret(secret_id: str, session: Session = Depends(db)) -> dict:
    """The chairman reads their own credential.

    There is one human here, they hold the master admin token, and they can
    already read any of this straight out of Postgres. Hiding a value from them
    protects nothing and costs a debugging session every time a data source
    will not mount. So values are *visually* masked in the UI and fetched only
    when the eye is pressed — one press, one call, one audit row.

    This is a `/admin` route. Agents authenticate against `/agent/*` with
    per-run tokens and cannot reach it; what a role receives is still decided
    solely by `Secret.role_scopes` at lease time, and a role is still told
    variable names only. Nothing about the agent side changes here.
    """
    from yaaf.core.crypto import decrypt

    row = session.get(Secret, secret_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown secret")
    _audit(session, row.project_id, "secret.reveal", "secret", row.id, {"name": row.name})
    return {"id": row.id, "name": row.name, "value": decrypt(row.ciphertext)}


@router.delete("/secrets/{secret_id}")
def delete_secret(secret_id: str, session: Session = Depends(db)) -> dict:
    """Remove a credential outright. Refused for the two kinds that something
    else owns: the credential the project is actively paying with, and a data
    source's address or token (delete the source and both go together)."""
    row = session.get(Secret, secret_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown secret")
    if row.project_id:
        project = session.get(Project, row.project_id)
        if project is not None and project.billing_secret_id == row.id:
            raise HTTPException(
                status_code=422,
                detail=f"'{row.name}' is how this project pays for Claude — connect a "
                       "different billing credential first, then delete this one",
            )
    source = _data_source_owning(session, row.project_id, row.name)
    if source is not None:
        raise HTTPException(
            status_code=422,
            detail=f"'{row.name}' belongs to data source '{source}' — delete the data "
                   "source instead and its address and token go with it",
        )
    session.delete(row)
    _audit(session, row.project_id, "secret.delete", "secret", row.id, {"name": row.name})
    return {"deleted": row.name}


@router.post("/projects/{project_id}/webhook_secrets")
def provision_webhook_secrets(project_id: str, session: Session = Depends(db)) -> dict:
    """Generate the signing secrets for the inbound evidence connectors. The
    secrets live only in project.integrations — never in a role scope — so no run
    ever holds them, which is what makes webhook evidence unforgeable by
    agents."""
    project = _get_project(session, project_id)
    github_secret = pysecrets.token_hex(32)
    generic_secret = pysecrets.token_hex(32)
    # Merge, never clobber: integrations also carries the runtime, Slack mapping, etc.
    project.integrations = {
        **(project.integrations or {}),
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
