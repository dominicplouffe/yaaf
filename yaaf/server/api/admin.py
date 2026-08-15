"""Chairman/admin API: project lifecycle, org setup, approvals, amendments.

Everything here is authenticated with the admin token (full user auth is the
UI milestone). Chairman powers are deliberately narrow at runtime: speak in the
boardroom, decide approvals, extend a hypothesis once, amend the constitution
through the cooling-off, pause/stop things, unfreeze threads, spot-audit. The
chairman never edits tasks, posts to team rooms, or touches memory — that
discipline is the product.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.constitution import ConstitutionDoc, create_initial, propose_amendment
from yaaf.core.db import notify
from yaaf.core.models import (
    Agent,
    AgentKind,
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
    employees: list[str] = Field(min_length=1)  # agent display names
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
        team_out.append({"id": team.id, "name": team.name,
                         "agents": [{"id": a.id, "name": a.name} for a in agents]})

    _audit(session, project.id, "project.create", "project", project.id)
    return {"id": project.id, "ceo_agent_id": ceo.id, "teams": team_out,
            "constitution": doc.model_dump(), "status": project.status}


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


class ProjectPatch(BaseModel):
    billing_mode: str | None = Field(default=None, pattern="^(api_key|subscription)$")
    billing_secret_id: str | None = None
    controlled_domains: list[str] | None = None
    # Merged into the existing config (mutable configuration only — the
    # constitution changes exclusively through amendments).
    config: dict | None = None


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
        project.billing_secret_id = body.billing_secret_id
        changed["billing_secret_id"] = body.billing_secret_id
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
        amendment = propose_amendment(
            session, project.id, project.chairman_user_id, body.reason, body.changes
        )
    except Exception as exc:  # invalid overlay
        raise HTTPException(status_code=422, detail=f"invalid amendment: {exc}") from exc
    _audit(session, project.id, "constitution.amend", "amendment", amendment.id,
           {"changes": body.changes, "effective_at": amendment.effective_at.isoformat()})
    return {
        "id": amendment.id,
        "effective_at": amendment.effective_at.isoformat(),
        "note": "Takes effect after the cooling-off period and is disclosed to the CEO.",
    }


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
    return {"id": row.id, "verification": row.verification}


class SecretIn(BaseModel):
    project_id: str | None = None
    name: str = Field(min_length=2, max_length=120)
    # Omit value to update role grants only — no re-pasting to change access.
    value: str | None = Field(default=None, min_length=1)
    role_scopes: list[str] = Field(default_factory=list)


@router.get("/secrets")
def list_secrets(project_id: str | None = None, session: Session = Depends(db)) -> list[dict]:
    """Names and scopes only — values are write-only, decrypted solely at
    run-injection time."""
    stmt = select(Secret)
    if project_id is not None:
        stmt = stmt.where(Secret.project_id == project_id)
    return [
        {"id": s.id, "name": s.name, "project_id": s.project_id, "role_scopes": s.role_scopes}
        for s in session.execute(stmt.order_by(Secret.created_at)).scalars()
    ]


@router.post("/secrets")
def put_secret(body: SecretIn, session: Session = Depends(db)) -> dict:
    from yaaf.core.crypto import encrypt

    existing = session.execute(
        select(Secret).where(Secret.project_id == body.project_id, Secret.name == body.name)
    ).scalar_one_or_none()
    if existing:
        rotated = False
        if body.value is not None:
            existing.ciphertext = encrypt(body.value)
            rotated = True
        existing.role_scopes = body.role_scopes
        _audit(session, body.project_id, "secret.rotate" if rotated else "secret.rescope",
               "secret", existing.id, {"role_scopes": body.role_scopes})
        return {"id": existing.id, "rotated": rotated, "role_scopes": existing.role_scopes}
    if body.value is None:
        raise HTTPException(status_code=422, detail="a new secret needs a value")
    row = Secret(
        id=ids.new_id(ids.SECRET),
        project_id=body.project_id,
        name=body.name,
        ciphertext=encrypt(body.value),
        role_scopes=body.role_scopes,
    )
    session.add(row)
    _audit(session, body.project_id, "secret.create", "secret", row.id,
           {"role_scopes": body.role_scopes})
    return {"id": row.id, "rotated": False, "role_scopes": row.role_scopes}
