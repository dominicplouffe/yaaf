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
    Amendment,
    Approval,
    ApprovalStatus,
    AuditLog,
    Channel,
    ChannelKind,
    Evidence,
    EvidenceKind,
    Hypothesis,
    HypothesisStatus,
    Memory,
    MemoryScope,
    Message,
    Project,
    ProjectStatus,
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
) -> None:
    require_project_running(project)
    validate_success_test(cons, success_test)

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
        tz = ZoneInfo(cons.chairman_report_timezone)
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


def check_evidence_add(project: Project, kind: str, source_url: str | None) -> None:
    require_project_running(project)
    if kind == EvidenceKind.EXTERNAL and not (source_url or "").strip():
        raise Refusal(
            "External evidence needs a source_url the platform can fetch and verify. "
            "Evidence nobody can check is opinion."
        )
    if kind not in (EvidenceKind.EXTERNAL, EvidenceKind.INTERNAL):
        raise Refusal("kind must be 'external' or 'internal'.")
