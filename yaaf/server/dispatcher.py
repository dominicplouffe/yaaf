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
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
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
    Requirement,
    RequirementStatus,
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
from yaaf.server.rows import chairman_time

log = logging.getLogger("yaaf.dispatcher")

# Wake reasons that may spend the constitution's reserved governance runs.
# These are the wakes whose job is to *report on* the work — a status review, a
# new cycle's plan, a commitment that needs re-forecasting, a question to the
# chairman that ran out its window — so they must still be possible on a day
# the project has spent its ordinary allowance.
GOVERNANCE_REASON_KINDS = {"review", "cycle_plan", "commitment", "ask_lapsed"}

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
                       NoticeKind.MEMORY_FULL, NoticeKind.EXTERNAL_EVENT,
                       # A stall notice that waited out a batching window would
                       # be extending the exact silence it exists to report.
                       NoticeKind.STALLED,
                       NoticeKind.COMMITMENT_AT_RISK, NoticeKind.COMMITMENT_BREACHED,
                       # A flag that waits out a batching window is a warning
                       # delivered after the deadline it was warning about.
                       NoticeKind.FLAG_OPENED,
                       # A lapsed ask is the strongest case of all: the project
                       # emptied its own board to wait for this answer, so there
                       # is no next wake to batch into. This notice IS the wake
                       # that makes the CEO's stated default happen.
                       NoticeKind.ASK_LAPSED}

# What still buys a run on a project the chairman has been told is finished.
# Everything else waits for his click: a CEO reading an amendment note or a
# routine memo on a delivered project is a run spent on the exact thing the
# banner, the email and the nag all say is not happening. DELIVERY_READY is in
# here because it IS the news — the CEO has to learn it finished, and saying so
# in the boardroom is what ends the silence the incident was made of.
DELIVERED_WAKE_NOTICE_KINDS = URGENT_NOTICE_KINDS | {NoticeKind.DELIVERY_READY}

# A commitment that stopped holding needs a re-forecast wake even on a day
# ordinary work already spent the allowance — that wake is one of the three
# things the reserved governance slice exists for.
COMMITMENT_NOTICE_KINDS = {NoticeKind.COMMITMENT_AT_RISK, NoticeKind.COMMITMENT_BREACHED,
                           # Telling the chairman the date will not hold IS
                           # governance, and the day the allowance is gone is
                           # exactly the day he most needs to hear it.
                           NoticeKind.FLAG_OPENED}


def dispatcher_loop() -> None:  # pragma: no cover - thin loop around dispatch_all
    while True:
        dispatch_all()
        time.sleep(settings().dispatch_poll_s)


def dispatch_all() -> int:
    """One tick, with each project in its own transaction.

    The whole tick used to share one, so an exception anywhere — one project
    with a broken row — rolled back and abandoned the tick for *every* project
    on the box. The only evidence was a line on stderr, and a platform-wide
    stall reads to a chairman exactly like his own project going quiet. A
    project that raises now loses its own tick and nothing else.
    """
    with db_session() as session:
        project_ids = [
            row for (row,) in session.execute(
                select(Project.id).where(Project.status == ProjectStatus.RUNNING)
            )
        ]
    enqueued = 0
    for project_id in project_ids:
        try:
            with db_session() as session:
                project = session.get(Project, project_id)
                if project is None or project.status != ProjectStatus.RUNNING:
                    continue  # paused between the listing and now
                enqueued += _dispatch_project(
                    session, project, effective(session, project_id))
        except LookupError:
            # No constitution row. Both loops skip such a project entirely, so
            # it is stopped in the most complete way we have; project_health
            # raises it as a hard failure rather than leaving it silent.
            log.warning("project %s has no constitution; skipping dispatch", project_id)
        except Exception:
            log.exception("dispatch failed for project %s", project_id)
    return enqueued


def dispatch_once(session: Session) -> int:
    """One tick over all running projects inside the caller's transaction.

    The test harness's entry point, and the honest single-transaction reading
    of a tick. Production uses `dispatch_all`, which gives each project its own.
    """
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


# Kinds that do not spend the project's run allowance. Chat is the talk lane,
# not work. The two check kinds are the PLATFORM verifying a claim: a command
# in a container, no model, no token, no dollar — the thing these ceilings
# exist to bound is agent spend, and a check is none of it.
#
# Counting checks here was not a rounding error. A requirement whose acceptance
# test kept failing re-queued one every fifteen seconds, and the trailing hour
# reached 237 checks against a ceiling of 15: the dispatcher returned 0 for
# eighty minutes straight. The only actor who could have repaired the
# requirement was the CEO, and the failure had eaten the runs it needed to wake.
# A verification storm must never be able to starve the project of the work it
# takes to end one.
UNMETERED_RUN_KINDS = (
    RunKind.CHAT_SESSION,
    RunKind.SUCCESS_CHECK,
    RunKind.REQUIREMENT_CHECK,
)

# Kinds that spend the INBOX allowance instead of the work allowance. Same
# logging, same ledger, same visibility — a different purse. See
# RunKind.INBOX_RUN for why reading had to stop being billed as work.
INBOX_RUN_KINDS = (RunKind.INBOX_RUN,)

#: Neither work nor inbox: everything the work allowance must ignore.
NON_WORK_RUN_KINDS = (*UNMETERED_RUN_KINDS, *INBOX_RUN_KINDS)

#: Queued kinds where an AGENT thinks, as opposed to the platform running a
#: check in a container. Both are a model call, both authenticate with the
#: project's billing credential, both are the agent acting. Anything asking
#: "was this an agent or the platform?" must consult this rather than test
#: against AGENT_RUN alone — the inbox lane was invisible to two such places
#: the day it was added, and one of them silently stopped running agents.
MODEL_RUN_KINDS = (RunKind.AGENT_RUN, RunKind.INBOX_RUN)


def run_kind_for_reasons(reasons: list[dict]) -> str:
    """Work, or just reading?

    An inbox run is a wake with nothing behind it but ordinary unread
    messages — no task, no notice, no heartbeat, no cycle plan, and nothing
    from the chairman or the boardroom (those set `urgent` on the reason and
    are work by definition: an unanswered owner is not correspondence).

    Deciding this at wake time rather than after the fact is what lets the
    dispatcher gate the two lanes separately, which is the entire point: the
    project had 4 runs an hour and spent all four on agents acknowledging a
    message, while the two agents holding ready tasks waited an hour.
    """
    if not reasons:
        return RunKind.AGENT_RUN
    for reason in reasons:
        if reason.get("kind") != "messages":
            return RunKind.AGENT_RUN
        if reason.get("urgent"):
            return RunKind.AGENT_RUN
    return RunKind.INBOX_RUN


def heartbeat_only(trigger) -> bool:
    """True when every wake reason is a heartbeat or a daily cadence — the
    model woke on a timer, not because anything happened.

    Sits beside `run_kind_for_reasons` because it answers the sibling question
    about the same object: that one separates working from reading, this one
    separates both from waking up to look around. It lived in the spend CLI,
    which is why the only surface that could tell a chairman what his planning
    wakes cost was one he had to be at a terminal to read.
    """
    if not isinstance(trigger, dict):
        return False
    reasons = trigger.get("reasons")
    if not isinstance(reasons, list) or not reasons:
        return False
    return all(isinstance(reason, dict) and reason.get("kind") in ("heartbeat", "daily")
               for reason in reasons)


def daily_runs_used(session: Session, project: Project, since) -> int:
    """Runs created today, queued or historical, that spend the WORK allowance.

    Two exclusions beyond the unmetered kinds. Inbox runs spend their own
    ceiling, so reading cannot crowd out working. And a run that finished
    having changed nothing is refunded (`metered=False`) — the project was not
    slowed by it, so the allowance should not act as though it was. Queued rows
    always count: a wake that has not run yet cannot be known to be empty, and
    the pessimistic read is the safe one.
    """
    used = session.execute(
        select(func.count(Run.id)).where(Run.project_id == project.id,
                                         Run.kind.not_in(NON_WORK_RUN_KINDS),
                                         Run.metered.is_(True),
                                         Run.created_at >= since)
    ).scalar_one()
    used += session.execute(
        select(func.count(RunQueue.id)).where(RunQueue.project_id == project.id,
                                              RunQueue.kind.not_in(NON_WORK_RUN_KINDS),
                                              RunQueue.created_at >= since)
    ).scalar_one()
    return used


def hourly_runs_used(session: Session, project: Project, now) -> int:
    """Runs in the TRAILING hour. The daily ceiling is a fixed window that
    refills in a lump, which is how a project spends its whole day before
    lunch; this is the same count over a window that clears continuously."""
    return daily_runs_used(session, project, now - timedelta(hours=1))


def inbox_runs_used(session: Session, project: Project, since) -> int:
    """The reading lane's own count, on the same shape as the work lane.

    No `metered` filter: an inbox run that produced nothing is the ordinary
    case — reading and finding nothing worth acting on is the job — and
    refunding it would leave the lane with no ceiling at all.
    """
    used = session.execute(
        select(func.count(Run.id)).where(Run.project_id == project.id,
                                         Run.kind.in_(INBOX_RUN_KINDS),
                                         Run.created_at >= since)
    ).scalar_one()
    used += session.execute(
        select(func.count(RunQueue.id)).where(RunQueue.project_id == project.id,
                                              RunQueue.kind.in_(INBOX_RUN_KINDS),
                                              RunQueue.created_at >= since)
    ).scalar_one()
    return used


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
    audit's sharpest failure was a project that spent its whole allowance
    before lunch: on such a day ordinary work stopping is the brake working,
    but the status review stopping is the brake eating the one run whose entire
    job is to tell the chairman what happened.
    """
    return any(r.get("kind") in GOVERNANCE_REASON_KINDS for r in reasons)


def _dispatch_project(session: Session, project: Project, cons: ConstitutionDoc) -> int:
    now = utcnow()
    start = day_start(now)

    today = daily_runs_used(session, project, start)
    # Past this line ordinary work is done for the day and only governance may
    # still spend. The reservation is against the *daily* ceiling alone: the
    # hourly brake below clears continuously, so it can only ever delay a
    # review by an hour, never cost the day one.
    #
    # Never more than half the day, whatever the rule says. A reservation of 3
    # against a ceiling of 1 would make every run a governance run and the
    # project would report on work it was never allowed to do — the brake
    # eating the thing it exists to protect.
    # The plan and the wall are two numbers now, as they already are for money.
    # The daily cap is what the project should need; the wall is cap x
    # runs_hard_stop_multiple. Between them work continues and the project owes
    # an account (flags.RUN_OVERRUN). At the wall, ordinary work stops.
    #
    # `max_concurrent_runs` is deliberately untouched: that is the dial whose
    # help text says it protects the provider rate limit every project on the
    # box shares, and it caps simultaneity rather than volume — so it goes on
    # binding absolutely no matter how far past its daily plan a project runs.
    wall = max(cons.runs_per_project_per_day,
               int(cons.runs_per_project_per_day * cons.runs_hard_stop_multiple))
    reserved = min(cons.reserved_governance_runs, wall // 2)
    governance_only = today >= wall - reserved

    # Pacing. Without this the daily ceiling is the only brake, and it is a
    # fixed window with no rate limit: six agents at six wakes an hour is
    # thirty-six an hour, so a hundred-run day is gone in under three hours and
    # the project sleeps until midnight with tasks still on the board.
    #
    # Both walls are now computed rather than returned on, because they bound
    # the WORK lane only. A project out of work runs may still read its mail,
    # and — the case this was written for — a project whose hourly ceiling went
    # to four agents acknowledging a broadcast may still run the two agents
    # holding ready tasks.
    work_blocked = (today >= wall
                    or hourly_runs_used(session, project, now)
                    >= cons.runs_per_project_per_hour)

    inbox_today = inbox_runs_used(session, project, start)
    inbox_hour = inbox_runs_used(session, project, now - timedelta(hours=1))
    inbox_blocked = (inbox_today >= cons.inbox_runs_per_project_per_day
                     or inbox_hour >= cons.inbox_runs_per_project_per_hour)

    if work_blocked and inbox_blocked:
        return 0

    # Asked once per tick, not once per agent: it is several queries and
    # `_wake_reasons` runs for every agent, thirty times a minute.
    delivered = policy.delivered_awaiting_confirmation(session, project)

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
        reasons = _wake_reasons(session, project, cons, agent, delivered=delivered)
        if not reasons:
            continue
        # Which purse this wake spends, decided before it is queued so the two
        # lanes can be gated independently. Reading the mail must not be
        # stopped by an exhausted work allowance, and must not consume one.
        kind = run_kind_for_reasons(reasons)
        if kind == RunKind.INBOX_RUN:
            # Counted as we go: one tick may enqueue several agents, and a
            # broadcast makes every agent unread in the same instant — exactly
            # the case the hourly ceiling exists for.
            if inbox_blocked or inbox_hour >= cons.inbox_runs_per_project_per_hour:
                continue
            inbox_hour += 1
            inbox_today += 1
        else:
            if work_blocked:
                continue
            if governance_only and not _is_governance(reasons):
                continue
        task_id = _primary_task(reasons)
        session.add(
            RunQueue(
                id=ids.new_id(ids.RUN),
                project_id=project.id,
                agent_id=agent.id,
                kind=kind,
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


def _utc(value):
    """Timestamps read back from SQLite come home naive. Comparing one of those
    against an aware `now` raises, and a raise inside a read path is a blank
    page where a reason should be."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def unread_messages_for(
    session: Session, project: Project, agent: Agent
) -> list[tuple[Message, Channel | None]]:
    """The messages this agent would wake to read, each with its channel.

    Mirrors the filter in _wake_reasons (not self-authored, not a sender-less
    platform line, not under a frozen root, boardroom skipped for a CEO whose
    chat session is live) minus the pair-breaker, which is a trip detector
    rather than a filter and cannot run from a read path. The project inbox
    lists exactly these, and the banner counts exactly these, so the two cannot
    name different numbers.
    """
    skip: set[str] = set()
    if agent.kind == AgentKind.CEO and chat_session_live(session, project):
        skip = {
            c.id for c in session.execute(
                select(Channel).where(Channel.project_id == project.id,
                                      Channel.kind == ChannelKind.BOARDROOM)
            ).scalars()
        }
    unread: list[tuple[Message, Channel | None]] = []
    for channel_id in policy.agent_channel_ids(session, agent):
        if channel_id in skip:
            continue
        channel = session.get(Channel, channel_id)
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
            # A platform boardroom line is addressed to the chairman and never
            # wakes anyone. Counting it here put "1 unread" on a CEO the
            # dispatcher would never wake for it — a number with no wake behind it.
            if message.sender_agent_id is None and message.sender_user_id is None:
                continue
            root = session.get(Message, message.thread_id or message.id)
            if root is not None and root.frozen_at is not None:
                continue
            unread.append((message, channel))
    return unread


def _unread_detail_for(
    session: Session, project: Project, agent: Agent
) -> tuple[int, object | None, bool]:
    """How many messages this agent would wake for, plus the two facts the
    coalescing window turns on: when the oldest of them arrived, and whether
    any is urgent enough to skip the window entirely."""
    unread = unread_messages_for(session, project, agent)
    oldest = None
    urgent = False
    for message, channel in unread:
        # Same urgency rule as _wake_reasons: the chairman never waits out
        # a batching window, and neither does anything said in his room.
        if (message.sender_user_id is not None
                or (channel is not None and channel.kind == ChannelKind.BOARDROOM)):
            urgent = True
        at = _utc(message.created_at)
        if oldest is None or at < oldest:
            oldest = at
    return len(unread), oldest, urgent


def _unread_for(session: Session, project: Project, agent: Agent) -> int:
    return _unread_detail_for(session, project, agent)[0]


def _notices_detail_for(session: Session, agent: Agent) -> tuple[int, object | None, bool]:
    """Undelivered notices for this agent, shaped like `_unread_detail_for` so
    the same coalescing arithmetic reads both."""
    rows = session.execute(
        select(Notice.kind, Notice.created_at).where(Notice.agent_id == agent.id,
                                                     Notice.consumed_at.is_(None))
    ).all()
    if not rows:
        return 0, None, False
    return (
        len(rows),
        min(_utc(created_at) for _, created_at in rows),
        any(kind in URGENT_NOTICE_KINDS for kind, _ in rows),
    )


def _coalesced_until(count: int, oldest, urgent: bool, now, minutes: float, threshold: int):
    """When a batched wake stops being held, or None if nothing is being held.

    The dispatcher deliberately refuses to buy a full-price run for one or two
    notices, so it waits for a third or for the oldest to age out. That is
    correct and it is also, on the clock, the single most common reason a
    healthy project looks stopped — the wait is minutes long and named nowhere.
    This is the same arithmetic as `_wake_reasons`, read-only, so a screen can
    say which minute it ends.
    """
    if not count or urgent or count >= threshold or oldest is None:
        return None
    ripe = oldest + timedelta(minutes=minutes)
    return ripe if ripe > now else None


def _board_reachability(session: Session, project: Project, cons: ConstitutionDoc,
                        now) -> dict:
    """The board as the dispatcher can actually reach it.

    An empty board and a board nobody may touch both produce zero wakes, and
    until now both rendered as "an idle project costs nothing". These are the
    ways work can exist and still be unreachable, each with the fact a reader
    needs to act on it.
    """
    ready = list(session.execute(
        select(Task).where(Task.project_id == project.id,
                           Task.status == TaskStatus.READY)
    ).scalars())

    claimable, gates = [], []
    for task in ready:
        gate = _utc(task.not_before)
        if gate is not None and gate > now:
            gates.append(gate)
        else:
            claimable.append(task)

    # A team task is claimed by its lead. A team of two or more with no lead
    # set is a team whose queue nobody may touch — silently, forever.
    leaderless: list[str] = []
    for team_id in {t.team_id for t in claimable if t.parent_task_id is None
                    and t.assignee_agent_id is None}:
        team = session.get(Team, team_id)
        if team is not None and policy.effective_lead(session, team) is None:
            leaderless.append(team.name or team_id)

    # Work owned by an agent that will never be woken for it. The dispatcher
    # skips those agents entirely, so their queue leaves no trace in any
    # counter.
    #
    # Split by WHY the seat stopped, because the two have opposite fixes. A
    # paused seat is the chairman reaching in, and the answer is to resume it.
    # A stood-down seat has finished, and telling anybody to resume it is
    # advice to undo a decision that was correct — reassigning the work is the
    # only sane move. A stand-down is refused while a team holds work, so this
    # arm should be unreachable through that door; it is here because the
    # platform must still describe a state it can reach another way (work
    # assigned to a seat after it stopped, an import, a clone).
    parked: list[str] = []
    finished: list[str] = []
    for agent in session.execute(
        select(Agent).where(Agent.project_id == project.id,
                           Agent.status != AgentStatus.ACTIVE)
    ).scalars():
        holds = session.execute(
            select(func.count(Task.id)).where(
                Task.project_id == project.id,
                or_(and_(Task.assignee_agent_id == agent.id,
                         Task.status == TaskStatus.READY),
                    and_(Task.claimed_by_agent_id == agent.id,
                         Task.status == TaskStatus.CLAIMED)),
            )
        ).scalar_one()
        if holds:
            (finished if agent.status == AgentStatus.STOOD_DOWN
             else parked).append(agent.name)

    blocked = session.execute(
        select(func.count(Task.id)).where(Task.project_id == project.id,
                                          Task.status == TaskStatus.BLOCKED)
    ).scalar_one()

    # Scope waiting on the chairman, and scope that quietly stopped moving.
    proposed, observable, stalled, rework = [], [], [], []
    for req in session.execute(
        select(Requirement).where(Requirement.project_id == project.id)
    ).scalars():
        if req.status == RequirementStatus.PROPOSED:
            proposed.append(req.key)
        elif req.status == RequirementStatus.CHECKING and (
                (req.acceptance_test or {}).get("type") == "observable"):
            observable.append(req.key)
        elif req.status == RequirementStatus.IN_PROGRESS:
            counts = {
                status: session.execute(
                    select(func.count(Task.id)).where(Task.requirement_id == req.id,
                                                      Task.status == status)
                ).scalar_one()
                for status in (*policy.OPEN_TASK_STATUSES, TaskStatus.DONE)
            }
            total = session.execute(
                select(func.count(Task.id)).where(Task.requirement_id == req.id)
            ).scalar_one()
            open_now = sum(counts[s] for s in policy.OPEN_TASK_STATUSES)
            # Every task it ever had is closed and none of them succeeded: the
            # janitor's completion check returns early here, so it never
            # becomes MET and never becomes anyone's problem.
            if total and not open_now and not counts[TaskStatus.DONE]:
                stalled.append(req.key)
            # The chairman sent it back and nobody picked the rework up. Its
            # board is empty and it will not return to him on its own — that is
            # deliberate, since a deliverable nobody has touched cannot get a
            # different answer — so this is the one quiet in the ladder that
            # needs the CEO rather than a clock or the chairman.
            elif not open_now and policy.rejected_by_chairman(req):
                rework.append(req.key)

    # Work that is with the platform's own checker. A CHECKING row wakes
    # nobody and is claimed by nobody, so it counted as nothing at all — and a
    # board holding only CHECKING rows read "Nothing waiting. The board is
    # empty" while a stage gate held every next step behind it. `checks_lost`
    # names the wedged subset: checks whose run no longer exists anywhere,
    # which the janitor requeues (and escalates to the CEO if they keep dying).
    checking_tasks = session.execute(
        select(func.count(Task.id)).where(Task.project_id == project.id,
                                          Task.status == TaskStatus.CHECKING)
    ).scalar_one()
    checking_requirements = max(0, session.execute(
        select(func.count(Requirement.id)).where(
            Requirement.project_id == project.id,
            Requirement.status == RequirementStatus.CHECKING)
    ).scalar_one() - len(observable))
    # Finished work waiting on a second pair of eyes. Same shape as CHECKING —
    # it wakes nobody in the ordinary lane and is claimed by nobody — with one
    # difference that matters: no platform process moves it. It waits on an
    # agent, so it is a real rung rather than a reassurance, and without it a
    # board holding nothing but reads renders as "Nothing waiting. The board is
    # empty", which is the exact sentence CHECKING was added here to stop.
    reviewing = session.execute(
        select(func.count(Task.id)).where(Task.project_id == project.id,
                                          Task.status == TaskStatus.REVIEW)
    ).scalar_one()

    lost_tasks, lost_requirements = policy.lost_checks(session, project.id, now)
    checks_lost = sorted([t.title for t in lost_tasks]
                         + [r.key for r in lost_requirements])

    plan = policy.plan_requirement(session, project.id)
    plan_open = plan is not None and plan.status not in policy.CLOSED_REQUIREMENT_STATUSES

    # Whose decision, actually. Each of these three waits on whoever the
    # authority dial says holds the verb — and on a project that delegated
    # them, calling it "awaiting_chairman" told a chairman he was the
    # bottleneck on a project he had deliberately handed over. R-00 closes by
    # arithmetic rather than by a click, but what it waits FOR is scope being
    # accepted, so it follows the same switch.
    ceo_scope = policy.ceo_holds(cons, "plan_authority")
    ceo_verdicts = policy.ceo_holds(cons, "verdict_policy")
    chairman_waits = bool(
        ((proposed or plan_open) and not ceo_scope) or (observable and not ceo_verdicts)
    )

    # Order matters: things that never clear on their own come before things
    # that do, and "you have to decide" comes before "a clock has to pass".
    if leaderless:
        unreachable = "no_lead"
    elif parked:
        unreachable = "paused_agents"
    elif finished:
        unreachable = "stood_down_agents"
    elif stalled:
        unreachable = "stalled_requirement"
    elif rework:
        unreachable = "rework_owed"
    elif chairman_waits:
        unreachable = "awaiting_chairman"
    elif proposed or observable or plan_open:
        unreachable = "awaiting_ceo"
    elif gates:
        unreachable = "scheduled"
    elif blocked:
        unreachable = "blocked_tasks"
    elif checks_lost:
        unreachable = "checks_lost"
    elif reviewing:
        unreachable = "review_owed"
    elif checking_tasks or checking_requirements:
        unreachable = "checking"
    else:
        unreachable = None

    return {
        "claimable": len(claimable),
        "gated": len(gates),
        "next_gate_at": min(gates).isoformat() if gates else None,
        "blocked": blocked,
        "checking": checking_tasks + checking_requirements,
        "reviewing": reviewing,
        "checks_lost": checks_lost,
        "leaderless_teams": sorted(leaderless),
        "paused_agents": sorted(parked),
        "stood_down_agents": sorted(finished),
        "stalled_requirements": sorted(stalled),
        "rework_owed": sorted(rework),
        "proposed_requirements": sorted(proposed),
        "observable_checking": sorted(observable),
        "plan_open": plan_open,
        # Which of the three the dial has handed to the CEO, so the sentence
        # can name the right party instead of guessing.
        "ceo_decides": sorted(
            (["scope"] if ceo_scope and (proposed or plan_open) else [])
            + (["verdicts"] if ceo_verdicts and observable else [])
        ),
        "unreachable": unreachable,
    }


def _hhmm(iso: str | None, tz: str | None = None) -> str:
    """An ISO instant as the only part of it a reader acts on. Absolute rather
    than relative on purpose: "in 4 minutes" is wrong the moment the response
    is cached, and a time you can check against a clock is falsifiable. In the
    project's own zone when it has one, so the reader does no arithmetic."""
    if not iso:
        return "shortly"
    if tz and tz != "UTC":
        return chairman_time(datetime.fromisoformat(iso), tz)
    return f"{iso[11:16]} UTC"


def _midnight(state: dict) -> str:
    """When the daily counts reset: midnight UTC for every project, which is
    the reader's evening or morning, so it is said in their zone as well."""
    tz = state.get("timezone")
    resets = (state.get("daily_runs") or {}).get("resets_at")
    if tz and tz != "UTC" and resets:
        return f"00:00 UTC ({chairman_time(datetime.fromisoformat(resets), tz)})"
    return "00:00 UTC"


def _listed(names: list[str]) -> str:
    if not names:
        return "something"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _activity_brief(state: dict) -> dict:
    """The one sentence, written once, next to the ladder that decided it.

    This lives here rather than in the browser because it kept going stale
    there: `pacing` was added to the ladder, and the three hand-written
    switches in the UI carried on saying "a run should start shortly" for a
    gate that stops work every hour. A new gate now cannot ship without a
    sentence — `tests/selftest_invariants` fails the build if one is missing.

    `banner` marks the briefs that deserve the box above the tabs. Concurrency
    and busy agents mean the project is *working*; a banner that fires when
    things are fine is how a reader learns to stop reading it. `sig` and
    `clears_at` are set only on the briefs that are waiting out a clock, which
    is what makes them dismissible.
    """
    gate = state["blocked_by"]
    waiting = state["waiting"]
    board = state["board"]
    tz = state.get("timezone")
    behind = []
    if waiting["claimable_tasks"]:
        behind.append(f"{waiting['claimable_tasks']} task(s) ready to claim")
    if waiting["gated_tasks"]:
        behind.append(f"{waiting['gated_tasks']} task(s) scheduled for later")
    if waiting["notices"]:
        behind.append(f"{waiting['notices']} notice(s) undelivered")
    if waiting["unread"]:
        behind.append(f"{waiting['unread']} message(s) unread")
    queue = f" {_listed(behind)} waiting behind it." if behind else ""

    def brief(tone, chip, short, headline, detail, *, banner=True, sig=None, clears_at=None):
        return {"tone": tone, "chip": chip, "short": short, "headline": headline,
                "detail": detail, "banner": banner, "sig": sig, "clears_at": clears_at}

    # A banner is for something the chairman has to do, or has to know about
    # because it will not fix itself. Pacing, the wake ceiling, a batching
    # window and a start time the CEO set are none of those: each clears on
    # its own within the hour or at a moment already chosen, and each said so
    # in its own words — "nothing here needs you". Raising a box for them put
    # one over a project doing exactly what it was told, most of the day, and
    # a box that is always up is a box nobody reads. They keep their sentence
    # and their clearing time, which the Overview prints as a quiet line.
    def quiet_wait(chip, short, headline, detail, *, sig, clears_at):
        return brief("ok", chip, short, headline, detail, banner=False,
                     sig=sig, clears_at=clears_at)

    if gate == "delivery_ready":
        return brief(
            "ok", "delivered",
            "everything is true; waiting on your confirmation",
            "Everything is true. This delivery is waiting on your confirmation.",
            "Nothing will run until you complete it — the project has stopped waking "
            "for its own agents' messages, so it is not spending while it waits. "
            "Confirm it on the project page to write the receipt and close it, or "
            "reopen scope if something is still missing.")

    if gate == "daily_runs":
        d = state["daily_runs"]
        # Falls back to the plan: a state assembled without the wall (an older
        # cached shape, a synthetic one) should read as it always did rather
        # than raise inside the sentence that explains why nothing is running.
        wall = state["governance_reserve"].get("wall") or d["cap"]
        # The wall, not the plan, because the wall is where work actually stops.
        # Reporting the plan here printed "out of runs for today (225 of 150)".
        over = (f" That is past the daily plan of {d['cap']}, which is allowed — "
                f"the CEO owes an account of what the extra runs bought."
                if d["used"] > d["cap"] else "")
        return brief(
            "serious", "capped",
            f"out of runs for today ({d['used']} of {wall}), resumes {_midnight(state)}",
            f"Out of runs for today — {d['used']} of {wall} used.",
            f"Nothing starts until the count resets at {_midnight(state)}.{queue} Runs that "
            f"died still spent the allowance.{over} Raise 'Runs per day, whole "
            "project' in Settings if this is tighter than you meant it to be.",
            sig="daily_runs", clears_at=d["resets_at"])

    if gate == "pacing":
        p = state["pacing"]
        return quiet_wait(
            "pacing",
            f"pacing itself ({p['used']} of {p['cap']} this hour), next run "
            f"{_hhmm(p['frees_at'], tz)}",
            f"Pacing itself — {p['used']} of {p['cap']} runs used in the last hour.",
            f"The next run may start at {_hhmm(p['frees_at'], tz)}.{queue} This is the "
            "brake that stops a whole day's runs going before lunch; it clears on "
            "its own and usually needs nothing from you.",
            sig="pacing", clears_at=p["frees_at"])

    if gate == "inbox_runs":
        i = state["inbox_runs"]
        n = waiting["unread"]
        unread = f"{n} unread message{'' if n == 1 else 's'}"
        are = "is" if n == 1 else "are"
        # A ceiling of zero is not a spent allowance — it is reading switched off,
        # which the quieter tempo cards do on purpose. Calling it "spent" and
        # promising midnight sent chairmen looking for a fault, and midnight
        # changes nothing: zero refills to zero.
        #
        # Nor is it a banner. Measured across five such projects, unread mail was
        # the only thing waiting most of the time (95% on one), 62% of it status
        # updates, with no task stuck behind it and one message-only wake in a
        # fortnight — the setting doing what it says. A box that is up whenever
        # the project rests is a box the chairman learns to stop reading. The
        # long waits it seemed to warn about were seats that never run, which
        # the health check now names on its own.
        if i["cap_today"] == 0 or i["cap_this_hour"] == 0:
            return brief(
                "ok", "inbox",
                f"quiet; {unread} wait for the next work wake",
                f"Quiet. {unread.capitalize()} {are} waiting for the next work wake.",
                "This project doesn't wake agents just to read messages, so they read "
                "them the next time they wake up for real work, like a task or the "
                "CEO's scheduled check-in.",
                banner=False, sig=None)
        daily = i["used_today"] >= i["cap_today"]
        used = (f"today's {i['cap_today']} inbox reads are used up" if daily
                else f"this hour's {i['cap_this_hour']} inbox reads are used up")
        return brief(
            "serious", "inbox",
            f"{unread} waiting, {used}",
            f"{unread.capitalize()} {are} waiting, and {used}.",
            (f"Inbox reads reset at {_midnight(state)}." if daily
             else "More inbox reads free up within the hour.")
            + " Until then, agents read messages only when they wake up for real "
            "work. Reading has its own limit so chatter can't use up the runs "
            "meant for work. If this project needs to talk more, allow more inbox "
            "reads per day.",
            sig=None)

    if gate == "governance_reserve":
        g = state["governance_reserve"]
        d = state["daily_runs"]
        return brief(
            "serious", "reserved",
            f"ordinary work is done for today ({d['used']} of {g['ordinary_cap']})",
            f"Ordinary work has spent its day — {d['used']} of {g['ordinary_cap']}.",
            f"The last {g['reserved']} run(s) of the day are reserved so a status "
            f"review or a re-forecast always has somewhere to run.{queue} Everything "
            f"else resumes at {_midnight(state)}. Raise 'Runs per day, whole project', or lower "
            "'Reserved governance runs', in Settings.",
            sig="governance_reserve", clears_at=d["resets_at"])

    if gate == "wake_ceiling":
        w = state["wake_ceiling"]
        return quiet_wait(
            "throttled",
            f"every agent with work has hit its hourly wake ceiling, clearing "
            f"{_hhmm(w['frees_at'], tz)}",
            f"Every agent with work has hit its hourly wake ceiling ({w['cap']}/hour).",
            f"The oldest wake ages out at {_hhmm(w['frees_at'], tz)} and work starts again "
            f"on its own — nothing here needs you.{queue} Raise 'Wakes per agent per "
            "hour' in Settings only if the cap is tighter than you meant it to be.",
            sig="wake_ceiling", clears_at=w["frees_at"])

    if gate == "coalescing":
        c = state["coalescing"]
        return quiet_wait(
            "batching",
            f"batching a wake until it is worth a run, due {_hhmm(c['ripe_at'], tz)}",
            f"Holding a wake until it is worth the run — due {_hhmm(c['ripe_at'], tz)}.",
            "Waking an agent costs a full-price run, so one or two notices wait for a "
            f"third or for the oldest to age out.{queue} Nothing is wrong and nothing "
            "here needs you; anything you say yourself skips the wait entirely.",
            sig="coalescing", clears_at=c["ripe_at"])

    if gate == "scheduled":
        return quiet_wait(
            "scheduled",
            f"the ready work is gated until {_hhmm(board['next_gate_at'], tz)}",
            f"The only work on the board is scheduled for {_hhmm(board['next_gate_at'], tz)}.",
            f"{board['gated']} task(s) carry a start time the CEO set, so nobody may "
            "claim them yet and nobody is woken for them. Work resumes on its own at "
            "that moment. If that is later than you meant, open the task and clear its "
            "start time.",
            sig="scheduled", clears_at=board["next_gate_at"])

    if gate == "no_lead":
        teams = _listed(board["leaderless_teams"])
        return brief(
            "critical", "no lead",
            f"{teams} has no lead, so its tasks can never be claimed",
            f"{teams} has ready work and no lead.",
            "A team task is claimed by that team's lead. With two or more members and "
            "nobody named, the queue is unclaimable and no one is ever woken for it — "
            "this does not clear on its own. Name a lead in Settings.")

    if gate == "paused_agents":
        who = _listed(board["paused_agents"])
        return brief(
            "serious", "paused agent",
            f"{who} holds the open work and is paused",
            f"{who} is paused and holds the work.",
            "A paused agent is skipped by the dispatcher entirely, so its tasks wake "
            "nobody and show in no counter. Resume the agent, or reassign the work.")

    if gate == "stood_down_agents":
        who = _listed(board["stood_down_agents"])
        return brief(
            "serious", "stood-down seat",
            f"{who} holds the open work and has been stood down",
            f"{who} has been stood down and holds the work.",
            "That role's job was declared finished, so the dispatcher never wakes it "
            "and its tasks show in no counter. Do not resume it to clear this — "
            "reassign the work to a team that is running, or kill it. Recall the role "
            "only if standing it down was the mistake.")

    if gate == "stalled_requirement":
        keys = _listed(board["stalled_requirements"])
        return brief(
            "serious", "stalled",
            f"{keys} has no live work left and was never met",
            f"{keys}: every task it had was killed.",
            "The requirement is still open, its board is empty, and its acceptance "
            "test will never run because nothing succeeded. It needs new work or it "
            "needs cutting — it will not resolve itself.")

    if gate == "rework_owed":
        keys = _listed(board["rework_owed"])
        return brief(
            "serious", "rework owed",
            f"{keys} came back from you and the rework has not started",
            f"{keys}: you sent it back and nobody has picked up the rework.",
            "It is open, its board is empty, and it will not come back to you until "
            "new work closes under it — which is the point, since re-asking you about "
            "a deliverable nobody has touched is not a question. The CEO has what you "
            "said and is free to create the work. If this stays here, say so in the "
            "boardroom.")

    if gate == "awaiting_chairman":
        # One sentence per cause, because they are not the same refusal and
        # saying they are cost a project its rework: this read "no work may be
        # created until this is settled" whatever the cause, the CEO quoted it
        # to the chairman as fact — the context pack tells it to quote this
        # rather than reason from a directory — and stood down for the night.
        # Only an unaccepted plan actually stops everything.
        wants, why = [], []
        if board["plan_open"]:
            wants.append("the plan needs accepting")
            why.append("Until you accept the plan nothing may be created at all; that "
                       "one is a hard refusal and the CEO will quote it.")
        if board["proposed_requirements"]:
            wants.append(f"{_listed(board['proposed_requirements'])} proposed")
            why.append("Work may not be created for scope you have not accepted — "
                       "scope you already accepted is untouched by it.")
        if board["observable_checking"]:
            wants.append(
                f"{_listed(board['observable_checking'])} waiting on your verdict")
            why.append("A requirement sitting with you is finished being worked, so it "
                       "blocks nothing but itself; if the rest of the board is quiet "
                       "too, that is a different reason and this is not it.")
        asks = _listed(wants)
        return brief(
            "warn", "your call",
            f"waiting on you — {asks}",
            f"Waiting on you: {asks}.",
            " ".join(why) + " Decide it on the Roadmap.")

    if gate == "awaiting_ceo":
        # The same three causes as awaiting_chairman, on a project whose dial
        # handed them to the CEO. Nothing here is the chairman's to do, and
        # saying so is the whole value of the sentence: the alternative was
        # telling him to go and click something he had delegated away.
        wants = []
        if board["plan_open"] or board["proposed_requirements"]:
            wants.append("scope it has proposed but not yet accepted")
        if board["observable_checking"]:
            wants.append(
                f"a verdict on {_listed(board['observable_checking'])}")
        asks = _listed(wants)
        return brief(
            "ok", "the CEO's call",
            f"the CEO owes itself a decision — {asks}",
            f"The CEO has not yet decided: {asks}.",
            "Nothing here is waiting on you — the constitution gives the CEO these "
            "verbs, and it takes them on its next planning wake. Everything it "
            "decides lands under Records → Decisions with its reason, and you can "
            "reverse any of it. If it is still sitting here next time you look, say "
            "so in the boardroom.",
            banner=False)

    if gate == "blocked_tasks":
        return brief(
            "serious", "blocked",
            f"{board['blocked']} task(s) blocked and nothing else on the board",
            f"{board['blocked']} task(s) are blocked.",
            "An agent stopped and asked a question rather than guessing. Read the "
            "block reason on the Board tab and either answer it or kill the task.")

    if gate == "checks_lost":
        lost = _listed(board["checks_lost"])
        return brief(
            "warn", "check lost",
            f"the check on {lost} went quiet; the platform is re-running it",
            f"The check for {lost} went quiet and is being re-run.",
            "The finished work is with the platform's checker, but its check run "
            "died before reporting a result. The platform requeues a lost check "
            "itself within minutes and tells the CEO if it keeps dying — the "
            "deliverable itself may well be fine, and nothing here needs you yet.")

    if gate == "review_owed":
        return brief(
            "ok", "being read",
            f"{board['reviewing']} finished piece(s) waiting on an independent read",
            f"{board['reviewing']} finished piece(s) waiting to be read.",
            "This project asks for work to be read by somebody who did not write it "
            "before it closes, so these are done and waiting on their reader's next "
            "wake. If they sit here, the reader is paused or gone — the platform "
            "closes them itself rather than leaving them stuck, and you can turn the "
            "read off under The bar in Settings.", banner=False)

    if gate == "checking":
        return brief(
            "ok", "verifying",
            f"{board['checking']} check(s) running on finished work",
            f"Verifying finished work — {board['checking']} check(s) with the platform.",
            "Agents finished this work and the platform is running the success tests "
            "itself, in a container, with no model involved. Results post on their "
            "own; nothing here needs you.", banner=False)

    if gate == "concurrency":
        c = state["concurrency"]
        return brief(
            "ok", "working",
            f"at the concurrency cap ({c['in_flight']} of {c['cap']}) — working",
            f"At the concurrency cap — {c['in_flight']} of {c['cap']} running.",
            "The project is working flat out. Raise 'Concurrent runs' in Settings to "
            "widen it.", banner=False)

    if gate == "agents_busy":
        return brief(
            "ok", "working",
            "every agent that has work is already running",
            "Every agent with work is already running.",
            "The project is working. Nothing here needs you.", banner=False)

    if behind:
        return brief(
            "ok", "starting",
            f"nothing blocking — a run should start shortly —{queue.rstrip('.')}",
            "Nothing blocking — a run should start within seconds.",
            f"There is capacity and there is work.{queue}", banner=False)

    return brief(
        "ok", "idle",
        "nothing waiting; an idle project costs nothing",
        "Nothing waiting.",
        "The board is empty and nobody is holding unread work. An idle project costs "
        "exactly nothing — that is the point.", banner=False)


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

    `blocked_by` names the first gate that stops a run. It is None only when
    there is genuinely nothing to do: work that exists but cannot be reached —
    gated by a clock, owned by a paused agent, sitting on a leaderless team —
    now names its own wall rather than rendering as a healthy quiet.
    """
    now = utcnow()
    start = day_start(now)
    used = daily_runs_used(session, project, start)
    used_hour = hourly_runs_used(session, project, now)
    inbox_today = inbox_runs_used(session, project, start)
    inbox_hour = inbox_runs_used(session, project, now - timedelta(hours=1))
    in_flight = _in_flight(session, project)
    max_concurrent = _max_concurrent(cons)
    # The same clamp `_dispatch_project` enforces with: past this line ordinary
    # work is done for the day and only governance may still spend.
    run_wall = max(cons.runs_per_project_per_day,
                   int(cons.runs_per_project_per_day * cons.runs_hard_stop_multiple))
    reserved = min(cons.reserved_governance_runs, run_wall // 2)
    governance_only = used >= run_wall - reserved

    agents = list(
        session.execute(
            select(Agent).where(Agent.project_id == project.id,
                                Agent.status == AgentStatus.ACTIVE)
        ).scalars()
    )

    rows: list[dict] = []
    for agent in agents:
        notice_n, notice_oldest, notice_urgent = _notices_detail_for(session, agent)
        unread_n, unread_oldest, unread_urgent = _unread_detail_for(session, project, agent)
        work = len(_work_reasons(session, agent, now))
        pending = work + notice_n + unread_n
        # A batching window only *holds* an agent that has nothing else to wake
        # for. One with a claimable task is going to run regardless, so calling
        # it coalesced would name the wrong wall.
        ripe = [
            r for r in (
                _coalesced_until(notice_n, notice_oldest, notice_urgent, now,
                                 NOTICE_COALESCE_MINUTES, NOTICE_COALESCE_COUNT),
                _coalesced_until(unread_n, unread_oldest, unread_urgent, now,
                                 MESSAGE_COALESCE_MINUTES, MESSAGE_COALESCE_COUNT),
            ) if r is not None
        ]
        heartbeat_due = False
        if agent.kind == AgentKind.CEO:
            hours = float(cons.ceo_heartbeat_hours)
            beat = session.execute(
                select(Heartbeat).where(Heartbeat.agent_id == agent.id,
                                        Heartbeat.name == "planning")
            ).scalar_one_or_none()
            due = beat is None or beat.last_fired_at <= now - timedelta(hours=hours)
            if due and (beat is None
                        or cons.ceo_heartbeat_always
                        or _ceo_has_planning_material(session, project, cons)):
                pending += 1
                heartbeat_due = True
        wakes = _wakes_last_hour(session, agent)
        at_ceiling = wakes >= cons.wakes_per_agent_per_hour
        frees_at = (_wake_slot_frees_at(session, agent, cons.wakes_per_agent_per_hour)
                    if at_ceiling and pending else None)
        # Everything this agent is waiting on is sitting in a batching window.
        held = (min(ripe) if ripe and not work and not heartbeat_due
                and notice_n + unread_n == pending else None)
        rows.append({
            "id": agent.id,
            "name": agent.name,
            "kind": agent.kind,
            "busy": _agent_busy(session, agent),
            "wakes_last_hour": wakes,
            "wake_cap": cons.wakes_per_agent_per_hour,
            "wake_slot_frees_at": frees_at.isoformat() if frees_at else None,
            "pending": pending,
            # The parts, because the total is three different things added up
            # and a reader cannot tell which. A CEO asked why its project was
            # still running read "pending: 1", decided on its own that it meant
            # a notice rather than a task, and told the chairman nothing was
            # happening. It was an unread message, and it bought a run.
            "pending_detail": {
                "work": work,
                "notices": notice_n,
                "unread": unread_n,
                "heartbeat": 1 if heartbeat_due else 0,
            },
            "batching_until": held.isoformat() if held else None,
        })

    board = _board_reachability(session, project, cons, now)
    delivered = policy.delivered_awaiting_confirmation(session, project)

    with_work = [r for r in rows if r["pending"]]
    free = [r for r in with_work
            if not r["busy"] and r["wakes_last_hour"] < r["wake_cap"]]
    if used >= run_wall:
        blocked_by = "daily_runs"
    elif used_hour >= cons.runs_per_project_per_hour:
        blocked_by = "pacing"
    elif in_flight >= max_concurrent:
        blocked_by = "concurrency"
    elif delivered:
        # Above the rungs below because it outranks them: agents with unread
        # chatter on a delivered project are not "about to start" and are not
        # "coalescing" — they will never wake at all. Reading that as an
        # ordinary quiet is how a CEO told its chairman nothing was running.
        blocked_by = "delivery_ready"
    elif not with_work:
        # Nobody has anything to do. That is either correct, or there is work
        # on the board that nobody can reach — and those two used to render
        # identically, as an empty, reassuring sentence.
        blocked_by = board["unreachable"]
    elif (inbox_today >= cons.inbox_runs_per_project_per_day
          or inbox_hour >= cons.inbox_runs_per_project_per_hour) and all(
              not r["busy"] and r["pending_detail"]["unread"] and not (
                  r["pending_detail"]["work"] or r["pending_detail"]["notices"]
                  or r["pending_detail"]["heartbeat"])
              for r in with_work):
        # Everyone holding anything is idle, holding only unread mail, and the
        # lane that reads mail is spent. `_dispatch_project` refuses every one
        # of those runs; this ladder had no rung for it, so a project with
        # sixty-five unread messages and no way to read any of them reported
        # that a run should start on the next tick, for the rest of the day.
        # `not busy` matters: a run still in flight means the project is
        # working, and `agents_busy` below is the honest sentence for that.
        blocked_by = "inbox_runs"
    elif all(r["busy"] for r in with_work):
        blocked_by = "agents_busy"
    elif all(r["busy"] or r["wakes_last_hour"] >= r["wake_cap"] for r in with_work):
        blocked_by = "wake_ceiling"
    elif free and all(r["batching_until"] for r in free):
        blocked_by = "coalescing"
    elif governance_only:
        # Ordinary work is done for the day; only a review, a cycle plan or a
        # re-forecast may still spend. Nothing said this out loud before, so
        # the last three runs of every day looked like an unexplained stop.
        blocked_by = "governance_reserve"
    else:
        blocked_by = None  # a run should start on the next tick

    claimed = session.execute(
        select(func.count(Task.id)).where(Task.project_id == project.id,
                                          Task.status == TaskStatus.CLAIMED)
    ).scalar_one()
    notices_total = session.execute(
        select(func.count(Notice.id)).where(Notice.project_id == project.id,
                                            Notice.consumed_at.is_(None))
    ).scalar_one()
    unread_total = sum(r["pending_detail"]["unread"] for r in rows)

    # When the throttle lifts: the earliest slot among the agents it is
    # actually holding back. Only meaningful when the ceiling is the gate —
    # an agent parked at its cap with nothing to do is not blocking anything.
    slots = [r["wake_slot_frees_at"] for r in with_work
             if not r["busy"] and r["wake_slot_frees_at"]]
    frees_at = min(slots) if slots else None

    batches = [r["batching_until"] for r in free if r["batching_until"]]

    state = {
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
        "governance_reserve": {
            "reserved": reserved,
            "ordinary_cap": max(0, run_wall - reserved),
            # The plan stays visible beside the wall: it is what the CEO paces
            # against and what the overrun account is measured from.
            "wall": run_wall,
            "active": governance_only,
        },
        # The reading lane, on the same shape as the work lane. Reported even
        # when it is not the gate, because the question it answers — "how much
        # of today went to the project talking to itself?" — is one nobody
        # could ask before, and the answer was 57%.
        "inbox_runs": {
            "used_today": inbox_today,
            "cap_today": cons.inbox_runs_per_project_per_day,
            "used_this_hour": inbox_hour,
            "cap_this_hour": cons.inbox_runs_per_project_per_hour,
        },
        "wake_ceiling": {
            "cap": cons.wakes_per_agent_per_hour,
            # The trailing hour never "rolls over" on a clock, so this is the
            # only honest answer to "when does this clear?".
            "frees_at": frees_at,
            "agents_held": len([r for r in with_work
                                if not r["busy"] and r["wake_slot_frees_at"]]),
        },
        "coalescing": {
            "ripe_at": min(batches) if batches else None,
            "agents_held": len(batches),
        },
        "concurrency": {"in_flight": in_flight, "cap": max_concurrent},
        "waiting": {
            # Split, because the old single number was the lie at the heart of
            # this: a board whose only task was gated until 03:00 reported "1
            # task ready" beside "a run should start shortly", for two hours.
            "ready_tasks": board["claimable"] + board["gated"],
            "claimable_tasks": board["claimable"],
            "gated_tasks": board["gated"],
            "claimed_tasks": claimed,
            "blocked_tasks": board["blocked"],
            # Finished work the platform is still verifying. Counted so a board
            # holding only CHECKING rows reads as work in hand, not as empty —
            # the stall watchdog and the chairman's screen both read this.
            "checking_tasks": board["checking"],
            # And finished work waiting on a reader, for the same reason: a
            # board of nothing but reads is a board with work in hand.
            "reviewing_tasks": board["reviewing"],
            # Scope that is open with nothing left under it. Every counter
            # above is a task row or a notice, and this is the one state where
            # all of them are legitimately zero while the project is doing
            # nothing about work it has not finished — which is how the stall
            # watchdog came to disarm itself on the exact projects it was
            # written for.
            "open_scope_no_board": _open_scope_with_no_board(session, project, cons),
            "notices": notices_total,
            # Counted here because the fallback brief used to assert its
            # absence — "nobody has unread work" — on a project where three
            # agents held sixty-five between them.
            "unread": unread_total,
            "agents_with_work": len(with_work),
        },
        "board": board,
        "agents": rows,
    }
    state["timezone"] = cons.timezone
    state["brief"] = _activity_brief(state)
    return state


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
    session: Session, project: Project, cons: ConstitutionDoc, agent: Agent,
    *, delivered: bool = False
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
            # Platform boardroom lines (needs/assists announced to the
            # chairman) have no sender and are addressed to HIM — they mirror
            # to Slack and never wake an agent, or every need the CEO filed
            # would urgently buy a run to read its own announcement.
            and not (m.sender_agent_id is None and m.sender_user_id is None)
        ]
        actionable = 0
        channel_urgent = False
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
                channel_urgent = True
            if oldest_at is None or message.created_at < oldest_at:
                oldest_at = message.created_at
        if actionable:
            # `urgent` rides on the reason because the lane is decided from it:
            # the chairman speaking, or anything in the boardroom, is work and
            # spends the work allowance. Ordinary agent-to-agent traffic is
            # reading, and spends the inbox ceiling instead.
            message_reasons.append(
                {"kind": "messages", "channel_id": channel_id,
                 "count": actionable, "urgent": channel_urgent})
    if delivered:
        # Everything in scope is true and only the chairman's confirmation is
        # left. Agent-to-agent traffic about a finished project buys a run to
        # discuss work nobody asked for, and the only thing that ever stopped it
        # was the daily inbox ceiling — which re-arms at midnight, so the
        # chairman gets a fresh burst of messages every morning about a project
        # he was told was done. The chairman still gets through: his messages
        # and anything in the boardroom are urgent, and urgent survives.
        message_reasons = [r for r in message_reasons if r["urgent"]]
        urgent = any(r["urgent"] for r in message_reasons)
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
        if delivered:
            # Coalescing is the wrong question here. On a delivered project a
            # third notice arriving, or an ordinary one ageing out, is not a
            # reason to spend — only the short list above is.
            wakes = any(kind in DELIVERED_WAKE_NOTICE_KINDS for kind, _ in pending)
        else:
            wakes = urgent or ripe or len(pending) >= NOTICE_COALESCE_COUNT
        if wakes:
            reasons.append({"kind": "notices", "count": len(pending)})
        # The re-forecast wake is governance: it must land on the day the
        # allowance is gone, because that is exactly the day the date slipped.
        if any(kind in COMMITMENT_NOTICE_KINDS for kind, _ in pending):
            reasons.append({"kind": "commitment"})
        # Governance-classed for the same reason: the CEO told him what it
        # would do unanswered, and a day whose allowance is gone must not be a
        # day it silently does not do it. This is the only wake that can make
        # the default happen at all.
        if any(kind == NoticeKind.ASK_LAPSED for kind, _ in pending):
            reasons.append({"kind": "ask_lapsed"})

    # 3. Work: ready tasks this agent could claim, or a claim to push forward.
    reasons.extend(_work_reasons(session, agent, now))

    # 3b. A per-agent daily wake (agents.daily_at) — the CEO's standup
    #     generalized to any agent (stage E): the support agent that reads
    #     the inbox every morning, the metrics agent that reports at close.
    #     Same helper, same heartbeat dedup, same brakes.
    if agent.daily_at:
        personal = _daily_wake_due(session, project, cons, agent, now,
                                   raw=agent.daily_at, name="agent_daily")
        # Consumed even when delivered — the dedup beat has to advance or the
        # rhythm bursts the moment the chairman confirms, replaying every
        # morning it slept through.
        if personal and not delivered:
            reasons.append(personal)

    # 4. Heartbeats: the CEO plans on a cadence even in silence — but silence
    #    with an empty board and no live bet leaves nothing to plan, and an
    #    idle wake is a full-price run. The first-ever heartbeat always fires
    #    (it bootstraps the project); after that the cadence still advances,
    #    so a busy project keeps its rhythm and an idle one sleeps until
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
                    and not _ceo_has_planning_material(session, project, cons))
            # A delivered project has nothing left to plan: every requirement
            # is true and new scope is refused (policy.check_new_scope). A
            # planning beat here is a run spent deciding not to invent work,
            # and ceo_heartbeat_always does not override it — that setting is
            # about silence on a live project, not about a finished one.
            skip = skip or delivered
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
        if daily and not delivered:
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


def _ceo_has_planning_material(session: Session, project: Project,
                               cons: ConstitutionDoc) -> bool:
    """Anything the planning heartbeat could act on: an open top-level task, a
    live (or expired-awaiting-decision) bet, or scope that is open with nothing
    left on the board under it. With none of them — and no other wake reason —
    a heartbeat wake buys a full-price run to conclude "nothing to do".

    That third term is the one this function was missing, and its absence had
    the sign backwards. Open scope with an empty board is not "nothing to
    plan"; it is the single state where planning is the ONLY thing left to do,
    because every other wake reason on this project is a task or a message and
    there are none of either. `check_stalled_requirements` has named this in
    its docstring since it was written — "an empty board is exactly what
    suppresses the CEO's planning heartbeat, so the project goes completely
    silent over work that is still formally open" — and it stayed a comment
    about a bug rather than a fix for one until a project spent six hours
    proving it.
    """
    if policy.open_top_level_tasks(session, project.id):
        return True
    if _open_scope_with_no_board(session, project, cons):
        return True
    return session.execute(
        select(func.count(Hypothesis.id)).where(
            Hypothesis.project_id == project.id,
            Hypothesis.status.in_([HypothesisStatus.ACTIVE, HypothesisStatus.EXPIRED]),
        )
    ).scalar_one() > 0


def _open_scope_with_no_board(session: Session, project: Project,
                              cons: ConstitutionDoc) -> int:
    """Requirements still open, whose every task is closed, and which THIS
    project's CEO could actually do something about.

    Read-only, and counted rather than merely tested, because two places need
    it: the planning wake above, and the stall watchdog — whose guard was six
    task-and-notice counters, every one of which reads zero in exactly this
    state. A project can be doing nothing, with scope open, and be invisible to
    both.

    The subtraction is the whole of the correctness here. Scope parked on the
    chairman's own verdict has an empty board for a reason that is not a fault:
    it is HIS move, `blocked_by` already says so, and `nag_observable_requirements`
    already chases him daily. Counting it here would wake the CEO every heartbeat
    to conclude "still waiting on him", at full price, and email him every few
    hours that his project has gone quiet — about a silence he is producing. The
    set is taken from `policy.awaiting_chairman` rather than from the statuses,
    so it follows the autonomy dial: where the CEO holds `verdict_policy`, a
    CHECKING requirement IS its move and does count.
    """
    parked = {r.id for r in policy.awaiting_chairman(session, project, cons)["verdicts"]}
    open_reqs = [
        rid for rid in session.execute(
            select(Requirement.id).where(
                Requirement.project_id == project.id,
                Requirement.status.in_(
                    [RequirementStatus.IN_PROGRESS, RequirementStatus.CHECKING]),
            )
        ).scalars()
        if rid not in parked
    ]
    if not open_reqs:
        return 0
    with_board = set(session.execute(
        select(Task.requirement_id).where(
            Task.requirement_id.in_(open_reqs),
            Task.status.in_(policy.OPEN_TASK_STATUSES),
        ).distinct()
    ).scalars())
    return sum(1 for rid in open_reqs if rid not in with_board)


def _daily_wake_due(session: Session, project: Project, cons: ConstitutionDoc,
                    agent: Agent, now, raw: str | None = None,
                    name: str = "daily") -> dict | None:
    """A once-per-local-day wall-clock wake. Called with the constitution's
    ceo_daily_at for the CEO, and with agents.daily_at for anyone the chairman
    put on a rhythm (stage E) — same dedup Heartbeat, distinct `name`, so the
    CEO can hold both without either eating the other."""
    raw = cons.ceo_daily_at if raw is None else raw
    if not raw:
        return None
    try:
        from zoneinfo import ZoneInfo

        hour, minute = (int(part) for part in str(raw).split(":", 1))
        tz = ZoneInfo(cons.timezone)
    except Exception:
        log.warning("project %s has invalid daily_at/timezone config", project.slug)
        return None
    now_local = now.astimezone(tz)
    target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now_local < target:
        return None
    beat = session.execute(
        select(Heartbeat).where(Heartbeat.agent_id == agent.id, Heartbeat.name == name)
    ).scalar_one_or_none()
    if beat is not None and beat.last_fired_at.astimezone(tz).date() >= now_local.date():
        return None
    if beat is None:
        session.add(Heartbeat(agent_id=agent.id, name=name, last_fired_at=now))
    else:
        beat.last_fired_at = now
    return {"kind": "heartbeat", "name": name,
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

    # Work finished by somebody else and waiting on a read. Without this a
    # REVIEW task wakes nobody and sits there — the state would be a hole the
    # board falls into rather than a step it passes through.
    #
    # One seat per task, not every seat. A roster with an editor AND a
    # plan-adversary — which is the default this project's own doctrine argues
    # for — woke both for the same piece, and the second one paid full price to
    # find the task already closed. `policy.review_seats` is ordered, so the
    # first eligible seat is the same answer on every tick and in every worker.
    seats = policy.review_seats(session, agent.project_id)
    if agent.id in {seat.id for seat in seats}:
        for task in session.execute(
            select(Task).where(Task.project_id == agent.project_id,
                               Task.status == TaskStatus.REVIEW,
                               Task.claimed_by_agent_id != agent.id)
        ).scalars():
            eligible = [s for s in seats if s.id != task.claimed_by_agent_id]
            if eligible and eligible[0].id != agent.id:
                continue
            reasons.append({"kind": "review_owed", "task_id": task.id})

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
