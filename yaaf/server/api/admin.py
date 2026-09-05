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
import os
import re
import secrets as pysecrets
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import ids, platform
from yaaf.core.constitution import (
    GROUP_THEMES,
    THEME_ORDER,
    ConstitutionDoc,
    create_initial,
    default_autonomy,
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
    Crew,
    Cycle,
    Evidence,
    Hypothesis,
    HypothesisStatus,
    McpClient,
    McpToken,
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
from yaaf.server import briefing, policy
from yaaf.server.api.agent import WAKE_CHANNEL
from yaaf.server.deps import admin_auth, db
from yaaf.server.mcp.auth import SCOPES as MCP_SCOPES

log = logging.getLogger("yaaf.admin")

router = APIRouter(dependencies=[Depends(admin_auth)])


#: Who the audit trail should name for writes on this surface.
#:
#: "user:admin" is true when the chairman is holding the bearer themselves. It
#: is a lie when a model client is driving through MCP, and the difference has
#: to survive into a review six weeks later — so the MCP layer sets this for
#: the duration of a call rather than every handler growing an actor argument.
ACTOR: ContextVar[str] = ContextVar("yaaf_audit_actor", default="user:admin")


def _audit(session: Session, project_id: str | None, action: str, entity: str,
           entity_id: str | None, detail: dict | None = None) -> None:
    session.add(
        AuditLog(
            id=ids.new_id(ids.AUDIT),
            project_id=project_id,
            actor=ACTOR.get(),
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


# The six aisles a role can be filed under. Discipline, not domain: a
# Compliance Reviewer is a challenge role that happens to know markets, and
# a single tree would file it in one place and send the chairman hunting in
# the other. Domain lives in `domains` instead, as a filter.
ROLE_CATEGORIES = (
    "leadership",   # decides what happens next
    "research",     # finds out what's true
    "build",        # makes it work
    "content",      # makes it worth reading, using, learning
    "challenge",    # tries to prove it wrong before the world does
    "growth",       # finds buyers, and keeps them
)
ROLE_DOMAINS = ("general", "markets", "data", "games", "education")

#: How hard the model thinks, as the Agent SDK names it. A closed set, so a
#: typo is worth a 422 here rather than a container that starts, spends the
#: claim, and dies inside the sandbox where the chairman sees only "the run
#: failed".
EFFORTS = ("low", "medium", "high", "xhigh", "max")
_EFFORT_PATTERN = f"^({'|'.join(EFFORTS)})$"


class RoleIn(BaseModel):
    slug: str = Field(pattern="^[a-z0-9-]{2,64}$")
    name: str
    is_ceo_role: bool = False
    # Absent is legal: a role written before the taxonomy, or by a chairman
    # who did not care, is "uncategorised" and still appears in every picker.
    category: str | None = None
    domains: list[str] = Field(default_factory=list)
    description: str
    system_prompt: str
    model: str = "claude-sonnet-5"
    effort: str = Field(default="high", pattern=_EFFORT_PATTERN)
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
    if body.category is not None and body.category not in ROLE_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"category must be one of {', '.join(ROLE_CATEGORIES)} — "
                   f"or absent, which files the role under Uncategorised",
        )
    unknown_domains = sorted(set(body.domains) - set(ROLE_DOMAINS))
    if unknown_domains:
        raise HTTPException(
            status_code=422,
            detail=f"unknown domain {', '.join(unknown_domains)} — "
                   f"pick from {', '.join(ROLE_DOMAINS)}",
        )
    role = session.execute(select(Role).where(Role.slug == body.slug)).scalar_one_or_none()
    if role is None:
        role = Role(
            id=ids.new_id(ids.ROLE), slug=body.slug, name=body.name,
            is_ceo_role=body.is_ceo_role,
            category=body.category, domains=body.domains,
        )
        session.add(role)
        session.flush()
        version = 1
    else:
        # A re-seed is how an edited taxonomy reaches a running install, so
        # identity fields follow the file rather than sticking at v1's values.
        role.name = body.name
        role.category = body.category
        role.domains = body.domains
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
        "category": role.category,
        "domains": role.domains or [],
        "version": latest.version,
        "description": latest.description,
        "system_prompt": latest.system_prompt,
        "model": latest.model,
        "effort": latest.effort,
        "tool_permissions": latest.tool_permissions,
        "secret_scopes": latest.secret_scopes,
    }


class RoleTaxonomyIn(BaseModel):
    """The identity fields only — deliberately not the prompt."""
    name: str | None = None
    category: str | None = None
    domains: list[str] | None = None


@router.patch("/roles/{slug}")
def update_role_taxonomy(
    slug: str, body: RoleTaxonomyIn, session: Session = Depends(db)
) -> dict:
    """Refile a role without stamping a new version.

    An install seeded before the taxonomy landed has all thirty roles and a
    category on none of them, and POSTing the files again to fix that would
    stamp thirty new versions — putting every running team behind its role and
    lighting up the drift warning for a change that touches no prompt. These
    fields live on `roles` precisely so they can move on their own, and this
    is the endpoint that moves them. What an agent runs is untouched.
    """
    role = session.execute(select(Role).where(Role.slug == slug)).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=404, detail="unknown role")
    if body.category is not None and body.category not in ROLE_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"category must be one of {', '.join(ROLE_CATEGORIES)}")
    if body.domains is not None:
        unknown = sorted(set(body.domains) - set(ROLE_DOMAINS))
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"unknown domain {', '.join(unknown)} — "
                       f"pick from {', '.join(ROLE_DOMAINS)}")
        role.domains = body.domains
    if body.name is not None:
        role.name = body.name
    if body.category is not None:
        role.category = body.category
    return {
        "slug": role.slug,
        "name": role.name,
        "category": role.category,
        "domains": role.domains or [],
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
                "category": role.category,
                "domains": role.domains or [],
                "latest_version": latest.version if latest else 0,
                "model": latest.model if latest else None,
                "effort": latest.effort if latest else None,
                "description": latest.description if latest else "",
                "tools": sorted(
                    k for k, v in (latest.tool_permissions or {}).items()
                    if v is True
                ) if latest else [],
                # What the role cannot work without. The picker shows this at
                # choosing time so a missing key stops being something the
                # chairman discovers from a run that already failed.
                "secret_scopes": (latest.secret_scopes or []) if latest else [],
                "usage": usage,
            }
        )
    return out


# ── crews ───────────────────────────────────────────────────────────────────────


class CrewTeamIn(BaseModel):
    name: str
    role_slug: str
    employees: list[str] = Field(default_factory=list)
    lead: str | None = None
    schedules: dict[str, str] = Field(default_factory=dict)


class CrewIn(BaseModel):
    slug: str = Field(pattern="^[a-z0-9-]{2,64}$")
    name: str
    outcome: str
    ceo_role_slug: str = "ceo"
    domains: list[str] = Field(default_factory=list)
    source: str | None = None
    teams: list[CrewTeamIn] = Field(default_factory=list)


@router.post("/crews")
def create_crew(body: CrewIn, session: Session = Depends(db)) -> dict:
    """Upsert a crew by slug. Unlike a role there are no versions: a crew is
    read once, at hiring time, and copied into the project's own teams — so
    editing one can never reach a project that already started."""
    unknown_domains = sorted(set(body.domains) - set(ROLE_DOMAINS))
    if unknown_domains:
        raise HTTPException(
            status_code=422,
            detail=f"unknown domain {', '.join(unknown_domains)} — "
                   f"pick from {', '.join(ROLE_DOMAINS)}")
    crew = session.execute(
        select(Crew).where(Crew.slug == body.slug)).scalar_one_or_none()
    if crew is None:
        crew = Crew(id=ids.new_id(ids.CREW), slug=body.slug)
        session.add(crew)
    crew.name = body.name
    crew.outcome = body.outcome
    crew.ceo_role_slug = body.ceo_role_slug
    crew.domains = body.domains
    crew.source = body.source
    crew.teams = [t.model_dump() for t in body.teams]
    session.flush()
    return {"crew_id": crew.id, "slug": crew.slug}


@router.get("/crews")
def list_crews(session: Session = Depends(db)) -> list[dict]:
    """Every crew, with the roles it hires resolved against this install's
    library. A crew naming a role the library lacks is still returned, marked
    — the same courtesy a setup file gets, so the ask stays visible by name
    instead of the crew silently vanishing from the picker."""
    library = {
        role.slug: role
        for role in session.execute(select(Role)).scalars()
    }
    out = []
    # Not alphabetical: eleven of these are the examples, and examples/README
    # orders them "in the order worth doing them" — which puts the one-team
    # starter first and the four-team desks last. Alphabetical put every crew
    # whose name begins with "A" in front of the ones a new chairman wants.
    crews = sorted(
        session.execute(select(Crew)).scalars(),
        key=lambda c: (0, c.source or "") if (c.source or "").startswith("examples/")
        else (1, c.name),
    )
    for crew in crews:
        teams = list(crew.teams or [])
        missing = sorted({
            slug for slug in
            [t.get("role_slug") for t in teams] + [crew.ceo_role_slug]
            if slug and slug not in library
        })
        out.append({
            "slug": crew.slug,
            "name": crew.name,
            "outcome": crew.outcome,
            "ceo_role_slug": crew.ceo_role_slug,
            "domains": crew.domains or [],
            "source": crew.source,
            "teams": [
                {
                    **team,
                    "role_name": (
                        library[team["role_slug"]].name
                        if team.get("role_slug") in library else None
                    ),
                }
                for team in teams
            ],
            "missing_role_slugs": missing,
        })
    return out


# ── project creation ────────────────────────────────────────────────────────────


# A per-agent daily wake, "HH:MM" in the constitution's timezone — the same
# shape AgentSchedule accepts after creation.
_DAILY_AT_PATTERN = "^([01]?\\d|2[0-3]):[0-5]\\d$"


class TeamSpec(BaseModel):
    role_slug: str
    name: str
    # Agent display names. Empty is only legal in solo mode (the CEO is the
    # team); create_project/add_team enforce that below.
    employees: list[str] = Field(default_factory=list)
    lead: str | None = None  # employee name; defaults to first when >1
    # Per-employee settings keyed by employee name, so a setup file rebuilds
    # the people it describes and not just their names: "HH:MM" daily wakes
    # and per-agent model overrides. A key naming nobody on the team is
    # refused rather than dropped — a wake schedule that silently fails to
    # attach is missed at 09:00 every day, with no error anywhere.
    schedules: dict[str, str] = Field(default_factory=dict)
    model_overrides: dict[str, str] = Field(default_factory=dict)
    effort_overrides: dict[str, str] = Field(default_factory=dict)


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
    # The repo evidence webhooks attest against. Part of the design, so a
    # setup file carries it and a restore does not come back half-wired.
    github_repo: str | None = None
    # Overrides onto the field defaults — except the Autonomy group, which a
    # spec that names none of its five rules gets filled in with the default
    # preset (`constitution.DEFAULT_AUTONOMY`) rather than left supervised.
    constitution: dict = Field(default_factory=dict)
    integrations: dict = Field(default_factory=dict)
    ceo_role_slug: str = "ceo"
    ceo_name: str = "CEO"
    ceo_daily_at: str | None = Field(default=None, pattern=_DAILY_AT_PATTERN)
    ceo_model_override: str | None = None
    ceo_effort_override: str | None = Field(default=None, pattern=_EFFORT_PATTERN)
    teams: list[TeamSpec] = Field(default_factory=list)
    # Solo mode (the M2 single-agent arm): the CEO is placed as the sole member
    # — hence derived lead — of the only team, so one brain plans AND executes
    # under byte-identical policy. Requires exactly one team with no employees.
    solo: bool = False
    # Explore's bounded window on open scope ("one sale in 60 days"), or a
    # Deliver TARGET. Deliver's promise is still the commitment, which only the
    # platform dates; this is the date somebody wants, held where it can be
    # compared to the arithmetic instead of hidden in the goal prose.
    deadline: datetime | None = None
    # Where that date came from. Required on Deliver, defaulted on Explore.
    # {"kind": "external", "url"/"note": ...} for a fact the world imposes;
    # {"kind": "chairman", "note": why} for a target he wants.
    deadline_source: dict | None = None


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


def _check_team_people(spec: TeamSpec) -> None:
    """Per-employee settings name people who are actually on the team, and
    carry values the runtime understands.

    A setting that silently fails to attach is the failure this exists for: a
    wake schedule for a name nobody has is a standup missed at 09:00 every day
    with no error anywhere, and an effort the SDK does not know reaches the
    sandbox as an env var and kills the run there, where the chairman sees a
    dead container rather than his own typo. Shared by project creation and
    `add_team` so a team added later is checked exactly as one hired at
    birth.
    """
    for field_name, mapping in (("schedules", spec.schedules),
                                ("model_overrides", spec.model_overrides),
                                ("effort_overrides", spec.effort_overrides)):
        unknown = sorted(set(mapping) - set(spec.employees))
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"team '{spec.name}' has {field_name} for "
                       f"{', '.join(unknown)} — not among its employees",
            )
    bad_time = next((v for v in spec.schedules.values()
                     if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", v)), None)
    if bad_time is not None:
        raise HTTPException(
            status_code=422,
            detail=f"team '{spec.name}' schedule '{bad_time}' is not HH:MM",
        )
    bad_effort = next((v for v in spec.effort_overrides.values()
                       if v not in EFFORTS), None)
    if bad_effort is not None:
        raise HTTPException(
            status_code=422,
            detail=f"team '{spec.name}' effort '{bad_effort}' is not one of "
                   f"{', '.join(EFFORTS)}",
        )

#: Whose work IS the deliverable, or is the check on it. `growth` is outreach
#: and support triage and `leadership` plans rather than writes, so neither is
#: lifted — the CEO is already the only Opus seat in most rosters and its
#: output is not what the chairman keeps.
CRAFT_CATEGORIES = ("content", "research", "build", "challenge")

#: Seats whose job is to read somebody else's work and disagree with it.
REVIEW_CATEGORY = "challenge"


def _refuse_without_review_seat(session: Session, slugs: set[str]) -> None:
    """Refuse unless one of these roles is a seat that reads somebody else's work.

    Category rather than slug on purpose — `editor`, `adversary`,
    `data-skeptic`, `plan-adversary` and a role this install wrote itself all
    answer, and a library that grows does not have to come back and edit a
    list.
    """
    if slugs and session.execute(
        select(Role.slug).where(Role.slug.in_(slugs), Role.category == REVIEW_CATEGORY)
    ).scalars().first():
        return
    available = session.execute(
        select(Role.slug).where(Role.category == REVIEW_CATEGORY).order_by(Role.slug)
    ).scalars().all()
    raise HTTPException(
        status_code=422,
        detail=(
            "deliverable_review is 'independent_read', but no team on this roster "
            "hires a reviewing seat — so the only reader of a deliverable would be "
            "the agent that wrote it. Add a team on one of: "
            f"{', '.join(available) or 'none installed — seed the role library first'}"
            "; or set deliverable_review to 'none' and say that is what you want."
        ),
    )


def _read_is_impossible(session: Session, project_id: str) -> bool:
    """Whether this project has nowhere to put a reader.

    Solo is the shape: the CEO *is* the team, one brain plans and executes, so
    there is no second pair of eyes to hire and a rule demanding one would make
    solo projects uncreatable rather than reviewed. Creation already exempted
    it; the three doors that reopen the rule afterwards did not, so a solo
    project created through the wizard — whose presets all set the read — had
    every later "Add a team" refused for lacking a seat it was exempt from
    hiring. The exemption has to hold at every door or it is not an exemption,
    it is a trap.

    It reads the roster rather than the stored topology, because the moment a
    solo project gains a second team it is not solo any more and the rule has
    somewhere to bind. The team being added counts: `add_team` asks this after
    its own team is on the roster, so adding the reader is how the rule is
    satisfied rather than the thing it refuses.
    """
    return session.execute(
        select(func.count(Team.id)).where(Team.project_id == project_id)
    ).scalar_one() <= 1 and session.execute(
        select(func.count(Agent.id)).where(Agent.project_id == project_id,
                                           Agent.kind != AgentKind.CEO)
    ).scalar_one() == 0


def _project_role_slugs(session: Session, project_id: str) -> set[str]:
    """The roles a project's teams are actually pinned to.

    Through the version rather than by name: a team keeps the role version it
    was hired on, and this has to describe the roster as it is rather than as
    the library has since become.
    """
    return set(session.execute(
        select(Role.slug)
        .join(RoleVersion, RoleVersion.role_id == Role.id)
        .join(Team, Team.role_version_id == RoleVersion.id)
        .where(Team.project_id == project_id)
    ).scalars().all())


def _check_review_seat(session: Session, doc: ConstitutionDoc, teams: list[TeamSpec],
                       *, solo: bool = False) -> None:
    """A project that asks for an independent read must hire someone who can
    give it one.

    This is the whole reason the rule is enforced HERE rather than in a prompt:
    the form, the Founder and `mcp__yaaf__create_project` all build a
    ProjectCreate and all land on this endpoint, so a check written once covers
    three front doors. The same sentence written into three prompts covers
    whichever one the author remembered.

    Two shapes are exempt, and the second one cost a green test to find.

    A spec with no teams at all is refused too. It used to be exempt on the
    reasoning that refusing there answers a question about staffing before
    anyone has staffed anything — but the rule then bound nothing, forever,
    because no later route re-checked it. A project asking for a read with an
    empty roster is asking for something it has not staffed.

    **Solo mode is exempt because an independent read is impossible in it.**
    The CEO *is* the team — one brain plans and executes — so there is no
    second reader to hire, and a rule demanding one would make solo projects
    uncreatable rather than reviewed. The setting is stored as the chairman
    wrote it and simply has nobody to bind; the form says so where he picks it.
    """
    if doc.deliverable_review != "independent_read" or solo:
        return
    _refuse_without_review_seat(session, {spec.role_slug for spec in teams})

#: What "this matters" buys a producing seat. Only ever upward: a role already
#: at or above these keeps what it has.
CRAFT_MODEL = "claude-opus-5"
CRAFT_EFFORT = "xhigh"
_EFFORT_RANK = {name: i for i, name in enumerate(EFFORTS)}

#: Weakest to strongest, for the same "never downward" reason as the effort
#: rank beside it. A model this install has never heard of ranks 0, so it is
#: lifted rather than left — an unknown string is not evidence of strength.
_MODEL_RANK = {
    "claude-haiku-4-5": 1,
    "claude-sonnet-5": 2,
    "claude-opus-5": 3,
    "claude-fable-5-1": 4,
}


def _craft_lift(
    session: Session, slug: str, version: RoleVersion, cons: ConstitutionDoc
) -> tuple[str | None, str | None]:
    """The model and effort a high-stakes project hires this seat on.

    Resolved HERE, at hire, and stamped onto the agent row — not read at
    dispatch. Three reasons. The runner stays a lookup with no policy in it;
    the choice is visible in the roster and in an exported bundle rather than
    implied by a rule somebody has to go and read; and the chairman can still
    hand-adjust one seat afterwards without fighting the setting that put it
    there.

    Returns what to stamp, or (None, None) to leave the role's own answer
    alone. Never returns something weaker than the role already has: a dial
    labelled "how much this matters" that could quietly downgrade a seat would
    be a trap.
    """
    if cons.stakes != "high":
        return None, None
    role = session.execute(select(Role).where(Role.slug == slug)).scalar_one_or_none()
    if role is None or role.category not in CRAFT_CATEGORIES:
        return None, None
    model = None if _MODEL_RANK.get(version.model, 0) >= _MODEL_RANK[CRAFT_MODEL] \
        else CRAFT_MODEL
    at_least = _EFFORT_RANK.get(CRAFT_EFFORT, 0)
    effort = (None if _EFFORT_RANK.get(version.effort, 0) >= at_least else CRAFT_EFFORT)
    return model, effort


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
    for spec in body.teams:
        _check_team_people(spec)
    # The constitution freezes at creation. Overrides are validated against the
    # schema; a spec with no opinion about the authority dial is given one
    # here, so the form, the Founder, the MCP surface, a spec file and the fire
    # drill all create the same project from the same silence.
    #
    # Built here rather than after the insert because the roster check below is
    # a refusal, and a refusal that fires after rows are written is a rollback
    # waiting to be got wrong.
    doc = ConstitutionDoc.model_validate(
        {**ConstitutionDoc().model_dump(), **default_autonomy(body.constitution)})
    _check_review_seat(session, doc, body.teams, solo=body.solo)
    deadline = body.deadline
    deadline_source = None
    if deadline is not None:
        if body.mode == "operate":
            raise HTTPException(
                status_code=422,
                detail="Operate runs on cycles, and a cycle carries its own due-by "
                       "with the source it came from — set that with cycle_goal "
                       "rather than a second date on the project.",
            )
        deadline_source = _deadline_source(body.mode, body.deadline_source)
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
        github_repo=body.github_repo,
        integrations=body.integrations,
        deadline=deadline,
        deadline_source=deadline_source,
        topology=topology,
    )
    session.add(project)
    session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=chairman.id, role="chairman"))

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
        model_override=body.ceo_model_override,
        effort_override=body.ceo_effort_override,
        daily_at=body.ceo_daily_at,
    )
    session.add(ceo)
    session.add(Channel(id=ids.new_id(ids.CHANNEL), project_id=project.id,
                        kind=ChannelKind.BOARDROOM))
    session.add(Channel(id=ids.new_id(ids.CHANNEL), project_id=project.id,
                        kind=ChannelKind.LEADERSHIP))

    team_out = []
    for spec in body.teams:
        role_version = _latest_role_version(session, spec.role_slug)
        lift_model, lift_effort = _craft_lift(session, spec.role_slug, role_version, doc)
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
                daily_at=spec.schedules.get(employee_name),
                # What the chairman named for this person wins; the stakes lift
                # only fills a silence. Someone who deliberately put one seat on
                # a cheaper model must not have it undone by a dial about the
                # project as a whole.
                model_override=spec.model_overrides.get(employee_name) or lift_model,
                effort_override=spec.effort_overrides.get(employee_name) or lift_effort,
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

    # The boardroom on day one, when the chairman has already told us the
    # workspace. The ask behind app-wide Slack settings is "I made a project —
    # tell me where to talk to it", and that is one room: the boardroom. The
    # other rooms are half a dozen channels a workspace does not want until the
    # project is actually being run from Slack, so they stay behind the button
    # on the project's own Slack screen. Best-effort by construction: a Slack
    # outage costs the channel map, never the project. Runs after the flush
    # above so the deferred rooms can be named accurately.
    session.flush()
    from yaaf.server.slack import provision as slack_provision

    slack = slack_provision.provision_quietly(session, project)
    if slack is not None:
        _audit(session, project.id, "slack.provision_channels", "project", project.id,
               {"auto": True, "channels": [{"key": row["key"], "name": row["name"],
                                            "created": row["created"]}
                                           for row in slack["channels"]],
                "deferred": [row["key"] for row in slack.get("deferred", [])]})

    _audit(session, project.id, "project.create", "project", project.id)
    return {"id": project.id, "ceo_agent_id": ceo.id, "teams": team_out,
            "constitution": doc.model_dump(), "status": project.status,
            "slack": slack,
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
# Slack wiring and webhook signing secrets. Everything else in integrations
# (runtime, data sources…) travels with the bundle. A full backup
# (include_values=true) keeps all of them.
_NON_PORTABLE_INTEGRATION_KEYS = frozenset(
    {"slack", "github_webhook_secret", "webhook_secret"}
)


@router.get("/projects/{project_id}/export")
def export_project(project_id: str, include_values: bool = False,
                   session: Session = Depends(db)) -> dict:
    """The project as a setup file, in one of two strengths.

    Default: the portable *design*. The `project` section is a ready
    ProjectCreate body (the importer supplies slug and chairman); role
    definitions and credential *names* ride along so another install can
    rebuild the org, while secret values stay behind — they are encrypted
    with this deployment's master key and an import asks for them again.
    This is the shape the seed (wipe-survival) machinery stores.

    `include_values=true`: the full backup, made at the chairman's explicit
    request. Every credential is decrypted into the file — service keys, the
    billing key, Slack tokens, webhook signing keys, OAuth wiring — so a
    restore recreates the whole project with nothing to re-enter. The file is
    plaintext; the button that produces one says exactly what is inside.

    The New Project page accepts either file."""
    project = _get_project(session, project_id)
    chairman = session.get(User, project.chairman_user_id)
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
            "category": role.category,
            "domains": role.domains or [],
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
            # The people's settings, keyed by name: chairman-set daily wakes
            # and per-agent model overrides. Losing these on a round trip is
            # losing a standup nobody notices is gone until 09:00 passes.
            "schedules": {a.name: a.daily_at for a in members if a.daily_at},
            "model_overrides": {a.name: a.model_override
                                for a in members if a.model_override},
            "effort_overrides": {a.name: a.effort_override
                                 for a in members if a.effort_override},
        })

    billing_secret_name = None
    if include_values:
        # The backup half: everything travels, decrypted. Only data-source
        # check verdicts stay behind (stale claims about this install's
        # network — the import proves reachability itself).
        from yaaf.core.crypto import decrypt

        integrations = dict(project.integrations or {})
        if "mcp_servers" in integrations:
            integrations["mcp_servers"] = [
                {k: v for k, v in entry.items() if k != "last_check"}
                for entry in integrations["mcp_servers"]
            ]
        secret_out = [
            {"name": s.name, "role_scopes": s.role_scopes, "kind": s.kind,
             "value": decrypt(s.ciphertext)}
            for s in session.execute(
                select(Secret)
                .where(Secret.project_id == project.id)
                .order_by(Secret.created_at)
            ).scalars()
        ]
        if project.billing_secret_id:
            attached = session.get(Secret, project.billing_secret_id)
            billing_secret_name = attached.name if attached else None
    else:
        integrations = {k: v for k, v in (project.integrations or {}).items()
                        if k not in _NON_PORTABLE_INTEGRATION_KEYS}
        # OAuth state is install-bound twice over: the registered client names
        # THIS install's redirect URI, and the tokens name this install's
        # consent. The `auth` marker travels so the importing side knows to
        # offer Connect.
        oauth_bound_secrets: set[str] = set()
        if "mcp_servers" in integrations:
            for entry in integrations["mcp_servers"]:
                if _source_auth(entry) == "oauth":
                    oauth_bound_secrets.update(_oauth_secret_names(entry))
            integrations["mcp_servers"] = [
                {k: v for k, v in entry.items()
                 if k not in ("last_check", "oauth", "bearer_secret")
                 or (k == "bearer_secret" and _source_auth(entry) != "oauth")}
                for entry in integrations["mcp_servers"]
            ]

        # Billing credentials stay out entirely: their values cannot travel,
        # and the twin connects its own in Settings (the page already
        # insists). OAuth token/client secrets stay out too — "paste the
        # refresh token" is not a request a human can meet; the import
        # re-connects instead.
        secret_out = [
            {"name": s.name, "role_scopes": s.role_scopes, "kind": s.kind}
            for s in session.execute(
                select(Secret)
                .where(Secret.project_id == project.id, Secret.kind == "service")
                .order_by(Secret.created_at)
            ).scalars()
            if s.name not in oauth_bound_secrets
        ]

    _audit(session, project.id, "project.export", "project", project.id,
           {"include_values": include_values})
    return {
        "yaaf_bundle": 1,
        "kind": "project_setup",
        "source_slug": project.slug,
        "exported_at": utcnow().isoformat(),
        "values_included": include_values,
        "project": {
            "slug": project.slug,
            "name": project.name,
            "goal": project.goal,
            "billing_mode": project.billing_mode,
            "mode": project.mode,
            "controlled_domains": project.controlled_domains,
            "github_repo": project.github_repo,
            "deadline": project.deadline.isoformat() if project.deadline else None,
            "chairman_email": chairman.email if chairman else None,
            "constitution": effective(session, project.id).model_dump(),
            "integrations": integrations,
            "billing_secret_name": billing_secret_name,
            "ceo_role_slug": ceo_role_slug,
            "ceo_name": ceo.name,
            "ceo_daily_at": ceo.daily_at,
            "ceo_model_override": ceo.model_override,
            "ceo_effort_override": ceo.effort_override,
            "teams": teams,
            "solo": ceo.team_id is not None,
        },
        "roles": list(roles.values()),
        "secrets": secret_out,
    }


# Config a twin must not inherit: this project's own webhook signing keys.
# Slack is handled separately below — its tokens travel, its room bindings do
# not.
_NON_CLONABLE_INTEGRATION_KEYS = frozenset({"github_webhook_secret", "webhook_secret"})


class ProjectCloneIn(BaseModel):
    slug: str = Field(pattern="^[a-z0-9-]{2,64}$")
    name: str | None = None  # defaults to the source's name plus " (clone)"
    # The usual reason to duplicate a project is to point it at the next thing.
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
    two projects posting into one room is nobody's idea of a twin.
    """
    source = _get_project(session, project_id)
    if session.execute(select(Project).where(Project.slug == body.slug)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="project slug already exists")
    # A clone copies the constitution and the roster verbatim, so a source that
    # cannot keep its own rule would hand the twin the same broken promise. It
    # only reaches here from a project amended before that door was shut, and
    # duplicating that state is how one install's mistake becomes two.
    if (effective(session, source.id).deliverable_review == "independent_read"
            and not _read_is_impossible(session, source.id)):
        _refuse_without_review_seat(session, _project_role_slugs(session, source.id))

    integrations = {k: v for k, v in (source.integrations or {}).items()
                    if k not in _NON_CLONABLE_INTEGRATION_KEYS}
    slack_rooms_dropped = bool((integrations.get("slack") or {}).get("channel_map"))
    if "slack" in integrations:
        # The bridge is inert without a channel map, so the twin mirrors
        # nowhere until the chairman maps rooms — with the tokens already there.
        integrations["slack"] = {**integrations["slack"], "channel_map": {}}

    # Not inheriting the source's signing keys is right — they are its own, and
    # sharing them would let either project's senders attest evidence for the
    # other. Leaving the twin with *none* is not: github_repo and the controlled
    # domains do come across, so the twin looks wired to GitHub while no inbound
    # event could ever be attested. Every other birth mints these (see
    # create_project); a twin is no less born. The values are readable any time
    # from the twin's own Settings, so nothing here has to be shown once.
    integrations["github_webhook_secret"] = pysecrets.token_hex(32)
    integrations["webhook_secret"] = pysecrets.token_hex(32)

    # A twin of a Deliver project that comes back as an Explore one is not a
    # twin: mode decides whether the project has requirements at all, and it is
    # frozen at creation — so getting it wrong here cannot be corrected later,
    # only deleted and redone. It defaults to EXPLORE on the model, which is
    # what an omission silently produced.
    #
    # The deadline is Explore's window, and copying one verbatim can hand the
    # twin a date that has already passed — a project the janitor closes at
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
        # claiming a hierarchy it does not have. Like mode, it is frozen at
        # creation, so getting it wrong here cannot be corrected later.
        topology=source.topology,
        deadline=None if deadline_expired else source.deadline,
        deadline_source=None if deadline_expired else source.deadline_source,
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
    #
    # OAuth data-source tokens are the one exception: providers rotate refresh
    # tokens on use, so two projects sharing one would invalidate each other's
    # copy on the first refresh. The registered client (id + secret) does come
    # across — same install, same redirect URI, one registration serves both —
    # so reconnecting the twin is a single Connect click, not a re-setup.
    oauth_token_names: set[str] = set()
    for entry in (source.integrations or {}).get("mcp_servers", []):
        if _source_auth(entry) == "oauth":
            oauth = entry.get("oauth") or {}
            oauth_token_names.update(
                n for n in (entry.get("bearer_secret"), oauth.get("refresh_secret")) if n)
    ciphertexts: dict[str, str] = {}
    secret_ids: dict[str, str] = {}
    for row in session.execute(
        select(Secret).where(Secret.project_id == source.id).order_by(Secret.created_at)
    ).scalars():
        if row.name in oauth_token_names:
            continue
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
                status=source_ceo.status,
                model_override=source_ceo.model_override,
                effort_override=source_ceo.effort_override,
                daily_at=source_ceo.daily_at)
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
        # silently upgrades its roles is not running the same project.
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
                          model_override=source_agent.model_override,
                          effort_override=source_agent.effort_override,
                          daily_at=source_agent.daily_at)
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
        if _source_auth(entry) == "oauth":
            # Its tokens deliberately did not come across (see the copy loop);
            # keep the endpoints and client so Connect is one click, drop
            # every reference to token rows the twin does not have.
            entry.pop("bearer_secret", None)
            entry["oauth"] = {
                **{k: v for k, v in (entry.get("oauth") or {}).items()
                   if k in ("authorization_endpoint", "token_endpoint",
                            "registration_endpoint", "client_id",
                            "client_secret_secret", "resource", "scope")},
                "status": "needs_auth",
            }
            check = {"ok": False,
                     "detail": "OAuth sources need reconnecting on the twin — press Connect"}
        elif not url_cipher:
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


def _void_pending_approvals(session: Session, project_id: str, why: str) -> int:
    """Close out a terminal project's open requests. Ending a project answers
    them by making them moot, and leaving them PENDING has two costs: they keep
    their clock, so the janitor expires them days later and writes a denial into
    the record of a project that no longer exists — and until then they sit on
    the chairman's desk asking for a decision that cannot matter.

    Always EXPIRED, never the constitution's `approval_timeout_decision`. An
    install that auto-approves on timeout must not approve a $4,800 spend
    because the project it belonged to was shut down: ending a project decides
    nothing on the merits, so the deny-shaped outcome is the only honest one.

    No notice to the requester, unlike every other approval outcome. A notice is
    consumed by a run, and a terminal project never runs again — the audit row
    is the record here.
    """
    n = 0
    now = utcnow()
    for approval in session.execute(
        select(Approval).where(
            Approval.project_id == project_id,
            Approval.status == ApprovalStatus.PENDING,
        )
    ).scalars():
        approval.status = ApprovalStatus.EXPIRED
        approval.decision_note = f"voided unanswered: {why}"
        approval.decided_at = now
        n += 1
    return n


@router.post("/projects/{project_id}/teams")
def add_team(project_id: str, body: TeamSpec, session: Session = Depends(db)) -> dict:
    """Add a team to an existing project: agents, derived lead, team channel."""
    if not body.employees:
        raise HTTPException(status_code=422, detail="a team needs at least one employee")
    project = _get_project(session, project_id)
    _check_team_people(body)
    role_version = _latest_role_version(session, body.role_slug)
    # A team hired later is hired on the same terms as one hired at birth. It
    # used to drop the per-employee settings on the floor — the TeamSpec
    # carried them, this route ignored them — so a team added on Tuesday came
    # back without the wakes and models its spec named.
    cons = effective(session, project.id)
    lift_model, lift_effort = _craft_lift(session, body.role_slug, role_version, cons)
    # The one route that can satisfy the review rule after creation, so it is
    # also the one that must not leave a project able to satisfy it and still
    # not doing so. Checked AFTER this team is counted, since adding the
    # reviewer is exactly how a project fixes it — and skipped entirely while
    # the project is still solo, which is the shape that has nowhere to put a
    # reader and is exempt at creation for the same reason.
    if cons.deliverable_review == "independent_read" and not _read_is_impossible(
            session, project.id):
        _refuse_without_review_seat(
            session, _project_role_slugs(session, project.id) | {body.role_slug})
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
            daily_at=body.schedules.get(employee_name),
            model_override=body.model_overrides.get(employee_name) or lift_model,
            effort_override=body.effort_overrides.get(employee_name) or lift_effort,
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
    # The tools this source may be called with. None on a NEW source means
    # unrestricted — every install that predates this keeps behaving exactly as
    # it did. None on an EDIT keeps whatever allowlist the source already has,
    # for the same reason `bearer` does: a save that only regrants roles must
    # never silently disarm enforcement. An empty list is the strict end of the
    # range, not a way to clear it — no tool may be called without an approval.
    # To go back to unrestricted, delete the source and add it again.
    tools: list[str] | None = None
    # None means "infer from bearer" so every existing caller keeps meaning
    # what it always meant; "oauth" switches the source to the browser
    # sign-in flow, where the token secret holds a refreshed access token
    # instead of a pasted credential.
    auth: Literal["none", "bearer", "oauth"] | None = None
    # Only for OAuth servers without dynamic client registration: credentials
    # from the provider's developer console. Ignored for other auth modes.
    client_id: str | None = None
    client_secret: str | None = None
    scope: str | None = None


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
        oauth = source.get("oauth") or {}
        if secret_name in (source.get("url_secret"), source.get("bearer_secret"),
                           oauth.get("refresh_secret"), oauth.get("client_secret_secret")):
            return source.get("name")
    return None


def _source_auth(entry: dict) -> str:
    """The source's auth mode, with the legacy inference spelled out once:
    entries saved before OAuth existed carry no `auth` key, and for them a
    stored token means bearer."""
    return entry.get("auth") or ("bearer" if entry.get("bearer_secret") else "none")


def _oauth_secret_names(entry: dict) -> list[str]:
    """Every secret an OAuth source owns beyond its URL: the access token and,
    when present, the refresh token and registered client secret."""
    oauth = entry.get("oauth") or {}
    return [name for name in (entry.get("bearer_secret"),
                              oauth.get("refresh_secret"),
                              oauth.get("client_secret_secret")) if name]


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
                return {"ok": False, "code": "unauthorized",
                        "detail": f"the server refused the credential (HTTP "
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


def _oauth_checked(url: str, token: str, entry: dict, transport=None) -> dict:
    """The ordinary reachability check, with its 401 advice corrected for
    OAuth: "check the bearer token" tells the chairman to paste something,
    and there is nothing to paste — the fix is a fresh sign-in."""
    check = _check_data_source(url, token, transport)
    if check.get("code") == "unauthorized":
        entry["oauth"] = {**(entry.get("oauth") or {}), "status": "needs_reauth"}
        check = {**check, "detail": "the server rejected the access token — press Reconnect"}
    return check


@router.post("/projects/{project_id}/mcp_servers")
def add_data_source(project_id: str, body: DataSourceIn, session: Session = Depends(db)) -> dict:
    """One form, everything wired: validates the URL at the door, stores it
    (and any token) as role-granted secrets, records the mapping in project
    config, and preflights reachability. No role edit, no repin, no CLI.

    Saving under an existing name is the edit path, so `bearer` reads as:
    omitted = keep the current token, `""` = remove it, anything else = rotate.
    A URL-only edit that dropped the auth header would fail hours later inside
    an agent run, which is exactly the class of failure this form exists to
    prevent.

    OAuth sources follow the same shape with a browser sign-in where the paste
    would be: saving records the mode and answers `needs_connect`, the Connect
    button walks the sign-in, and from then on the token secret holds an access
    token this server refreshes — an edit that only regrants roles must never
    force the chairman back through the provider's consent screen."""
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
    # `auth` reads like `bearer` does: absent keeps an existing source's mode
    # (so pre-OAuth callers keep meaning what they always meant), a pasted
    # token implies bearer, and a brand-new source with neither is open.
    if body.auth:
        auth = body.auth
    elif body.bearer:
        auth = "bearer"
    elif prior is not None and body.bearer is None:
        auth = _source_auth(prior)
    else:
        auth = "none"

    if prior is not None and _source_auth(prior) == "oauth" and auth != "oauth":
        # Switching away from OAuth: its tokens are not pasted credentials and
        # must not survive as one — drop them all and let the new mode start clean.
        for stale in _oauth_secret_names(prior):
            _delete_project_secret(session, project.id, stale)
        prior = {k: v for k, v in prior.items() if k not in ("bearer_secret", "oauth")}

    url_secret = _upsert_project_secret(
        session, project.id, f"mcp-{body.name}-url", body.url.strip(), body.roles)
    entry = {
        "name": body.name,
        "url_secret": url_secret.name,
        "roles": body.roles,
        # Host/port recorded in config so the chairman can see where a source
        # points without decrypting anything.
        "url_host": parsed.hostname,
        "url_port": parsed.port or (443 if parsed.scheme == "https" else 80),
        "access": body.access,
        "auth": auth,
    }
    allowlist = body.tools if body.tools is not None else (prior or {}).get("tools")
    if allowlist is not None:
        entry["tools"] = sorted({t.strip() for t in allowlist if t.strip()})

    needs_connect = False
    if auth == "oauth":
        token = None
        carried_oauth = dict((prior or {}).get("oauth") or {}) \
            if prior is not None and _source_auth(prior) == "oauth" else {}
        # Only decrypted here, in the one branch that compares it — ordinary
        # bearer/none saves must not pay a secret read they never look at.
        prior_url = (_project_secret_value(session, project.id, prior["url_secret"])
                     if prior and prior.get("url_secret") else None)
        if carried_oauth and (prior_url or "").strip() == body.url.strip():
            # Same source, same address: the connection survives the edit. The
            # access token is re-stored so its grants track this save's roles.
            oauth_state = carried_oauth
            held = prior.get("bearer_secret")
            carried = _project_secret_value(session, project.id, held) if held else None
            if carried is not None:
                _upsert_project_secret(session, project.id, held, carried, body.roles)
                entry["bearer_secret"] = held
                token = carried
        else:
            # New source, changed address, or a switch from pasted-token auth:
            # any tokens that exist were minted for something else. Start over.
            oauth_state = {"status": "needs_auth", "resource": body.url.strip()}
            if prior is not None:
                for stale in _oauth_secret_names(prior):
                    _delete_project_secret(session, project.id, stale)
        if body.client_id:
            oauth_state["client_id"] = body.client_id
        if body.client_secret:
            client_row = _upsert_project_secret(
                session, project.id, f"mcp-{body.name}-oauth-client", body.client_secret, [])
            oauth_state["client_secret_secret"] = client_row.name
        if body.scope is not None:
            oauth_state["scope"] = body.scope or None
        entry["oauth"] = oauth_state
        needs_connect = token is None or oauth_state.get("status") != "connected"
        if token:
            check = _oauth_checked(body.url.strip(), token, entry)
        else:
            check = {"ok": False, "detail": "OAuth connection required — press Connect"}
    else:
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
            "access": body.access, "auth": auth, "check_ok": check["ok"],
            "tools": entry.get("tools")})
    return {"name": body.name, "check": check, "needs_connect": needs_connect,
            "note": "Effective on each granted role's next run."}


@router.delete("/projects/{project_id}/mcp_servers/{name}")
def delete_data_source(project_id: str, name: str, session: Session = Depends(db)) -> dict:
    project = _get_project(session, project_id)
    servers = (project.integrations or {}).get("mcp_servers", [])
    entry = next((s for s in servers if s.get("name") == name), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown data source")
    for secret_name in {entry.get("url_secret"), *_oauth_secret_names(entry)}:
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
    if _source_auth(entry) == "oauth":
        from yaaf.server.mcp_oauth import ensure_fresh_token

        token = ensure_fresh_token(session, project, entry)
        if token is None:
            check = {"ok": False, "detail": "OAuth connection required — press Connect"}
        else:
            check = _oauth_checked(url, token, entry)
    else:
        check = _check_data_source(url, value_of(entry.get("bearer_secret")))
    entry["last_check"] = {**check, "at": utcnow().isoformat()}
    # Through persist_entry, not a bare reassignment: `entry` was edited in
    # place, so the reassigned value compares equal to the loaded one and a
    # plain assignment is silently skipped at flush.
    from yaaf.server.mcp_oauth import persist_entry

    persist_entry(project, entry)
    _audit(session, project.id, "mcp_server.check", "project", project.id,
           {"name": name, "ok": check["ok"]})
    return check


@router.post("/projects/{project_id}/mcp_servers/{name}/oauth/start")
def start_data_source_oauth(project_id: str, name: str, request: Request,
                            session: Session = Depends(db)) -> dict:
    """Begin the browser sign-in for an OAuth data source: discover the
    authorization server, register a client there (or reuse one the chairman
    pasted), and mint the URL the chairman's browser must visit. The `state`
    in that URL is single-use and 256 bits — it is the whole credential the
    unauthenticated callback route later redeems, which is why a fresh call
    here voids any earlier half-finished sign-in."""
    from yaaf.core.config import settings
    from yaaf.core.crypto import encrypt
    from yaaf.server import mcp_oauth

    project = _get_project(session, project_id)
    servers = (project.integrations or {}).get("mcp_servers", [])
    entry = next((s for s in servers if s.get("name") == name), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown data source")
    if _source_auth(entry) != "oauth":
        raise HTTPException(status_code=422, detail="this data source is not set to OAuth "
                                                    "sign-in — edit it and pick OAuth first")
    url = _project_secret_value(session, project.id, entry.get("url_secret") or "")
    if not url:
        raise HTTPException(status_code=422, detail="the data source's URL secret is missing")

    # The browser-facing origin. Deriving it from the request is a dev
    # convenience; behind a TLS-terminating proxy it reports http, so
    # production sets YAAF_PUBLIC_BASE_URL.
    base = (settings().public_base_url or str(request.base_url)).rstrip("/")
    redirect_uri = f"{base}/oauth/mcp/callback"

    oauth = dict(entry.get("oauth") or {})
    try:
        disc = mcp_oauth.discover(url)
        if not oauth.get("client_id"):
            client_id, client_secret = mcp_oauth.register_client(disc, redirect_uri)
            oauth["client_id"] = client_id
            if client_secret:
                client_row = _upsert_project_secret(
                    session, project.id, f"mcp-{name}-oauth-client", client_secret, [])
                oauth["client_secret_secret"] = client_row.name
    except mcp_oauth.OAuthSetupError as exc:
        entry["oauth"] = {**oauth, "error": str(exc)}
        mcp_oauth.persist_entry(project, entry)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    state = pysecrets.token_urlsafe(32)
    verifier = pysecrets.token_urlsafe(64)
    oauth.update({
        "status": "pending",
        "authorization_endpoint": disc.authorization_endpoint,
        "token_endpoint": disc.token_endpoint,
        "registration_endpoint": disc.registration_endpoint,
        "resource": disc.resource,
        # The PKCE verifier is a secret with a fifteen-minute life; it still
        # never touches the config JSON in plaintext.
        "pending": {"state": state, "verifier_cipher": encrypt(verifier),
                    "redirect_uri": redirect_uri, "created_at": utcnow().isoformat()},
    })
    oauth.pop("error", None)
    entry["oauth"] = oauth
    mcp_oauth.persist_entry(project, entry)
    authorize_url = mcp_oauth.build_authorize_url(
        disc, oauth["client_id"], redirect_uri, state, verifier, oauth.get("scope"))
    _audit(session, project.id, "mcp_server.oauth_start", "project", project.id,
           {"name": name, "authorization_endpoint": disc.authorization_endpoint})
    return {"authorize_url": authorize_url,
            "note": "Complete the sign-in in the opened tab, then test the source."}


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
    # project rather than constraining it, and needs no written reason.
    goal: str | None = Field(default=None, min_length=10)
    billing_mode: str | None = Field(default=None, pattern="^(api_key|subscription)$")
    billing_secret_id: str | None = None
    controlled_domains: list[str] | None = None
    # Merged into the existing integrations (outward wiring only — every
    # limit lives in the constitution and changes only through amendments).
    integrations: dict | None = None
    # Set a new window/target, or clear it with clear_deadline. A new date
    # needs its provenance on Deliver, exactly as at creation.
    deadline: datetime | None = None
    deadline_source: dict | None = None
    clear_deadline: bool = False


#: What a date can be. `external` is a fact the world imposes — a booking
#: window, a filing date, a client's meeting — and cites where it was read.
#: `chairman` is a target he wants, which is legitimate and is simply not the
#: same kind of object; it is recorded as a wish so nothing downstream can
#: quote it back as physics.
DEADLINE_SOURCE_KINDS = ("external", "chairman")


def _deadline_source(mode: str, source: dict | None) -> dict:
    """Validate a date's provenance, and refuse a Deliver date that has none.

    Deliver used to refuse a deadline outright — which relocated the date
    rather than removing it. The founder still had one in mind and wrote it
    into the goal prose, where nothing validates it, nothing re-projects it,
    and nothing compares it to the platform's own arithmetic. It paced the
    work anyway, and the measured result across this install is that every
    commitment was met the day it was made against promises of up to 127 days.

    So Deliver may carry a target, on the terms `set_cycle_goal` already sets
    for a cycle's due-by: say where it came from. Explore's bare window keeps
    working untouched — it has always been "open scope, bounded time", the
    chairman's own judgement call, and nothing here makes it stricter.
    """
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    if source is None:
        if mode == "explore":
            return {"kind": "chairman", "note": "Explore's window",
                    "recorded_at": _dt.now(_UTC).isoformat()}
        raise HTTPException(
            status_code=422,
            detail=(
                "A delivery date needs its provenance. A deadline the outside world "
                "imposes is a FACT — a booking window, a filing date, a client's "
                "meeting — so pass deadline_source={'kind': 'external', 'url': ..., "
                "'note': ...} and cite it. A date you simply want is also fine and is "
                "not the same object: pass {'kind': 'chairman', 'note': why}. The "
                "difference is load-bearing — the platform will not let a wish be "
                "quoted back to you as arithmetic, and the CEO is told which it is "
                "rather than pacing ten weeks of work to a number in a paragraph."
            ),
        )
    kind = (source.get("kind") or "").strip()
    if kind not in DEADLINE_SOURCE_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"deadline_source.kind must be one of {', '.join(DEADLINE_SOURCE_KINDS)}.",
        )
    if kind == "external" and not (source.get("url") or source.get("note")):
        raise HTTPException(
            status_code=422,
            detail=("kind='external' means the world imposed this date. Name the url "
                    "you read it from, or a note saying who or what imposed it — a "
                    "fact cites its source, and the next re-plan needs to know "
                    "whether it moved."),
        )
    return {
        "kind": kind,
        "url": source.get("url"),
        "note": source.get("note"),
        "recorded_at": _dt.now(_UTC).isoformat(),
    }


def _billing_shape_error(mode: str, plaintext: str) -> str | None:
    """Subscription tokens come from `claude setup-token` and start sk-ant-oat;
    API keys start sk-ant-api. A mismatched pair fails every future run with a
    baffling 'Not logged in' — refuse it while the human is still looking.

    The rule itself lives in `yaaf.core.platform` now, because the platform
    credential the Founder runs on needs the identical check and a chairman
    should not get a different sentence depending on which form he pasted into.
    """
    from yaaf.core.platform import shape_error

    return shape_error(mode, plaintext)


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
            f"The chairman changed this project's goal.\n\nIT IS NOW: {project.goal}\n\n"
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
        # Operate keeps the refusal: its dates belong to cycles, which already
        # carry their own due-by AND its provenance, and a second competing
        # date on the project would be exactly the ambiguity this all exists to
        # remove. Deliver is now allowed one, with a source (see
        # `_deadline_source`); Explore's bare window is unchanged.
        if project.mode == ProjectMode.OPERATE:
            raise HTTPException(
                status_code=422,
                detail="Operate runs on cycles, and a cycle carries its own due-by "
                       "with the source it came from — set that with cycle_goal "
                       "rather than a second date on the project.",
            )
        if body.clear_deadline:
            project.deadline = None
            project.deadline_source = None
            changed["deadline"] = None
        else:
            deadline = body.deadline
            project.deadline_source = _deadline_source(
                project.mode, body.deadline_source)
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
    voided = _void_pending_approvals(session, project.id, "the project was stopped")
    _audit(session, project.id, "project.stop", "project", project.id,
           {"revoked_runs": revoked, "voided_approvals": voided})
    return {"id": project.id, "status": project.status, "revoked_runs": revoked,
            "voided_approvals": voided}


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
    voided = _void_pending_approvals(session, project.id, "the project was completed")

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
    _audit(session, project.id, "project.complete", "project", project.id,
           {**receipt, "voided_approvals": voided})
    return {"id": project.id, "status": project.status, "receipt": receipt,
            "revoked_runs": revoked, "voided_approvals": voided}


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


class RehearseIn(BaseModel):
    # The same object `POST /admin/projects` takes, or a whole setup file with
    # it under a `project` key. Nothing is created here — it is handed to a
    # throwaway process that builds its own database and throws it away.
    spec: dict
    days: int = Field(default=30, ge=1, le=90)
    usd_per_run: float | None = Field(default=None, ge=0)


#: The drill is CPU-bound and short (a few seconds). This is the wall it dies
#: at, so a pathological spec cannot pin a request worker.
REHEARSAL_TIMEOUT_S = 180


def _carry_library_roles(session: Session, spec: dict) -> dict:
    """Put a definition in the spec for every role it hires from this library.

    The drill builds its own throwaway database, so the live library is not
    there to hire from: a role resolves only if the spec carries it, or if the
    process happens to be sitting in a checkout with a `roles/` directory. The
    CLI usually is. The server never is — the image ships `yaaf/`, not
    `roles/` — so the dashboard's drill could not resolve a single library
    role, and every spec that hired one reported a project that would not
    start. That is the one verdict a fire drill must never get wrong.

    Definitions already carried win: a setup file describing a role this
    install has not got is exactly the case the carried copy exists for.
    """
    project = spec.get("project") if isinstance(spec.get("project"), dict) else spec
    if not isinstance(project, dict):
        return spec
    named = {project.get("ceo_role_slug") or "ceo"}
    named.update(team.get("role_slug") for team in (project.get("teams") or [])
                 if isinstance(team, dict) and team.get("role_slug"))
    carried = [r for r in (spec.get("roles") or []) if isinstance(r, dict)]
    missing = named - {r.get("slug") for r in carried} - {None}
    if not missing:
        return spec
    out = list(carried)
    for slug in sorted(missing):
        role = session.execute(select(Role).where(Role.slug == slug)).scalar_one_or_none()
        if role is None:
            continue  # the drill reports it unresolved, which is the truth
        version = session.execute(
            select(RoleVersion)
            .where(RoleVersion.role_id == role.id)
            .order_by(RoleVersion.version.desc())
            .limit(1)
        ).scalar_one()
        out.append({
            "slug": role.slug,
            "name": role.name,
            "is_ceo_role": role.is_ceo_role,
            "category": role.category,
            "domains": role.domains or [],
            "description": version.description,
            "system_prompt": version.system_prompt,
            "model": version.model,
            "effort": version.effort,
            # Roles no longer own MCP servers; an old pinned version carrying
            # the key would 422 on the way into the drill's library.
            "tool_permissions": {k: v for k, v in (version.tool_permissions or {}).items()
                                 if k != "mcp_servers"},
            "secret_scopes": version.secret_scopes,
        })
    return {**spec, "roles": out}


@router.post("/rehearse")
def rehearse(body: RehearseIn, session: Session = Depends(db)) -> dict:
    """Run a spec as a project for nothing, and report what happened.

    **In a subprocess, always.** The drill re-points the process-global engine
    in :mod:`yaaf.core.db` at a throwaway SQLite file, and doing that inside
    the API process would re-point the *server* — every request after it would
    be answered from a scratch database. So the work happens in
    `python -m yaaf.rehearsal`, which owns its own globals and takes them to
    the grave. The drill also asserts its database is the file it created and
    refuses to start otherwise, so the isolation is checked at both ends.

    The spec travels on a temp file rather than argv: it can be tens of
    kilobytes, and a process list is not a place to put a chairman's goal.

    So does one more thing the child could not otherwise know: what this
    install already has wired. The drill has no live database on purpose, so
    left to itself it reports "Slack not exercised" identically whether a
    workspace is connected or not — and a chairman who had connected one was
    being told to go and attach it. This is the one place that holds both a
    live session and the drill, so it reads the facts here and hands them over
    as data. See :mod:`yaaf.rehearsal.install`.
    """
    import json as jsonlib
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    from yaaf.rehearsal import install as install_facts

    with tempfile.TemporaryDirectory(prefix="yaaf-rehearse-") as tmp:
        spec_path = Path(tmp) / "spec.json"
        spec_path.write_text(jsonlib.dumps(_carry_library_roles(session, body.spec)))
        argv = [sys.executable, "-m", "yaaf.rehearsal", str(spec_path),
                "--json", "--days", str(body.days)]
        try:
            install_path = Path(tmp) / "install.json"
            install_path.write_text(jsonlib.dumps(install_facts.collect(session)))
            argv += ["--install", str(install_path)]
        except Exception:  # pragma: no cover - the verdict is not worth a footnote
            # The wiring note is a courtesy; the drill's verdict is the product.
            # Without it the report says nothing either way about Slack, which
            # is what it said before this existed.
            log.warning("rehearsal: could not read this install's wiring",
                        exc_info=True)
        if body.usd_per_run:
            argv += ["--usd-per-run", str(body.usd_per_run)]
        # The child never reads this, because the drill points itself at the
        # file it creates — it is set anyway so that a future code path which
        # DID read it could not reach the live database by accident.
        env = {**os.environ, "YAAF_DATABASE_URL": f"sqlite:///{tmp}/unused.db"}
        try:
            done = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=REHEARSAL_TIMEOUT_S, env=env, check=False)
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(
                status_code=504,
                detail=(f"the rehearsal did not finish within {REHEARSAL_TIMEOUT_S}s. "
                        "That is itself a finding — something in this spec does not "
                        "settle."),
            ) from exc
    if not done.stdout.strip():
        raise HTTPException(
            status_code=500,
            detail=f"the rehearsal produced nothing: {(done.stderr or '')[-500:]}")
    try:
        return jsonlib.loads(done.stdout)
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"the rehearsal produced unreadable output: {done.stdout[:300]}",
        ) from exc


class BriefingSeen(BaseModel):
    # The mark is normally "now", but a client that rendered a briefing a
    # moment ago should stamp the window IT showed rather than the instant the
    # button was pressed — otherwise anything that landed while the panel was
    # on screen is silently swallowed.
    until: datetime | None = None


@router.post("/projects/{project_id}/briefing/seen")
def briefing_seen(project_id: str, body: BriefingSeen | None = None,
                  session: Session = Depends(db)) -> dict:
    """Move the chairman's read mark for one project.

    Deliberately an explicit act, not a side effect of fetching the briefing:
    the desk polls, and a mark that moved on every poll would mean the window
    was always empty and "since you were last here" would never survive a tab
    left open. Nothing else in the system reads this column, so a wrong stamp
    costs one window of narrative and no governance.
    """
    project = _get_project(session, project_id)
    at = (body.until if body and body.until else None)
    if at is not None and at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    stamped = briefing.mark_read(session, project, at)
    return {"ok": stamped, "project_id": project.id,
            "last_briefed_at": (at or utcnow()).isoformat() if stamped else None}


# ── approvals, hypotheses, amendments, threads ─────────────────────────────────


class ApprovalDecision(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=4000)


def _apply_decision(session: Session, approval: Approval, project: Project,
                    approve: bool, note: str, notice_body: str,
                    audit_detail: dict | None = None) -> None:
    """ONE implementation of "an approval is decided": status, timestamps,
    the requester's wake notice, the audit row, the wake. decide_approval and
    fulfill_need both land here, so decision semantics can never fork."""
    approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
    approval.decided_at = utcnow()
    approval.decision_note = note
    approval.decided_by_user_id = project.chairman_user_id
    session.add(
        Notice(
            id=ids.new_id(ids.NOTICE),
            project_id=approval.project_id,
            agent_id=approval.requested_by_agent_id,
            kind=NoticeKind.APPROVAL_DECIDED,
            body=notice_body,
        )
    )
    _audit(session, approval.project_id, "approval.decide", "approval", approval.id,
           {"approve": approve, **(audit_detail or {})})
    notify(session, WAKE_CHANNEL, approval.project_id)


class ApprovalExtend(BaseModel):
    days: int = Field(ge=1, le=30)
    reason: str = Field(min_length=3, max_length=500)


@router.post("/approvals/{approval_id}/extend")
def extend_approval(
    approval_id: str, body: ApprovalExtend, session: Session = Depends(db)
) -> dict:
    """Move a pending approval's deadline out. For the receiver that could not
    reach the chairman — the clock should not run while nobody could see it.
    The nag re-arms so the new deadline gets its own chase."""
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="unknown approval")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"approval already {approval.status}")
    now = utcnow()
    was = approval.expires_at
    approval.expires_at = max(approval.expires_at, now) + timedelta(days=body.days)
    approval.nag_sent_at = None
    _audit(session, approval.project_id, "approval.extend", "approval", approval.id,
           {"days": body.days, "reason": body.reason,
            "from": was.isoformat(), "to": approval.expires_at.isoformat()})
    return {"id": approval.id, "expires_at": approval.expires_at.isoformat()}


@router.post("/approvals/{approval_id}/decide")
def decide_approval(
    approval_id: str, body: ApprovalDecision, session: Session = Depends(db)
) -> dict:
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="unknown approval")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"approval already {approval.status}")
    project = session.get(Project, approval.project_id)
    _apply_decision(
        session, approval, project, body.approve, body.note,
        notice_body=(f"Approval '{approval.title}' was "
                     f"{'APPROVED' if body.approve else 'DENIED'}"
                     + (f": {body.note}" if body.note else ".")),
    )
    return {"id": approval.id, "status": approval.status}


@router.get("/projects/{project_id}/accounts")
def list_accounts(project_id: str, session: Session = Depends(db)) -> list[dict]:
    """The account registry: everything the project answers to out in the
    world. Record-only by decision — winding down is the CEO's call while the
    project runs, and the chairman's reading when he stops it for good."""
    from yaaf.core.models import Account

    _get_project(session, project_id)
    rows = session.execute(
        select(Account).where(Account.project_id == project_id)
        .order_by(Account.created_at)
    ).scalars()
    return [{"id": a.id, "service": a.service, "username": a.username,
             "owner": a.owner, "credential_name": a.credential_name,
             "url": a.url, "requirement_id": a.requirement_id, "note": a.note,
             "created_at": a.created_at.isoformat()} for a in rows]


class AgentSchedule(BaseModel):
    # "HH:MM" in the constitution's timezone, or null to clear.
    daily_at: str | None = Field(default=None, pattern="^([01]?\\d|2[0-3]):[0-5]\\d$")


@router.patch("/agents/{agent_id}/schedule")
def set_agent_schedule(
    agent_id: str, body: AgentSchedule, session: Session = Depends(db)
) -> dict:
    """A per-agent daily wake — the CEO's standup generalized to any agent
    (stage E). Fires through the same heartbeat dedup and wake-rate brakes;
    every firing is still a paid model call, which is why it is the
    chairman's to set."""
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="unknown agent")
    was = agent.daily_at
    agent.daily_at = body.daily_at
    _audit(session, agent.project_id, "agent.schedule", "agent", agent.id,
           {"name": agent.name, "was": was, "now": body.daily_at})
    return {"id": agent.id, "name": agent.name, "daily_at": agent.daily_at}


class NeedFulfillIn(BaseModel):
    secret_name: str = Field(min_length=2, max_length=120)
    value: str = Field(min_length=1)
    roles: list[str] = Field(min_length=1)
    note: str = Field(default="", max_length=4000)


@router.post("/approvals/{approval_id}/fulfill")
def fulfill_need(
    approval_id: str, body: NeedFulfillIn, session: Session = Depends(db)
) -> dict:
    """One motion: store the credential, scope the roles, approve the need.

    The needs loop's whole point — 'I need AWS, give me a key' should cost the
    chairman one form, not a form plus a settings hunt plus remembering to
    answer the approval. The stored value never appears anywhere again; the
    CEO is told the env-var name and who received it."""
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="unknown approval")
    if approval.kind != "tool_request":
        raise HTTPException(status_code=422,
                            detail="only a need (kind=tool_request) is fulfillable "
                                   "with a credential; decide other kinds normally")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"approval already {approval.status}")
    try:
        policy.check_roles_on_project(session, approval.project_id, body.roles)
    except policy.Refusal as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    project = _get_project(session, approval.project_id)
    secret = _upsert_project_secret(
        session, project.id, body.secret_name, body.value, body.roles)
    # The hint MUST be the runner's own mapping — a second normalization once
    # announced a variable that did not exist.
    from yaaf.core.naming import env_var_name

    env_var = env_var_name(secret.name)
    _apply_decision(
        session, approval, project, True,
        (body.note or "fulfilled") + f" [credential: {secret.name}]",
        notice_body=(f"Your need '{approval.title}' was FULFILLED: the credential "
                     f"'{secret.name}' now reaches roles {', '.join(body.roles)} as "
                     f"the environment variable {env_var} on their next run."
                     + (f" His note: {body.note}" if body.note else "")),
        audit_detail={"fulfilled": True, "secret_name": secret.name,
                      "roles": body.roles},
    )
    return {"id": approval.id, "status": approval.status,
            "secret_name": secret.name, "env_var_hint": env_var,
            "roles": body.roles}


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
    carry its own list of six, and the rules a stuck project actually needs to
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
            # project opens is Money.
            "theme": theme,
            "theme_rank": THEME_ORDER.index(theme) if theme in THEME_ORDER else len(THEME_ORDER),
            "type": typed.get("type", "string"),
            "minimum": typed.get("minimum", typed.get("exclusiveMinimum")),
            "maximum": typed.get("maximum", typed.get("exclusiveMaximum")),
            "choices": spec.get("choices"),
            # What the rule would be if nobody had touched it — the editor
            # marks anything that differs, so "what is this project actually
            # running under" is a glance instead of a diff against the docs.
            "default": spec.get("default"),
        })
    return fields


# ── overruns: the conversation after a budget turns out to be wrong ─────────────


class OverrunDecision(BaseModel):
    """Carry on, hold the line, or change the work.

    `new_budget` lets the decision and the correction be one motion, because
    "fine, carry on at that level" and "and here is the budget that reflects
    it" are the same thought, and splitting them across two screens is how a
    budget stays wrong for another month.
    """

    outcome: str = Field(pattern="^(continue|hold|revise)$")
    note: str = Field(min_length=1, max_length=4000)
    new_budget: float | None = Field(default=None, ge=0)


@router.get("/projects/{project_id}/overruns")
def list_overruns(project_id: str, session: Session = Depends(db)) -> dict:
    """Every month that went past its plan, newest first, with the platform's
    breakdown, the CEO's account and your decision — the whole conversation in
    one place, which is what makes the budget converge rather than drift."""
    from yaaf.core.models import BudgetOverrun

    project = _get_project(session, project_id)
    rows = session.execute(
        select(BudgetOverrun).where(BudgetOverrun.project_id == project.id)
        .order_by(BudgetOverrun.period.desc())
    ).scalars().all()
    return {"overruns": [{
        "id": o.id,
        "period": o.period.date().isoformat(),
        "unit": o.unit,
        "budget": o.budget,
        "spent": o.final_spent if o.final_spent is not None else o.spent_at_crossing,
        "over_by": round((o.final_spent or o.spent_at_crossing) - o.budget, 2),
        "crossed_at": o.crossed_at.isoformat(),
        "breakdown": o.breakdown,
        "ceo_account": o.ceo_account,
        "accounted_at": o.accounted_at.isoformat() if o.accounted_at else None,
        "outcome": o.outcome,
        "chairman_note": o.chairman_note,
        "decided_at": o.decided_at.isoformat() if o.decided_at else None,
    } for o in rows]}


@router.post("/overruns/{overrun_id}/decide")
def decide_overrun(
    overrun_id: str, body: OverrunDecision, session: Session = Depends(db)
) -> dict:
    """Rule on a month that went over, and correct the budget while you are here."""
    from yaaf.core.models import BudgetOverrun, OverrunOutcome

    overrun = session.get(BudgetOverrun, overrun_id)
    if overrun is None:
        raise HTTPException(status_code=404, detail="no such overrun")
    if overrun.outcome != OverrunOutcome.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"that overrun is already decided ({overrun.outcome}).")
    project = _get_project(session, overrun.project_id)
    overrun.outcome = body.outcome
    overrun.chairman_note = body.note.strip()
    overrun.decided_at = utcnow()
    overrun.decided_by_user_id = project.chairman_user_id

    raised = None
    if body.new_budget is not None:
        # Through the ordinary amendment path, so it is audited, versioned and
        # disclosed to the CEO like every other rule change. A budget moved by
        # a side door is a budget the CEO cannot see move.
        key = ("monthly_usd_cap" if project.billing_mode == "api_key"
               else "monthly_token_cap")
        amendment = AmendmentIn(
            reason=f"After the {overrun.period:%B} overrun: {body.note.strip()[:300]}",
            changes={key: body.new_budget},
        )
        raised = amend_constitution(project.id, amendment, session)

    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="chairman",
        action="overrun.decided", entity="budget_overrun", entity_id=overrun.id,
        detail={"outcome": body.outcome, "new_budget": body.new_budget,
                "note": overrun.chairman_note[:500]},
    ))
    _notice_ceo(
        session, project.id, NoticeKind.BUDGET_WARNING,
        f"The chairman ruled on the {overrun.period:%B} overrun: "
        f"{body.outcome.upper()}.\n\n{overrun.chairman_note}\n\n"
        + ("The budget moved with it — plan against the new number."
           if body.new_budget is not None else
           "The budget is unchanged. Plan against it as it stands."),
    )
    return {"id": overrun.id, "outcome": overrun.outcome,
            "budget_amended": raised is not None}


@router.get("/projects/{project_id}/budget-advice")
def budget_advice(project_id: str, session: Session = Depends(db)) -> dict:
    """What the settled months say the budget should be.

    The learning half: "over time we learn what the actual cost is and we
    adjust the budgets accordingly". Sized to the worst normal month rather
    than the average one, and widened by stakes — a budget exists to cover the
    month you would not want to be caught short in.
    """
    from yaaf.server import budget as budget_lib

    project = _get_project(session, project_id)
    cons = effective(session, project.id)
    advice = budget_lib.proposed_budget(session, project, cons)
    if advice is None:
        return {"known": False,
                "why": "no settled month has finished yet, so there is nothing "
                       "measured to size a budget from."}
    return {"known": True, **advice}


# ── measurement windows: funding a question instead of arguing about it ─────────


class SpendWindowIn(BaseModel):
    """What the chairman funds. The question is required, because a window
    without one is a larger cap with extra steps."""

    question: str = Field(min_length=10, max_length=2000)
    allowance: float = Field(gt=0)
    days: float = Field(gt=0, le=365)
    checkpoint_hours: float = Field(default=168.0, gt=0)


class SpendWindowPatch(BaseModel):
    """Adjusting along the way — the chairman's own condition for agreeing to
    this. Every field optional; every change appended to `adjustments`."""

    allowance: float | None = Field(default=None, gt=0)
    extend_days: float | None = Field(default=None)
    checkpoint_hours: float | None = Field(default=None, gt=0)
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/projects/{project_id}/spend-window")
def open_spend_window(
    project_id: str, body: SpendWindowIn, session: Session = Depends(db)
) -> dict:
    """Fund a period to find out what something costs.

    The chairman's, not the CEO's — it is his money and his appetite, and the
    absence of any way to express that appetite is what produced three
    contradictory estimates in two hours. The CEO can ask for one in the
    boardroom; only this route opens it.
    """
    from yaaf.core.models import SpendWindow, SpendWindowStatus
    from yaaf.server import spendwindow

    project = _get_project(session, project_id)
    if spendwindow.open_window(session, project.id) is not None:
        raise HTTPException(
            status_code=409,
            detail="A measurement window is already open on this project. Two "
                   "overlapping answers to 'what does this cost' is the state this "
                   "replaces — adjust the open one or stop it first.",
        )
    now = utcnow()
    window = SpendWindow(
        id=ids.new_id(ids.SPEND_WINDOW),
        project_id=project.id,
        question=body.question.strip(),
        allowance=body.allowance,
        unit="usd" if project.billing_mode == "api_key" else "tokens",
        started_at=now,
        ends_at=now + timedelta(days=body.days),
        checkpoint_hours=body.checkpoint_hours,
        status=SpendWindowStatus.OPEN,
        opened_by_user_id=project.chairman_user_id,
        adjustments=[],
    )
    session.add(window)
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="chairman",
        action="spend_window.opened", entity="spend_window", entity_id=window.id,
        detail={"allowance": body.allowance, "days": body.days,
                "question": window.question[:500]},
    ))
    _notice_ceo(
        session, project.id, NoticeKind.BUDGET_WARNING,
        f"The chairman funded a measurement window: {window.allowance:,.0f} "
        f"{window.unit} over {body.days:g} days, to answer — {window.question}\n\n"
        "This is not a bigger cap. It is a question he is paying to have "
        "ANSWERED, and the platform writes the answer from the ledger when it "
        "closes; you do not. Until then: do the work the mandate asks for at the "
        "pace it deserves rather than the pace that feels safe, and when he asks "
        "what it will cost, show him the measured days so far and say plainly "
        "what is still unknown. A confident estimate you cannot support is the "
        "thing this window exists to stop.",
    )
    session.flush()
    return {"id": window.id, "ends_at": window.ends_at.isoformat(),
            "allowance": window.allowance, "unit": window.unit}


@router.patch("/projects/{project_id}/spend-window")
def adjust_spend_window(
    project_id: str, body: SpendWindowPatch, session: Session = Depends(db)
) -> dict:
    """Change the allowance, the end date or the checkpoint, mid-flight.

    "As long as we can adjust along the way based on our findings" was the
    condition attached to funding one of these at all, so the adjustment is a
    first-class verb rather than a delete-and-recreate — and every one lands on
    the record, because an adjustable number nobody can audit is not a budget.
    """
    from yaaf.server import spendwindow

    project = _get_project(session, project_id)
    window = spendwindow.open_window(session, project.id)
    if window is None:
        raise HTTPException(status_code=404, detail="no measurement window is open")
    before = {"allowance": window.allowance, "ends_at": window.ends_at.isoformat(),
              "checkpoint_hours": window.checkpoint_hours}
    if body.allowance is not None:
        window.allowance = body.allowance
    if body.extend_days is not None:
        window.ends_at = window.ends_at + timedelta(days=body.extend_days)
    if body.checkpoint_hours is not None:
        window.checkpoint_hours = body.checkpoint_hours
    entry = {
        "at": utcnow().isoformat(), "reason": body.reason.strip(),
        "from": before,
        "to": {"allowance": window.allowance, "ends_at": window.ends_at.isoformat(),
               "checkpoint_hours": window.checkpoint_hours},
    }
    window.adjustments = [*(window.adjustments or []), entry]
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="chairman",
        action="spend_window.adjusted", entity="spend_window", entity_id=window.id,
        detail=entry,
    ))
    _notice_ceo(
        session, project.id, NoticeKind.BUDGET_WARNING,
        f"The chairman adjusted the measurement window. Reason: {entry['reason']}\n\n"
        f"It now allows {window.allowance:,.0f} {window.unit} and ends "
        f"{window.ends_at:%Y-%m-%d}. The question it is answering has not changed.",
    )
    return {"id": window.id, "adjustments": len(window.adjustments), **entry["to"]}


@router.post("/projects/{project_id}/spend-window/stop")
def stop_spend_window(project_id: str, session: Session = Depends(db)) -> dict:
    """End it early. The findings are still written — a window stopped on day
    four measured four days, and four measured days beat any estimate."""
    from yaaf.core.models import SpendWindowStatus
    from yaaf.server import spendwindow

    project = _get_project(session, project_id)
    window = spendwindow.open_window(session, project.id)
    if window is None:
        raise HTTPException(status_code=404, detail="no measurement window is open")
    spendwindow.close(session, window, "the chairman ended it early",
                      status=SpendWindowStatus.STOPPED)
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor="chairman",
        action="spend_window.stopped", entity="spend_window", entity_id=window.id,
        detail=window.findings or {},
    ))
    return {"id": window.id, "findings": window.findings}


@router.get("/projects/{project_id}/spend-window")
def read_spend_window(project_id: str, session: Session = Depends(db)) -> dict:
    """The live window and where it stands, or the last one that closed."""
    from yaaf.core.models import SpendWindow
    from yaaf.server import spendwindow

    project = _get_project(session, project_id)
    window = spendwindow.open_window(session, project.id)
    if window is not None:
        return {"open": True, **spendwindow.progress(session, window),
                "adjustments": window.adjustments or []}
    last = session.execute(
        select(SpendWindow).where(SpendWindow.project_id == project.id)
        .order_by(SpendWindow.created_at.desc()).limit(1)
    ).scalars().first()
    if last is None:
        return {"open": False, "window": None}
    return {"open": False, "window": {
        "id": last.id, "question": last.question, "status": last.status,
        "closed_at": last.closed_at.isoformat() if last.closed_at else None,
        "close_reason": last.close_reason, "findings": last.findings,
        "adjustments": last.adjustments or []}}


def _apply_craft_lift(session: Session, project_id: str, cons: ConstitutionDoc) -> list[str]:
    """Re-hire the producing seats after `stakes` moved, and say whose.

    Without this the dial is a dial only at creation: amending `stakes` to
    `high` changed the budget headroom and the tone of one paragraph and left
    every seat on whatever the shared library said — which is the exact
    complaint the rule was written to answer, one door along.

    It only ever raises, and it only touches a seat carrying no override at
    all. A NULL override is the platform's own "nobody has said anything about
    this seat"; anything else was either the chairman's answer or an earlier
    lift's, and neither is this amendment's to overwrite. That also makes it
    one-way: lowering `stakes` again does not demote, because the stamp on the
    row is no longer distinguishable from a choice somebody made.
    """
    if cons.stakes != "high":
        return []
    lifted = []
    rows = session.execute(
        select(Agent, Role.slug, RoleVersion)
        .join(Team, Team.id == Agent.team_id)
        .join(RoleVersion, RoleVersion.id == Team.role_version_id)
        .join(Role, Role.id == RoleVersion.role_id)
        .where(Agent.project_id == project_id, Agent.kind != AgentKind.CEO)
    ).all()
    for agent, slug, version in rows:
        if agent.model_override or agent.effort_override:
            continue
        model, effort = _craft_lift(session, slug, version, cons)
        if model or effort:
            agent.model_override = model
            agent.effort_override = effort
            lifted.append(agent.name)
    return sorted(lifted)


@router.post("/projects/{project_id}/amendments")
def amend_constitution(
    project_id: str, body: AmendmentIn, session: Session = Depends(db)
) -> dict:
    project = _get_project(session, project_id)
    # A rule may not be amended into a state the roster cannot keep. Creation
    # refuses this; amendment is the other door onto the same rule, and a rule
    # that holds at one door and not the other is a rule the chairman can set
    # and then watch do nothing. There is no route that removes a team, so the
    # roster only ever grows — which is why this is the last door to close.
    if (body.changes.get("deliverable_review") == "independent_read"
            and not _read_is_impossible(session, project.id)):
        _refuse_without_review_seat(session, _project_role_slugs(session, project.id))
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
    # `stakes` is the one amended rule that changes the roster rather than a
    # number, so it has to be applied here too — the dial reads as live on the
    # Settings screen and bought nothing after creation.
    lifted = _apply_craft_lift(session, project.id, effective(session, project.id))

    # Name the fields rather than a category: a note that lists what you just
    # changed cannot go stale the way a note about which bucket it fell in did.
    changed_now = sorted({k for a in applied_now for k in a.changes})
    note_parts = [f"In force NOW: {', '.join(changed_now)}."]
    if lifted:
        note_parts.append(
            f"Re-hired on a stronger model or deeper reasoning: {', '.join(lifted)}.")
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
                "of failed samples is how a project loses the benefit of the doubt.",
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


# ── apply helpers: one mutation, two hands ─────────────────────────────────────
#
# Each scope verb has exactly one implementation, called by the chairman's
# route here and — when the constitution's authority dial grants the verb —
# by the CEO's self-exercise route in the agent API. The actor string keeps
# the record honest about whose hand moved; `notify` is False when the CEO
# acted itself (a notice telling the CEO what the CEO just did is noise —
# the chairman's digest carries it instead). Every helper raises
# policy.Refusal; the admin routes translate to HTTPException, the agent
# route lets it count as a refusal like any other.


def apply_accept(session: Session, project: Project, row: Requirement, actor: str,
                 stage: int | None, note: str, by_user: str | None,
                 notify: bool = True) -> dict:
    policy.check_requirement_accept(row)
    from_status = row.status
    row.status = RequirementStatus.PENDING
    row.accepted_by_user_id = by_user
    row.accepted_at = utcnow()
    if stage is not None:
        row.stage = stage
    policy.record_requirement_event(session, row, RequirementStatus.PENDING, actor,
                                    note=note or None, from_status=from_status)
    _audit(session, row.project_id, "requirement.accept", "requirement", row.id,
           {"key": row.key, "stage": row.stage, "actor": actor})
    if notify:
        _notice_ceo(
            session, row.project_id, NoticeKind.GOAL_CHANGED,
            f"The chairman ACCEPTED {row.key} into scope: {row.statement[:400]}\n"
            f"Stage {row.stage}. It is now something you may create work for, and it "
            "counts toward what has to be true before this project is finished.",
        )
    return {"id": row.id, "key": row.key, "status": row.status, "stage": row.stage}


def apply_cut(session: Session, project: Project, row: Requirement, actor: str,
              reason: str, notify: bool = True) -> dict:
    policy.check_requirement_cut(row)
    from_status = row.status
    row.status = RequirementStatus.CUT
    row.cut_reason = reason
    row.cut_at = utcnow()
    policy.record_requirement_event(session, row, RequirementStatus.CUT, actor,
                                    note=reason, from_status=from_status)
    _audit(session, row.project_id, "requirement.cut", "requirement", row.id,
           {"key": row.key, "reason": reason, "actor": actor})
    if notify:
        _notice_ceo(
            session, row.project_id, NoticeKind.GOAL_CHANGED,
            f"The chairman CUT {row.key} from scope: {row.statement[:300]}\n"
            f"His reason: {reason[:1000]}\nStop work on it. Anything that depended on "
            "it is now unblocked — a cut requirement counts as settled, not as pending "
            "forever.",
        )
    return {"id": row.id, "key": row.key, "status": row.status}


def apply_reopen(session: Session, project: Project, row: Requirement, actor: str,
                 reason: str, notify: bool = True) -> dict:
    policy.check_requirement_reopen(row)
    from_status = row.status
    row.status = RequirementStatus.PENDING
    row.met_at = None
    row.met_by_run_id = None
    row.check_output = None
    row.cut_reason = None
    row.cut_at = None
    policy.record_requirement_event(session, row, RequirementStatus.PENDING, actor,
                                    note=f"reopened: {reason[:500]}",
                                    from_status=from_status)
    _audit(session, row.project_id, "requirement.reopen", "requirement", row.id,
           {"key": row.key, "was": from_status, "reason": reason, "actor": actor})
    if notify:
        _notice_ceo(
            session, row.project_id, NoticeKind.GOAL_CHANGED,
            f"{row.key} was REOPENED (it was {from_status}): {row.statement[:300]}\n"
            f"Reason: {reason[:800]}\nIt is scope again — its stage reopened with it, "
            "so plan for it before anything staged later.",
        )
    return {"id": row.id, "key": row.key, "status": row.status, "was": from_status}


def apply_restage(session: Session, project: Project, row: Requirement, actor: str,
                  stage: int, reason: str, notify: bool = True) -> dict:
    policy.check_requirement_restage(row)
    was = row.stage
    if stage == was:
        raise policy.Refusal(f"{row.key} is already in stage {was}.", status_code=409)
    row.stage = stage
    policy.record_requirement_event(
        session, row, row.status, actor,
        note=f"restaged {was} → {stage}: {reason[:500]}", from_status=row.status)
    _audit(session, row.project_id, "requirement.restage", "requirement", row.id,
           {"key": row.key, "from_stage": was, "to_stage": stage,
            "reason": reason, "actor": actor})
    if notify:
        _notice_ceo(
            session, row.project_id, NoticeKind.GOAL_CHANGED,
            f"{row.key} moved from stage {was} to stage {stage}: {reason[:800]}\n"
            "The open stage is derived, so the order of work may just have changed — "
            "re-read the plan before creating tasks.",
        )
    return {"id": row.id, "key": row.key, "stage": row.stage, "was": was}


def apply_reestimate(session: Session, project: Project, row: Requirement, actor: str,
                     est_runs: int, reason: str, notify: bool = True) -> dict:
    policy.check_requirement_reestimate(row)
    was = row.est_runs
    if row.est_runs_first is None:
        row.est_runs_first = was
    row.est_runs = est_runs
    policy.record_requirement_event(
        session, row, row.status, actor,
        note=f"reestimated {was} → {est_runs} runs: {reason[:500]}",
        from_status=row.status)
    _audit(session, row.project_id, "requirement.reestimate", "requirement", row.id,
           {"key": row.key, "from_est": was, "to_est": est_runs,
            "first_est": row.est_runs_first, "reason": reason, "actor": actor})
    if notify:
        _notice_ceo(
            session, row.project_id, NoticeKind.GOAL_CHANGED,
            f"{row.key} is now estimated at {est_runs} runs (was {was}): "
            f"{reason[:800]}\nThe plan arithmetic reads the new number; the bias "
            "ratio still scores the original.",
        )
    return {"id": row.id, "key": row.key, "est_runs": row.est_runs,
            "est_runs_first": row.est_runs_first, "was": was}


def apply_reword(session: Session, project: Project, row: Requirement, actor: str,
                 statement: str, reason: str, notify: bool = True) -> dict:
    if row.status in policy.CLOSED_REQUIREMENT_STATUSES:
        raise policy.Refusal(
            f"{row.key} is {row.status}; settled scope is history. Add a new "
            "requirement instead of editing what already happened.",
            status_code=409,
        )
    old = row.statement
    row.statement = statement.strip()
    policy.record_requirement_event(
        session, row, row.status, actor,
        note=f"reworded: {reason[:500]}", from_status=row.status)
    _audit(session, project.id, "requirement.reword", "requirement", row.id,
           {"key": row.key, "reason": reason, "actor": actor})
    if notify:
        _notice_ceo(
            session, project.id, NoticeKind.GOAL_CHANGED,
            f"The chairman REWORDED {row.key}.\nWas: {old[:300]}\nIs now: "
            f"{row.statement[:300]}\nHis reason: {reason[:500]}\nThe key, estimate "
            "and history are unchanged — read the new wording before working it.",
        )
    return {"id": row.id, "key": row.key, "statement": row.statement}


def apply_verdict(session: Session, project: Project, row: Requirement, actor: str,
                  ok: bool, note: str, notify: bool = True) -> dict:
    if row.status != RequirementStatus.CHECKING:
        raise policy.Refusal(
            f"{row.key} is {row.status}; only a requirement awaiting a verdict can "
            "take one.",
            status_code=409,
        )
    note = (note or "").strip()
    # A rejection with no reason is a dead end: everything downstream — what the
    # CEO rebuilds, and whether already-accepted stages have to be redone — is
    # read from this note and nothing else.
    if not ok and not note:
        raise policy.Refusal(
            "A rejection needs a reason. Everything that happens next is built from it: "
            "the CEO decides what to rebuild, and whether stages already accepted have "
            "to be redone, by reading this and nothing else. 'Not met' on its own is a "
            "dead end.",
            status_code=422,
        )
    from_status = row.status
    if ok:
        row.status = RequirementStatus.MET
        row.met_at = utcnow()
        row.check_output = note or "observable confirmed"
        policy.record_requirement_event(session, row, RequirementStatus.MET, actor,
                                        note=note or None, from_status=from_status)
    else:
        row.status = RequirementStatus.IN_PROGRESS
        row.failure_load += 1
        # On the requirement, not only in a notice: a notice is consumed by the
        # first run that reads it and is then gone, but the reason a deliverable
        # was rejected has to still be there on the tenth run of the rework, and
        # on the chairman's own screen next to the thing he rejected.
        row.check_output = f"{policy.VERDICT_REJECTED}{note}"[:policy.VERDICT_NOTE_CAP]
        policy.record_requirement_event(session, row, RequirementStatus.IN_PROGRESS,
                                        actor, note=note, from_status=from_status)
        if notify:
            _notice_ceo(
                session, row.project_id, NoticeKind.AUDIT_FAILED,
                f"The chairman looked at {row.key} and it does NOT hold. This is what he "
                f"said, in full:\n\n{note}\n\n"
                f"{row.key} is back in progress and you may create work for it right now — "
                "nothing is blocking you and nothing is waiting on him. Before you do, work "
                "through his words in this order:\n"
                f"1. What does {row.key} itself have to rebuild to meet them?\n"
                "2. Do requirements you already settled have to be redone? Defects usually "
                "surface at the last deliverable and are made further upstream — if what he "
                "says invalidates an earlier stage, that stage is wrong too. Settled scope "
                "is history here: a met requirement cannot be reopened or reworded by "
                "anyone. Propose a NEW requirement at that earlier stage instead. The moment "
                "he accepts it the open stage drops back to it by itself, and the later "
                "stages freeze until it settles — which is the correct order to rebuild in.\n"
                "3. Then create the work, and say in the boardroom what you concluded at "
                "steps 1 and 2 so he can see the shape of the rebuild before it runs.\n"
                f"Only he can change what {row.key} asks for. If his words move the bar "
                "rather than the work, say so in the boardroom rather than quietly building "
                "to a different one.",
            )
    _audit(session, row.project_id, "requirement.verdict", "requirement", row.id,
           {"key": row.key, "ok": ok, "actor": actor})
    return {"id": row.id, "key": row.key, "status": row.status}


def apply_edge_remove(session: Session, project: Project, actor: str,
                      from_requirement_id: str, to_requirement_id: str,
                      reason: str, notify: bool = True) -> dict:
    edge = session.execute(
        select(RequirementEdge).where(
            RequirementEdge.from_requirement_id == from_requirement_id,
            RequirementEdge.to_requirement_id == to_requirement_id,
        )
    ).scalar_one_or_none()
    if edge is None:
        raise policy.Refusal("no such dependency edge", status_code=404)
    source = session.get(Requirement, edge.from_requirement_id)
    target = session.get(Requirement, edge.to_requirement_id)
    session.delete(edge)
    _audit(session, edge.project_id, "requirement.unsequence", "requirement",
           target.id if target else edge.to_requirement_id,
           {"from": source.key if source else edge.from_requirement_id,
            "to": target.key if target else edge.to_requirement_id,
            "reason": reason, "actor": actor})
    if notify:
        _notice_ceo(
            session, edge.project_id, NoticeKind.GOAL_CHANGED,
            f"The dependency {source.key if source else '?'} → "
            f"{target.key if target else '?'} was removed: {reason[:800]}\n"
            f"{target.key if target else 'The blocked requirement'} no longer waits "
            "for it.",
        )
    return {"removed": True,
            "from": source.key if source else None, "to": target.key if target else None}


@router.post("/requirements/{requirement_id}/accept")
def accept_requirement(
    requirement_id: str, body: RequirementDecision, session: Session = Depends(db)
) -> dict:
    """Scope becomes scope here — or, under `plan_authority: ceo`, through the
    CEO's own hand in the agent API; both run this same apply helper.

    Under the default dial the CEO proposes and only the chairman accepts:
    remaining ÷ velocity is a number about the CEO's work, and a CEO that could
    accept its own requirements would own both sides of its own scoreboard.
    The dial is the chairman's to set, and turning it is an amendment on the
    record — authority is granted, never seized.
    """
    row = session.get(Requirement, requirement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown requirement")
    project = _get_project(session, row.project_id)
    try:
        return apply_accept(session, project, row,
                            f"user:{project.chairman_user_id}",
                            body.stage, body.note, project.chairman_user_id)
    except policy.Refusal as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


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
        return apply_cut(session, project, row,
                         f"user:{project.chairman_user_id}", body.reason)
    except policy.Refusal as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


class RequirementVerdict(BaseModel):
    ok: bool
    # Long on purpose. A rejection is the whole brief for the rework that
    # follows it — the chairman who found six methodology defects in a report
    # has six paragraphs to write, and a field that clips him at a tweet turns
    # the most valuable thing he does all week into "it doesn't".
    note: str = Field(default="", max_length=10_000)


@router.post("/requirements/{requirement_id}/verdict")
def requirement_verdict(
    requirement_id: str, body: RequirementVerdict, session: Session = Depends(db)
) -> dict:
    """The chairman's answer on an *observable* acceptance test.

    A command test is run by the platform and needs nobody. An observable one
    names something a script cannot check — "the memo reads well" — so somebody
    has to look. That somebody is never the agent whose work it judges, which
    is why this route is here and not in the agent API.

    A rejection STICKS. The requirement goes back to in-progress and stays
    there until new work closes under it; the janitor will not park it in front
    of the chairman again on its own. For a while it did — the guard that stops
    an unchanged workspace being re-tested keyed on the platform's checker, and
    an observable never reaches the checker — so pressing "not met" was undone
    fifteen seconds later, every time, and the button did nothing a chairman
    could see. See `janitor._work_closed_since_last_check`.
    """
    row = session.get(Requirement, requirement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown requirement")
    project = _get_project(session, row.project_id)
    try:
        return apply_verdict(session, project, row,
                             f"user:{project.chairman_user_id}", body.ok, body.note)
    except policy.Refusal as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


# ── the missing verbs: reopen, restage, reestimate, unsequence ──────────────────
#
# Stage A of the CEO Charter (docs/AUTONOMY.md). Until these existed, MET and
# CUT were terminal for every principal including the chairman, a stage could
# never be moved, an estimate could never honestly grow, and an edge was a
# one-way ratchet. Each verb writes a requirement_event, so history is
# recorded, never rewritten — and because the open stage is derived
# (min(stage) over open requirements), reopening a requirement reopens its
# stage with zero extra machinery.


class RequirementReopen(BaseModel):
    reason: str = Field(min_length=5, max_length=4000)


@router.post("/requirements/{requirement_id}/reopen")
def reopen_requirement(
    requirement_id: str, body: RequirementReopen, session: Session = Depends(db)
) -> dict:
    """The undo that never existed: a wrong verdict or a premature cut stops
    being permanent. The settlement stays in the event log; the requirement
    returns to PENDING and its stage reopens by derivation."""
    row = session.get(Requirement, requirement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown requirement")
    project = _get_project(session, row.project_id)
    try:
        return apply_reopen(session, project, row,
                            f"user:{project.chairman_user_id}", body.reason)
    except policy.Refusal as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


class RequirementRestage(BaseModel):
    stage: int = Field(ge=1, le=50)
    reason: str = Field(min_length=5, max_length=4000)


@router.post("/requirements/{requirement_id}/restage")
def restage_requirement(
    requirement_id: str, body: RequirementRestage, session: Session = Depends(db)
) -> dict:
    """Move an open requirement to a different stage. 'This should have been
    stage 1' finally has an expression."""
    row = session.get(Requirement, requirement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown requirement")
    project = _get_project(session, row.project_id)
    try:
        return apply_restage(session, project, row,
                             f"user:{project.chairman_user_id}",
                             body.stage, body.reason)
    except policy.Refusal as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


class RequirementReestimate(BaseModel):
    est_runs: int = Field(ge=1, le=10_000)
    reason: str = Field(min_length=5, max_length=4000)


@router.post("/requirements/{requirement_id}/reestimate")
def reestimate_requirement(
    requirement_id: str, body: RequirementReestimate, session: Session = Depends(db)
) -> dict:
    """Revise est_runs with the revision on the record. The plan arithmetic
    reads the new number; the bias multiplier is always scored against the
    FIRST estimate, so honesty about work growing gets a channel and
    lowballing still cannot be laundered."""
    row = session.get(Requirement, requirement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown requirement")
    project = _get_project(session, row.project_id)
    try:
        return apply_reestimate(session, project, row,
                                f"user:{project.chairman_user_id}",
                                body.est_runs, body.reason)
    except policy.Refusal as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


class EdgeRemove(BaseModel):
    from_requirement_id: str
    to_requirement_id: str
    reason: str = Field(min_length=5, max_length=4000)


@router.post("/requirement_edges/remove")
def remove_requirement_edge(body: EdgeRemove, session: Session = Depends(db)) -> dict:
    """Unsequence: dependency edges stop being a one-way ratchet. The cycle
    check still guards every add; removal needs no check beyond existing."""
    source = session.get(Requirement, body.from_requirement_id)
    project = _get_project(session, source.project_id) if source else None
    try:
        if project is None:
            raise policy.Refusal("no such dependency edge", status_code=404)
        return apply_edge_remove(session, project, f"user:{project.chairman_user_id}",
                                 body.from_requirement_id, body.to_requirement_id,
                                 body.reason)
    except policy.Refusal as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


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
        est_runs_first=body.est_runs,
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
    project = _get_project(session, row.project_id)
    try:
        return apply_reword(session, project, row,
                            f"user:{project.chairman_user_id}",
                            body.statement, body.reason)
    except policy.Refusal as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


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


# ── slack: what this project inherits, and its rooms ────────────────────────────


@router.get("/projects/{project_id}/slack")
def project_slack(project_id: str, session: Session = Depends(db)) -> dict:
    """This project's effective Slack wiring and where each field came from.

    The settings screen needs the *sources*, not just the values: a chairman
    looking at an empty "bot token" box has no way to tell "not configured"
    from "inherited from the workspace you set up once" — and those are
    opposite situations. Token values never appear; only their provenance.
    """
    from yaaf.server.slack import settings as slack_settings

    project = _get_project(session, project_id)
    own = slack_settings.project_config(project) or {}
    effective = slack_settings.resolve(session, project)
    team_names = {
        team.id: team.name
        for team in session.execute(select(Team).where(Team.project_id == project.id)).scalars()
    }
    rooms: list[dict] = []
    if effective is not None:
        keys = [str(ChannelKind.BOARDROOM), str(ChannelKind.LEADERSHIP)]
        keys += [f"team:{team_id}" for team_id in team_names]
        for key in keys:
            rooms.append({
                "key": key,
                "label": slack_settings.room_label(key, team_names),
                "expected_name": slack_settings.channel_name(
                    effective.channel_prefix, key, team_names),
                "channel_id": effective.channel_map.get(key),
            })
    return {
        "enabled": effective is not None,
        "global_configured": slack_settings.is_configured(session),
        "can_mirror": bool(effective and effective.can_mirror),
        "can_receive": bool(effective and effective.can_receive),
        "sources": effective.sources if effective else {},
        "chairman_slack_user": effective.chairman_slack_user if effective else "",
        "channel_prefix": effective.channel_prefix if effective else project.slug,
        "channel_visibility": (effective.channel_visibility if effective
                               else platform.DEFAULT_SLACK_VISIBILITY),
        # The secret NAMES this project overrides with, so the UI can offer to
        # rotate them. Never the values.
        "overrides": {
            key: own.get(key)
            for key in ("bot_token_secret", "signing_secret", "app_token_secret")
        },
        "rooms": rooms,
        "events_url": f"/webhooks/slack/{project.id}",
    }


@router.post("/projects/{project_id}/slack/channels")
def provision_slack_channels(project_id: str, session: Session = Depends(db)) -> dict:
    """Create every Slack room this project is missing and say what they are.

    This is the "and now the rest" half of provisioning: creating a project
    makes only its boardroom, and this makes leadership and every team room
    besides. Idempotent: rooms already mapped are left alone, and a name Slack
    says is taken is adopted rather than duplicated. Run it after adding a
    team, or on a project that predates the workspace being configured.
    """
    from yaaf.server.slack import provision as slack_provision

    project = _get_project(session, project_id)
    result = slack_provision.provision(session, project)
    if not result["channels"] and not result["ok"]:
        raise HTTPException(status_code=422, detail=result["detail"])
    _audit(session, project.id, "slack.provision_channels", "project", project.id,
           {"channels": [{"key": row["key"], "name": row["name"],
                          "created": row["created"]} for row in result["channels"]]})
    return result


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

    # Every path that writes role_scopes asks the same question, and this one
    # was not asking it at all: the Settings table offered all thirty-one
    # library roles and stored whichever were ticked, including roles this
    # project never hired.
    if body.role_scopes:
        try:
            policy.check_roles_on_project(session, body.project_id, body.role_scopes)
        except policy.Refusal as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

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


# ── MCP clients ─────────────────────────────────────────────────────────────────


class McpClientIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    chairman_email: str
    # At least one, because a token with no scope is a credential that exists
    # to be widened later — and widening is the failure mode this table exists
    # to make deliberate.
    scopes: list[str] = Field(min_length=1)


@router.post("/mcp_clients")
def create_mcp_client(body: McpClientIn, session: Session = Depends(db)) -> dict:
    """Mint a token for a model client. The plaintext is shown once, here.

    This is the only place the chairman's authority is ever narrowed rather
    than copied: the admin bearer stays where it is, and what leaves is a
    scoped, revocable, attributable credential that names the person it acts
    for. See docs/MCP_SERVER.md.
    """
    from yaaf.core.tokens import mint_mcp

    unknown = sorted(set(body.scopes) - set(MCP_SCOPES))
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"unknown scope(s) {', '.join(unknown)}; valid: {', '.join(MCP_SCOPES)}",
        )
    chairman = session.execute(
        select(User).where(User.email == body.chairman_email)
    ).scalar_one_or_none()
    if chairman is None:
        raise HTTPException(status_code=422, detail="no such user")

    plaintext, token_hash = mint_mcp()
    row = McpClient(
        id=ids.new_id(ids.MCP_CLIENT),
        name=body.name,
        user_id=chairman.id,
        scopes=sorted(set(body.scopes)),
    )
    session.add(row)
    session.flush()
    # A CLI token is one row in the same table an OAuth access token lands in,
    # with no expiry and no audience. One validation path serves both, so a
    # rule proven for the browser holds for the terminal.
    session.add(McpToken(
        id=ids.new_id(ids.MCP_TOKEN), client_id=row.id, kind="static",
        token_hash=token_hash,
    ))
    _audit(session, None, "mcp_client.create", "mcp_client", row.id,
           {"name": row.name, "scopes": row.scopes})
    return {
        "id": row.id,
        "name": row.name,
        "scopes": row.scopes,
        "token": plaintext,
        "note": "Store this now — only its hash is kept, and it is never shown again.",
    }


@router.get("/mcp_clients")
def list_mcp_clients(session: Session = Depends(db)) -> list[dict]:
    return [
        {"id": c.id, "name": c.name, "user_id": c.user_id, "scopes": c.scopes,
         "created_at": c.created_at, "last_used_at": c.last_used_at,
         "revoked_at": c.revoked_at}
        for c in session.execute(select(McpClient).order_by(McpClient.created_at)).scalars()
    ]


@router.delete("/mcp_clients/{client_id}")
def revoke_mcp_client(client_id: str, session: Session = Depends(db)) -> dict:
    """Revoke, never delete: the audit trail names this client forever, so the
    row it names has to survive to be readable."""
    row = session.get(McpClient, client_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown MCP client")
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        # And every token issued under it. Revoking the grant while its access
        # token still validates would be a button that looks like it worked.
        for token_row in session.execute(
                select(McpToken).where(McpToken.client_id == row.id)).scalars():
            token_row.revoked_at = token_row.revoked_at or utcnow()
        _audit(session, None, "mcp_client.revoke", "mcp_client", row.id, {"name": row.name})
    return {"id": row.id, "name": row.name, "revoked_at": row.revoked_at}


class PasswordIn(BaseModel):
    email: str
    password: str = Field(min_length=12)


@router.post("/users/password")
def set_user_password(body: PasswordIn, session: Session = Depends(db)) -> dict:
    """Give a chairman a password, so a browser can authenticate them.

    The admin token authenticates a *client*; OAuth's consent screen has to
    know which person is at the keyboard. This is the seam between the two, and
    the only thing on the admin surface that writes `users.password_hash`.
    """
    from yaaf.server import login as login_module

    try:
        user = login_module.set_password(session, body.email.strip().lower(), body.password)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    _audit(session, None, "user.set_password", "user", user.id, {"email": user.email})
    return {"id": user.id, "email": user.email}
