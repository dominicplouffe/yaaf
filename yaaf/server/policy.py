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

from datetime import UTC, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from yaaf.core import ids
from yaaf.core.constitution import ConstitutionDoc, effective
from yaaf.core.models import (
    Agent,
    AgentKind,
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


def report_cadence(project: Project) -> ReportCadence:
    """The chairman's standing reporting rule, read live from project config.

    Deliberately config and not constitution: a cadence is a cost control, and
    the chairman changes it mid-conversation ("stop, every 4 hours") expecting
    it to bind the next run — not to clear a 72-hour cooling-off. Same argument
    the budget fields already won in :mod:`yaaf.core.constitution`.

    - ``chairman_report_interval_hours``: float; 0 (default) disables the rule
    - ``chairman_report_window_local``: ``"09:00-20:00"``, or absent for all day
    - ``timezone``: IANA name, shared with ``ceo_daily_at``
    """
    config = project.config or {}
    try:
        interval = float(config.get("chairman_report_interval_hours",
                                    DEFAULT_REPORT_INTERVAL_HOURS))
    except (TypeError, ValueError):
        interval = DEFAULT_REPORT_INTERVAL_HOURS
    try:
        tz = ZoneInfo(str(config.get("timezone", "UTC")))
    except Exception:
        tz = ZoneInfo("UTC")
    return ReportCadence(max(interval, 0.0),
                         _parse_window(config.get("chairman_report_window_local")), tz)


def _parse_window(raw: object) -> tuple[time, time] | None:
    """``"09:00-20:00"`` → (09:00, 20:00). Anything unparseable, or a window
    that wraps midnight, reads as "no window" — a malformed config must not
    silently gag the CEO."""
    if not raw:
        return None
    try:
        start_s, end_s = str(raw).split("-", 1)
        start = time(*(int(part) for part in start_s.strip().split(":", 1)))
        end = time(*(int(part) for part in end_s.strip().split(":", 1)))
    except (TypeError, ValueError):
        return None
    return (start, end) if start < end else None


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
    cadence = report_cadence(project)
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


def check_memory_add(
    session: Session, cons: ConstitutionDoc, agent: Agent, scope: str, team_id: str | None
) -> None:
    if scope == MemoryScope.PROJECT:
        if agent.kind != AgentKind.CEO:
            raise Refusal("Project-scope memory is the CEO's; write team-scope memory.", 403)
        cap = cons.memory_cap_project
        count_filter = [Memory.project_id == agent.project_id, Memory.scope == scope]
    elif scope == MemoryScope.TEAM:
        if agent.team_id is None or team_id != agent.team_id:
            raise Refusal("Team-scope memory belongs to your own team only.", status_code=403)
        cap = cons.memory_cap_team
        count_filter = [Memory.team_id == team_id, Memory.scope == scope]
    else:
        raise Refusal("scope must be 'project' or 'team'.")

    live = session.execute(
        select(func.count(Memory.id)).where(*count_filter, Memory.superseded_at.is_(None))
    ).scalar_one()
    if live >= cap:
        raise Refusal(
            f"Memory cap reached ({live}/{cap}). The cap is real: supersede or forget "
            "something first. A memory store that only grows becomes a journal, and a "
            "journal read every cycle is how the last project drowned.",
            status_code=409,
        )


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
