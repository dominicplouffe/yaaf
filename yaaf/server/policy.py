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
import re
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
    ApprovalKind,
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
    LedgerEntry,
    LedgerKind,
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
    RunQueue,
    RunStatus,
    SuccessTestKind,
    Task,
    TaskEvent,
    TaskStatus,
    Team,
    VerificationStatus,
    utcnow,
)

log = logging.getLogger("yaaf.policy")

OPEN_TASK_STATUSES = (TaskStatus.READY, TaskStatus.CLAIMED, TaskStatus.CHECKING,
                      TaskStatus.REVIEW, TaskStatus.BLOCKED)


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
    # Metric readings are deliberately NOT counted here, and the reasoning is
    # worth keeping because the opposite looked obviously right for a day.
    #
    # A reading is external and platform-verified, so counting it seems to fit —
    # and it would have given `drought_hours`, one of only two floors the
    # constitution has, something real to enforce. But the cadence is
    # agent-authored (`measure_every_hours`, floored at one hour), so a CEO that
    # wanted the drought never to fire again could propose a single metric on an
    # hourly schedule and switch the floor off permanently. A floor an agent's
    # own request can disable is precisely what house rule 1 forbids.
    #
    # The deeper objection is that it was never the same thing. A drought asks
    # whether the PROJECT has produced proof from outside itself. A metric
    # reading is the platform going and looking at a number on a timer; nobody
    # here did anything. A ledger-source metric does not even leave the box.
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
    elif kind == SuccessTestKind.METRIC:
        from yaaf.server import metrics

        metrics.validate_metric_test(success_test)
    else:
        raise Refusal(
            'success_test must be {"type": "command", "command": ...} (platform-executed), '
            '{"type": "observable", "target": ...} (spot-audited), or '
            '{"type": "metric", "source": ..., "direction": ..., "target": ...} '
            "(platform-measured, from a source you do not author). "
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


# How long a row may sit in CHECKING with no check run behind it before the
# platform calls the check lost and requeues it. Generous against every honest
# cause of a slow check — a queue backlog, a busy runner — and tiny against the
# failure it exists to catch, which previously lasted forever.
CHECK_LOST_GRACE = timedelta(minutes=5)


def _utc_or_none(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def check_in_flight(session: Session, *, task_id: str | None = None,
                    requirement_id: str | None = None) -> bool:
    """Is there still a check run — queued or running — that will move this
    row out of CHECKING? False means nothing left in the system will ever
    touch it again."""
    kind = RunKind.SUCCESS_CHECK if task_id else RunKind.REQUIREMENT_CHECK
    queue = select(func.count(RunQueue.id)).where(RunQueue.kind == kind)
    runs = select(func.count(Run.id)).where(Run.kind == kind,
                                            Run.status == RunStatus.RUNNING)
    if task_id:
        queue = queue.where(RunQueue.task_id == task_id)
        runs = runs.where(Run.task_id == task_id)
    else:
        queue = queue.where(RunQueue.requirement_id == requirement_id)
        runs = runs.where(Run.requirement_id == requirement_id)
    return bool(session.execute(queue).scalar_one()
                or session.execute(runs).scalar_one())


def task_checking_since(session: Session, task_id: str) -> datetime | None:
    return _utc_or_none(session.execute(
        select(func.max(TaskEvent.at)).where(
            TaskEvent.task_id == task_id,
            TaskEvent.to_status == TaskStatus.CHECKING)
    ).scalar())


def requirement_checking_since(session: Session, requirement_id: str) -> datetime | None:
    return _utc_or_none(session.execute(
        select(func.max(RequirementEvent.at)).where(
            RequirementEvent.requirement_id == requirement_id,
            RequirementEvent.to_status == RequirementStatus.CHECKING)
    ).scalar())


def _last_check_run_at(session: Session, *, task_id: str | None = None,
                       requirement_id: str | None = None) -> datetime | None:
    """When the platform last did anything about this row's check: the newest
    check run's finish (or start, if it never finished). Lost-check detection
    ages off this as well as off the CHECKING transition, so a check that dies
    and is requeued waits out a fresh grace period between attempts instead of
    being re-run on every sweep."""
    kind = RunKind.SUCCESS_CHECK if task_id else RunKind.REQUIREMENT_CHECK
    stmt = select(func.max(func.coalesce(Run.finished_at, Run.created_at))).where(
        Run.kind == kind)
    if task_id:
        stmt = stmt.where(Run.task_id == task_id)
    else:
        stmt = stmt.where(Run.requirement_id == requirement_id)
    return _utc_or_none(session.execute(stmt).scalar())


def lost_checks(session: Session, project_id: str,
                now: datetime | None = None) -> tuple[list[Task], list[Requirement]]:
    """Rows wedged in CHECKING: the check run they are waiting on no longer
    exists anywhere — not queued, not running — and nothing about the check
    has moved for longer than the grace period.

    This state used to be invisible and permanent. A CHECKING task wakes
    nobody and counts as no one's work, so the board read as idle — while its
    requirement still counted as OPEN and gated every stage behind it. One
    project sat wedged this way for 80 minutes, with zero failed checks and
    no alarm, until the chairman asked why nothing was happening. The check
    run had been reaped (or its queue row purged by a pause) and nothing ever
    requeued it: a reaped run only releases CLAIMED tasks, and the janitor's
    completion sweep only looks at IN_PROGRESS requirements.

    Requirements with observable tests are excluded — those are legitimately
    waiting on a human verdict, and `nag_observable_requirements` owns them.
    """
    now = now or utcnow()
    horizon = now - CHECK_LOST_GRACE

    def _lost(since: datetime | None, last_run: datetime | None) -> bool:
        newest = max(filter(None, (since, last_run)), default=now)
        return newest <= horizon

    tasks = [
        t for t in session.execute(
            select(Task).where(Task.project_id == project_id,
                               Task.status == TaskStatus.CHECKING)
        ).scalars()
        if _lost(task_checking_since(session, t.id),
                 _last_check_run_at(session, task_id=t.id))
        and not check_in_flight(session, task_id=t.id)
    ]
    requirements = [
        r for r in session.execute(
            select(Requirement).where(Requirement.project_id == project_id,
                                      Requirement.status == RequirementStatus.CHECKING)
        ).scalars()
        if (r.acceptance_test or {}).get("type") == SuccessTestKind.COMMAND
        and _lost(requirement_checking_since(session, r.id),
                  _last_check_run_at(session, requirement_id=r.id))
        and not check_in_flight(session, requirement_id=r.id)
    ]
    return tasks, requirements


def check_task_gate(cons: ConstitutionDoc, not_before: datetime | None) -> None:
    """How far ahead work may be laid out.

    `not_before` is a real and useful verb — it is how a CEO sequences a weekly
    rhythm, or a step that only makes sense after another lands. It is also how
    a project goes silent for no visible reason: one CEO gated its only open
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
    if success_test.get("type") == SuccessTestKind.METRIC:
        raise Refusal(
            "A metric belongs on a requirement, not a task. A task's check runs when "
            "the work closes; a metric is read on a schedule and may not move for "
            "weeks, so a task carrying one could never be completed. Propose the "
            "metric as a requirement and give this task a command the platform can "
            "run against what you actually built."
        )
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
    # relaxed there: with one agent in the project, there is nobody to hand it
    # to, and refusing would mean a solo project could never claim anything.
    # This is a real difference in what "done" is worth between topologies —
    # a solo project self-grades every observable — and it is a cost of running
    # solo, not a free pass.
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
            # REVIEW counts: the work is finished but still this agent's, and a
            # return hands it straight back. Without it the slot frees on
            # completion and a returned task lands on somebody already holding one.
            Task.status.in_([TaskStatus.CLAIMED, TaskStatus.CHECKING, TaskStatus.REVIEW]),
        )
    ).scalar_one()
    if already > 0:
        raise Refusal(
            "One item at a time: you already hold a claimed task. Complete it, block it, "
            "or let it be reaped — do not collect work.",
            status_code=409,
        )


def _has_another_agent(session: Session, agent: Agent) -> bool:
    """Is there anyone else in this project at all? Solo mode is one agent that
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
    check_search_record(session, project, task, evidence)
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


#: The discipline whose job is reading somebody else's work and disagreeing.
REVIEW_CATEGORY = "challenge"


def review_seats(session: Session, project_id: str) -> list[Agent]:
    """Agents on this project whose role is a reviewing one.

    Ordered, and callers depend on it: the dispatcher picks the first eligible
    seat as the one seat woken for a given task, so an unordered result would
    wake a different reviewer on every tick and pay for each of them.
    """
    from yaaf.core.models import Role, RoleVersion

    return list(session.execute(
        select(Agent)
        .join(Team, Team.id == Agent.team_id)
        .join(RoleVersion, RoleVersion.id == Team.role_version_id)
        .join(Role, Role.id == RoleVersion.role_id)
        .where(Agent.project_id == project_id,
               Agent.status == AgentStatus.ACTIVE,
               Role.category == REVIEW_CATEGORY)
        .order_by(Agent.id)
    ).scalars())


def is_review_seat(session: Session, agent: Agent) -> bool:
    return agent.id in {seat.id for seat in review_seats(session, agent.project_id)}


def review_is_owed(session: Session, project: Project, task: Task) -> bool:
    """Whether this finished task waits for a second pair of eyes.

    Only observable tests. A command test is already judged by the platform
    running it, so a reviewer would be a second opinion about an exit code;
    an observable is the one that used to close on the author's own word.

    And only where somebody can actually give the read. Creation refuses a
    roster that asks for one and hires nobody, but a project can lose its
    reviewer afterwards — an agent paused, a role refiled — and a task stuck
    in REVIEW forever would be worse than the self-report it replaced.
    """
    if task.success_test.get("type") != SuccessTestKind.OBSERVABLE:
        return False
    if effective(session, project.id).deliverable_review != "independent_read":
        return False
    return any(agent.id != task.claimed_by_agent_id
               for agent in review_seats(session, project.id))


def check_task_review(
    session: Session, project: Project, agent: Agent, task: Task
) -> None:
    require_project_running(project)
    if task.status != TaskStatus.REVIEW:
        raise Refusal(
            f"Task {task.id} is {task.status}; only work waiting on a read can be "
            "reviewed.", status_code=409)
    if task.claimed_by_agent_id == agent.id:
        raise Refusal(
            "You wrote this. The whole value of the read is that somebody who did "
            "not write it looked at it — reviewing your own work is the thing this "
            "state exists to prevent.", status_code=403)
    if agent.id not in {seat.id for seat in review_seats(session, project.id)}:
        raise Refusal(
            "Only a reviewing seat closes a read. Your role is not in the "
            f"'{REVIEW_CATEGORY}' discipline, so this is not yours to sign off — "
            "say what you think in the team room instead.", status_code=403)


def notice_review_returned(
    session: Session, task: Task, reviewer: Agent, notes: str, *, blocked: bool = False
) -> None:
    """Tell the author, in the reviewer's own words.

    A returned task goes back to CLAIMED and would otherwise look to its
    claimant exactly like a task it had already finished — the one thing that
    would make the second pass useless.
    """
    from yaaf.core.models import Notice, NoticeKind

    session.add(Notice(
        id=ids.new_id(ids.NOTICE),
        project_id=task.project_id,
        agent_id=task.claimed_by_agent_id,
        kind=NoticeKind.TASK_CHECK_FAILED,
        body=(f"{reviewer.name} read your finished work on \"{task.title}\" and sent "
              f"it back. This is ordinary, not an escalation.\n\n{notes}\n\n"
              + ("Fix what they named, or say in your team room why you disagree, "
                 f"then complete the task again. ({task.id})" if not blocked else
                 f"That is {task.check_failures} returns, so the task is BLOCKED "
                 "and now "
                 "belongs to whoever planned it. Do not re-claim it; say in your "
                 f"team room where you and {reviewer.name} disagree. ({task.id})")),
    ))


#: What a search record has to name. Not a format — three ideas, in whatever
#: words the agent uses. "Empty" is the load-bearing one: it is what separates
#: a question that was investigated from one that was summarised.
_SEARCH_RECORD_HINTS = (
    ("looked for", "searched for", "went looking", "set out to"),
    ("looked in", "searched", "sources", "checked", "read", "fetched"),
    ("empty", "nothing", "could not find", "no result", "not there",
     "found none", "turned up nothing", "unanswered", "no source"),
)

#: Matched on word boundaries, not as substrings. The middle group is the
#: reason: "read" is inside "already", "ready" and "reader", so as a substring
#: it matched almost any completion note ever written and the middle third of
#: the floor was free. A multi-word phrase still matches across whitespace.
_SEARCH_RECORD_PATTERNS = tuple(
    tuple(re.compile(r"\b" + r"\s+".join(re.escape(w) for w in phrase.split()) + r"\b")
          for phrase in group)
    for group in _SEARCH_RECORD_HINTS
)


def check_search_record(
    session: Session, project: Project, task: Task, evidence: str
) -> None:
    """Research that says what it did NOT find, or it does not close.

    The alternative floor — a minimum number of distinct cited domains — is
    satisfied by fetching three pages and citing them whether or not a claim
    derives from any of them. That buys a citation ritual which passes the
    gate and reads as rigour, and it is worse than nothing because it is
    indistinguishable from the real thing.

    A search record is unverifiable: the platform cannot confirm a search
    happened. But it is not gameable in a way that *resembles compliance* — a
    thin one reads as thin, to the reviewer and to the chairman — and that is
    the more useful property here. So this checks that the three ideas are
    present at all, and leaves whether they are any good to the person reading
    it. A keyword floor is a floor, not a judgement.
    """
    if not task.produces_external_evidence:
        return
    if not effective(session, project.id).require_search_record:
        return
    said = (evidence or "").lower()
    if all(any(pattern.search(said) for pattern in group)
           for group in _SEARCH_RECORD_PATTERNS):
        return
    raise Refusal(
        "This task produces evidence, and its completion carries no search record. "
        "Say what you went looking for, where you looked, and what came back EMPTY "
        "— the last one is the half that does the work. A question you could not "
        "answer, a figure everyone cites to one origin, a source that should exist "
        "and does not: those are findings, and they are invisible unless you write "
        "them down. Without them a shallow pass and a deep one look identical."
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
    one level down; a proposal in Explore would park the project forever on an
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


def check_requirement_reopen(requirement: Requirement) -> None:
    """Only settled scope can be reopened. Reopening is the undo that never
    existed: a wrong verdict or a premature cut used to be permanent for
    everyone, the chairman included."""
    if requirement.key == PLAN_KEY:
        raise Refusal(
            "R-00 is the plan gate; the janitor opens and closes it by arithmetic. "
            "It is not scope to reopen.",
            status_code=409,
        )
    if requirement.status not in CLOSED_REQUIREMENT_STATUSES:
        raise Refusal(
            f"{requirement.key} is {requirement.status} — it is already open. Only a "
            "met or cut requirement can be reopened.",
            status_code=409,
        )


def check_requirement_restage(requirement: Requirement) -> None:
    if requirement.status in CLOSED_REQUIREMENT_STATUSES:
        raise Refusal(
            f"{requirement.key} is {requirement.status}; settled scope is history. "
            "Reopen it first if it truly belongs back in the plan.",
            status_code=409,
        )
    if requirement.key == PLAN_KEY:
        raise Refusal("R-00 has no stage to move.", status_code=409)


def check_requirement_reestimate(requirement: Requirement) -> None:
    if requirement.status in CLOSED_REQUIREMENT_STATUSES:
        raise Refusal(
            f"{requirement.key} is {requirement.status}; what it actually took is "
            "already on the record, and an estimate revised after the fact would "
            "only exist to flatter the ratio.",
            status_code=409,
        )
    if requirement.key == PLAN_KEY:
        raise Refusal("R-00 carries no estimate.", status_code=409)


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
    same loop, forever, and the project looks idle for a reason no screen could
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


def delivery_outstanding(session: Session, project: Project) -> list[str]:
    """What still stands between this Deliver project and COMPLETED. Empty
    means ready — the same list the complete route refuses with, the dashboard
    renders, and the dispatcher gates on, so the email, the refusal, the button
    and the brake can never disagree.

    It lives here rather than in the janitor because the dispatcher has to ask
    it thirty times a minute and the janitor imports the dispatcher.
    """
    outstanding: list[str] = []
    plan = plan_requirement(session, project.id)
    if plan is None or plan.status != RequirementStatus.MET:
        outstanding.append("the plan gate is open")
    scope = [r for r in scope_requirements(session, project.id)
             if r.status != RequirementStatus.PROPOSED]
    if not scope:
        outstanding.append("no requirements were ever accepted")
    open_scope = [r for r in scope if r.status not in CLOSED_REQUIREMENT_STATUSES]
    if open_scope:
        outstanding.append(
            "still open: " + ", ".join(f"{r.key} ({r.status})" for r in open_scope[:8]))
    if scope and not any(r.status == RequirementStatus.MET for r in scope):
        outstanding.append("nothing was made true — everything was cut")
    open_tasks = open_top_level_tasks(session, project.id)
    if open_tasks:
        outstanding.append(f"{len(open_tasks)} task(s) still open")
    return outstanding


def delivered_awaiting_confirmation(session: Session, project: Project) -> bool:
    """Everything is true and the chairman has not pressed Complete yet. The
    project is finished; only its status has not caught up.

    This state used to be indistinguishable from an ordinary quiet day, and it
    is not one: a delivered project that keeps waking is a project paying to
    discuss work nobody asked for. One project spent 79 runs and 35.8M tokens
    in the 33 hours after its CEO told the chairman it was done — five agents
    reviewing each other's citation indexes, because the only thing that ever
    stopped it was the daily inbox ceiling, and that re-arms at midnight.
    """
    return (project.mode == ProjectMode.DELIVER
            and project.status == ProjectStatus.RUNNING
            and not delivery_outstanding(session, project))


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


#: How many settled requirements the bias is measured over. A window, not all
#: history — because without one the multiplier is a permanent sentence.
#:
#: A project that overran early carried that overrun into every plan it would
#: ever make: the FPL desk sat at 1.64 on eight settled requirements from its
#: first fortnight, so `check_plan_affordable` refused any plan that did not fit
#: inside 61% of its own budget, forever, and the only legal responses were to
#: cut scope or to estimate smaller than it believed.
#:
#: The anti-gaming property survives intact, because it never depended on the
#: window being infinite. Within any window the ratio is still floored at 1, so
#: over-estimating once still buys nothing. What the window removes is the part
#: that was never anti-gaming at all — that a project which has since become
#: accurate goes on paying for a bad month it cannot work off.
#:
#: Long relative to a cycle on purpose: short enough to be earnable, far too
#: long to be gamed by one deliberate over-estimate.
BIAS_WINDOW_SETTLED = 12


def estimate_bias(session: Session, project_id: str) -> tuple[float, dict]:
    """How wrong this CEO's recent estimates have been, as a multiplier ≥ 1.

    Never below 1 within the window. A ratio that could shrink an estimate would
    let a CEO buy headroom by over-estimating once and then under-estimating
    everything after — the same gaming the multiplier exists to price in, run
    backwards.
    """
    settled = [
        r
        for r in scope_requirements(session, project_id, all_cycles=True)
        if r.status in CLOSED_REQUIREMENT_STATUSES and (r.est_runs_first or r.est_runs)
    ]
    # Most recently settled first, then trimmed to the window: accuracy is a
    # thing a project can now demonstrate, rather than a verdict it received
    # once. `met_at` is when the platform ruled; a cut requirement has none, so
    # it falls back to creation order via the id, which is time-ordered.
    settled.sort(key=lambda r: (r.met_at is not None, r.met_at or "", r.id), reverse=True)
    settled = settled[:BIAS_WINDOW_SETTLED]
    # Scored against the FIRST estimate, always: reestimate moves est_runs for
    # the plan arithmetic, never the number the ratio judges.
    pairs = [(r.est_runs_first or r.est_runs, runs_spent_on(session, r.id))
             for r in settled]
    pairs = [(est, actual) for est, actual in pairs if actual > 0]
    if len(pairs) < MIN_SETTLED_FOR_BIAS:
        return 1.0, {"basis": "no history yet", "settled": len(pairs)}
    estimated = sum(est for est, _ in pairs)
    actual = sum(a for _, a in pairs)
    ratio = max(1.0, actual / estimated) if estimated else 1.0
    return ratio, {
        "basis": f"{len(pairs)} most recently settled requirements",
        "window": BIAS_WINDOW_SETTLED,
        "estimated": estimated,
        "actual": actual,
        "multiplier": round(ratio, 2),
    }


def avg_usd_per_run_box(session: Session) -> float | None:
    """What a run has actually cost on this install, across every project.

    For the one question asked before a project exists: the new-project form
    tells the chairman what a run has to average for his cap to hold, and that
    sentence is only worth reading beside a number from a bill he has already
    seen. None when there is not enough priced history to be honest — a fresh
    box says nothing rather than guessing, the same refusal
    :func:`affordable_runs` makes under subscription billing.
    """
    average, count = session.execute(
        select(func.avg(Run.cost_usd), func.count(Run.id)).where(
            Run.cost_usd.is_not(None), Run.cost_usd > 0
        )
    ).one()
    return float(average) if count and count >= MIN_PRICED_RUNS and average else None


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
        "project arrives at 100% of its cap with nothing finished.",
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


def check_commitment_reset(
    session: Session, project: Project, cons: ConstitutionDoc, agent: Agent
) -> tuple[Commitment, dict]:
    """The CEO resets a promise that stopped holding — where the dial grants it.

    Without this verb an autonomous CEO facing an at-risk date had no move at
    all: it could cut scope (scope_authority) and it could finish work, but if
    the honest answer was "the date has to move" its only remaining path was an
    owner_decision approval — a blocker on the desk of a chairman who had
    already delegated exactly this. Authority is configuration; a granted
    authority with no verb behind it is neither.

    The integrity rule does not bend: the CEO still never WRITES a date. The
    only date it can reset to is the platform's own current projection, the
    same arithmetic `commitment_propose` stamps. So a reset can never buy slack
    the measured pace has not already spent — it can only stop the promise
    being a number nobody believes.
    """
    require_project_running(project)
    if agent.kind != AgentKind.CEO:
        raise Refusal(
            "The promise is the CEO's to reset, where the constitution grants it at "
            "all. If you think the date cannot hold, say so to the CEO — the promise "
            "is made from the whole plan, not one desk.",
            status_code=403,
        )
    if not ceo_holds(cons, "commitment_authority"):
        raise Refusal(
            "The constitution holds commitment_authority = "
            f"'{cons.commitment_authority}': the date is the chairman's to move on this "
            "project. Two paths remain — his own button on the Plan screen, or, if he "
            "told you in the boardroom to move it, file it as a directive citing his "
            "message. Recommend the reset to him plainly; the dial itself is his "
            "amendment to turn.",
            status_code=403,
        )
    commitment = live_commitment(session, project.id)
    if commitment is None:
        raise Refusal(
            "There is no live commitment to reset. Propose one when the plan holds.",
            status_code=409,
        )
    frozen = commitment_scope_frozen(session, project, cons)
    if frozen is None:
        raise Refusal(
            f"The promise still holds: you said {commitment.deliver_by:%Y-%m-%d} and the "
            "projection lands inside it. There is nothing to reset — a date moved while "
            "it is still good is a date that means nothing. Deliver it.",
            status_code=409,
        )
    _, projection, _ = frozen
    if not projection["known"]:
        raise Refusal(
            "There is nothing to project a new date from: " + projection["why"] + " "
            "Resetting to a number you made up is exactly what this platform refuses. "
            "Meet something or estimate the open scope first.",
            status_code=409,
        )
    return commitment, projection


def commitment_resets(session: Session, commitment: Commitment) -> int:
    """How many times this promise has already been reset by the CEO. Disclosed
    on every reset: one is a project adapting, four in a fortnight is a date
    nobody should believe, and the chairman should not have to count them."""
    return int(session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.project_id == commitment.project_id,
            AuditLog.action == "commitment.self_move",
            AuditLog.entity_id == commitment.id,
        )
    ).scalar_one() or 0)


# The approval kinds that stay the chairman's at every autonomy level, because
# each is something the dial does not and cannot delegate: money above his line,
# legal weight, a credential only he holds the value of, a security challenge
# only a human may answer — and the project's own capacity, which is the one
# kind where the CEO is arguing for itself. A dial that could delegate that
# would let a project vote itself more room, which is the thing `directive_file`
# is restricted to prevent.
UNDELEGABLE_APPROVALS = frozenset({"spend", "legal", "tool_request", "assist", "capacity"})

# Which switches would have to be the CEO's for "a decision that is genuinely
# the owner's" to be, in fact, the CEO's.
_DIRECTION_SWITCHES = ("plan_authority", "scope_authority", "commitment_authority")


def check_approval_request(cons: ConstitutionDoc, kind: str) -> None:
    """An approval is a blocker on a human's desk with a default-deny clock on
    it. Filing one for something the constitution already put in the CEO's
    hands is the single way an autonomous project can still stall on its
    chairman — so it is a refusal, named, with the verbs it should have used.

    Only `owner_decision` is affected. The other kinds are undelegable at any
    setting and keep working exactly as before.
    """
    if kind != ApprovalKind.OWNER_DECISION:
        return
    if not all(ceo_holds(cons, switch) for switch in _DIRECTION_SWITCHES):
        return
    raise Refusal(
        "This project's constitution puts direction in your hands — you accept scope, "
        "you change the plan, and you sign and reset the delivery date. An "
        "owner_decision approval would park a decision you already hold on the "
        "chairman's desk with a clock on it, and he delegated it precisely so that "
        "would not happen. Decide it: scope_act to accept, cut, reopen, restage, "
        "reestimate or reword, commitment_reset to reset the date to the platform's "
        "projection. Every one is audited, disclosed to him in full, and reversible by "
        "him at any time. If you genuinely want his opinion first, ask him with "
        "ask_chairman — it never blocks and never stops you deciding in the meantime, "
        "and it carries the moment you stop waiting and what you do then, so the "
        "project moves either way. Money over the threshold, anything legally "
        "ambiguous, a credential you do not have, and a step only a human can take are "
        "still his: those approval kinds are unaffected.",
        status_code=403,
    )


def check_ask(session: Session, project: Project, agent: Agent,
              cons: ConstitutionDoc, answer_by_hours: float) -> None:
    """The two limits on asking: how long you may wait, and how many at once.

    Neither is about whether the question is worth his time — that judgement
    stays the CEO's, and the desk shows him how often it is exercised. These
    bound the two ways asking stops being cheap for him: a window so long the
    project is parked behind it, and a queue deep enough that answering becomes
    a chore he puts off, which is the failure this whole mechanism exists to
    make impossible.
    """
    from yaaf.server import asks as ask_lib

    if agent.kind != AgentKind.CEO:
        raise Refusal(
            "The boardroom is the CEO's channel with the chairman. Put it to your CEO "
            "in the leadership room and let it carry what needs carrying.",
            status_code=403,
        )
    if answer_by_hours > cons.ask_window_hours_max:
        raise Refusal(
            f"{answer_by_hours:g}h is longer than the {cons.ask_window_hours_max:g}h this "
            "constitution allows you to wait. Pick a window you can defend against the "
            "work's own deadline — the point of naming one is that the project keeps "
            "moving when it passes, and a window nobody would ever reach is the same as "
            "not naming one.",
            status_code=422,
        )
    open_now = ask_lib.open_count(session, project.id)
    if open_now >= cons.max_open_asks:
        raise Refusal(
            f"{open_now} question(s) already waiting on the chairman, and this "
            f"constitution allows {cons.max_open_asks}. Asking is cheap for you and "
            "expensive for him. Wait for an answer, let a window close and act on the "
            "default you named, or decide this one yourself and tell him what you did.",
            status_code=429,
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
    """The guarantee, as a refusal. When the date stops holding, the project
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


def check_proposals_after_delivery(
    session: Session, project: Project, cons: ConstitutionDoc
) -> None:
    """A delivered project may propose, within a bound.

    Its predecessor refused post-delivery proposals outright, after a CEO woken
    by its own agents' chatter opened R-14 and accepted it on its own authority
    36 hours after reporting 13 of 13 true. Acceptance is now guarded directly
    (`check_self_accept_after_delivery`), which is where the growth actually
    happens — but that left the other half of the incident unbounded. The CEO in
    it was not misbehaving: every task it could create was refused, and a new
    requirement was the only move the platform did not refuse. A project in that
    state, woken repeatedly, will keep finding that move.

    So the number of proposals it may have waiting is bounded by
    `queue_ceiling` — the dial that already means "how many open things at once"
    — rather than by a new number nobody chose. He is looking at a finished
    project and a decision; he should not also be looking at a list.
    """
    if not delivered_awaiting_confirmation(session, project):
        return
    waiting = session.execute(
        select(func.count(Requirement.id)).where(
            Requirement.project_id == project.id,
            Requirement.status == RequirementStatus.PROPOSED,
        )
    ).scalar_one()
    if waiting >= cons.queue_ceiling:
        raise Refusal(
            f"{waiting} proposals are already waiting on the chairman and this project "
            f"has reported itself finished. That is the ceiling ({cons.queue_ceiling}). "
            "He is looking at a delivery and a decision; a queue of ideas behind it "
            "makes the decision harder, not better. If one of the waiting proposals "
            "matters less than this one, cut it.",
            status_code=409,
        )


def check_self_accept_after_delivery(session: Session, project: Project) -> None:
    """A delivered project may still be PROPOSED to; it may not accept its own.

    The freeze this replaces refused the proposal itself, which made "I have
    finished early and can see the next move" unsayable in any form with state
    behind it — the only remaining channel was a boardroom remark nobody can
    act on later. Scope frozen on lateness, on breach AND on success meant the
    one thing a project could never do was succeed and keep going.

    But read what the freeze was actually built for: a CEO that, 36 hours after
    reporting 13 of 13 true, "opened R-14 and accepted it on its own authority".
    The abuse is the second half. A proposal creates no scope by construction —
    the chairman accepts it, and until he does no work may be created for it —
    so a project that proposes after delivering has put a card on the table and
    changed nothing. Self-accepting is how it grows itself back open behind a
    chairman who believes it is done, and that is what stays refused, at every
    dial setting, including the ones that normally delegate scope to the CEO.
    """
    if not delivered_awaiting_confirmation(session, project):
        return
    raise Refusal(
        "You may propose this, and you may not accept it. Every requirement is true "
        "and the chairman has been told the project is finished, so scope does not "
        "grow here without him — whatever the authority dial says the rest of the "
        "time. Your proposal is already on his desk with the delivery; he can accept "
        "it in one click, and until he does, nothing about the delivery has changed.",
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
#: 'question' used to be the fourth. It is gone because it was a way past the
#: cadence and nothing else — the same code path as 'blocker', leaving a message
#: with no clock, no default and no row on his desk. A CEO used it correctly and
#: still waited six hours for an answer nobody knew it was owed. Asking is now
#: `ask_chairman`, which is not a status and never faced this gate.
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
    """(CEO's last boardroom post, chairman's last). The chairman is identified
    by a set sender_user_id — NOT merely by a null sender_agent_id, because
    platform-authored boardroom lines (needs/assist announcements, the
    session-ended notice) have BOTH sender columns null. Counting those as the
    chairman would let filing a need mint a free bypass of his report cadence."""
    last_ceo = session.execute(
        select(func.max(Message.created_at)).where(
            Message.channel_id == channel_id, Message.sender_agent_id == ceo_agent_id)
    ).scalar_one()
    last_chairman = session.execute(
        select(func.max(Message.created_at)).where(
            Message.channel_id == channel_id, Message.sender_user_id.is_not(None))
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
    # Two standing obligations to speak, each spent the same way the chairman's
    # own open turn is: they buy the post that discharges them, not a standing
    # exemption. Without that bound a CEO could ask one question, let it lapse,
    # never close it, and have turned the cadence off for the life of the
    # project — which is a worse hole than the one this whole change closes.
    #
    # An ask he answered, or whose window ran out: what the CEO owes now is not
    # a status, it is what it did about the answer or in the absence of one.
    # And an open flag, which is where "the cadence does not hold you here" has
    # to be true — it was written into the context pack and enforced nowhere,
    # so a CEO that read it, believed it and posted was refused anyway, and the
    # only door left was to claim 'blocker', a different sentence about a
    # different thing.
    if obligation_unmet(session, project, agent, last_ceo):
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


def obligation_unmet(session: Session, project: Project, agent: Agent,
                      last_ceo: datetime | None) -> bool:
    """Does the CEO owe the chairman a sentence it has not yet said?

    An ask becomes owed the moment he answers it or its window closes; a flag
    the moment it opens. Either buys exactly one post — the one that discharges
    it — and it is spent by posting, the same way his own open turn is. A CEO
    that posts and still leaves the ask unclosed or the flag unraised faces the
    cadence again on its next status, and he can see both counts, so going
    quiet stays visible rather than becoming free.
    """
    from yaaf.core.models import FlagStatus
    from yaaf.server import asks as ask_lib
    from yaaf.server import flags as flag_lib

    moments = [ask.answered_at or ask.lapsed_at
               for ask in ask_lib.owed(session, project.id, agent.id)]
    moments += [flag.opened_at for flag in flag_lib.open_flags(session, project.id)
                if flag.status == FlagStatus.OPEN]
    moments = [m for m in moments if m is not None]
    if not moments:
        return False
    return last_ceo is None or last_ceo < max(moments)


def _check_report_override(
    session: Session, project: Project, agent: Agent, override: str
) -> None:
    if override == "question":
        raise Refusal(
            "Asking is no longer an override. It used to be one, and it bought nothing: a "
            "question that got past the cadence was still only a message, so a project "
            "could sit waiting on an answer the chairman did not know he owed, on a desk "
            "that truthfully said nothing was waiting. Use ask_chairman instead — it posts "
            "to the boardroom, the cadence has never held it, and it carries the two things "
            "a message cannot: when you stop waiting, and what you do then. It does not "
            "block you; keep working while you wait.")
    if override not in REPORT_OVERRIDES:
        raise Refusal(
            f"'{override}' is not a break-glass reason. The cadence breaks for exactly "
            f"these: {', '.join(REPORT_OVERRIDES)}. A question is not one of them — that "
            "is ask_chairman, and it is not a status.")
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
    # 'blocker' is a judgment call the platform cannot check. So is 'question'
    # — the ask verb: the CEO may put a real question to the chairman when it
    # feels strongly, at any dial setting, and asking never spends the report
    # cadence. Both pass — and both are recorded, so a CEO that cries blocker
    # or asks four times a day is visible to the chairman rather than merely
    # irritating.
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


# ── the spend envelope ──────────────────────────────────────────────────────────
#
# Real-world money (CEO Charter, stage C — docs/AUTONOMY.md). The dial for
# money is three numbers, not a switch: `approval_threshold_usd` (the line
# above which the chairman is asked regardless), `max_usd_per_purchase` (the
# line above which a single purchase is asked even below the threshold), and
# `external_monthly_usd_cap` (the month's hard wall, enforced in EVERY billing
# mode — token-billed projects used to meter real dollars and never cap them).
# Inside the envelope an autonomous CEO buys without asking; the ledger and
# the spot-audit are how the owner trusts the envelope after the fact.


def external_spent_this_month(session: Session, project_id: str) -> float:
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return float(session.execute(
        select(func.coalesce(func.sum(LedgerEntry.usd), 0.0)).where(
            LedgerEntry.project_id == project_id,
            LedgerEntry.kind == LedgerKind.EXTERNAL_SPEND,
            LedgerEntry.at >= month_start,
        )
    ).scalar_one())


def pending_purchase_usd(session: Session, project_id: str) -> float:
    """Cleared-but-unfinished purchases. They reserve the envelope: money the
    project has been told it may move is committed the moment it is cleared,
    or two overlapping purchases could each clear against the same headroom
    and together bust the wall. A failed finish releases the reservation; a
    stale one can be released by finishing it ok=false after checking with
    the vendor."""
    from yaaf.core.models import SideEffect, SideEffectStatus

    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = session.execute(
        select(SideEffect.payload).where(
            SideEffect.project_id == project_id,
            SideEffect.kind == "purchase",
            SideEffect.status == SideEffectStatus.RECORDED,
            SideEffect.created_at >= month_start,
        )
    ).scalars()
    return float(sum(float((p or {}).get("usd") or 0) for p in rows))


def check_external_spend(
    session: Session,
    project: Project,
    cons: ConstitutionDoc,
    usd: float,
    approval: Approval | None,
    enforce_wall: bool = True,
) -> float:
    """The envelope, refused with its arithmetic. Returns the month's
    committed real-world USD (booked + cleared-but-unfinished) so callers can
    quote headroom without re-running the aggregates.

    `enforce_wall=False` is for booking money that ALREADY moved (spend_book):
    refusing the record would un-report real spending, which is worse than any
    overshoot — the caller discloses the overshoot loudly instead. The
    approval lines still apply on both paths: they were the agent's to clear
    BEFORE paying."""
    committed = 0.0
    if cons.external_monthly_usd_cap > 0:
        committed = (external_spent_this_month(session, project.id)
                     + pending_purchase_usd(session, project.id))
        if enforce_wall and committed + usd > cons.external_monthly_usd_cap:
            raise Refusal(
                f"This would put the month's real-world spend at "
                f"${committed + usd:,.2f}, over the "
                f"${cons.external_monthly_usd_cap:,.0f} envelope "
                f"(${committed:,.2f} already booked or cleared — an unfinished "
                "purchase reserves the envelope until purchase_finish closes it). "
                "The wall does not take approvals — raising it is a constitution "
                "amendment, which is the chairman's, and yours to recommend with "
                "the arithmetic.",
                status_code=403,
            )
    over_purchase = 0 < cons.max_usd_per_purchase < usd
    over_threshold = usd >= cons.approval_threshold_usd
    if not (over_purchase or over_threshold):
        return committed
    ok = (
        approval is not None
        and approval.project_id == project.id
        and approval.status == ApprovalStatus.APPROVED
        and (approval.amount_usd or 0) >= usd
    )
    if not ok:
        line = (f"${cons.approval_threshold_usd:,.0f} (the approval threshold)"
                if over_threshold
                else f"${cons.max_usd_per_purchase:,.0f} (the per-purchase line)")
        raise Refusal(
            f"A spend of ${usd:,.2f} crosses {line} and needs an approved chairman "
            "approval covering the amount. Request one at POST /agent/approvals with "
            "kind='spend' — as a recommendation you would defend.",
            status_code=403,
        )
    return committed


def check_roles_exist(session: Session, slugs: list[str]) -> None:
    """One IN query; refused with the unknown names spelled out."""
    from yaaf.core.models import Role

    known = set(session.execute(
        select(Role.slug).where(Role.slug.in_(slugs))
    ).scalars())
    missing = sorted(set(slugs) - known)
    if missing:
        raise Refusal(
            f"No such role(s): {', '.join(missing)}. A grant to a role that does "
            "not exist grants nothing — check the roster and use exact slugs.",
            status_code=422,
        )


def roles_on_project(session: Session, project_id: str) -> set[str]:
    """Every role slug this project actually employs.

    Both halves of the org: a team's pinned role version, and the CEO's, which
    hangs off the agent rather than a team (`Agent.team_id` is null for it). A
    credential granted to the CEO's role is a real grant, so leaving it out
    would refuse something that works.
    """
    from yaaf.core.models import Agent, Role, RoleVersion, Team

    from_teams = select(Role.slug).join(
        RoleVersion, RoleVersion.role_id == Role.id
    ).join(Team, Team.role_version_id == RoleVersion.id).where(Team.project_id == project_id)
    from_ceo = select(Role.slug).join(
        RoleVersion, RoleVersion.role_id == Role.id
    ).join(Agent, Agent.role_version_id == RoleVersion.id).where(Agent.project_id == project_id)
    return set(session.execute(from_teams).scalars()) | set(session.execute(from_ceo).scalars())


def check_roles_on_project(session: Session, project_id: str, slugs: list[str]) -> None:
    """Refuse a grant to a role this project does not employ.

    `check_roles_exist` asks whether the role exists in the *library*, which is
    the wrong question: a project with no researcher accepts a grant to
    `researcher`, stores it, injects it nowhere, and says nothing. The comment
    on the agent-side grant already names the failure mode — "a grant to a role
    that does not exist grants nothing while telling everyone it did, the worst
    kind of success" — and it does not stop at the library.

    The refusal lists the roster, because an error that says no without saying
    what instead is how a chairman ends up typing employee names into a box
    that wanted role slugs.
    """
    wanted = [slug for slug in dict.fromkeys(slugs) if slug]
    if not wanted:
        return
    employed = roles_on_project(session, project_id)
    strangers = [slug for slug in wanted if slug not in employed]
    if not strangers:
        return
    roster = ", ".join(sorted(employed)) or "nobody yet — this project has no teams"
    raise Refusal(
        f"No role on this project called {', '.join(repr(s) for s in strangers)}. "
        f"This project employs: {roster}. A grant to a role nobody here has "
        "reaches no one, so it is refused rather than stored.",
        status_code=422,
    )


# ── the authority dial, in one place ────────────────────────────────────────────
#
# Every "does the CEO hold this?" question routes through here, so the day the
# Advised setting (announce, wait a window, silence means go) lands, it is one
# function's change instead of a hunt across five files.

def awaiting_chairman(session: Session, project: Project, cons: ConstitutionDoc) -> dict:
    """What this project is blocked on the chairman for, per the autonomy dial.

    One definition, because two of them would drift: the desk renders this, the
    sidebar counts it, and the janitor chases it by email. A page that says a
    project needs nothing while the janitor is mailing about it — or the
    reverse — is worse than either alone.

    Read the emptiness literally. On a fully autonomous project every entry
    here is empty, and not because the work vanished: the CEO accepts its own
    plan, adopts the platform's projected date and judges its own observables,
    each on the record and disclosed. Asking the chairman as well would be
    asking for a decision he gave away.
    """
    from yaaf.core.models import Commitment, CommitmentStatus, SuccessTestKind

    out: dict = {"plan": [], "verdicts": [], "commitment": None}

    # Asks are deliberately NOT here, though they were once. Every member of
    # this dict is a decision the dial left with the chairman, which is what
    # lets an autonomous project read empty and mean it. An ask is the
    # opposite: a question the CEO put to him about something it holds, and it
    # carries a clock. Clocked things that are his to answer live beside
    # `pending_approvals` — same shelf, same shape, same screen — rather than
    # in here, where one member exempt from the dial would make the emptiness
    # unreadable for all of them.
    if not ceo_holds(cons, "plan_authority"):
        # Proposed scope is not scope: the CEO may not build against it and may
        # not accept it, so the requirement waits on a human or on nothing.
        out["plan"] = list(session.execute(
            select(Requirement).where(
                Requirement.project_id == project.id,
                Requirement.status == RequirementStatus.PROPOSED,
            ).order_by(Requirement.key)
        ).scalars())

    if not ceo_holds(cons, "verdict_policy"):
        # All work closed, and the acceptance test is an observable no script
        # can run. The janitor's own words to the CEO: "You cannot mark it met
        # and neither can anyone here."
        out["verdicts"] = [
            r for r in session.execute(
                select(Requirement).where(
                    Requirement.project_id == project.id,
                    Requirement.status == RequirementStatus.CHECKING,
                ).order_by(Requirement.key)
            ).scalars()
            if (r.acceptance_test or {}).get("type") == SuccessTestKind.OBSERVABLE
        ]

    if not ceo_holds(cons, "commitment_authority"):
        out["commitment"] = session.execute(
            select(Commitment).where(
                Commitment.project_id == project.id,
                Commitment.status == CommitmentStatus.PROPOSED,
            ).order_by(Commitment.created_at.desc()).limit(1)
        ).scalar_one_or_none()
    return out


def ceo_holds(cons: ConstitutionDoc, switch: str) -> bool:
    value = getattr(cons, switch)
    return value == ("ceo_judgment" if switch == "verdict_policy" else "ceo")


def slip_moves(cons: ConstitutionDoc) -> str:
    """What the CEO can actually do about a slipped promise, per the dial.

    Written from the constitution rather than from habit because the old text
    said "propose cuts to the chairman" at every setting — advice that is
    simply false on a project whose chairman had already handed over the scope
    and the date, and which pushed the CEO toward the one move that could still
    stall the project: an approval on a desk nobody had asked to be involved.
    """
    holds_scope = ceo_holds(cons, "scope_authority")
    holds_date = ceo_holds(cons, "commitment_authority")
    if holds_scope and holds_date:
        return (
            "This is yours to solve and you have every verb for it: finish what is "
            "open, cut scope with scope_act, or — when the honest answer is that the "
            "date cannot hold — reset it with commitment_reset, which adopts the "
            "platform's own projection. Reach for cutting first if the real problem is "
            "that the scope is too big; a date reset repeatedly is a plan problem "
            "wearing a date problem's clothes. Decide, then tell the chairman what you "
            "did and why in your next report. Do NOT file an approval for this — it is "
            "not his decision on this project, and the platform will refuse it."
        )
    if holds_scope:
        return (
            "Your moves: finish what is open, or cut scope yourself with scope_act. "
            "The date itself is the chairman's to move — recommend it to him plainly "
            "with the arithmetic; he has been emailed."
        )
    if holds_date:
        return (
            "Your moves: finish what is open, or reset the date with commitment_reset, "
            "which adopts the platform's own projection. Cuts are the chairman's to "
            "make — recommend them plainly; he has been emailed."
        )
    return (
        "Your moves are the ones that change the arithmetic — finish what is open, or "
        "propose cuts to the chairman. The date is his to move. He has been emailed."
    )


# ── chairman directives ─────────────────────────────────────────────────────────
#
# The CEO may turn something the chairman said into something the platform
# does. Two shapes: a RULE directive files reporting-cadence policy, and a
# SCOPE directive executes a plan order — accept, cut, reopen, restage,
# reestimate, reword, unsequence — through the same admin machinery the
# chairman's own buttons drive. What keeps either from being a hole: it must
# cite a real, recent boardroom message he actually wrote, the authority
# exercised is his (proven by the citation), and every execution is echoed
# back threaded to his words, where a misreading is one sentence from
# correction — and every one of these verbs is reversible.

DIRECTIVE_FIELDS = CADENCE_FIELDS
DIRECTIVE_MAX_AGE_HOURS = 24

# The scope verbs a directive may execute, and the fields each one needs
# beyond the requirement it names. `accept` covers "add" too: the CEO
# proposes, then files the chairman's yes from the message where he said it.
DIRECTIVE_ACTIONS: dict[str, tuple[str, ...]] = {
    "accept": (),
    "cut": (),
    "reopen": (),
    "restage": ("stage",),
    "reestimate": ("est_runs",),
    "reword": ("statement",),
    "unsequence": ("depends_on_key",),
}


def check_directive_action(action: dict) -> None:
    _check_action_shape(action, DIRECTIVE_ACTIONS,
                        "a scope order a directive can carry",
                        "Anything else is the chairman's own button to press.")


# The verbs the CEO may exercise as ITS OWN authority when the constitution's
# dial grants them (stage B — docs/AUTONOMY.md). Same shapes as directive
# orders, plus the verdict; the difference is whose authority is exercised:
# a directive runs on the chairman's cited word, a self-action on a rule he
# wrote into the constitution. `verdict` requires `ok` (bool) and is gated on
# verdict_policy rather than scope_authority.
SELF_SCOPE_ACTIONS: dict[str, tuple[str, ...]] = {
    **DIRECTIVE_ACTIONS,
    "verdict": ("ok",),
}

# Which constitution switch holds each verb.
_ACTION_AUTHORITY: dict[str, str] = {
    "accept": "plan_authority",
    "verdict": "verdict_policy",
    **{kind: "scope_authority"
       for kind in ("cut", "reopen", "restage", "reestimate", "reword", "unsequence")},
}


def check_self_scope_action(cons: ConstitutionDoc, action: dict) -> None:
    """Validate the shape, then the authority. A verb the dial has not granted
    is a 403 that names the two legitimate paths: the chairman's own button,
    or a directive citing his boardroom order."""
    _check_action_shape(action, SELF_SCOPE_ACTIONS,
                        "a scope verb the CEO can hold",
                        "Anything else is the chairman's own button to press.")
    kind = action["kind"]
    switch = _ACTION_AUTHORITY[kind]
    held = getattr(cons, switch)
    if not ceo_holds(cons, switch):
        raise Refusal(
            f"The constitution holds {switch} = '{held}': '{kind}' is not yours to "
            "exercise on this project. Two paths remain — the chairman's own button, "
            "or, if he ordered it in the boardroom, file it as a directive citing his "
            "message. The dial itself is his amendment to turn, and yours to recommend.",
            status_code=403,
        )


def _check_action_shape(action: dict, allowed: dict[str, tuple[str, ...]],
                        what: str, tail: str) -> None:
    kind = action.get("kind")
    if kind not in allowed:
        raise Refusal(
            f"'{kind}' is not {what}. The verbs are: "
            f"{', '.join(sorted(allowed))}. {tail}",
            status_code=422,
        )
    if not action.get("requirement_key"):
        raise Refusal("A scope action names the requirement by key (e.g. R-07).",
                      status_code=422)
    missing = [f for f in allowed[kind] if action.get(f) in (None, "")]
    if missing:
        raise Refusal(f"The {kind} action needs {', '.join(missing)}.", status_code=422)
    _coerce_action_fields(action)


def _coerce_action_fields(action: dict) -> None:
    """Type- and range-check the action's fields, refusing instead of letting
    a bad value 500 mid-transaction or write a stage the chairman's own routes
    would never accept. Mutates the dict so callers read coerced values. The
    bounds mirror the admin Pydantic models exactly — the same verb must face
    the same walls whichever hand exercises it."""
    def _int_field(name: str, lo: int, hi: int, required: bool) -> None:
        value = action.get(name)
        if value in (None, ""):
            if required:
                raise Refusal(f"'{name}' is required here.", status_code=422)
            return
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise Refusal(f"'{name}' must be a whole number, got {value!r}.",
                          status_code=422)
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise Refusal(f"'{name}' must be a whole number, got {value!r}.",
                          status_code=422) from None
        if not lo <= number <= hi:
            raise Refusal(f"'{name}' must be between {lo} and {hi}, got {number}. "
                          "Stage 0 is R-00's, the plan gate's, and nobody else's.",
                          status_code=422)
        action[name] = number

    kind = action["kind"]
    if kind in ("accept", "restage"):
        _int_field("stage", 1, 50, required=(kind == "restage"))
    if kind == "reestimate":
        _int_field("est_runs", 1, 10_000, required=True)
    if kind == "reword":
        statement = action.get("statement")
        if not isinstance(statement, str) or not 10 <= len(statement.strip()) <= 2000:
            raise Refusal("'statement' must be 10-2000 characters.", status_code=422)
        action["statement"] = statement.strip()
    if kind == "verdict" and not isinstance(action.get("ok"), bool):
        raise Refusal("'ok' must be true or false.", status_code=422)


def check_directive_scope(changes: dict) -> None:
    if not changes:
        raise Refusal("A directive has to change something.")
    outside = sorted(set(changes) - DIRECTIVE_FIELDS)
    if outside:
        raise Refusal(
            f"You may not set {', '.join(outside)} from a directive. An agent filing its own "
            f"constitution is how a project votes itself a bigger budget; the only rules you "
            f"may file are the ones that limit what you ask of the chairman "
            f"({', '.join(sorted(DIRECTIVE_FIELDS))}). Everything else is his amendment to "
            "make, and yours to recommend.",
            status_code=403,
        )


def check_directive_citation(
    session: Session, project: Project, message_id: str, for_rules: bool = True
) -> Message:
    """The cited message must be the chairman's own words, in the boardroom,
    recent — and, for a rule directive, newer than the rule it replaces, so a
    stale message cannot be re-cited to undo a fresher instruction. A scope
    order skips that last check: it executes an act, it does not replace a
    standing rule."""
    message = session.get(Message, message_id)
    if message is None or message.project_id != project.id:
        raise Refusal(f"No message {message_id} in this project to cite.", status_code=404)
    channel = session.get(Channel, message.channel_id)
    if channel is None or channel.kind != ChannelKind.BOARDROOM:
        raise Refusal(
            "Cite something the chairman said in the BOARDROOM. Instructions relayed through "
            "leadership are not his, however confidently they are repeated.")
    # The chairman is identified by his own user id, not by the mere absence of
    # an agent sender: platform-authored boardroom lines (a filed need, an
    # assist announcement, the session-ended notice) carry NEITHER sender, and
    # accepting one as "the chairman's words" would let a CEO cite a line it
    # caused to execute scope actions recorded as the chairman's own act.
    if message.sender_agent_id is not None or message.sender_user_id != project.chairman_user_id:
        raise Refusal("That message is not the chairman's own words in the boardroom. "
                      "You cannot cite an agent, or a line the platform posted.")
    age = utcnow() - message.created_at
    if age > timedelta(hours=DIRECTIVE_MAX_AGE_HOURS):
        raise Refusal(
            f"That message is {age.days}d old. File a directive when he gives it, not from "
            f"history — anything older than {DIRECTIVE_MAX_AGE_HOURS}h is his to restate.")
    if not for_rules:
        return message
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
    session: Session,
    project: Project,
    agent: Agent,
    cited: Message,
    changes: dict | None = None,
    action_detail: str | None = None,
) -> None:
    """Say it back to him. The platform can verify that he spoke and when, but
    never what he meant — so the safeguard against a misread directive is that
    he sees it land, in his own channel, threaded to the words it came from."""
    if action_detail is not None:
        body = (f"Done, from your message above: **{action_detail}**. Executed on your "
                f"authority and on the record. Say so if I read you wrong — every one of "
                f"these is reversible.")
        title = "Plan changed on your order"
    else:
        readable = ", ".join(f"{key.removeprefix('chairman_report_')} = {value}"
                             for key, value in sorted((changes or {}).items()))
        body = (f"Recorded as policy, effective now: **{readable}**. I took this from your "
                f"message above and the platform enforces it from here — it is no longer "
                f"something I have to remember between runs. Say so if I read you wrong.")
        title = "Reporting cadence changed"
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
    # Best-effort out-of-band: a change he did not watch happen is exactly the
    # thing he should not have to be watching Slack to catch.
    from yaaf.server.notify import Attention, chairman_notify

    chairman_notify(session, project, title, body, attention=Attention.FYI)


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
