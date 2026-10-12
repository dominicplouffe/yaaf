"""Standing a role down, recalling it, and the condition that does it for you.

A project is front-loaded and then it is not. Planning, requirements, build,
validation and quality assurance all crowd the first half; the second half is
execution and needs a fraction of the seats. Every roster on this platform was
hired at the one moment nobody could know that — before any work existed — and
then kept, because there was no verb for the transition. On the desk that
prompted this, two of five seats had done no work for two days and had spent
139 million tokens between them, and the CEO watching it happen could describe
the problem in the boardroom and do nothing else.

The machinery was already here. `agents.status` has always had `paused` and the
dispatcher has always skipped it; the only door to it was an admin endpoint
reachable by curl. What was missing was a decision this module can represent:

* **Standing down is not pausing.** They are the same to the dispatcher and
  opposite to a reader. Pausing is the chairman reaching in, and the fix is to
  resume. A stand-down is a job that ended, and the right response is nothing.
* **A stand-down is refused while the seat holds work.** The platform already
  reported orphaned work after the fact — `blocked_by: paused_agents`, with the
  brief telling the chairman to go resume somebody. Refusing up front means that
  state cannot be reached by a stand-down at all: kill the work or reassign it,
  then stand the role down.
* **The CEO cannot stand down the two seats that exist to contradict it** — the
  last `challenge` seat, and the last reader when the constitution asks for an
  independent read. Both rules already exist at hiring time; this is the same
  rule at the other end of the project, where it matters more. The chairman can
  still do either.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.constitution import ConstitutionDoc, effective
from yaaf.core.models import (
    Agent,
    AgentKind,
    AgentStatus,
    AuditLog,
    Notice,
    NoticeKind,
    Project,
    ProjectMode,
    Requirement,
    RequirementStatus,
    Role,
    RoleVersion,
    Run,
    RunStatus,
    Task,
    TaskEvent,
    TaskStatus,
    Team,
    utcnow,
)
from yaaf.server import policy
from yaaf.server.policy import Refusal

#: Seats whose job is to read somebody else's work and disagree with it. Same
#: category `admin._refuse_without_review_seat` and `founder.roster_problems`
#: use, for the same reason: a library that grows must not need this edited.
REVIEW_CATEGORY = "challenge"

#: The condition kinds a CEO may declare. Both are arithmetic against objects
#: the plan already has, because the platform has to be able to fire one
#: without asking anybody what it meant.
CONDITION_KINDS = ("requirement_met", "stage_cleared")


# ── reading the roster ──────────────────────────────────────────────────────────


def team_role_slug(session: Session, team: Team) -> str | None:
    version = session.get(RoleVersion, team.role_version_id)
    if version is None:
        return None
    role = session.get(Role, version.role_id)
    return role.slug if role else None


def running_teams(session: Session, project_id: str) -> list[Team]:
    """Teams with at least one seat the dispatcher will wake.

    A team is stood down by standing its people down, so "is this team running"
    is a question about its members and never about a flag on the team.
    """
    out = []
    for team in session.execute(
        select(Team).where(Team.project_id == project_id).order_by(Team.created_at)
    ).scalars():
        if policy.team_agents(session, team.id, running_only=True):
            out.append(team)
    return out


def _categories(session: Session, teams: list[Team]) -> dict[str, str | None]:
    slugs = {team.id: team_role_slug(session, team) for team in teams}
    by_slug = {
        role.slug: role.category
        for role in session.execute(
            select(Role).where(Role.slug.in_({s for s in slugs.values() if s}))
        ).scalars()
    }
    return {team_id: by_slug.get(slug) for team_id, slug in slugs.items()}


# ── refusals ────────────────────────────────────────────────────────────────────


def check_hire(session: Session, project: Project, cons: ConstitutionDoc) -> None:
    """The roster ceiling.

    Hiring buys no runs: the daily ration, the hourly ceiling and the monthly
    wall are all project-level, so a bigger roster splits the same allowance
    thinner rather than raising it. What it buys is wake-ups — every seat reads
    its channels, and a seat that does nothing still spends inbox runs. So the
    ceiling is on seats, and the way past it is the verb that already exists
    for arguing you need more of something.
    """
    live = len(running_teams(session, project.id))
    if live >= cons.max_teams:
        raise Refusal(
            f"This project runs {live} teams and may run {cons.max_teams}. Stand a role "
            "down whose job is finished and the seat is yours, or put a case to the "
            "chairman with `capacity_request` on `max_teams` — what the extra seat "
            "buys, what it costs, what happens without it. Hiring does not buy you "
            "runs: the daily ration is the project's, not the seat's, so a bigger "
            "roster spreads the same allowance thinner.",
            status_code=409,
        )


def check_stand_down(session: Session, project: Project, team: Team, *,
                     by_chairman: bool = False) -> None:
    """Whether this team may stop now.

    `by_chairman` lifts the two rules that exist to keep the CEO from removing
    its own contradiction. It does not lift the work rule: orphaning a claimed
    task is the same mess whoever causes it.
    """
    holding = session.execute(
        select(Task.id, Task.title).where(
            Task.project_id == project.id,
            Task.team_id == team.id,
            Task.status.in_([TaskStatus.READY, TaskStatus.CLAIMED,
                             TaskStatus.CHECKING, TaskStatus.REVIEW]),
        )
    ).all()
    if holding:
        titles = ", ".join(title for _, title in holding[:3])
        raise Refusal(
            f"{team.name} is holding {len(holding)} open task"
            f"{'' if len(holding) == 1 else 's'} ({titles}). Standing it down now "
            "would leave that work owned by seats the dispatcher never wakes — it "
            "would sit on the board waking nobody and showing in no counter. Finish "
            "it, kill it, or move it to another team, then stand the role down.",
            status_code=409,
        )

    if by_chairman:
        return

    cons = effective(session, project.id)
    others = [t for t in running_teams(session, project.id) if t.id != team.id]
    categories = _categories(session, [team, *others])

    if categories.get(team.id) == REVIEW_CATEGORY and not any(
        categories.get(t.id) == REVIEW_CATEGORY for t in others
    ):
        if cons.deliverable_review == "independent_read":
            raise Refusal(
                f"{team.name} is the only seat left that can read somebody else's "
                "work, and this project's constitution requires an independent read "
                "before a deliverable closes. Standing it down would leave every "
                "finished task waiting on a reader who does not exist. Hire the "
                "replacement first, or ask the chairman — he can do this one.",
                status_code=409,
            )
        if project.mode in (ProjectMode.DELIVER, ProjectMode.OPERATE):
            raise Refusal(
                f"{team.name} is the last seat on this project whose job is to attack "
                "the work rather than produce it, and you wrote the plan it attacks. "
                "You are the only reader of that plan with no distance from it, which "
                "is exactly why this seat is not yours to remove. Hire another "
                "challenge role first, or ask the chairman — he can do this one.",
                status_code=409,
            )


def check_condition(condition: dict) -> dict:
    """Validate a declared stand-down condition, or refuse it in plain words."""
    if not isinstance(condition, dict):
        raise Refusal("A stand-down condition is an object, not a sentence.",
                      status_code=422)
    kind = condition.get("kind")
    if kind not in CONDITION_KINDS:
        raise Refusal(
            f"{kind!r} is not a stand-down condition. Use "
            '{"kind": "requirement_met", "keys": ["R-03"]} — the seat stops when '
            'those requirements are all met — or {"kind": "stage_cleared", '
            '"stage": 2}, which stops it when every requirement at or below that '
            "stage has settled. Both are things the platform can check; a date or a "
            "sentence is not, and would leave the seat running forever.",
            status_code=422,
        )
    if kind == "requirement_met":
        keys = condition.get("keys")
        if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
            raise Refusal("`keys` is a non-empty list of requirement keys, e.g. "
                          '["R-03", "R-04"].', status_code=422)
        return {"kind": kind, "keys": [k.strip().upper() for k in keys]}
    stage = condition.get("stage")
    if not isinstance(stage, int) or stage < 1:
        raise Refusal("`stage` is the stage number this seat works through, 1 or more.",
                      status_code=422)
    return {"kind": kind, "stage": stage}


# ── evaluating a declared condition ─────────────────────────────────────────────

#: A requirement that is no longer going to move. `CUT` counts: scope the
#: chairman removed is as settled as scope that was met, and a seat waiting on
#: a requirement nobody will ever work would otherwise never stand down.
SETTLED = (RequirementStatus.MET, RequirementStatus.CUT)


def condition_met(session: Session, project: Project, condition: dict | None) -> bool:
    if not condition:
        return False
    rows = list(session.execute(
        select(Requirement).where(Requirement.project_id == project.id)
    ).scalars())
    if condition.get("kind") == "requirement_met":
        wanted = set(condition.get("keys") or [])
        if not wanted:
            return False
        settled = {r.key for r in rows if r.status in SETTLED}
        # Every named key has to exist AND be settled. A condition naming a
        # requirement that was never written must not fire by vacuum — that
        # would stand a seat down for a typo.
        known = {r.key for r in rows}
        return wanted <= known and wanted <= settled
    stage = condition.get("stage")
    at_or_below = [r for r in rows if (r.stage or 0) <= stage and (r.stage or 0) > 0]
    return bool(at_or_below) and all(r.status in SETTLED for r in at_or_below)


# ── the transitions ─────────────────────────────────────────────────────────────


def stand_down(session: Session, project: Project, team: Team, reason: str,
               *, actor: str) -> dict:
    """Stop every seat on this team. Idempotent: already-stopped seats are left
    exactly as they are, so the platform firing a condition cannot overwrite
    the reason a person wrote."""
    now = utcnow()
    stopped = []
    for agent in policy.team_agents(session, team.id):
        if agent.status == AgentStatus.STOOD_DOWN:
            continue
        agent.status = AgentStatus.STOOD_DOWN
        agent.stood_down_at = now
        agent.stood_down_reason = reason
        stopped.append(agent.name)
        # A seat that stops mid-run keeps no live token. Same revocation the
        # chairman's pause performs, for the same reason: the run is still in
        # the sandbox and would otherwise keep writing.
        for run in session.execute(
            select(Run).where(Run.agent_id == agent.id, Run.status == RunStatus.RUNNING)
        ).scalars():
            run.token_revoked = True
    team.stood_down_at = now
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor=actor,
        action="team.stand_down", entity="team", entity_id=team.id,
        detail={"team": team.name, "reason": reason, "agents": stopped},
    ))
    return {"team_id": team.id, "team": team.name, "stood_down": stopped}


def recall(session: Session, project: Project, team: Team, reason: str,
           *, actor: str) -> dict:
    """Bring a team back. Only seats that were STOOD_DOWN return — a seat the
    chairman paused stays paused, because recalling a role is not a lever for
    undoing his intervention."""
    returned = []
    for agent in policy.team_agents(session, team.id):
        if agent.status != AgentStatus.STOOD_DOWN:
            continue
        agent.status = AgentStatus.ACTIVE
        agent.stood_down_at = None
        agent.stood_down_reason = None
        returned.append(agent)
    # A seat that was down when the credential moved was never checked against
    # it; coming back is when it is.
    from yaaf.server.api.admin import refuse_unrunnable_seats

    refuse_unrunnable_seats(session, project, returned)
    returned = [agent.name for agent in returned]
    team.stood_down_at = None
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor=actor,
        action="team.recall", entity="team", entity_id=team.id,
        detail={"team": team.name, "reason": reason, "agents": returned},
    ))
    return {"team_id": team.id, "team": team.name, "recalled": returned}


def notify_ceo(session: Session, project: Project, body: str) -> None:
    """Tell the CEO its roster changed under it. The decision was made when the
    condition was written; this is the receipt, so the next run plans against
    the roster it actually has."""
    ceo = session.execute(
        select(Agent).where(Agent.project_id == project.id, Agent.kind == AgentKind.CEO)
    ).scalar_one_or_none()
    if ceo is None:
        return
    session.add(Notice(
        id=ids.new_id(ids.NOTICE), project_id=project.id, agent_id=ceo.id,
        kind=NoticeKind.SEAT_STOOD_DOWN, body=body,
    ))


# ── the idle-seat arithmetic ────────────────────────────────────────────────────


def idle_seats(session: Session, project: Project, cons: ConstitutionDoc,
               since) -> list[dict]:
    """Seats that spent runs in the window and closed no work in it.

    Three conditions, and all three are load-bearing:

    * **Nothing open on its board.** A team sitting on unclaimed work is not a
      role whose job is finished — it is a stalled claimant, and the board
      already has flags that own that failure. Telling a CEO to stand down a
      seat with work waiting for it would be the flag proposing the wrong fix
      confidently, which is worse than not firing.
    * **It spent runs in the window.** Deliberately not "has not run recently":
      a seat waiting behind a start time has not run and is not idle, and a
      flag that cried wolf on that would be muted inside a week.
    * **It moved no work in the window.** Awake, reading its channels, spending
      the project's ration, and moving nothing.

    "Moved work" deliberately means two different things, because two kinds of
    seat do two different jobs. A maker claims a task and finishes it. A
    **reviewer never claims anything** — it reads somebody else's work and
    gives a verdict, which lands as a task event it authored moving the task
    out of REVIEW. Counting only completions would have marked every reviewer
    on the platform permanently idle however much reviewing it did, and then
    proposed standing down the one seat the platform refuses to let a CEO
    stand down: its last critic. An obligation the CEO cannot discharge is
    worse than no flag at all.
    """
    out = []
    for team in running_teams(session, project.id):
        members = policy.team_agents(session, team.id, running_only=True)
        ids_ = [a.id for a in members]
        open_work = session.execute(
            select(Task.id).where(
                Task.project_id == project.id,
                or_(Task.team_id == team.id, Task.claimed_by_agent_id.in_(ids_)),
                Task.status.in_([TaskStatus.READY, TaskStatus.CLAIMED,
                                 TaskStatus.CHECKING, TaskStatus.REVIEW]),
            )
        ).scalars().first()
        if open_work:
            continue
        runs = session.execute(
            select(Run.id).where(
                Run.project_id == project.id,
                Run.agent_id.in_(ids_),
                Run.created_at >= since,
            )
        ).scalars().all()
        if not runs:
            continue
        closed = session.execute(
            select(Task.id).where(
                Task.project_id == project.id,
                or_(Task.team_id == team.id, Task.claimed_by_agent_id.in_(ids_)),
                Task.status == TaskStatus.DONE,
                Task.updated_at >= since,
            )
        ).scalars().first()
        if closed:
            continue
        reviewed = session.execute(
            select(TaskEvent.id).where(
                TaskEvent.project_id == project.id,
                TaskEvent.from_status == TaskStatus.REVIEW,
                TaskEvent.actor.in_([f"agent:{i}" for i in ids_]),
                TaskEvent.at >= since,
            )
        ).scalars().first()
        if reviewed:
            continue
        out.append({
            "team_id": team.id, "team": team.name,
            "role_slug": team_role_slug(session, team),
            "runs": len(runs), "days": cons.staffing_review_days,
            "agents": [a.name for a in members],
        })
    return out
