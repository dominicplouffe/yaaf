"""The policy engine: every constitutional rule as a refusal or a derived fact.

Nothing in here is advisory. A function either passes silently or raises
:class:`Refusal` with a message written for the *agent* to read — the refusal
text is part of the system's interface with the model, exactly like lana's
``plan.py`` refusals were. The API layer translates a Refusal into an HTTP
error and counts it against the run's refusal cap.

Rules enforced here (constitution reference in parentheses):

- only the CEO creates top-level tasks; only a team's lead decomposes them
- success tests are structural: a platform-executable command or a named
  observable — prose is refused
- queue ceiling, subtask ceiling, external-evidence floor (verified only)
- drought: while active, internal task creation is refused
- expired hypotheses block their tasks; only the chairman extends, once
- claims: lead claims team tasks, assignee claims subtasks, one at a time
- completion requires evidence; command tests are platform-executed
- channel membership is hierarchy: boardroom (chairman<->CEO), leadership
  (CEO+leads), team rooms (lead+employees); frozen threads refuse posts
- the chairman's reporting cadence: unprompted boardroom statuses are refused
  between slots, replies to him never are
- memory caps refuse adds; only the scope's curator supersedes/forgets
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from yaaf.core import ids
from yaaf.core.constitution import (
    CADENCE_FIELDS,
    ConstitutionDoc,
    effective,
    parse_report_window,
)
from yaaf.core.models import (
    Agent,
    AgentKind,
    AgentStatus,
    Amendment,
    Approval,
    ApprovalStatus,
    AuditLog,
    Channel,
    ChannelKind,
    Commitment,
    CommitmentStatus,
    Evidence,
    EvidenceKind,
    Hypothesis,
    HypothesisStatus,
    Memory,
    MemoryScope,
    Message,
    Project,
    ProjectMode,
    ProjectStatus,
    Requirement,
    RequirementEdge,
    RequirementEvent,
    RequirementStatus,
    Run,
    RunKind,
    SuccessTestKind,
    Task,
    TaskEvent,
    TaskStatus,
    Team,
    VerificationStatus,
    utcnow,
)

log = logging.getLogger("yaaf.policy")

OPEN_TASK_STATUSES = (TaskStatus.READY, TaskStatus.CLAIMED, TaskStatus.CHECKING, TaskStatus.BLOCKED)


class Refusal(Exception):
    """A rule said no. The message is written for the agent that hit it."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# ── org derivations ─────────────────────────────────────────────────────────────


def team_agents(session: Session, team_id: str) -> list[Agent]:
    return list(
        session.execute(
            select(Agent).where(Agent.team_id == team_id).order_by(Agent.created_at)
        ).scalars()
    )


def effective_lead(session: Session, team: Team) -> Agent | None:
    """Explicit lead if set; a sole employee is the de-facto lead. Derived,
    never stored on the agent row."""
    members = team_agents(session, team.id)
    if team.lead_agent_id:
        for agent in members:
            if agent.id == team.lead_agent_id:
                return agent
    if len(members) == 1:
        return members[0]
    return None


def ceo_of(session: Session, project_id: str) -> Agent | None:
    return session.execute(
        select(Agent).where(Agent.project_id == project_id, Agent.kind == AgentKind.CEO)
    ).scalar_one_or_none()


def planner_of(session: Session, task: Task) -> Agent | None:
    """Whoever planned this task's level: the CEO for a top-level task, the
    parent's claimant for a subtask. A block is a question addressed to them."""
    if task.parent_task_id is None:
        return ceo_of(session, task.project_id)
    parent = session.get(Task, task.parent_task_id)
    planner_id = parent.claimed_by_agent_id if parent else None
    return session.get(Agent, planner_id) if planner_id else None


def is_lead(session: Session, agent: Agent) -> bool:
    if agent.team_id is None:
        return False
    team = session.get(Team, agent.team_id)
    lead = effective_lead(session, team) if team else None
    return lead is not None and lead.id == agent.id


def agent_channel_ids(session: Session, agent: Agent) -> list[str]:
    """Channels this agent is a member of, per the strict hierarchy."""
    channels = list(
        session.execute(select(Channel).where(Channel.project_id == agent.project_id)).scalars()
    )
    member_of = []
    for channel in channels:
        if channel.kind == ChannelKind.BOARDROOM and agent.kind == AgentKind.CEO or channel.kind == ChannelKind.LEADERSHIP and (
            agent.kind == AgentKind.CEO or is_lead(session, agent)
        ) or channel.kind == ChannelKind.TEAM and channel.team_id == agent.team_id:
            member_of.append(channel.id)
    return member_of


def require_project_running(project: Project) -> None:
    if project.status != ProjectStatus.RUNNING:
        raise Refusal(
            f"Project is {project.status}; no state may change until the chairman resumes it.",
            status_code=409,
        )


# ── evidence facts ──────────────────────────────────────────────────────────────


def countable_external_evidence(session: Session, project_id: str, since: datetime) -> int:
    """External evidence that actually counts: platform-attested, or
    agent-claimed AND verified. The most gameable field in the system is only
    trusted after the platform checked it."""
    return session.execute(
        select(func.count(Evidence.id)).where(
            Evidence.project_id == project_id,
            Evidence.kind == EvidenceKind.EXTERNAL,
            Evidence.verification.in_(
                [VerificationStatus.VERIFIED, VerificationStatus.NOT_APPLICABLE]
            ),
            Evidence.created_at >= since,
        )
    ).scalar_one()


def ceo_judgment(session: Session, project_id: str) -> dict:
    """The per-CEO quality signal: of the top-level tasks the CEO created, how
    many ever produced countable external evidence — directly or through their
    subtasks. What to queue, kill, and prioritize is the CEO's actual product;
    this is its scoreboard, and a primary M2 rubric input."""
    created = session.execute(
        select(func.count(Task.id)).where(
            Task.project_id == project_id, Task.parent_task_id.is_(None)
        )
    ).scalar_one()
    if not created:
        return {"tasks_created": 0, "tasks_with_verified_external": 0, "conversion": None}
    sub = aliased(Task)
    converted = session.execute(
        select(func.count(func.distinct(Task.id)))
        .select_from(Task)
        .join(sub, or_(sub.id == Task.id, sub.parent_task_id == Task.id))
        .join(Evidence, Evidence.task_id == sub.id)
        .where(
            Task.project_id == project_id,
            Task.parent_task_id.is_(None),
            Evidence.kind == EvidenceKind.EXTERNAL,
            Evidence.verification.in_(
                [VerificationStatus.VERIFIED, VerificationStatus.NOT_APPLICABLE]
            ),
        )
    ).scalar_one()
    return {
        "tasks_created": int(created),
        "tasks_with_verified_external": int(converted),
        "conversion": round(converted / created, 3),
    }


def drought_active(session: Session, project_id: str, cons: ConstitutionDoc) -> bool:
    """No countable external evidence for drought_hours. Grace period: a young
    project (younger than the drought window) is never in drought."""
    project = session.get(Project, project_id)
    window_start = utcnow() - timedelta(hours=cons.drought_hours)
    created = project.created_at if project.created_at.tzinfo else project.created_at.replace(tzinfo=UTC)
    if created > window_start:
        return False
    return countable_external_evidence(session, project_id, window_start) == 0


# ── task rules ──────────────────────────────────────────────────────────────────


def validate_success_test(cons: ConstitutionDoc, success_test: dict) -> None:
    """Structural check, not an LLM judgment: a test must name a command the
    platform can execute or an observable someone can check."""
    kind = success_test.get("type")
    if kind == SuccessTestKind.COMMAND:
        command = (success_test.get("command") or "").strip()
        if len(command) < cons.min_success_test_len:
            raise Refusal(
                "success_test.command is too short to be a real check. Give the exact "
                "command the platform should run, e.g. "
                '{"type": "command", "command": "curl -sf https://example.com/health"}.'
            )
    elif kind == SuccessTestKind.OBSERVABLE:
        target = (success_test.get("target") or "").strip()
        if len(target) < cons.min_success_test_len:
            raise Refusal(
                "success_test.target must name a checkable artifact, URL, or metric — "
                "not a sentiment. 'The report looks good' is not a test; "
                "'https://example.com/report.pdf exists and contains a Q3 revenue table' is."
            )
    else:
        raise Refusal(
            'success_test must be {"type": "command", "command": ...} (platform-executed) '
            'or {"type": "observable", "target": ...} (spot-audited). '
            "A task without a falsifiable test is not work, it is intention."
        )


def open_top_level_tasks(session: Session, project_id: str) -> list[Task]:
    return list(
        session.execute(
            select(Task).where(
                Task.project_id == project_id,
                Task.parent_task_id.is_(None),
                Task.status.in_(OPEN_TASK_STATUSES),
            )
        ).scalars()
    )


def success_test_key(success_test: dict | None) -> str | None:
    """The identity of a check across re-issues. A blocked task can only be
    killed and re-created, so the task id is worthless for spotting a checker
    that keeps killing tasks — the command is what survives."""
    if not success_test or success_test.get("type") != SuccessTestKind.COMMAND:
        return None
    command = (success_test.get("command") or "").strip()
    return command or None


def tasks_dead_on_check(
    session: Session, project_id: str, success_test: dict | None,
    exclude_task_id: str | None = None,
) -> list[Task]:
    """Tasks already killed or blocked *by* this exact success test.

    ``check_failures > 0`` is what separates "died on the check" from "the CEO
    changed its mind": a task that never failed a check never counted."""
    key = success_test_key(success_test)
    if key is None:
        return []
    rows = session.execute(
        select(Task).where(
            Task.project_id == project_id,
            Task.status.in_([TaskStatus.BLOCKED, TaskStatus.KILLED]),
            Task.check_failures > 0,
        )
    ).scalars()
    return [
        t for t in rows
        if t.id != exclude_task_id and success_test_key(t.success_test) == key
    ]


def check_task_gate(cons: ConstitutionDoc, not_before: datetime | None) -> None:
    """How far ahead work may be laid out.

    `not_before` is a real and useful verb — it is how a CEO sequences a weekly
    rhythm, or a step that only makes sense after another lands. It is also how
    a company goes silent for no visible reason: one CEO gated its only open
    task two hours out, the dispatcher correctly woke nobody, and the chairman
    paused the project with the reason "been idle and I have no idea why".

    The rule is not "never schedule" — it is "sequence on the predecessor
    landing, not on a clock you guessed at". A gate you can see the end of from
    here is fine; a gate that outlives the chairman's patience is not.
    """
    if not_before is None or cons.max_task_gate_hours <= 0:
        return
    gate = not_before if not_before.tzinfo else not_before.replace(tzinfo=UTC)
    limit = utcnow() + timedelta(hours=cons.max_task_gate_hours)
    if gate > limit:
        raise Refusal(
            f"not_before is {(gate - utcnow()).total_seconds() / 3600:.1f}h out and the "
            f"limit is {cons.max_task_gate_hours}h. Work gated on a clock wakes nobody "
            "and explains itself to no one: if this task depends on another finishing, "
            "create it when that one lands (you will be woken) rather than guessing at "
            "a time. If it genuinely belongs to a later date, say so to the chairman.",
            status_code=422,
        )


def check_task_create(
    session: Session,
    cons: ConstitutionDoc,
    project: Project,
    creator: Agent,
    *,
    team_id: str,
    parent_task_id: str | None,
    success_test: dict,
    produces_external_evidence: bool,
    hypothesis_id: str | None,
    requirement: Requirement | None = None,
    not_before: datetime | None = None,
) -> None:
    require_project_running(project)
    validate_success_test(cons, success_test)
    check_task_gate(cons, not_before)

    # What is this work FOR? Outside Explore, a task that serves nothing is a
    # refusal rather than a smell. The audit's diagnosis in one line was that
    # work got planned, claimed, done and evidenced without ever becoming a
    # thing that exists — a pipeline with no terminus, because the CEO's
    # standing instruction was "keep the queue fed but small".
    if requires_requirements(project):
        # Nothing gets built before there is a plan. The chairman's words were
        # that the CEO's FIRST task is the plan; this is that, as a refusal
        # rather than an instruction it could read past.
        plan = plan_requirement(session, project.id)
        if plan is not None and plan.status not in CLOSED_REQUIREMENT_STATUSES:
            cons_now = cons
            outstanding = evaluate_plan(session, project, cons_now)
            raise Refusal(
                "No work yet — this project has no plan. " + " ".join(outstanding)
                + " Propose requirements with requirement_propose; the chairman accepts "
                "them; the moment the plan holds, R-00 closes by itself and the board "
                "opens.",
                status_code=409,
            )
        if requirement is None:
            raise Refusal(
                "Every task must serve a requirement. Name the one this makes true with "
                "requirement_id — if none of them do, either this work is not needed or "
                "the plan is missing something, and both of those are worth saying out "
                "loud before spending a run on it.",
                status_code=422,
            )
        check_work_allowed_for(session, project, requirement)
    elif requirement is not None:
        raise Refusal(
            "This is an Explore project: it has no requirements, by design. Its shape is "
            "bets and evidence, and the chairman judges when it has earned its keep.",
            status_code=422,
        )

    # Two tasks have now died on this exact check. Re-issuing is the only lever
    # a planner has over a blocked task, so without this the same broken
    # checker can mint fresh tasks — and a fresh task carries a fresh failure
    # counter — indefinitely.
    dead = tasks_dead_on_check(session, project.id, success_test)
    if len(dead) >= 2:
        raise Refusal(
            f"{len(dead)} tasks have already died on this exact success test and none "
            "ever passed it. At that point the check is the suspect, not the deliverable "
            "— re-issuing a third time buys another round of the same failure. The "
            "chairman has been told. Fix the check itself, write a different one, or "
            "wait for him. Tasks: " + ", ".join(t.id for t in dead[:5]),
            status_code=409,
        )

    team = session.get(Team, team_id)
    if team is None or team.project_id != project.id:
        raise Refusal("Unknown team for this project.", status_code=404)

    if hypothesis_id is not None:
        hypothesis = session.get(Hypothesis, hypothesis_id)
        if hypothesis is None or hypothesis.project_id != project.id:
            raise Refusal("Unknown hypothesis.", status_code=404)
        if hypothesis.status != HypothesisStatus.ACTIVE:
            raise Refusal(
                f"Hypothesis {hypothesis_id} is {hypothesis.status}. Work may not be created "
                "under a dead hypothesis — pivot or pick a live one. Only the chairman can "
                "extend an expired hypothesis, and only once."
            )

    if parent_task_id is None:
        _check_top_level_create(session, cons, project, creator, produces_external_evidence)
    else:
        _check_subtask_create(session, cons, creator, team, parent_task_id)


def _check_top_level_create(
    session: Session,
    cons: ConstitutionDoc,
    project: Project,
    creator: Agent,
    produces_external_evidence: bool,
) -> None:
    if creator.kind != AgentKind.CEO:
        raise Refusal(
            "Only the CEO creates tasks. Found something that needs doing? "
            "POST /agent/suggestions and the CEO will see it next planning cycle. "
            "That is the whole of your authority over the backlog.",
            status_code=403,
        )

    open_tasks = open_top_level_tasks(session, project.id)
    if len(open_tasks) >= cons.queue_ceiling:
        raise Refusal(
            f"Queue ceiling: {len(open_tasks)}/{cons.queue_ceiling} tasks are open. "
            "You cannot plan your way around a stalled project by adding more work — "
            "finish something or kill something. A refusal here is the system working.",
            status_code=409,
        )

    if drought_active(session, project.id, cons) and not produces_external_evidence:
        raise Refusal(
            f"Evidence drought: no verified external evidence in {cons.drought_hours}h. "
            "While the drought holds you may only create tasks with "
            "produces_external_evidence=true. Your legal moves: change the channel, "
            "change the offer, change the bet, or escalate to the chairman.",
            status_code=409,
        )

    # External-evidence floor, computed over the queue as it would be after this add.
    would_be = open_tasks + [None]
    if len(would_be) >= cons.external_ratio_applies_from:
        external = sum(1 for t in open_tasks if t.produces_external_evidence) + (
            1 if produces_external_evidence else 0
        )
        if external / len(would_be) < cons.external_ratio_min:
            raise Refusal(
                f"External-evidence floor: at least {cons.external_ratio_min:.0%} of open "
                "tasks must produce external evidence, and this add would break that. "
                "You cannot fill the queue with plumbing.",
                status_code=409,
            )


def _check_subtask_create(
    session: Session,
    cons: ConstitutionDoc,
    creator: Agent,
    team: Team,
    parent_task_id: str,
) -> None:
    parent = session.get(Task, parent_task_id)
    if parent is None or parent.team_id != team.id or team.id != creator.team_id:
        raise Refusal("Parent task must belong to your own team.", status_code=403)

    lead = effective_lead(session, team)
    if lead is None or lead.id != creator.id:
        raise Refusal(
            "Only the team lead decomposes a team task into assignments.", status_code=403
        )
    if parent.parent_task_id is not None:
        raise Refusal("Subtasks cannot have subtasks; decomposition is one level deep.")
    if parent.status != TaskStatus.CLAIMED or parent.claimed_by_agent_id != creator.id:
        raise Refusal(
            "Claim the team task first (POST /agent/tasks/{id}/claim), then decompose it. "
            "A lead decomposes work it owns, not work sitting in the queue."
        )
    open_subtasks = session.execute(
        select(func.count(Task.id)).where(
            Task.parent_task_id == parent_task_id,
            Task.status.in_(OPEN_TASK_STATUSES),
        )
    ).scalar_one()
    if open_subtasks >= cons.subtask_ceiling:
        raise Refusal(
            f"Subtask ceiling: {open_subtasks}/{cons.subtask_ceiling} open under this task. "
            "Finish assignments before creating more.",
            status_code=409,
        )


def check_task_claim(session: Session, project: Project, agent: Agent, task: Task) -> None:
    require_project_running(project)
    if task.status != TaskStatus.READY:
        raise Refusal(f"Task {task.id} is {task.status}, not ready. It cannot be claimed.", 409)
    if task.not_before is not None:
        gate = task.not_before if task.not_before.tzinfo else task.not_before.replace(tzinfo=UTC)
        if gate > utcnow():
            raise Refusal(f"Task {task.id} is gated until {gate.isoformat()}.", 409)

    if task.parent_task_id is not None:
        if task.assignee_agent_id != agent.id:
            raise Refusal("This assignment belongs to another agent.", status_code=403)
    else:
        team = session.get(Team, task.team_id)
        lead = effective_lead(session, team) if team else None
        if lead is None or lead.id != agent.id:
            raise Refusal(
                "Team tasks are claimed by the team lead, who decomposes or does them. "
                "If you are an employee, work the assignments your lead creates for you.",
                status_code=403,
            )

    # The requester owns the test. A claimant who wrote the check that judges
    # it is grading its own homework with a pen it chose — the convention one
    # CEO invented after thirty hours of being lied to, promoted to a rule.
    #
    # Solo mode is exempt, because the rule is unsatisfiable there rather than
    # relaxed there: with one agent in the company, there is nobody to hand it
    # to, and refusing would mean a solo project could never claim anything.
    # This is a real difference in what "done" is worth between topologies —
    # a solo arm self-grades every observable — and the topology experiment
    # should read it as a cost of solo, not pretend the arms are equivalent.
    if task.created_by_agent_id == agent.id and _has_another_agent(session, agent):
        raise Refusal(
            "You wrote this task's success test, so you may not also be the one it "
            "judges. Whoever asks for the work owns the check — that is the whole "
            "reason the check means anything. Hand it to someone else.",
            status_code=403,
        )

    already = session.execute(
        select(func.count(Task.id)).where(
            Task.claimed_by_agent_id == agent.id,
            Task.status.in_([TaskStatus.CLAIMED, TaskStatus.CHECKING]),
        )
    ).scalar_one()
    if already > 0:
        raise Refusal(
            "One item at a time: you already hold a claimed task. Complete it, block it, "
            "or let it be reaped — do not collect work.",
            status_code=409,
        )


def _has_another_agent(session: Session, agent: Agent) -> bool:
    """Is there anyone else in this company at all? Solo mode is one agent that
    is CEO, sole team member and derived lead; every separation-of-duties rule
    degrades to nothing there, and pretending otherwise would just deadlock."""
    return session.execute(
        select(func.count(Agent.id)).where(
            Agent.project_id == agent.project_id,
            Agent.id != agent.id,
            Agent.status == AgentStatus.ACTIVE,
        )
    ).scalar_one() > 0


def check_task_complete(
    session: Session, project: Project, agent: Agent, task: Task, evidence: str
) -> None:
    require_project_running(project)
    if task.status != TaskStatus.CLAIMED:
        raise Refusal(f"Task {task.id} is {task.status}; only a claimed task can be completed.", 409)
    if task.claimed_by_agent_id != agent.id:
        raise Refusal("Only the claimant completes a task.", status_code=403)
    if len((evidence or "").strip()) < 10:
        raise Refusal(
            "Completion requires observed evidence: what you ran or looked at and what you "
            "actually saw. 'Done' is not evidence. Quote the output."
        )
    open_subtasks = session.execute(
        select(func.count(Task.id)).where(
            Task.parent_task_id == task.id, Task.status.in_(OPEN_TASK_STATUSES)
        )
    ).scalar_one()
    if open_subtasks > 0:
        raise Refusal(
            f"{open_subtasks} subtask(s) are still open under this task. A team task is done "
            "when its assignments are done or killed — not before.",
            status_code=409,
        )


def check_task_kill(session: Session, agent: Agent, task: Task) -> None:
    if agent.kind == AgentKind.CEO and task.parent_task_id is None:
        return
    if task.parent_task_id is not None:
        team = session.get(Team, task.team_id)
        lead = effective_lead(session, team) if team else None
        if lead is not None and lead.id == agent.id:
            return
    raise Refusal(
        "Only the CEO kills team tasks; only the team lead kills its own subtasks.",
        status_code=403,
    )


def record_task_event(
    session: Session,
    task: Task,
    to_status: str,
    actor: str,
    note: str | None = None,
    from_status: str | None = None,
) -> None:
    session.add(
        TaskEvent(
            id=ids.new_id(ids.TASK_EVENT),
            project_id=task.project_id,
            task_id=task.id,
            from_status=from_status if from_status is not None else task.status,
            to_status=to_status,
            actor=actor,
            note=note,
        )
    )


# ── requirement rules ───────────────────────────────────────────────────────────
#
# A requirement is what work is FOR. Three rules make it load-bearing rather
# than decorative, and all three are enforced here or by an absent verb:
#
#   * the CEO proposes, the chairman accepts — scope is never self-served
#   * nothing but the platform writes `met` — there is no agent tool for it
#   * work may not be created for a requirement whose dependencies are unmet
#
# The third is what turns the dependency graph from a picture into a gate.


# A chairman's rejection of an observable is kept ON the requirement, not only
# in a notice the first run consumes and destroys. `check_output` is where it
# goes, because it is literally the output of the check — but that column also
# holds a command's stdout, so the prefix is what lets every reader downstream
# (the context pack, the Plan tab, the dispatcher's ladder) tell a person's
# verdict from a shell's. Change it here and they all follow.
VERDICT_REJECTED = "chairman, not met: "
# The note itself is capped at 10_000 by the API model; this leaves room for
# the prefix rather than silently eating the last words of a long rejection.
VERDICT_NOTE_CAP = 10_400


def rejected_by_chairman(requirement) -> str | None:
    """The chairman's rejection note, or None if the last thing to happen to
    this requirement was not one."""
    output = requirement.check_output or ""
    if not output.startswith(VERDICT_REJECTED):
        return None
    return output[len(VERDICT_REJECTED):]


OPEN_REQUIREMENT_STATUSES = (
    RequirementStatus.PENDING,
    RequirementStatus.IN_PROGRESS,
    RequirementStatus.CHECKING,
)

CLOSED_REQUIREMENT_STATUSES = (RequirementStatus.MET, RequirementStatus.CUT)


_UNSET = object()


def record_requirement_event(
    session: Session,
    requirement: Requirement,
    to_status: str,
    actor: str,
    note: str | None = None,
    from_status: str | None | object = _UNSET,
) -> None:
    """`from_status=None` means "there was no prior status" — the birth of a
    requirement — and is deliberately distinguishable from omitting it, which
    means "read it off the row". Collapsing the two would stamp the first event
    with the status it was being created in and make the log lie about where
    scope came from."""
    session.add(
        RequirementEvent(
            id=ids.new_id(ids.REQUIREMENT_EVENT),
            project_id=requirement.project_id,
            requirement_id=requirement.id,
            from_status=requirement.status if from_status is _UNSET else from_status,
            to_status=to_status,
            actor=actor,
            note=note,
        )
    )


def next_requirement_key(session: Session, project_id: str) -> str:
    """R-01, R-02, … Stable and human-readable, because the chairman says "cut
    R-09" and an opaque id would make him quote a hash back at his own CEO."""
    used = session.execute(
        select(Requirement.key).where(Requirement.project_id == project_id)
    ).scalars().all()
    highest = 0
    for key in used:
        try:
            highest = max(highest, int(str(key).split("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    return f"R-{highest + 1:02d}"


def requires_requirements(project: Project) -> bool:
    """Explore has no scope by design — it is the open-ended arm, and demanding
    a requirement there would be demanding a plan for work whose whole point is
    that the plan is not known yet."""
    return project.mode != ProjectMode.EXPLORE


def check_requirement_propose(
    cons: ConstitutionDoc, project: Project, agent: Agent, acceptance_test: dict
) -> None:
    """Only the CEO proposes scope, and only in a mode that has scope. A lead
    proposing requirements would be a team writing its own definition of done
    one level down; a proposal in Explore would park the company forever on an
    acceptance the mode deliberately gives the chairman no screen to perform."""
    if project.mode == ProjectMode.EXPLORE:
        raise Refusal(
            "This is an Explore project: scope is open by design, there is no "
            "requirement list, and the chairman has no Plan screen to accept a "
            "proposal on — waiting on acceptance here would wait forever. Create "
            "tasks directly toward the goal. If this work now needs a fixed "
            "definition of done, suggest that to the chairman: reshaping the "
            "project as Deliver is a decision only the chairman can make.",
            status_code=409,
        )
    if agent.kind != AgentKind.CEO:
        raise Refusal(
            "Only the CEO proposes requirements. If you think something must be true at "
            "the end and is not on the list, say so to the CEO — a suggestion is the "
            "authority you have over scope, and it survives a crash.",
            status_code=403,
        )
    validate_success_test(cons, acceptance_test)


def check_requirement_accept(requirement: Requirement) -> None:
    if requirement.status != RequirementStatus.PROPOSED:
        raise Refusal(
            f"{requirement.key} is {requirement.status}, not proposed. Only a proposal "
            "can be accepted into scope.",
            status_code=409,
        )


def check_requirement_cut(requirement: Requirement) -> None:
    if requirement.status in CLOSED_REQUIREMENT_STATUSES:
        raise Refusal(
            f"{requirement.key} is already {requirement.status}.", status_code=409
        )


def unmet_dependencies(session: Session, requirement: Requirement) -> list[Requirement]:
    """Everything that must be true before this one may be worked."""
    rows = session.execute(
        select(Requirement)
        .join(RequirementEdge, RequirementEdge.from_requirement_id == Requirement.id)
        .where(RequirementEdge.to_requirement_id == requirement.id)
    ).scalars().all()
    return [r for r in rows if r.status not in CLOSED_REQUIREMENT_STATUSES]


def would_cycle(session: Session, from_id: str, to_id: str) -> list[str] | None:
    """Does adding from→to close a loop? Returns the offending path if so.

    A cycle makes the critical path infinite and the dependency gate
    unsatisfiable — every requirement in the loop waits for another one in the
    same loop, forever, and the company looks idle for a reason no screen could
    explain. Cheaper to refuse at the edge than to detect as a mystery later.
    """
    if from_id == to_id:
        return [from_id, to_id]
    # Walk forward from `to`: if we can already reach `from`, the new edge closes it.
    seen: set[str] = set()
    stack: list[tuple[str, list[str]]] = [(to_id, [to_id])]
    while stack:
        node, path = stack.pop()
        if node == from_id:
            return [*path, from_id]
        if node in seen:
            continue
        seen.add(node)
        for nxt in session.execute(
            select(RequirementEdge.to_requirement_id).where(
                RequirementEdge.from_requirement_id == node
            )
        ).scalars():
            stack.append((nxt, [*path, nxt]))
    return None


def check_requirement_edge(
    session: Session, project: Project, source: Requirement, target: Requirement
) -> None:
    if source.project_id != project.id or target.project_id != project.id:
        raise Refusal("Both requirements must belong to this project.", status_code=404)
    if source.id == target.id:
        raise Refusal(
            f"{source.key} cannot depend on itself.", status_code=422
        )
    existing = session.execute(
        select(RequirementEdge).where(
            RequirementEdge.from_requirement_id == source.id,
            RequirementEdge.to_requirement_id == target.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise Refusal(f"{target.key} already depends on {source.key}.", status_code=409)

    loop = would_cycle(session, source.id, target.id)
    if loop is not None:
        keys = {
            r.id: r.key
            for r in session.execute(
                select(Requirement).where(Requirement.id.in_(loop))
            ).scalars()
        }
        drawn = " → ".join(keys.get(node, node) for node in loop)
        raise Refusal(
            f"That edge closes a loop: {drawn}. A cycle can never be satisfied — every "
            "requirement in it waits for another one in it — so the work would sit "
            "unstartable with nothing to point at. Break the loop first.",
            status_code=409,
        )


def check_work_allowed_for(session: Session, project: Project, requirement: Requirement) -> None:
    """The gate that makes the graph real."""
    if requirement.status == RequirementStatus.PROPOSED:
        raise Refusal(
            f"{requirement.key} is only proposed. The chairman has not accepted it into "
            "scope, so it is not yet something to build. Work on what he has accepted, "
            "or wait for him.",
            status_code=409,
        )
    if requirement.status in CLOSED_REQUIREMENT_STATUSES:
        raise Refusal(
            f"{requirement.key} is {requirement.status}. "
            + ("It is already true — creating more work for it would be work with no "
               "outcome to change." if requirement.status == RequirementStatus.MET
               else "The chairman cut it from scope."),
            status_code=409,
        )
    blocked = unmet_dependencies(session, requirement)
    if blocked:
        names = ", ".join(f"{r.key} ({r.status})" for r in blocked[:5])
        raise Refusal(
            f"{requirement.key} depends on work that is not finished: {names}. Its "
            "dependencies were accepted as the order this has to happen in, so starting "
            "here would be starting in the middle. Work the blockers first.",
            status_code=409,
        )

    # The waterfall, enforced. Stages are the chairman's sequencing of his own
    # requirements — which is what the first design of this got wrong: its
    # phases were CEO-authored and CEO-estimated, so the gate measured nothing
    # but the CEO's ability to write a number that fit.
    current = open_stage(session, requirement.project_id)
    if requirement.stage > current:
        raise Refusal(
            f"{requirement.key} is in stage {requirement.stage} and stage {current} is "
            "still open. The chairman owns this order. Finish or cut what is left in "
            f"stage {current} and this opens by itself.",
            status_code=409,
        )


# ── the plan: R-00, stages, and arithmetic that has to close ────────────────────
#
# The chairman asked that a Deliver or Operate project's FIRST task be a
# waterfall-type plan "built from the budget, team, and all the arguments that
# have been set by the user (i.e. number of daily runs, budget, number of open
# tasks allowed, and so on)."
#
# An earlier design deleted the arithmetic half of that — "the feasibility
# check, replaced by a definition of done you set yourself" — on the correct
# grounds that per-phase budgets were a guess the CEO could satisfy by writing
# smaller numbers. That objection is answerable, and this is the answer: the
# estimate is recorded, compared against what the work actually cost, and the
# next plan is judged at the CEO's own historical ratio. Writing smaller
# numbers buys one round and is then priced in.

PLAN_KEY = "R-00"

# Below this many priced runs there is no honest cost-per-run, so the money
# half of feasibility is skipped and said to be skipped rather than guessed.
MIN_PRICED_RUNS = 5

# Two settled requirements is not a trend, but it is enough to stop treating a
# 3x under-estimate as noise. Below it the multiplier is 1.0.
MIN_SETTLED_FOR_BIAS = 2


def plan_requirement(session: Session, project_id: str) -> Requirement | None:
    """R-00, the planning requirement. Platform-created, platform-judged."""
    return session.execute(
        select(Requirement).where(
            Requirement.project_id == project_id, Requirement.key == PLAN_KEY
        )
    ).scalar_one_or_none()


def current_cycle(session: Session, project_id: str):
    """The one open cycle of an Operate project, or None. At most one is ever
    open — the janitor closes before it opens, in the same transition."""
    from yaaf.core.models import Cycle, CycleStatus

    return session.execute(
        select(Cycle).where(Cycle.project_id == project_id,
                            Cycle.status == CycleStatus.OPEN)
    ).scalar_one_or_none()


def scope_requirements(session: Session, project_id: str, *,
                       all_cycles: bool = False) -> list[Requirement]:
    """Everything except R-00. Planning is work, but it is not the deliverable,
    and counting it as scope would put the plan in its own denominator.

    For an Operate project this is the OPEN CYCLE's scope: each turn of the
    rhythm plans fresh, and unsettled work carries its cycle_id forward when
    the janitor rolls. `all_cycles=True` is for the readers that must learn
    across turns — the estimate-bias multiplier above all, because a CEO that
    lowballs every cycle would otherwise start each one forgiven.
    """
    rows = [
        r
        for r in session.execute(
            select(Requirement)
            .where(Requirement.project_id == project_id)
            .order_by(Requirement.stage, Requirement.key)
        ).scalars()
        if r.key != PLAN_KEY
    ]
    if all_cycles:
        return rows
    project = session.get(Project, project_id)
    if project is not None and project.mode == ProjectMode.OPERATE:
        cycle = current_cycle(session, project_id)
        if cycle is not None:
            rows = [r for r in rows if r.cycle_id == cycle.id]
    return rows


def create_plan_requirement(session: Session, project: Project) -> Requirement:
    """The obligation a Deliver or Operate project starts with.

    It is accepted on creation rather than proposed, because it is not scope
    the chairman chose — it is the platform saying "before you spend anything,
    say what you are going to do and show that it fits".
    """
    requirement = Requirement(
        id=ids.new_id(ids.REQUIREMENT),
        project_id=project.id,
        key=PLAN_KEY,
        statement=(
            "A plan exists: requirements the chairman has accepted, each in a stage, "
            "each with a run estimate, sequenced without a loop, and adding up to "
            "something this project can afford."
        ),
        acceptance_test={"type": SuccessTestKind.PLAN},
        status=RequirementStatus.PENDING,
        stage=0,
        accepted_at=utcnow(),
    )
    session.add(requirement)
    session.flush()
    record_requirement_event(session, requirement, RequirementStatus.PENDING,
                             "platform", note="project started in "
                                              f"{project.mode} mode", from_status=None)
    return requirement


def open_stage(session: Session, project_id: str) -> int:
    """The lowest stage that still has unsettled accepted work.

    This is the waterfall, enforced. Stages are the chairman's sequencing of
    his own requirements, so the gate cannot be gamed the way CEO-authored
    phases could: the CEO proposes an order, he owns it, and the platform
    refuses work that jumps ahead of it.
    """
    stages = [
        r.stage
        for r in scope_requirements(session, project_id)
        if r.status in OPEN_REQUIREMENT_STATUSES
    ]
    return min(stages) if stages else 1


def runs_spent_on(session: Session, requirement_id: str) -> int:
    """Every run stamped against this requirement — including the planning and
    supervision runs that carried no single task, which is the half of spend
    that used to be unattributable by construction."""
    return session.execute(
        select(func.count(Run.id)).where(Run.requirement_id == requirement_id)
    ).scalar_one()


def estimate_bias(session: Session, project_id: str) -> tuple[float, dict]:
    """How wrong this CEO's estimates have been, as a multiplier ≥ 1.

    Never below 1. A ratio that could shrink an estimate would let a CEO buy
    headroom by over-estimating once and then under-estimating everything
    after — the same gaming the multiplier exists to price in, run backwards.
    """
    settled = [
        r
        for r in scope_requirements(session, project_id, all_cycles=True)
        if r.status in CLOSED_REQUIREMENT_STATUSES and r.est_runs
    ]
    pairs = [(r.est_runs, runs_spent_on(session, r.id)) for r in settled]
    pairs = [(est, actual) for est, actual in pairs if actual > 0]
    if len(pairs) < MIN_SETTLED_FOR_BIAS:
        return 1.0, {"basis": "no history yet", "settled": len(pairs)}
    estimated = sum(est for est, _ in pairs)
    actual = sum(a for _, a in pairs)
    ratio = max(1.0, actual / estimated) if estimated else 1.0
    return ratio, {
        "basis": f"{len(pairs)} settled requirements",
        "estimated": estimated,
        "actual": actual,
        "multiplier": round(ratio, 2),
    }


def avg_usd_per_run(session: Session, project_id: str) -> float | None:
    """Trailing cost of a run, this project first and the whole box as a
    fallback. None when there is not enough priced history to be honest."""
    for scope in ({Run.project_id: project_id}, None):
        stmt = select(func.avg(Run.cost_usd), func.count(Run.id)).where(
            Run.cost_usd.is_not(None), Run.cost_usd > 0
        )
        if scope is not None:
            stmt = stmt.where(Run.project_id == project_id)
        average, count = session.execute(stmt).one()
        if count and count >= MIN_PRICED_RUNS and average:
            return float(average)
    return None


def affordable_runs(session: Session, project: Project, cons: ConstitutionDoc) -> dict:
    """How many runs the money left will actually buy.

    Under subscription billing there is no per-run price to divide by, so this
    says so instead of inventing one — the honest answer to "can we afford it"
    is sometimes "that is not a question this billing mode can answer".
    """
    from yaaf.server.janitor import month_to_date

    if project.billing_mode != "api_key":
        return {"known": False, "why": "subscription billing prices no individual run"}
    spent, _ = month_to_date(session, project.id)
    remaining = max(0.0, cons.monthly_usd_cap - spent)
    per_run = avg_usd_per_run(session, project.id)
    if per_run is None:
        return {"known": False, "why": f"fewer than {MIN_PRICED_RUNS} priced runs so far",
                "usd_remaining": round(remaining, 2)}
    return {
        "known": True,
        "usd_remaining": round(remaining, 2),
        "usd_per_run": round(per_run, 4),
        "runs": int(remaining // per_run),
    }


def plan_arithmetic(session: Session, project: Project, cons: ConstitutionDoc,
                    extra_est: int = 0, include_proposed: bool = False) -> dict:
    """The whole sum, in one place, so a refusal can show its working.

    Two callers ask slightly different questions and the difference matters.
    While the CEO is *planning*, unaccepted proposals count — that is the
    feedback that makes it cut something now rather than discover the wall
    later. When the platform decides whether the plan HOLDS, only what the
    chairman actually accepted counts: otherwise a CEO could freeze its own
    board by proposing ten speculative requirements nobody ever took.
    """
    live = [
        r
        for r in scope_requirements(session, project.id)
        if r.status not in CLOSED_REQUIREMENT_STATUSES
        and (include_proposed or r.status != RequirementStatus.PROPOSED)
    ]
    estimated = sum(r.est_runs or 0 for r in live) + extra_est
    multiplier, bias = estimate_bias(session, project.id)
    adjusted = int(round(estimated * multiplier))
    budget = affordable_runs(session, project, cons)
    return {
        "requirements": len(live) + (1 if extra_est else 0),
        "estimated_runs": estimated,
        "multiplier": round(multiplier, 2),
        "adjusted_runs": adjusted,
        "bias": bias,
        "budget": budget,
        "days_at_the_daily_cap": round(adjusted / cons.runs_per_project_per_day, 1)
        if cons.runs_per_project_per_day else None,
        "affordable": (not budget["known"]) or adjusted <= budget["runs"],
    }


def check_plan_affordable(session: Session, project: Project, cons: ConstitutionDoc,
                          extra_est: int) -> None:
    """Refuse a proposal the project cannot pay for, showing the arithmetic.

    The CEO is not asked to guess what it can afford; it is told, in runs, with
    its own historical over-run priced in. That turns "we ran out of money"
    from a thing discovered at 100% of the cap into a thing decided at planning
    time, when cutting is still cheap.
    """
    if not requires_requirements(project):
        return
    sums = plan_arithmetic(session, project, cons, extra_est=extra_est,
                           include_proposed=True)
    if sums["affordable"]:
        return
    budget = sums["budget"]
    over = sums["adjusted_runs"] - budget["runs"]
    bias_note = ""
    if sums["multiplier"] > 1.0:
        bias_note = (
            f" That is your {sums['estimated_runs']} estimated runs at ×"
            f"{sums['multiplier']}, because {sums['bias']['basis']} came in at "
            f"{sums['bias']['actual']} runs against {sums['bias']['estimated']} estimated."
        )
    raise Refusal(
        f"This plan does not fit the money. It needs about {sums['adjusted_runs']} runs "
        f"and ${budget['usd_remaining']} at ${budget['usd_per_run']} a run buys about "
        f"{budget['runs']} — you are over by roughly {over}.{bias_note} "
        "Cut something, estimate it smaller only if you actually believe it, or put the "
        "budget to the chairman as an approval. Adding scope you cannot pay for is how a "
        "company arrives at 100% of its cap with nothing finished.",
        status_code=409,
    )


def evaluate_plan(session: Session, project: Project, cons: ConstitutionDoc) -> list[str]:
    """What is still missing before R-00 can be met. Empty means the plan holds.

    Structural, not a judgement: the platform is not deciding whether the plan
    is any good — that is the chairman's call when he accepts requirements. It
    is checking that a plan exists at all, is ordered, is estimated, and adds
    up. Every one of those is a thing an agent could otherwise skip and still
    report itself ready.
    """
    missing: list[str] = []
    scope = scope_requirements(session, project.id)
    accepted = [r for r in scope if r.status not in (RequirementStatus.PROPOSED,)]
    live = [r for r in accepted if r.status not in CLOSED_REQUIREMENT_STATUSES]

    if not accepted:
        proposed = [r for r in scope if r.status == RequirementStatus.PROPOSED]
        if proposed:
            missing.append(
                f"{len(proposed)} requirement(s) proposed and none accepted yet — the "
                "chairman decides what is in scope, so this is waiting on him, not on you."
            )
        else:
            missing.append(
                "no requirements proposed yet. Say what must be TRUE at the end, one "
                "statement at a time, with an acceptance test for each."
            )
        return missing

    unestimated = [r.key for r in live if not r.est_runs]
    if unestimated:
        missing.append(
            f"no run estimate on {', '.join(sorted(unestimated))}. An estimate is what "
            "makes the plan checkable against the budget; it is compared with what the "
            "work actually costs and your next plan is read at that ratio."
        )

    sums = plan_arithmetic(session, project, cons)
    if not sums["affordable"]:
        missing.append(
            f"the plan needs about {sums['adjusted_runs']} runs and the budget buys about "
            f"{sums['budget']['runs']}. Cut scope or ask the chairman for budget."
        )
    return missing


def charge_failure(session: Session, run: Run) -> None:
    """A run that died charges the requirement it was serving.

    This is the "errors" third of the chairman's ask — "adaptable to plan
    changes, parameter changes, errors" — as a number instead of a vibe. It is
    what lets a review say "the rate fell because one requirement ate fifty
    dead runs" rather than "we got slower". Called exactly once, at the run's
    terminal transition, from both places a run can die: finalize (failed,
    killed, refusal-terminated) and the janitor (reaped).
    """
    if not run.requirement_id:
        return
    requirement = session.get(Requirement, run.requirement_id)
    if requirement is None or requirement.status in CLOSED_REQUIREMENT_STATUSES:
        return
    requirement.failure_load += 1


def requirement_edges(session: Session, project_id: str) -> list[RequirementEdge]:
    return list(
        session.execute(
            select(RequirementEdge).where(RequirementEdge.project_id == project_id)
        ).scalars()
    )


def critical_path(session: Session, project_id: str) -> dict:
    """The longest chain of unsettled work, weighted by adjusted estimates.

    This is what makes the projection *explicable* rather than asserted: a
    number of days is an argument nobody can act on, but "R-07 sits on the
    path and has eaten 53 runs" names the thing to cut, fix or fund. Computed
    server-side so the screen can never disagree with the data it draws.

    Weights are est_runs × the CEO's historical bias — the same arithmetic the
    feasibility gate uses, so the path and the refusals never quote different
    numbers. Settled requirements weigh nothing but still conduct: met work on
    a chain does not shorten the drawing, it just stops costing.
    """
    scope = scope_requirements(session, project_id)
    if not scope:
        return {"keys": [], "adjusted_runs": 0}
    multiplier, _ = estimate_bias(session, project_id)
    by_id = {r.id: r for r in scope}
    weight = {
        r.id: (round((r.est_runs or 0) * multiplier)
               if r.status not in CLOSED_REQUIREMENT_STATUSES else 0)
        for r in scope
    }
    inbound: dict[str, list[str]] = {r.id: [] for r in scope}
    for edge in requirement_edges(session, project_id):
        if edge.from_requirement_id in by_id and edge.to_requirement_id in by_id:
            inbound[edge.to_requirement_id].append(edge.from_requirement_id)

    # Longest path over a DAG (would_cycle guards inserts, so ordering by
    # repeated relaxation terminates). Memoized walk from every sink.
    best: dict[str, tuple[int, list[str]]] = {}

    def walk(node: str) -> tuple[int, list[str]]:
        if node in best:
            return best[node]
        head_cost, head_path = 0, []
        for prev in inbound[node]:
            cost, path = walk(prev)
            if cost > head_cost:
                head_cost, head_path = cost, path
        best[node] = (head_cost + weight[node], [*head_path, node])
        return best[node]

    top_cost, top_path = 0, []
    for r in scope:
        cost, path = walk(r.id)
        if cost > top_cost:
            top_cost, top_path = cost, path
    return {
        "keys": [by_id[n].key for n in top_path],
        "adjusted_runs": top_cost,
        "multiplier": round(multiplier, 2),
    }


# ── the commitment: a date, and what may not happen past it ─────────────────────
#
# The chairman asked for "a delivery date and anything of substance that can
# offer me some sort of guarantee". Two absent verbs and one refusal carry it:
#
#   * no agent writes a date — the CEO proposes a commitment and the platform
#     stamps its own projection as the proposed date; the chairman may replace
#     it with his when he accepts
#   * no agent moves a commitment's status — the janitor recomputes the
#     projection and walks accepted → at_risk → breached → met by itself
#   * while the projection is past the date, NEW SCOPE IS REFUSED. Existing
#     work continues; a new requirement needs the chairman himself.

LIVE_COMMITMENT_STATUSES = (
    CommitmentStatus.ACCEPTED,
    CommitmentStatus.AT_RISK,
    CommitmentStatus.BREACHED,
)


def live_commitment(session: Session, project_id: str) -> Commitment | None:
    """The commitment currently in force: accepted and not yet met. At most
    one exists, because proposing a second while any is open is refused."""
    return session.execute(
        select(Commitment)
        .where(Commitment.project_id == project_id,
               Commitment.status.in_(LIVE_COMMITMENT_STATUSES))
        .order_by(Commitment.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def open_commitment(session: Session, project_id: str) -> Commitment | None:
    """Live, or still on the chairman's desk. What "one promise at a time"
    counts against, and what the context pack shows the CEO."""
    return session.execute(
        select(Commitment)
        .where(Commitment.project_id == project_id,
               Commitment.status.in_((CommitmentStatus.PROPOSED,
                                      *LIVE_COMMITMENT_STATUSES)))
        .order_by(Commitment.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def commitment_scope_keys(session: Session, project_id: str) -> list[str]:
    """The requirement keys in force: accepted (met included — it was in
    force and delivered), cut and merely-proposed excluded. This is what gets
    frozen into scope_snapshot and what the janitor diffs against it."""
    return sorted(
        r.key
        for r in scope_requirements(session, project_id)
        if r.status not in (RequirementStatus.PROPOSED, RequirementStatus.CUT)
    )


def check_commitment_propose(
    session: Session, project: Project, cons: ConstitutionDoc, agent: Agent
) -> dict:
    """Only the CEO asks to commit, only on a plan that holds, and the date it
    proposes is not its to write: the platform computes the projection and
    that projection IS the proposal. Returns it so the route can stamp it."""
    require_project_running(project)
    if agent.kind != AgentKind.CEO:
        raise Refusal(
            "Only the CEO proposes a commitment. If you believe a date can be promised, "
            "say so to the CEO — the promise is made from the whole plan, not one desk.",
            status_code=403,
        )
    if not requires_requirements(project):
        raise Refusal(
            "This is an Explore project: it has no fixed scope, so there is nothing a "
            "delivery date could be a promise about. Its shape is bets and evidence.",
            status_code=422,
        )
    plan = plan_requirement(session, project.id)
    if plan is None or plan.status != RequirementStatus.MET:
        raise Refusal(
            "There is no plan yet, so there is nothing to promise a date on — a date "
            "without a plan under it is a wish. Close R-00 first: propose requirements, "
            "get them accepted, and the moment the plan holds you can ask to commit.",
            status_code=409,
        )
    existing = open_commitment(session, project.id)
    if existing is not None:
        raise Refusal(
            f"A commitment already exists and is {existing.status} "
            f"(deliver by {existing.deliver_by:%Y-%m-%d}). One promise at a time: "
            "deliver this one, or take what should change about it to the chairman.",
            status_code=409,
        )
    from yaaf.server.velocity import commitment_projection

    projection = commitment_projection(session, project, cons)
    if not projection["known"]:
        raise Refusal(
            "There is nothing to project a date from: " + projection["why"] + " "
            "Estimate the scope or meet something first — a promise with no arithmetic "
            "under it is exactly the kind of date this platform refuses to state.",
            status_code=409,
        )
    return projection


def check_commitment_accept(commitment: Commitment) -> None:
    if commitment.status != CommitmentStatus.PROPOSED:
        raise Refusal(
            f"This commitment is {commitment.status}, not proposed. Only a proposal "
            "can be accepted.",
            status_code=409,
        )


def commitment_scope_frozen(
    session: Session, project: Project, cons: ConstitutionDoc
) -> tuple[Commitment, dict, str] | None:
    """Is new scope frozen right now? None when it is not; otherwise the
    commitment, the live projection, and which wall — "breached" (the date has
    passed) or "late" (the projection lands past it).

    Computed live rather than read off the row, so the refusal can never lag
    the janitor by a tick: the janitor owns the *disclosed* status, this owns
    the wall itself, and both call the same projection producer.
    """
    commitment = live_commitment(session, project.id)
    if commitment is None:
        return None
    from yaaf.server.velocity import commitment_projection

    projection = commitment_projection(session, project, cons)
    if utcnow() > commitment.deliver_by:
        return commitment, projection, "breached"
    if projection["known"] and projection["remaining"] > 0 \
            and projection["projected_done_at"] > commitment.deliver_by:
        return commitment, projection, "late"
    return None


def check_scope_within_commitment(
    session: Session, project: Project, cons: ConstitutionDoc
) -> None:
    """The guarantee, as a refusal. When the date stops holding, the company
    cannot quietly add work — existing work continues, but a new requirement
    needs the chairman himself, with the slip in front of him."""
    frozen = commitment_scope_frozen(session, project, cons)
    if frozen is None:
        return
    commitment, projection, wall = frozen
    if wall == "breached":
        raise Refusal(
            f"No new scope: the commitment is breached. You promised "
            f"{commitment.deliver_by:%Y-%m-%d} and it has passed with "
            f"{projection['remaining']} requirement(s) still open. Existing work "
            "continues — finish it. New scope is the chairman's to add now, with the "
            "breach in front of him; he has been told, and this is not something you "
            "can plan your way around.",
            status_code=409,
        )
    projected = projection["projected_done_at"]
    days_late = max(1, (projected - commitment.deliver_by).days)
    raise Refusal(
        f"No new scope while the date does not hold. You promised "
        f"{commitment.deliver_by:%Y-%m-%d} and the projection lands "
        f"{projected:%Y-%m-%d} — about {days_late} day(s) past it "
        f"({projection['basis']}). Adding work moves that the wrong way. Finish "
        "something, or propose cuts to the chairman; if the scope truly must grow, "
        "that is his decision to make with the slip in front of him, not yours.",
        status_code=409,
    )


# ── message rules ───────────────────────────────────────────────────────────────


def check_agent_post(session: Session, project: Project, agent: Agent, channel: Channel) -> None:
    require_project_running(project)
    if channel.project_id != agent.project_id:
        raise Refusal("Channel belongs to another project.", status_code=403)
    if channel.id not in agent_channel_ids(session, agent):
        raise Refusal(
            "You are not a member of that channel. The hierarchy is the message bus: "
            "employees speak in their team room, leads also in leadership, the CEO in "
            "leadership and the boardroom. Cross-team needs go through leadership.",
            status_code=403,
        )
    # A one-person team's room has exactly one member, and an agent never reads
    # its own messages back — so posting there is writing to nobody. Said out
    # loud at the moment of the attempt, because the alternative is silence: a
    # claimant reported a stuck task into her own team room five times over 22
    # hours and no one ever saw a word of it.
    if channel.kind == ChannelKind.TEAM and channel.team_id is not None:
        others = [a for a in team_agents(session, channel.team_id) if a.id != agent.id]
        if not others:
            raise Refusal(
                "That channel has no other members — you are the only agent on your team, "
                "so your team room is a diary and nobody will ever read this. If it needs "
                "an answer, post it to leadership. If it is a note to yourself, write it to "
                "memory.",
                status_code=409,
            )


def check_thread_not_frozen(session: Session, thread_id: str | None) -> None:
    if thread_id is None:
        return
    root = session.get(Message, thread_id)
    if root is not None and root.frozen_at is not None:
        raise Refusal(
            "This thread was frozen by the circuit breaker: too many messages between the "
            "same two agents with no task changing state. Talk is not progress. Move a task, "
            "or ask the chairman to unfreeze the thread.",
            status_code=409,
        )


# ── chairman report cadence ─────────────────────────────────────────────────────
#
# A CEO run is stateless and cannot see its own last message, so "report every
# N hours" is the one rule the platform used to leave to the model's memory —
# and a rule an agent must remember is a suggestion. On 16 Aug 2026 that cost
# thirteen unprompted statuses in 2h20m, at Opus prices, three of them after
# the chairman had already said stop. So it is a refusal now, like every other
# limit here.

DEFAULT_REPORT_INTERVAL_HOURS = 0.0  # off unless the chairman sets a cadence
REPORT_OVERRIDES = ("blocker", "budget", "approval")


class ReportCadence(NamedTuple):
    interval_hours: float
    window: tuple[time, time] | None
    tz: ZoneInfo

    @property
    def enforced(self) -> bool:
        return self.interval_hours > 0


def cadence_of(cons: ConstitutionDoc) -> ReportCadence:
    """The chairman's standing reporting rule, as the constitution states it.

    It lives there rather than in project config because it is enforced as a
    refusal, like everything else in that document — and because amending it
    is disclosed to the CEO in its next context pack. The CEO not *learning*
    the rule had changed is half of what went wrong on 16 Aug; a silent config
    write would have reproduced exactly that.
    """
    try:
        tz = ZoneInfo(cons.timezone)
    except Exception:  # a body written before the field was validated
        tz = ZoneInfo("UTC")
    return ReportCadence(max(cons.chairman_report_interval_hours, 0.0),
                         parse_report_window(cons.chairman_report_window_local), tz)


def report_cadence(session: Session, project: Project) -> ReportCadence:
    return cadence_of(effective(session, project.id))


def boardroom_turns(
    session: Session, channel_id: str, ceo_agent_id: str
) -> tuple[datetime | None, datetime | None]:
    """(CEO's last boardroom post, chairman's last). The chairman is the only
    non-agent sender in there, so a null sender_agent_id identifies him."""
    last_ceo = session.execute(
        select(func.max(Message.created_at)).where(
            Message.channel_id == channel_id, Message.sender_agent_id == ceo_agent_id)
    ).scalar_one()
    last_chairman = session.execute(
        select(func.max(Message.created_at)).where(
            Message.channel_id == channel_id, Message.sender_agent_id.is_(None))
    ).scalar_one()
    return last_ceo, last_chairman


def next_report_slot(cadence: ReportCadence, last_report_at: datetime | None) -> datetime | None:
    """When an unprompted status may next be posted. None = unrestricted."""
    if not cadence.enforced or last_report_at is None:
        return None
    return _into_window(last_report_at + timedelta(hours=cadence.interval_hours), cadence)


def _into_window(moment: datetime, cadence: ReportCadence) -> datetime:
    """Push a moment forward to the next instant inside the chairman's waking
    hours — that is what "nothing overnight" means in wall-clock terms."""
    if cadence.window is None:
        return moment
    start, end = cadence.window
    local = moment.astimezone(cadence.tz)
    if local.time() < start:
        local = local.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    elif local.time() > end:
        local = (local + timedelta(days=1)).replace(
            hour=start.hour, minute=start.minute, second=0, microsecond=0)
    return local.astimezone(UTC)


def check_chairman_report(
    session: Session,
    project: Project,
    agent: Agent,
    channel: Channel,
    run: Run,
    override: str | None = None,
) -> None:
    """Three ways past the cadence, in the order the CEO should consider them:
    it is a reply to something the chairman actually said; the slot is open; or
    it declares a break-glass reason. Everything else folds into the next slot.
    """
    if agent.kind != AgentKind.CEO or channel.kind != ChannelKind.BOARDROOM:
        return
    # The chat lane is the conversation, not the report: it speaks only when
    # the chairman does, and its replies are delivered by the harness rather
    # than through this endpoint at all.
    if run.kind == RunKind.CHAT_SESSION:
        return
    cadence = report_cadence(session, project)
    if not cadence.enforced:
        return
    last_ceo, last_chairman = boardroom_turns(session, channel.id, agent.id)
    # A turn the chairman opened and the CEO has not answered. Allowed exactly
    # once: this reply becomes the newest message, so the next post is a status
    # again and faces the cadence.
    if last_chairman is not None and (last_ceo is None or last_chairman > last_ceo):
        return
    if override is not None:
        _check_report_override(session, project, agent, override)
        return
    if last_ceo is None:
        return  # nothing has ever been reported; the first status always lands
    now = utcnow()
    slot = next_report_slot(cadence, last_ceo)
    if slot is None or now >= slot:
        return
    raise Refusal(
        f"Not yet. You posted to the boardroom {_ago(now - last_ceo)} ago, and the chairman's "
        f"cadence is every {cadence.interval_hours:g}h{_window_prose(cadence)}. The next slot "
        f"opens {slot.isoformat(timespec='minutes')}. Fold this into that post rather than "
        "sending it now — status posts cost real money, and silence between slots is what he "
        "asked for, not a failure to report. This outranks your standing instruction to report "
        "unprompted on bets, milestones and first evidence. If it genuinely cannot wait, "
        f"re-post with override set to one of {', '.join(REPORT_OVERRIDES)} — overrides are "
        "recorded and the chairman sees them.",
        status_code=429,
    )


def _check_report_override(
    session: Session, project: Project, agent: Agent, override: str
) -> None:
    if override not in REPORT_OVERRIDES:
        raise Refusal(
            f"'{override}' is not a break-glass reason. The cadence breaks for exactly three "
            f"things: {', '.join(REPORT_OVERRIDES)}.")
    if override == "approval":
        pending = session.execute(
            select(func.count(Approval.id)).where(
                Approval.project_id == project.id, Approval.status == ApprovalStatus.PENDING)
        ).scalar_one()
        if not pending:
            raise Refusal(
                "override='approval' but this project has no pending approval. File the "
                "approval itself — that reaches the chairman on its own, without a status.")
    if override == "budget":
        # Local import: janitor imports this module, and budget arithmetic
        # lives there with the enforcement that uses it.
        from yaaf.server.janitor import month_to_date

        cons = effective(session, project.id)
        usd, tokens = month_to_date(session, project.id)
        spent, cap = ((usd, cons.monthly_usd_cap) if project.billing_mode == "api_key"
                      else (tokens, cons.monthly_token_cap))
        if not cap or spent < cap * cons.budget_warn_ratio:
            raise Refusal(
                f"override='budget' but spend is not at the warning line yet ({spent:,.0f} of "
                f"{cap:,.0f}). The platform raises the budget warning itself when it lands.")
    # 'blocker' is a judgment call the platform cannot check. It passes — and
    # it is recorded, so a CEO that cries blocker four times a day is visible
    # to the chairman rather than merely irritating.
    session.add(
        AuditLog(
            id=ids.new_id(ids.AUDIT),
            project_id=project.id,
            actor=f"agent:{agent.id}",
            action="boardroom.cadence_override",
            entity="agent",
            entity_id=agent.id,
            detail={"reason": override},
        )
    )


# ── chairman directives ─────────────────────────────────────────────────────────
#
# The CEO may turn something the chairman said into enforced policy. Two things
# keep that from being a hole: it may only touch rules that constrain what the
# company asks of *him* (there is nobody such a rule could be used against),
# and it must cite a real, recent message he actually wrote.

DIRECTIVE_FIELDS = CADENCE_FIELDS
DIRECTIVE_MAX_AGE_HOURS = 24


def check_directive_scope(changes: dict) -> None:
    if not changes:
        raise Refusal("A directive has to change something.")
    outside = sorted(set(changes) - DIRECTIVE_FIELDS)
    if outside:
        raise Refusal(
            f"You may not set {', '.join(outside)} from a directive. An agent filing its own "
            f"constitution is how a company votes itself a bigger budget; the only rules you "
            f"may file are the ones that limit what you ask of the chairman "
            f"({', '.join(sorted(DIRECTIVE_FIELDS))}). Everything else is his amendment to "
            "make, and yours to recommend.",
            status_code=403,
        )


def check_directive_citation(session: Session, project: Project, message_id: str) -> Message:
    """The cited message must be the chairman's own words, in the boardroom,
    recent, and newer than the rule it replaces — otherwise a stale message
    could be re-cited to undo a fresher instruction."""
    message = session.get(Message, message_id)
    if message is None or message.project_id != project.id:
        raise Refusal(f"No message {message_id} in this project to cite.", status_code=404)
    channel = session.get(Channel, message.channel_id)
    if channel is None or channel.kind != ChannelKind.BOARDROOM:
        raise Refusal(
            "Cite something the chairman said in the BOARDROOM. Instructions relayed through "
            "leadership are not his, however confidently they are repeated.")
    if message.sender_agent_id is not None:
        raise Refusal("That message is an agent's, not the chairman's. You cannot cite yourself.")
    age = utcnow() - message.created_at
    if age > timedelta(hours=DIRECTIVE_MAX_AGE_HOURS):
        raise Refusal(
            f"That message is {age.days}d old. File a directive when he gives it, not from "
            f"history — anything older than {DIRECTIVE_MAX_AGE_HOURS}h is his to restate.")
    newest = session.execute(
        select(func.max(Amendment.effective_at)).where(
            Amendment.project_id == project.id,
            Amendment.cancelled_at.is_(None),
            Amendment.applied_constitution_id.is_not(None))
    ).scalar_one()
    if newest is not None and message.created_at < newest:
        raise Refusal(
            "That message predates the rule currently in force, so filing it would undo a "
            "newer instruction with an older one. If he has changed his mind again, cite the "
            "message where he said so.")
    return message


def confirm_directive(
    session: Session, project: Project, agent: Agent, cited: Message, changes: dict
) -> None:
    """Say it back to him. The platform can verify that he spoke and when, but
    never what he meant — so the safeguard against a misread directive is that
    he sees it land, in his own channel, threaded to the words it came from."""
    readable = ", ".join(f"{key.removeprefix('chairman_report_')} = {value}"
                         for key, value in sorted(changes.items()))
    body = (f"Recorded as policy, effective now: **{readable}**. I took this from your message "
            f"above and the platform enforces it from here — it is no longer something I have "
            f"to remember between runs. Say so if I read you wrong.")
    session.add(
        Message(
            id=ids.new_id(ids.MESSAGE),
            project_id=project.id,
            channel_id=cited.channel_id,
            sender_agent_id=agent.id,
            thread_id=cited.thread_id or cited.id,
            body=body,
        )
    )
    # Best-effort out-of-band: a rule change he did not ask for is exactly the
    # thing he should not have to be watching Slack to catch.
    from yaaf.server.notify import chairman_notify

    chairman_notify(session, project, "Reporting cadence changed", body)


def _ago(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{minutes / 60:.1f} hours"


def _window_prose(cadence: ReportCadence) -> str:
    if cadence.window is None:
        return ""
    start, end = cadence.window
    return f", and only between {start.strftime('%H:%M')} and {end.strftime('%H:%M')} {cadence.tz.key}"


# ── memory rules ────────────────────────────────────────────────────────────────


def memory_curator_ok(session: Session, agent: Agent, memory: Memory) -> bool:
    if memory.scope == MemoryScope.PROJECT:
        return agent.kind == AgentKind.CEO
    if memory.scope == MemoryScope.TEAM:
        if agent.team_id != memory.team_id:
            return False
        team = session.get(Team, memory.team_id)
        lead = effective_lead(session, team) if team else None
        return lead is not None and lead.id == agent.id
    return False


def _memory_filter(scope: str, project_id: str, team_id: str | None) -> list:
    """The one definition of "which rows count against a cap". Project scope is
    counted per project, team scope per team — four call sites read this (the
    refusal, the context pack, the janitor's warning, the chairman's dashboard)
    and a number that disagreed with the refusal would be worse than no number.
    """
    if scope == MemoryScope.PROJECT:
        return [Memory.project_id == project_id, Memory.scope == scope]
    return [Memory.team_id == team_id, Memory.scope == scope]


def live_memory_count(
    session: Session, scope: str, project_id: str, team_id: str | None = None
) -> int:
    """Entries occupying a slot: superseded and forgotten rows survive in the
    table (that is what makes compaction reversible) but stop being counted."""
    return session.execute(
        select(func.count(Memory.id)).where(
            *_memory_filter(scope, project_id, team_id), Memory.superseded_at.is_(None)
        )
    ).scalar_one()


def memory_cap_for(cons: ConstitutionDoc, scope: str) -> int:
    return cons.memory_cap_project if scope == MemoryScope.PROJECT else cons.memory_cap_team


def compaction_candidates(
    session: Session, scope: str, project_id: str, team_id: str | None = None, limit: int = 5
) -> list[Memory]:
    """The oldest live entries in a scope — what a curator should look at first
    when it needs room. Carried in the refusal itself so the curator can act in
    the same run instead of spending one of five refusals learning to search."""
    return list(
        session.execute(
            select(Memory)
            .where(*_memory_filter(scope, project_id, team_id), Memory.superseded_at.is_(None))
            .order_by(Memory.created_at)
            .limit(limit)
        ).scalars()
    )


def check_memory_add(
    session: Session, cons: ConstitutionDoc, agent: Agent, scope: str, team_id: str | None
) -> None:
    if scope == MemoryScope.PROJECT:
        if agent.kind != AgentKind.CEO:
            raise Refusal("Project-scope memory is the CEO's; write team-scope memory.", 403)
    elif scope == MemoryScope.TEAM:
        if agent.team_id is None or team_id != agent.team_id:
            raise Refusal("Team-scope memory belongs to your own team only.", status_code=403)
    else:
        raise Refusal("scope must be 'project' or 'team'.")

    cap = memory_cap_for(cons, scope)
    live = live_memory_count(session, scope, agent.project_id, team_id)
    if live < cap:
        return

    log.warning(
        "memory cap reached: project=%s scope=%s team=%s %d/%d agent=%s",
        agent.project_id, scope, team_id, live, cap, agent.name,
    )
    # The old text said "supersede or forget". Supersede is 1→1 and frees
    # nothing, so an agent that followed it exactly was still at the cap — and
    # if it was not the curator, both calls were 403 anyway. Say what is true
    # for the agent actually reading this.
    head = (
        f"Memory cap reached ({live}/{cap}). A memory store that only grows becomes a "
        "journal, and a journal read every cycle is how the last project drowned."
    )
    curator = _curator_of(session, agent, scope)
    if curator is None or curator.id != agent.id:
        who = curator.name if curator else "this team's lead (none is set — ask the CEO)"
        # "is being woken", not "has been told": the janitor sends that notice on
        # its next tick, and a refusal cannot send one itself (the request's
        # session is rolled back by the very exception carrying this message).
        # Promise the thing that is actually true a few seconds from now.
        raise Refusal(
            f"{head} You are not this scope's curator, so there is nothing for you to "
            f"free: only {who} may compact or forget here, and {who} is being woken to "
            "make room. Put what you learned in a message to them instead — do not retry "
            "this write.",
            status_code=409,
        )
    oldest = compaction_candidates(session, scope, agent.project_id, team_id)
    listing = "; ".join(f"{m.id} — {m.subject}" for m in oldest)
    raise Refusal(
        f"{head} You curate this scope, so you can clear it in this run: memory_compact "
        f"folds several entries into one (freeing all but one slot), memory_forget drops "
        f"one. Note that memory_supersede replaces 1 with 1 and frees nothing. Your oldest "
        f"entries: {listing}",
        status_code=409,
    )


def _curator_of(session: Session, agent: Agent, scope: str) -> Agent | None:
    if scope == MemoryScope.PROJECT:
        return ceo_of(session, agent.project_id)
    team = session.get(Team, agent.team_id) if agent.team_id else None
    return effective_lead(session, team) if team else None


# ── evidence rules ──────────────────────────────────────────────────────────────


def check_evidence_add(
    session: Session,
    project: Project,
    kind: str,
    source_url: str | None,
    task_id: str | None,
) -> None:
    require_project_running(project)
    if kind == EvidenceKind.EXTERNAL and not (source_url or "").strip():
        raise Refusal(
            "External evidence needs a source_url the platform can fetch and verify. "
            "Evidence nobody can check is opinion."
        )
    if kind not in (EvidenceKind.EXTERNAL, EvidenceKind.INTERNAL):
        raise Refusal("kind must be 'external' or 'internal'.")
    # task_id used to be an un-FK'd string that ceo_judgment joined on: paste
    # any id and you inflate your own score, omit it and you suppress it. Now
    # a given id must be a real task of THIS project, and in the modes where
    # work is tasks (Deliver/Operate), evidence must say which work it came
    # from. Explore stays taskless-friendly — its shape is bets and evidence.
    if task_id is not None:
        task = session.get(Task, task_id)
        if task is None or task.project_id != project.id:
            raise Refusal(
                f"task_id {task_id!r} is not a task of this project. Evidence joins the "
                "record through the work that produced it; a made-up id would credit "
                "work that never happened.",
                status_code=404,
            )
    elif requires_requirements(project):
        raise Refusal(
            "Evidence needs task_id: the task this observation came from. In this "
            "project mode all work is tasks, so evidence that names none is not "
            "attributable to anything — and unattributable evidence is how a "
            "scoreboard gets gamed. (Connectors attest without a task; you are "
            "not one.)",
            status_code=422,
        )
