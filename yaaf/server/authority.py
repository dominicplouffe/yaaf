"""Who may do what — declared on every human route, and the declaration is the check.

Two levels of authority exist (docs/AUTH.md). On the box, the *instance admin*
runs it: people, platform settings, the libraries. On a project, a row in
`project_members` says what a person is there:

    chairman  (exactly one)  read · decide · configure · decide who decides
    co_chair  (any number)   read · decide · configure
    observer  (any number)   read

The instance admin also reads every project — whoever runs the box can read its
database, and the page should say so — and decides nothing it does not chair.

**Every `/admin`, `/observe` and `/auth` route carries exactly one of the
declarations below** as a route dependency, and `test_auth_surface.py` walks
the live app and fails on any that does not. A project-level declaration
resolves the project from the path — `project_id`, or the id of anything that
belongs to one — and refuses before the handler runs, so a handler cannot
forget the check and a reviewer can read the authority off the decorator.

A person with no standing on a project gets the 404 a missing project gets, in
the same words: the dashboard is not an oracle for what other people run. A
member whose role is too small gets a 403 that names the role the verb needs.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import IntEnum

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import (
    Agent,
    Amendment,
    Approval,
    AuditSample,
    BudgetOverrun,
    Channel,
    Commitment,
    Evidence,
    Flag,
    Hypothesis,
    Message,
    Project,
    ProjectMember,
    Requirement,
    Review,
    Run,
    Task,
    Team,
    User,
)
from yaaf.server import acting
from yaaf.server.deps import db
from yaaf.server.identity import Person, person_auth


class Level(IntEnum):
    READ = 1
    STEER = 2
    CHAIR = 3


CHAIRMAN, CO_CHAIR, OBSERVER = "chairman", "co_chair", "observer"
ROLES = (CHAIRMAN, CO_CHAIR, OBSERVER)
ROLE_LEVEL = {CHAIRMAN: Level.CHAIR, CO_CHAIR: Level.STEER, OBSERVER: Level.READ}

#: Who holds each level, in the words a refusal uses.
HOLDERS = {
    Level.READ: "a member of this project",
    Level.STEER: "this project's chairman or a co-chair",
    Level.CHAIR: "this project's chairman",
}

#: The path parameters a project-level declaration may resolve a project from,
#: and the table each one names. Every one of these rows belongs to exactly one
#: project, which is the whole of what makes the resolution sound.
ENTITY_PARAMS: dict[str, type] = {
    "team_id": Team,
    "agent_id": Agent,
    "approval_id": Approval,
    "hypothesis_id": Hypothesis,
    "overrun_id": BudgetOverrun,
    "amendment_id": Amendment,
    "message_id": Message,
    "evidence_id": Evidence,
    "task_id": Task,
    "flag_id": Flag,
    "sample_id": AuditSample,
    "requirement_id": Requirement,
    "review_id": Review,
    "commitment_id": Commitment,
    "channel_id": Channel,
    "run_id": Run,
}
PROJECT_PARAMS = ("project_id", *ENTITY_PARAMS)

#: What a handler says when the thing a path names does not exist — and so
#: what a person with no standing on its project is told too, word for word.
NOT_FOUND = {
    "project_id": "unknown project",
    "overrun_id": "no such overrun",
    "sample_id": "unknown audit sample",
    **{name: f"unknown {name.removesuffix('_id')}" for name in ENTITY_PARAMS
       if name not in ("overrun_id", "sample_id")},
}


# ── membership ──────────────────────────────────────────────────────────────────


def role_of(session: Session, user_id: str, project: Project) -> str | None:
    """This person's role on this project, or None.

    `projects.chairman_user_id` is the canonical chair; the member row mirrors
    it and is written beside it by :func:`set_chairman`. Read the column first
    so a project that predates its member row is still chaired by somebody.
    """
    if project.chairman_user_id == user_id:
        return CHAIRMAN
    role = session.execute(
        select(ProjectMember.role).where(ProjectMember.project_id == project.id,
                                         ProjectMember.user_id == user_id)
    ).scalar_one_or_none()
    # A stale `chairman` row for somebody who is no longer the chair is
    # nothing: the column decides, so a half-finished transfer cannot leave
    # two people holding the chair.
    return role if role in (CO_CHAIR, OBSERVER) else None


def level_of(session: Session, user_id: str, is_admin: bool,
             project: Project) -> Level | None:
    role = role_of(session, user_id, project)
    level = ROLE_LEVEL.get(role) if role else None
    if is_admin and level is None:
        return Level.READ
    return level


def person_level(session: Session, person: Person, project: Project) -> Level | None:
    return level_of(session, person.id, person.is_admin, project)


def user_level(session: Session, user_id: str, project: Project) -> Level | None:
    """The same answer for a user id, as the MCP surface holds one — read from
    the users row so a deactivated person reaches nothing."""
    user = session.get(User, user_id)
    if user is None or user.deactivated_at is not None:
        return None
    return level_of(session, user.id, bool(user.is_admin), project)


def reachable(session: Session, user_id: str, is_admin: bool,
              level: Level = Level.READ) -> list[Project]:
    """Every project this person holds at least `level` on, in creation order.

    One function for every cross-project rollup — the project list, the Desk,
    the briefing, the MCP desk — because the gate has to hold on payloads
    nobody wrote with it in mind, and the day one of them stops passing through
    here is the day it lists a project its reader cannot open.
    """
    query = select(Project).order_by(Project.created_at)
    if not (is_admin and level == Level.READ):
        member_of = select(ProjectMember.project_id).where(
            ProjectMember.user_id == user_id,
            ProjectMember.role.in_([r for r, lv in ROLE_LEVEL.items() if lv >= level]))
        query = query.where((Project.chairman_user_id == user_id)
                            | Project.id.in_(member_of))
    projects = list(session.execute(query).scalars())
    return [p for p in projects if (level_of(session, user_id, is_admin, p) or 0) >= level]


def require(session: Session, person: Person, project: Project | str | None,
            level: Level, *, not_found: str = "unknown project") -> Project:
    """Refuse unless this person holds `level` on this project.

    The function every declaration below resolves to, and the one a handler
    calls itself when its project is not in the path (IN_HANDLER). Somebody
    with no standing is told `not_found` — what the handler says of a thing
    that does not exist — so the refusal answers nothing about what does.
    """
    if isinstance(project, str):
        project = session.get(Project, project)
    have = person_level(session, person, project) if project is not None else None
    if project is None or have is None:
        raise HTTPException(status_code=404, detail=not_found)
    if have < level:
        raise HTTPException(
            status_code=403,
            detail=f"only {HOLDERS[level]} may do this; you are "
                   f"{_described(session, person, project)}")
    return project


def acting_person(session: Session) -> Person | None:
    """The person this work is bound to (`yaaf.server.acting`), as a Person."""
    user_id = acting.user_id()
    user = session.get(User, user_id) if user_id else None
    if user is None or user.deactivated_at is not None:
        return None
    return Person(id=user.id, email=user.email, display_name=user.display_name,
                  is_admin=bool(user.is_admin), via="bound")


def require_here(session: Session, project: Project | str | None, level: Level) -> Project:
    """`require`, for whoever this work is bound to.

    The form a handler uses when it is also called as a plain function — the
    MCP tools reach several handlers that way, and a function called directly
    has no dependency to take the person from. The MCP call bound its actor
    (`confirm.attributed`), the HTTP request bound its person; either way it is
    here.
    """
    person = acting_person(session)
    if person is None:
        raise HTTPException(status_code=401, detail="sign in first")
    return require(session, person, project, level)


def require_admin_here(session: Session) -> Person:
    person = acting_person(session)
    if person is None:
        raise HTTPException(status_code=401, detail="sign in first")
    return require_admin(person)


def _described(session: Session, person: Person, project: Project) -> str:
    role = role_of(session, person.id, project)
    if role == CO_CHAIR:
        return "a co-chair"
    if role == OBSERVER:
        return "an observer"
    if person.is_admin:
        return "the instance admin, which reads every project and decides on none it does not chair"
    return "a member"


def owns(person: Person, owner_id: str | None, *, admin_may: bool = False) -> bool:
    """For things a person owns rather than a project: their tokens, their
    model clients, their founding conversations."""
    return owner_id == person.id or (admin_may and person.is_admin)


def require_admin(person: Person) -> Person:
    if not person.is_admin:
        raise HTTPException(status_code=403, detail="only an instance admin may do this")
    return person


# ── the declarations ────────────────────────────────────────────────────────────


def _declares(kind: str) -> Callable[[Callable], Callable]:
    def mark(fn: Callable) -> Callable:
        fn.__authority__ = kind
        return fn
    return mark


@_declares("public")
def _public() -> None:
    """Open by design. Each use says why, beside it."""


@_declares("signed_in")
def _signed_in(person: Person = Depends(person_auth)) -> Person:
    return person


@_declares("instance_admin")
def _instance_admin(person: Person = Depends(person_auth)) -> Person:
    return require_admin(person)


@_declares("in_handler")
def _in_handler(person: Person = Depends(person_auth)) -> Person:
    """Signed in, and the handler checks the rest itself — for a route whose
    project is in its body, or whose object is owned by a person rather than a
    project. `test_auth_surface.py` holds each one to calling `require*`."""
    return person


def project_of(session: Session, request: Request) -> tuple[Project | None, str]:
    """The project the path names, and the words for "no such thing" there."""
    params = request.path_params
    if "project_id" in params:
        return session.get(Project, params["project_id"]), NOT_FOUND["project_id"]
    for name, model in ENTITY_PARAMS.items():
        if name in params:
            row = session.get(model, params[name])
            project = session.get(Project, row.project_id) if row is not None else None
            return project, NOT_FOUND[name]
    return None, NOT_FOUND["project_id"]


def _project_gate(level: Level) -> Callable:
    @_declares(f"project:{level.name.lower()}")
    def gate(request: Request, person: Person = Depends(person_auth),
             session: Session = Depends(db)) -> Person:
        project, not_found = project_of(session, request)
        if project is None:
            # Nothing to protect: let the handler say "not found" in its own
            # words, as it always has.
            return person
        require(session, person, project, level, not_found=not_found)
        return person
    return gate


PUBLIC = Depends(_public)
SIGNED_IN = Depends(_signed_in)
INSTANCE_ADMIN = Depends(_instance_admin)
IN_HANDLER = Depends(_in_handler)
READ = Depends(_project_gate(Level.READ))
STEER = Depends(_project_gate(Level.STEER))
CHAIR = Depends(_project_gate(Level.CHAIR))


# ── writing membership ──────────────────────────────────────────────────────────


def set_chairman(session: Session, project: Project, user_id: str) -> None:
    """Hand the chair to `user_id`: the column and the member rows together,
    so the two can never name different people.

    The outgoing chairman stays on as a co-chair. Handing the chair on is a
    change of who is accountable, not a removal; the new chairman can remove
    them in one more step if that is what was meant.
    """
    previous = project.chairman_user_id
    project.chairman_user_id = user_id
    rows = {m.user_id: m for m in session.execute(
        select(ProjectMember).where(ProjectMember.project_id == project.id)).scalars()}
    if user_id in rows:
        rows[user_id].role = CHAIRMAN
    else:
        session.add(ProjectMember(project_id=project.id, user_id=user_id, role=CHAIRMAN))
    if previous and previous != user_id:
        if previous in rows:
            rows[previous].role = CO_CHAIR
        else:
            session.add(ProjectMember(project_id=project.id, user_id=previous, role=CO_CHAIR))


def speaker(session: Session, project: Project, user_id: str | None) -> str | None:
    """How a person's message is labelled for the agents and the screens: None
    for the chairman — every message a project ever had was his, and saying so
    on each would change what every existing pack reads — and "Name (co-chair)"
    for anybody else whose words land in the boardroom, so a CEO taking an
    order knows whose it was."""
    if user_id is None or user_id == project.chairman_user_id:
        return None
    user = session.get(User, user_id)
    if user is None:
        return None
    role = role_of(session, user_id, project)
    suffix = {CO_CHAIR: " (co-chair)", OBSERVER: " (observer)"}.get(role, "")
    return f"{user.display_name}{suffix}"


def chairs(session: Session, project: Project) -> list[str]:
    """Everyone whose word binds this project: the chairman, then co-chairs.

    Active accounts only — a co-chair who has left the box is not somebody
    the CEO may still take orders from.
    """
    co_chairs = session.execute(
        select(ProjectMember.user_id).join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project.id, ProjectMember.role == CO_CHAIR,
               User.deactivated_at.is_(None))
        .order_by(ProjectMember.id)
    ).scalars().all()
    chairman = session.get(User, project.chairman_user_id)
    head = [chairman.id] if chairman is not None and chairman.deactivated_at is None else []
    return head + [u for u in co_chairs if u not in head]
