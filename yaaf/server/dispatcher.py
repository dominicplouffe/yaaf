"""The dispatcher: turns events into agent runs — or, mostly, into nothing.

lana's cheapest and most important property, moved into the platform: the
decision "is there work for this agent?" is a SQL query, made *before* a token
is spent. An idle project costs $0.

Hard limits enforced here (constitution class):

- per-agent wakes/hour cap
- per-project daily run ceiling
- one in-flight run per agent (coalescing: N triggers, one wake)
- per-project concurrent-run cap (config; protects shared rate limits)
- the pair circuit-breaker: a thread where the same two agents exchanged N
  messages with no task changing state gets frozen, not another wake
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.constitution import ConstitutionDoc, effective
from yaaf.core.db import db_session, notify
from yaaf.core.models import (
    Agent,
    AgentKind,
    AgentStatus,
    Channel,
    ChannelRead,
    Heartbeat,
    Message,
    Notice,
    NoticeKind,
    Project,
    ProjectStatus,
    Run,
    RunKind,
    RunQueue,
    RunStatus,
    Task,
    TaskEvent,
    TaskStatus,
    Team,
    utcnow,
)
from yaaf.server import policy

log = logging.getLogger("yaaf.dispatcher")

DEFAULT_CEO_HEARTBEAT_HOURS = 6.0
DEFAULT_MAX_CONCURRENT_RUNS = 3


def dispatcher_loop() -> None:  # pragma: no cover - thin loop around dispatch_once
    while True:
        try:
            with db_session() as session:
                dispatch_once(session)
        except Exception:
            log.exception("dispatch tick failed")
        time.sleep(settings().dispatch_poll_s)


def dispatch_once(session: Session) -> int:
    """One tick over all running projects. Returns the number of runs enqueued."""
    enqueued = 0
    for project in session.execute(
        select(Project).where(Project.status == ProjectStatus.RUNNING)
    ).scalars():
        try:
            cons = effective(session, project.id)
        except LookupError:
            continue
        enqueued += _dispatch_project(session, project, cons)
    return enqueued


def _dispatch_project(session: Session, project: Project, cons: ConstitutionDoc) -> int:
    now = utcnow()

    # Per-project daily ceiling: runs created today, queued or historical.
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = session.execute(
        select(func.count(Run.id)).where(Run.project_id == project.id,
                                         Run.created_at >= day_start)
    ).scalar_one()
    today += session.execute(
        select(func.count(RunQueue.id)).where(RunQueue.project_id == project.id,
                                              RunQueue.created_at >= day_start)
    ).scalar_one()
    if today >= cons.runs_per_project_per_day:
        return 0

    # Per-project concurrency cap (config, not constitution).
    max_concurrent = int(project.config.get("max_concurrent_runs", DEFAULT_MAX_CONCURRENT_RUNS))
    in_flight = session.execute(
        select(func.count(Run.id)).where(Run.project_id == project.id,
                                         Run.status == RunStatus.RUNNING)
    ).scalar_one()
    in_flight += session.execute(
        select(func.count(RunQueue.id)).where(RunQueue.project_id == project.id)
    ).scalar_one()

    enqueued = 0
    agents = list(
        session.execute(
            select(Agent).where(Agent.project_id == project.id,
                                Agent.status == AgentStatus.ACTIVE)
        ).scalars()
    )
    for agent in agents:
        if in_flight + enqueued >= max_concurrent:
            break
        if _agent_busy(session, agent):
            continue
        if _wakes_last_hour(session, agent) >= cons.wakes_per_agent_per_hour:
            continue
        reasons = _wake_reasons(session, project, cons, agent)
        if not reasons:
            continue
        session.add(
            RunQueue(
                id=ids.new_id(ids.RUN),
                project_id=project.id,
                agent_id=agent.id,
                kind=RunKind.AGENT_RUN,
                trigger={"type": "wake", "reasons": reasons},
                task_id=_primary_task(reasons),
            )
        )
        notify(session, "yaaf_run_queue", project.id)
        enqueued += 1
    return enqueued


def _agent_busy(session: Session, agent: Agent) -> bool:
    queued = session.execute(
        select(func.count(RunQueue.id)).where(RunQueue.agent_id == agent.id)
    ).scalar_one()
    if queued:
        return True
    running = session.execute(
        select(func.count(Run.id)).where(Run.agent_id == agent.id,
                                         Run.status == RunStatus.RUNNING)
    ).scalar_one()
    return bool(running)


def _wakes_last_hour(session: Session, agent: Agent) -> int:
    hour_ago = utcnow() - timedelta(hours=1)
    n = session.execute(
        select(func.count(Run.id)).where(Run.agent_id == agent.id,
                                         Run.created_at >= hour_ago)
    ).scalar_one()
    n += session.execute(
        select(func.count(RunQueue.id)).where(RunQueue.agent_id == agent.id,
                                              RunQueue.created_at >= hour_ago)
    ).scalar_one()
    return n


def _primary_task(reasons: list[dict]) -> str | None:
    for reason in reasons:
        if reason.get("task_id"):
            return reason["task_id"]
    return None


def _wake_reasons(
    session: Session, project: Project, cons: ConstitutionDoc, agent: Agent
) -> list[dict]:
    reasons: list[dict] = []
    now = utcnow()

    # 1. Unread messages — with the pair circuit-breaker standing between a
    #    ping-pong thread and yet another wake.
    for channel_id in policy.agent_channel_ids(session, agent):
        cursor = session.execute(
            select(ChannelRead).where(ChannelRead.agent_id == agent.id,
                                      ChannelRead.channel_id == channel_id)
        ).scalar_one_or_none()
        stmt = select(Message).where(Message.channel_id == channel_id)
        if cursor is not None:
            stmt = stmt.where(Message.created_at > cursor.last_read_at)
        unread = [
            m for m in session.execute(stmt.order_by(Message.created_at)).scalars()
            if m.sender_agent_id != agent.id
        ]
        actionable = 0
        for message in unread:
            root_id = message.thread_id or message.id
            if _breaker_trips(session, project, cons, message):
                _freeze_thread(session, project, root_id)
                continue
            root = session.get(Message, root_id)
            if root is not None and root.frozen_at is not None:
                continue
            actionable += 1
        if actionable:
            reasons.append({"kind": "messages", "channel_id": channel_id, "count": actionable})

    # 2. Unconsumed notices.
    notices = session.execute(
        select(func.count(Notice.id)).where(Notice.agent_id == agent.id,
                                            Notice.consumed_at.is_(None))
    ).scalar_one()
    if notices:
        reasons.append({"kind": "notices", "count": notices})

    # 3. Work: ready tasks this agent could claim, or a claim to push forward.
    reasons.extend(_work_reasons(session, agent, now))

    # 4. Heartbeats: the CEO plans on a cadence even in silence.
    if agent.kind == AgentKind.CEO:
        hours = float(project.config.get("ceo_heartbeat_hours", DEFAULT_CEO_HEARTBEAT_HOURS))
        beat = session.execute(
            select(Heartbeat).where(Heartbeat.agent_id == agent.id,
                                    Heartbeat.name == "planning")
        ).scalar_one_or_none()
        due = beat is None or beat.last_fired_at <= now - timedelta(hours=hours)
        if due:
            if beat is None:
                session.add(Heartbeat(agent_id=agent.id, name="planning", last_fired_at=now))
            else:
                beat.last_fired_at = now
            reasons.append({"kind": "heartbeat", "name": "planning"})

        # 4b. Daily wake at a wall-clock time (config: ceo_daily_at "HH:MM" +
        #     timezone). Fires once per local day, at or after the target;
        #     lana's standup pattern, generalized.
        daily = _daily_wake_due(session, project, agent, now)
        if daily:
            reasons.append(daily)

    return reasons


def _daily_wake_due(session: Session, project: Project, agent: Agent, now) -> dict | None:
    raw = project.config.get("ceo_daily_at")
    if not raw:
        return None
    try:
        from zoneinfo import ZoneInfo

        hour, minute = (int(part) for part in str(raw).split(":", 1))
        tz = ZoneInfo(project.config.get("timezone", "UTC"))
    except Exception:
        log.warning("project %s has invalid ceo_daily_at/timezone config", project.slug)
        return None
    now_local = now.astimezone(tz)
    target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now_local < target:
        return None
    beat = session.execute(
        select(Heartbeat).where(Heartbeat.agent_id == agent.id, Heartbeat.name == "daily")
    ).scalar_one_or_none()
    if beat is not None and beat.last_fired_at.astimezone(tz).date() >= now_local.date():
        return None
    if beat is None:
        session.add(Heartbeat(agent_id=agent.id, name="daily", last_fired_at=now))
    else:
        beat.last_fired_at = now
    return {"kind": "heartbeat", "name": "daily",
            "note": f"daily {raw} {project.config.get('timezone', 'UTC')} wake"}


def _work_reasons(session: Session, agent: Agent, now) -> list[dict]:
    reasons: list[dict] = []
    if agent.team_id is None:
        return reasons
    team = session.get(Team, agent.team_id)
    lead = policy.effective_lead(session, team) if team else None
    is_lead = lead is not None and lead.id == agent.id

    def _ready(stmt) -> list[Task]:
        return [
            t for t in session.execute(stmt).scalars()
            if t.not_before is None or t.not_before <= now
        ]

    if is_lead:
        for task in _ready(
            select(Task).where(Task.team_id == agent.team_id,
                               Task.parent_task_id.is_(None),
                               Task.status == TaskStatus.READY)
        ):
            reasons.append({"kind": "team_task_ready", "task_id": task.id})
    for task in _ready(
        select(Task).where(Task.assignee_agent_id == agent.id,
                           Task.status == TaskStatus.READY)
    ):
        reasons.append({"kind": "assignment_ready", "task_id": task.id})

    # A claim with nothing running behind it: wake to push it forward — unless
    # it's a lead's decomposed task still waiting on open subtasks.
    claimed = session.execute(
        select(Task).where(Task.claimed_by_agent_id == agent.id,
                           Task.status == TaskStatus.CLAIMED)
    ).scalars()
    for task in claimed:
        open_subtasks = session.execute(
            select(func.count(Task.id)).where(
                Task.parent_task_id == task.id, Task.status.in_(policy.OPEN_TASK_STATUSES)
            )
        ).scalar_one()
        if open_subtasks == 0:
            reasons.append({"kind": "claimed_task", "task_id": task.id})
    return reasons


def _breaker_trips(
    session: Session, project: Project, cons: ConstitutionDoc, message: Message
) -> bool:
    """True when this message's thread is a two-agent exchange of N messages
    with no task transition anywhere in the project during the exchange."""
    root_id = message.thread_id or message.id
    thread = list(
        session.execute(
            select(Message)
            .where((Message.thread_id == root_id) | (Message.id == root_id))
            .order_by(Message.created_at.desc())
            .limit(cons.pair_breaker_messages)
        ).scalars()
    )
    if len(thread) < cons.pair_breaker_messages:
        return False
    authors = {m.sender_agent_id for m in thread}
    if None in authors or len(authors) != 2:
        return False  # a human in the thread, or a wider conversation
    span_start = min(m.created_at for m in thread)
    transitions = session.execute(
        select(func.count(TaskEvent.id)).where(TaskEvent.project_id == project.id,
                                               TaskEvent.at >= span_start)
    ).scalar_one()
    return transitions == 0


def _freeze_thread(session: Session, project: Project, root_id: str) -> None:
    root = session.get(Message, root_id)
    if root is None or root.frozen_at is not None:
        return
    root.frozen_at = utcnow()
    ceo = policy.ceo_of(session, project.id)
    if ceo is not None:
        session.add(
            Notice(
                id=ids.new_id(ids.NOTICE),
                project_id=project.id,
                agent_id=ceo.id,
                kind=NoticeKind.THREAD_FROZEN,
                body=(
                    f"Thread {root_id} was frozen by the circuit breaker: "
                    f"{effective(session, project.id).pair_breaker_messages} messages between "
                    "the same two agents with no task changing state. Talk is not progress. "
                    "Re-plan the work; the chairman can unfreeze the thread if it truly "
                    "needs to continue."
                ),
            )
        )
    channel = session.get(Channel, root.channel_id)
    log.info("froze thread %s in channel %s (%s)", root_id, root.channel_id,
             channel.kind if channel else "?")
