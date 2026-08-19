"""The dispatcher: turns events into agent runs — or, mostly, into nothing.

lana's cheapest and most important property, moved into the platform: the
decision "is there work for this agent?" is a SQL query, made *before* a token
is spent. An idle project costs $0.

Hard limits enforced here (constitution class):

- per-agent wakes/hour cap
- per-project daily run ceiling
- one in-flight run per agent (coalescing: N triggers, one wake)
- per-project concurrent-run cap (protects shared rate limits)
- the reserved governance slice: past `daily cap - reserved_governance_runs`
  only a review, a cycle plan or a re-forecast may still spend
- the pair circuit-breaker: a thread where the same two agents exchanged N
  messages with no task changing state gets frozen, not another wake
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, timedelta

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
    ChannelKind,
    ChannelRead,
    Heartbeat,
    Hypothesis,
    HypothesisStatus,
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

# Wake reasons that may spend the constitution's reserved governance runs.
# These are the wakes whose job is to *report on* the work — a status review, a
# new cycle's plan, a commitment that needs re-forecasting — so they must still
# be possible on a day the company has spent its ordinary allowance.
GOVERNANCE_REASON_KINDS = {"review", "cycle_plan", "commitment"}

# Notice coalescing: the janitor drips notices (a reaped run, a drought, an
# expiry) and each alone is not worth a full-price run. Chairman-driven or
# budget notices wake immediately; the rest batch until the oldest has waited
# out the window or enough have piled up.
NOTICE_COALESCE_MINUTES = 10.0
NOTICE_COALESCE_COUNT = 3
# Agent-to-agent chatter batches like notices: half of all runs start within
# five minutes of the same agent's prior run — reply ping-pong at full price.
# The chairman's messages (and anything in the boardroom) still wake instantly.
MESSAGE_COALESCE_MINUTES = 5.0
MESSAGE_COALESCE_COUNT = 3
# MEMORY_FULL is urgent because the scope has stopped accepting writes and only
# this agent can restart it; MEMORY_PRESSURE deliberately is not — a nearly-full
# store is not worth a full-price wake, so it coalesces and rides the next one.
URGENT_NOTICE_KINDS = {NoticeKind.BUDGET_WARNING, NoticeKind.BUDGET_EXHAUSTED,
                       NoticeKind.APPROVAL_DECIDED, NoticeKind.AUDIT_FAILED,
                       NoticeKind.MEMORY_FULL,
                       NoticeKind.COMMITMENT_AT_RISK, NoticeKind.COMMITMENT_BREACHED}

# A commitment that stopped holding needs a re-forecast wake even on a day
# ordinary work already spent the allowance — that wake is one of the three
# things the reserved governance slice exists for.
COMMITMENT_NOTICE_KINDS = {NoticeKind.COMMITMENT_AT_RISK, NoticeKind.COMMITMENT_BREACHED}


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


def day_start(now):
    """Midnight UTC. The daily ceiling is a fixed window, not a rolling one —
    so it refills all at once, and the chairman deserves to be told when."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def daily_runs_used(session: Session, project: Project, since) -> int:
    """Runs created today, queued or historical. Chat sessions are the talk
    lane, not work — they never count against the ceilings enforced here."""
    used = session.execute(
        select(func.count(Run.id)).where(Run.project_id == project.id,
                                         Run.kind != RunKind.CHAT_SESSION,
                                         Run.created_at >= since)
    ).scalar_one()
    used += session.execute(
        select(func.count(RunQueue.id)).where(RunQueue.project_id == project.id,
                                              RunQueue.created_at >= since)
    ).scalar_one()
    return used


def hourly_runs_used(session: Session, project: Project, now) -> int:
    """Runs in the TRAILING hour. The daily ceiling is a fixed window that
    refills in a lump, which is how a project spends its whole day before
    lunch; this is the same count over a window that clears continuously."""
    return daily_runs_used(session, project, now - timedelta(hours=1))


def _in_flight(session: Session, project: Project) -> int:
    running = session.execute(
        select(func.count(Run.id)).where(Run.project_id == project.id,
                                         Run.kind != RunKind.CHAT_SESSION,
                                         Run.status == RunStatus.RUNNING)
    ).scalar_one()
    running += session.execute(
        select(func.count(RunQueue.id)).where(RunQueue.project_id == project.id)
    ).scalar_one()
    return running


def _max_concurrent(cons: ConstitutionDoc) -> int:
    """Concurrency cap. A constitution rule since the rebuild: it is a limit on
    the agents, so it belongs where limits are audited and disclosed."""
    return cons.max_concurrent_runs


def _is_governance(reasons: list[dict]) -> bool:
    """Does this wake exist to *report on* the work rather than do it?

    Governance wakes may spend the reserved slice of the daily ceiling. The
    audit's sharpest failure was a company that spent its whole allowance
    before lunch: on such a day ordinary work stopping is the brake working,
    but the status review stopping is the brake eating the one run whose entire
    job is to tell the chairman what happened.
    """
    return any(r.get("kind") in GOVERNANCE_REASON_KINDS for r in reasons)


def _dispatch_project(session: Session, project: Project, cons: ConstitutionDoc) -> int:
    now = utcnow()

    today = daily_runs_used(session, project, day_start(now))
    if today >= cons.runs_per_project_per_day:
        return 0
    # Past this line ordinary work is done for the day and only governance may
    # still spend. The reservation is against the *daily* ceiling alone: the
    # hourly brake below clears continuously, so it can only ever delay a
    # review by an hour, never cost the day one.
    #
    # Never more than half the day, whatever the rule says. A reservation of 3
    # against a ceiling of 1 would make every run a governance run and the
    # company would report on work it was never allowed to do — the brake
    # eating the thing it exists to protect.
    reserved = min(cons.reserved_governance_runs, cons.runs_per_project_per_day // 2)
    governance_only = today >= cons.runs_per_project_per_day - reserved

    # Pacing. Without this the daily ceiling is the only brake, and it is a
    # fixed window with no rate limit: six agents at six wakes an hour is
    # thirty-six an hour, so a hundred-run day is gone in under three hours and
    # the company sleeps until midnight with tasks still on the board.
    if hourly_runs_used(session, project, now) >= cons.runs_per_project_per_hour:
        return 0

    max_concurrent = _max_concurrent(cons)
    in_flight = _in_flight(session, project)

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
        if governance_only and not _is_governance(reasons):
            continue
        task_id = _primary_task(reasons)
        session.add(
            RunQueue(
                id=ids.new_id(ids.RUN),
                project_id=project.id,
                agent_id=agent.id,
                kind=RunKind.AGENT_RUN,
                trigger={"type": "wake", "reasons": reasons},
                task_id=task_id,
                # What this run is FOR, resolved at wake time. 62% of runs in
                # the audit carried no task id and consumed half the spend, so
                # cost per deliverable was not computable — and an estimate you
                # cannot compare with an actual is a number nobody is held to.
                requirement_id=_requirement_for_wake(session, project, agent, task_id),
            )
        )
        notify(session, "yaaf_run_queue", project.id)
        enqueued += 1
    return enqueued


def _requirement_for_wake(session: Session, project: Project, agent: Agent,
                          task_id: str | None) -> str | None:
    """Which requirement this wake is spending against.

    A working run inherits its task's requirement. A planning run has no task
    by construction — that is the half of spend the audit found unattributable
    — so it is stamped against the open plan while there is one, which is the
    honest answer to "what did planning cost" and makes R-00's own runs visible
    rather than free.
    """
    if task_id:
        task = session.get(Task, task_id)
        if task is not None and task.requirement_id:
            return task.requirement_id
    if agent.kind == AgentKind.CEO and policy.requires_requirements(project):
        plan = policy.plan_requirement(session, project.id)
        if plan is not None and plan.status not in policy.CLOSED_REQUIREMENT_STATUSES:
            return plan.id
    return None


def _unread_for(session: Session, project: Project, agent: Agent) -> int:
    """Messages this agent would wake for. Mirrors the filter in _wake_reasons
    (not self-authored, not under a frozen root, boardroom skipped for a CEO
    whose chat session is live) minus the pair-breaker, which is a trip
    detector rather than a filter and cannot run from a read path."""
    skip: set[str] = set()
    if agent.kind == AgentKind.CEO and chat_session_live(session, project):
        skip = {
            c.id for c in session.execute(
                select(Channel).where(Channel.project_id == project.id,
                                      Channel.kind == ChannelKind.BOARDROOM)
            ).scalars()
        }
    total = 0
    for channel_id in policy.agent_channel_ids(session, agent):
        if channel_id in skip:
            continue
        cursor = session.execute(
            select(ChannelRead).where(ChannelRead.agent_id == agent.id,
                                      ChannelRead.channel_id == channel_id)
        ).scalar_one_or_none()
        stmt = select(Message).where(Message.channel_id == channel_id)
        if cursor is not None:
            stmt = stmt.where(Message.created_at > cursor.last_read_at)
        for message in session.execute(stmt).scalars():
            if message.sender_agent_id == agent.id:
                continue
            root = session.get(Message, message.thread_id or message.id)
            if root is not None and root.frozen_at is not None:
                continue
            total += 1
    return total


def activity_state(session: Session, project: Project, cons: ConstitutionDoc) -> dict:
    """Why work is — or is not — starting right now.

    `_dispatch_project` decides this roughly thirty times a minute and throws
    it away, which is why "why is nothing happening?" had no answer anywhere in
    the product: a project blocked on a cap and a project with nothing to do
    both render as zero running runs. This is that decision, read-only.

    It reuses the same helpers the dispatcher enforces with, so the number on
    the screen cannot drift from the number that stopped the work. It
    deliberately does NOT call `_wake_reasons`: that advances the planning
    heartbeat and can freeze a thread, and loading a page must never move
    governance state. Message wakes are counted by `_unread_for` instead, so a
    reading can differ from a real wake by at most one tick.

    `blocked_by` names the first gate that stops a run, and only when there is
    work to stop — with nothing pending it stays None, because an idle project
    with an empty board is working correctly, not stuck.
    """
    now = utcnow()
    start = day_start(now)
    used = daily_runs_used(session, project, start)
    used_hour = hourly_runs_used(session, project, now)
    in_flight = _in_flight(session, project)
    max_concurrent = _max_concurrent(cons)

    agents = list(
        session.execute(
            select(Agent).where(Agent.project_id == project.id,
                                Agent.status == AgentStatus.ACTIVE)
        ).scalars()
    )

    rows: list[dict] = []
    for agent in agents:
        notices = session.execute(
            select(func.count(Notice.id)).where(Notice.agent_id == agent.id,
                                                Notice.consumed_at.is_(None))
        ).scalar_one()
        pending = len(_work_reasons(session, agent, now)) + notices
        pending += _unread_for(session, project, agent)
        if agent.kind == AgentKind.CEO:
            hours = float(cons.ceo_heartbeat_hours)
            beat = session.execute(
                select(Heartbeat).where(Heartbeat.agent_id == agent.id,
                                        Heartbeat.name == "planning")
            ).scalar_one_or_none()
            due = beat is None or beat.last_fired_at <= now - timedelta(hours=hours)
            if due and (beat is None
                        or cons.ceo_heartbeat_always
                        or _ceo_has_planning_material(session, project)):
                pending += 1
        wakes = _wakes_last_hour(session, agent)
        at_ceiling = wakes >= cons.wakes_per_agent_per_hour
        frees_at = (_wake_slot_frees_at(session, agent, cons.wakes_per_agent_per_hour)
                    if at_ceiling and pending else None)
        rows.append({
            "id": agent.id,
            "name": agent.name,
            "kind": agent.kind,
            "busy": _agent_busy(session, agent),
            "wakes_last_hour": wakes,
            "wake_cap": cons.wakes_per_agent_per_hour,
            "wake_slot_frees_at": frees_at.isoformat() if frees_at else None,
            "pending": pending,
        })

    with_work = [r for r in rows if r["pending"]]
    if used >= cons.runs_per_project_per_day:
        blocked_by = "daily_runs"
    elif used_hour >= cons.runs_per_project_per_hour:
        blocked_by = "pacing"
    elif in_flight >= max_concurrent:
        blocked_by = "concurrency"
    elif not with_work:
        blocked_by = None  # nothing waiting: idle is the correct answer
    elif all(r["busy"] for r in with_work):
        blocked_by = "agents_busy"
    elif all(r["busy"] or r["wakes_last_hour"] >= r["wake_cap"] for r in with_work):
        blocked_by = "wake_ceiling"
    else:
        blocked_by = None  # a run should start on the next tick

    ready = session.execute(
        select(func.count(Task.id)).where(Task.project_id == project.id,
                                          Task.status == TaskStatus.READY)
    ).scalar_one()
    claimed = session.execute(
        select(func.count(Task.id)).where(Task.project_id == project.id,
                                          Task.status == TaskStatus.CLAIMED)
    ).scalar_one()
    notices_total = session.execute(
        select(func.count(Notice.id)).where(Notice.project_id == project.id,
                                            Notice.consumed_at.is_(None))
    ).scalar_one()

    # When the throttle lifts: the earliest slot among the agents it is
    # actually holding back. Only meaningful when the ceiling is the gate —
    # an agent parked at its cap with nothing to do is not blocking anything.
    slots = [r["wake_slot_frees_at"] for r in with_work
             if not r["busy"] and r["wake_slot_frees_at"]]
    frees_at = min(slots) if slots else None

    return {
        "blocked_by": blocked_by,
        "daily_runs": {
            "used": used,
            "cap": cons.runs_per_project_per_day,
            "resets_at": (start + timedelta(days=1)).isoformat(),
        },
        "pacing": {
            "used": used_hour,
            "cap": cons.runs_per_project_per_hour,
            # A trailing window, so it thaws run by run rather than resetting.
            "frees_at": _pacing_frees_at(session, project, cons, now),
        },
        "wake_ceiling": {
            "cap": cons.wakes_per_agent_per_hour,
            # The trailing hour never "rolls over" on a clock, so this is the
            # only honest answer to "when does this clear?".
            "frees_at": frees_at,
            "agents_held": len([r for r in with_work
                                if not r["busy"] and r["wake_slot_frees_at"]]),
        },
        "concurrency": {"in_flight": in_flight, "cap": max_concurrent},
        "waiting": {
            "ready_tasks": ready,
            "claimed_tasks": claimed,
            "notices": notices_total,
            "agents_with_work": len(with_work),
        },
        "agents": rows,
    }


def _agent_busy(session: Session, agent: Agent) -> bool:
    queued = session.execute(
        select(func.count(RunQueue.id)).where(RunQueue.agent_id == agent.id)
    ).scalar_one()
    if queued:
        return True
    # A resident chat session is not "busy": the CEO must stay wakeable for
    # work while it holds a conversation.
    running = session.execute(
        select(func.count(Run.id)).where(Run.agent_id == agent.id,
                                         Run.kind != RunKind.CHAT_SESSION,
                                         Run.status == RunStatus.RUNNING)
    ).scalar_one()
    return bool(running)


def _wakes_last_hour(session: Session, agent: Agent) -> int:
    hour_ago = utcnow() - timedelta(hours=1)
    n = session.execute(
        select(func.count(Run.id)).where(Run.agent_id == agent.id,
                                         Run.kind != RunKind.CHAT_SESSION,
                                         Run.created_at >= hour_ago)
    ).scalar_one()
    n += session.execute(
        select(func.count(RunQueue.id)).where(RunQueue.agent_id == agent.id,
                                              RunQueue.created_at >= hour_ago)
    ).scalar_one()
    return n


def _wake_slot_frees_at(session: Session, agent: Agent, cap: int):
    """When this agent's next wake slot opens — the instant `_wakes_last_hour`
    drops below `cap`.

    The ceiling is a trailing hour, not a clock hour, so "work resumes as the
    hour rolls forward" named no moment a reader could check. A banner with no
    falsifiable time in it reads as a permanent fault, which is exactly how a
    self-clearing throttle got reported as a stuck error.

    With n wakes in the window and a cap of C, the (n - C + 1)-th oldest has to
    age out; that wake's timestamp plus an hour is the moment. Returns None when
    the agent is not actually at its ceiling.
    """
    if cap <= 0:
        return None
    hour_ago = utcnow() - timedelta(hours=1)
    stamps = [
        row for (row,) in session.execute(
            select(Run.created_at).where(Run.agent_id == agent.id,
                                         Run.kind != RunKind.CHAT_SESSION,
                                         Run.created_at >= hour_ago)
        )
    ] + [
        row for (row,) in session.execute(
            select(RunQueue.created_at).where(RunQueue.agent_id == agent.id,
                                              RunQueue.created_at >= hour_ago)
        )
    ]
    if len(stamps) < cap:
        return None
    stamps.sort()
    oldest_that_must_go = stamps[len(stamps) - cap]
    if oldest_that_must_go.tzinfo is None:
        oldest_that_must_go = oldest_that_must_go.replace(tzinfo=UTC)
    return oldest_that_must_go + timedelta(hours=1)


def _pacing_frees_at(session: Session, project: Project, cons: ConstitutionDoc, now):
    """When the next run may start under the hourly brake — the project-wide
    twin of `_wake_slot_frees_at`, and for the same reason: a trailing window
    names no clock moment on its own, and a throttle that cannot say when it
    lifts gets read as a stuck error. Returns None when the brake is not the
    thing holding work back.
    """
    cap = cons.runs_per_project_per_hour
    if cap <= 0:
        return None
    hour_ago = now - timedelta(hours=1)
    stamps = [
        row for (row,) in session.execute(
            select(Run.created_at).where(Run.project_id == project.id,
                                         Run.kind != RunKind.CHAT_SESSION,
                                         Run.created_at >= hour_ago)
        )
    ] + [
        row for (row,) in session.execute(
            select(RunQueue.created_at).where(RunQueue.project_id == project.id,
                                              RunQueue.created_at >= hour_ago)
        )
    ]
    if len(stamps) < cap:
        return None
    stamps.sort()
    oldest_that_must_go = stamps[len(stamps) - cap]
    if oldest_that_must_go.tzinfo is None:
        oldest_that_must_go = oldest_that_must_go.replace(tzinfo=UTC)
    return (oldest_that_must_go + timedelta(hours=1)).isoformat()


def _primary_task(reasons: list[dict]) -> str | None:
    for reason in reasons:
        if reason.get("task_id"):
            return reason["task_id"]
    return None


def chat_session_live(session: Session, project: Project) -> bool:
    """A resident chat session with a fresh heartbeat. While one is live, the
    boardroom is the chat lane's to answer; if it dies, message wakes resume as
    the fallback within one lease TTL."""
    cutoff = utcnow() - timedelta(seconds=settings().lease_ttl_s)
    return bool(session.execute(
        select(func.count(Run.id)).where(Run.project_id == project.id,
                                         Run.kind == RunKind.CHAT_SESSION,
                                         Run.status == RunStatus.RUNNING,
                                         Run.heartbeat_at >= cutoff)
    ).scalar_one())


def _wake_reasons(
    session: Session, project: Project, cons: ConstitutionDoc, agent: Agent
) -> list[dict]:
    reasons: list[dict] = []
    now = utcnow()

    # The chat lane answers the chairman in seconds; a work run for the same
    # message would produce a second, minutes-late reply. Boardroom messages
    # stop being a wake reason for the CEO while its chat session is live.
    skip_channels: set[str] = set()
    if agent.kind == AgentKind.CEO and chat_session_live(session, project):
        skip_channels = {
            c.id for c in session.execute(
                select(Channel).where(Channel.project_id == project.id,
                                      Channel.kind == ChannelKind.BOARDROOM)
            ).scalars()
        }

    # 1. Unread messages — with the pair circuit-breaker standing between a
    #    ping-pong thread and yet another wake, and ordinary agent-to-agent
    #    chatter coalescing like notices (see MESSAGE_COALESCE_* above). The
    #    breaker still trips on every tick, before the coalescing gate.
    channel_kinds = {
        c.id: c.kind
        for c in session.execute(
            select(Channel).where(Channel.project_id == project.id)
        ).scalars()
    }
    message_reasons: list[dict] = []
    urgent = False
    oldest_at = None
    total = 0
    for channel_id in policy.agent_channel_ids(session, agent):
        if channel_id in skip_channels:
            continue
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
            total += 1
            if (message.sender_user_id is not None
                    or channel_kinds.get(channel_id) == ChannelKind.BOARDROOM):
                urgent = True
            if oldest_at is None or message.created_at < oldest_at:
                oldest_at = message.created_at
        if actionable:
            message_reasons.append(
                {"kind": "messages", "channel_id": channel_id, "count": actionable})
    if message_reasons:
        ripe = oldest_at <= now - timedelta(minutes=MESSAGE_COALESCE_MINUTES)
        if urgent or ripe or total >= MESSAGE_COALESCE_COUNT:
            reasons.extend(message_reasons)

    # 2. Unconsumed notices, coalesced (see URGENT_NOTICE_KINDS above).
    pending = session.execute(
        select(Notice.kind, Notice.created_at).where(Notice.agent_id == agent.id,
                                                     Notice.consumed_at.is_(None))
    ).all()
    if pending:
        urgent = any(kind in URGENT_NOTICE_KINDS for kind, _ in pending)
        oldest = min(created_at for _, created_at in pending)
        ripe = oldest <= now - timedelta(minutes=NOTICE_COALESCE_MINUTES)
        if urgent or ripe or len(pending) >= NOTICE_COALESCE_COUNT:
            reasons.append({"kind": "notices", "count": len(pending)})
        # The re-forecast wake is governance: it must land on the day the
        # allowance is gone, because that is exactly the day the date slipped.
        if any(kind in COMMITMENT_NOTICE_KINDS for kind, _ in pending):
            reasons.append({"kind": "commitment"})

    # 3. Work: ready tasks this agent could claim, or a claim to push forward.
    reasons.extend(_work_reasons(session, agent, now))

    # 4. Heartbeats: the CEO plans on a cadence even in silence — but silence
    #    with an empty board and no live bet leaves nothing to plan, and an
    #    idle wake is a full-price run. The first-ever heartbeat always fires
    #    (it bootstraps the project); after that the cadence still advances,
    #    so a busy company keeps its rhythm and an idle one sleeps until
    #    something happens (config ceo_heartbeat_always restores always-wake).
    if agent.kind == AgentKind.CEO:
        hours = float(cons.ceo_heartbeat_hours)
        beat = session.execute(
            select(Heartbeat).where(Heartbeat.agent_id == agent.id,
                                    Heartbeat.name == "planning")
        ).scalar_one_or_none()
        due = beat is None or beat.last_fired_at <= now - timedelta(hours=hours)
        if due:
            skip = (beat is not None
                    and not reasons
                    and not cons.ceo_heartbeat_always
                    and not _ceo_has_planning_material(session, project))
            if beat is None:
                session.add(Heartbeat(agent_id=agent.id, name="planning", last_fired_at=now))
            else:
                beat.last_fired_at = now
            if not skip:
                reasons.append({"kind": "heartbeat", "name": "planning"})

        # 4b. Daily wake at a wall-clock time (config: ceo_daily_at "HH:MM" +
        #     timezone). Fires once per local day, at or after the target;
        #     lana's standup pattern, generalized.
        daily = _daily_wake_due(session, project, cons, agent, now)
        if daily:
            reasons.append(daily)

        # 4c. An open review missing its variance narrative. Governance-
        #     classed, so it can spend the reserved slice — the narrative is
        #     the run the reserve exists for, and the day the allowance is
        #     gone by lunch is exactly the day the meeting matters.
        from yaaf.core.models import Review, ReviewStatus

        unfiled = session.execute(
            select(Review.id).where(
                Review.project_id == project.id,
                Review.status == ReviewStatus.OPEN,
                Review.narrative.is_(None),
            )
        ).scalar_one_or_none()
        if unfiled is not None:
            reasons.append({"kind": "review", "review_id": unfiled})

        # 4d. An Operate cycle waiting on its plan. The CEO's first act in a
        #     new cycle is to read the narratives and plan; governance-classed
        #     for the same reason the review is — a rhythm that rolled on a
        #     spent day still deserves its planning run.
        from yaaf.core.models import ProjectMode, RequirementStatus

        if project.mode == ProjectMode.OPERATE:
            plan_row = policy.plan_requirement(session, project.id)
            if plan_row is not None and plan_row.status != RequirementStatus.MET:
                reasons.append({"kind": "cycle_plan"})

    return reasons


def _ceo_has_planning_material(session: Session, project: Project) -> bool:
    """Anything the planning heartbeat could act on: an open top-level task or
    a live (or expired-awaiting-decision) bet. With neither — and no other wake
    reason — a heartbeat wake buys a full-price run to conclude "nothing to
    do"."""
    if policy.open_top_level_tasks(session, project.id):
        return True
    return session.execute(
        select(func.count(Hypothesis.id)).where(
            Hypothesis.project_id == project.id,
            Hypothesis.status.in_([HypothesisStatus.ACTIVE, HypothesisStatus.EXPIRED]),
        )
    ).scalar_one() > 0


def _daily_wake_due(session: Session, project: Project, cons: ConstitutionDoc,
                    agent: Agent, now) -> dict | None:
    raw = cons.ceo_daily_at
    if not raw:
        return None
    try:
        from zoneinfo import ZoneInfo

        hour, minute = (int(part) for part in str(raw).split(":", 1))
        tz = ZoneInfo(cons.timezone)
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
            "note": f"daily {raw} {cons.timezone} wake"}


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
