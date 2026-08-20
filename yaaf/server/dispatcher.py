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


def _utc(value):
    """Timestamps read back from SQLite come home naive. Comparing one of those
    against an aware `now` raises, and a raise inside a read path is a blank
    page where a reason should be."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _unread_detail_for(
    session: Session, project: Project, agent: Agent
) -> tuple[int, object | None, bool]:
    """Messages this agent would wake for, plus the two facts the coalescing
    window turns on: when the oldest of them arrived, and whether any is urgent
    enough to skip the window entirely.

    Mirrors the filter in _wake_reasons (not self-authored, not under a frozen
    root, boardroom skipped for a CEO whose chat session is live) minus the
    pair-breaker, which is a trip detector rather than a filter and cannot run
    from a read path.
    """
    skip: set[str] = set()
    if agent.kind == AgentKind.CEO and chat_session_live(session, project):
        skip = {
            c.id for c in session.execute(
                select(Channel).where(Channel.project_id == project.id,
                                      Channel.kind == ChannelKind.BOARDROOM)
            ).scalars()
        }
    total = 0
    oldest = None
    urgent = False
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
            root = session.get(Message, message.thread_id or message.id)
            if root is not None and root.frozen_at is not None:
                continue
            total += 1
            # Same urgency rule as _wake_reasons: the chairman never waits out
            # a batching window, and neither does anything said in his room.
            if (message.sender_user_id is not None
                    or (channel is not None and channel.kind == ChannelKind.BOARDROOM)):
                urgent = True
            at = _utc(message.created_at)
            if oldest is None or at < oldest:
                oldest = at
    return total, oldest, urgent


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
    healthy company looks stopped — the wait is minutes long and named nowhere.
    This is the same arithmetic as `_wake_reasons`, read-only, so a screen can
    say which minute it ends.
    """
    if not count or urgent or count >= threshold or oldest is None:
        return None
    ripe = oldest + timedelta(minutes=minutes)
    return ripe if ripe > now else None


def _board_reachability(session: Session, project: Project, now) -> dict:
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

    # Work owned by an agent the chairman paused. The dispatcher skips those
    # agents entirely, so their queue leaves no trace in any counter.
    parked: list[str] = []
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
            parked.append(agent.name)

    blocked = session.execute(
        select(func.count(Task.id)).where(Task.project_id == project.id,
                                          Task.status == TaskStatus.BLOCKED)
    ).scalar_one()

    # Scope waiting on the chairman, and scope that quietly stopped moving.
    proposed, observable, stalled = [], [], []
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

    plan = policy.plan_requirement(session, project.id)
    plan_open = plan is not None and plan.status not in policy.CLOSED_REQUIREMENT_STATUSES

    # Order matters: things that never clear on their own come before things
    # that do, and "you have to decide" comes before "a clock has to pass".
    if leaderless:
        unreachable = "no_lead"
    elif parked:
        unreachable = "paused_agents"
    elif stalled:
        unreachable = "stalled_requirement"
    elif proposed or observable or plan_open:
        unreachable = "awaiting_chairman"
    elif gates:
        unreachable = "scheduled"
    elif blocked:
        unreachable = "blocked_tasks"
    else:
        unreachable = None

    return {
        "claimable": len(claimable),
        "gated": len(gates),
        "next_gate_at": min(gates).isoformat() if gates else None,
        "blocked": blocked,
        "leaderless_teams": sorted(leaderless),
        "paused_agents": sorted(parked),
        "stalled_requirements": sorted(stalled),
        "proposed_requirements": sorted(proposed),
        "observable_checking": sorted(observable),
        "plan_open": plan_open,
        "unreachable": unreachable,
    }


def _hhmm(iso: str | None) -> str:
    """An ISO instant as the only part of it a reader acts on. Absolute rather
    than relative on purpose: "in 4 minutes" is wrong the moment the response
    is cached, and a time you can check against a clock is falsifiable."""
    return f"{iso[11:16]} UTC" if iso else "shortly"


def _listed(names: list[str]) -> str:
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
    and busy agents mean the company is *working*; a banner that fires when
    things are fine is how a reader learns to stop reading it. `sig` and
    `clears_at` are set only on the briefs that are waiting out a clock, which
    is what makes them dismissible.
    """
    gate = state["blocked_by"]
    waiting = state["waiting"]
    board = state["board"]
    behind = []
    if waiting["claimable_tasks"]:
        behind.append(f"{waiting['claimable_tasks']} task(s) ready to claim")
    if waiting["gated_tasks"]:
        behind.append(f"{waiting['gated_tasks']} task(s) scheduled for later")
    if waiting["notices"]:
        behind.append(f"{waiting['notices']} notice(s) undelivered")
    queue = f" {_listed(behind)} waiting behind it." if behind else ""

    def brief(tone, chip, short, headline, detail, *, banner=True, sig=None, clears_at=None):
        return {"tone": tone, "chip": chip, "short": short, "headline": headline,
                "detail": detail, "banner": banner, "sig": sig, "clears_at": clears_at}

    if gate == "daily_runs":
        d = state["daily_runs"]
        return brief(
            "serious", "capped",
            f"out of runs for today ({d['used']} of {d['cap']}), resumes 00:00 UTC",
            f"Out of runs for today — {d['used']} of {d['cap']} used.",
            f"Nothing starts until the count resets at 00:00 UTC.{queue} Runs that "
            "died still spent the allowance. Raise 'Runs per day, whole project' in "
            "Settings if this is tighter than you meant it to be.",
            sig="daily_runs", clears_at=d["resets_at"])

    if gate == "pacing":
        p = state["pacing"]
        return brief(
            "serious", "pacing",
            f"pacing itself ({p['used']} of {p['cap']} this hour), next run "
            f"{_hhmm(p['frees_at'])}",
            f"Pacing itself — {p['used']} of {p['cap']} runs used in the last hour.",
            f"The next run may start at {_hhmm(p['frees_at'])}.{queue} This is the "
            "brake that stops a whole day's runs going before lunch; it clears on "
            "its own and usually needs nothing from you.",
            sig="pacing", clears_at=p["frees_at"])

    if gate == "governance_reserve":
        g = state["governance_reserve"]
        d = state["daily_runs"]
        return brief(
            "serious", "reserved",
            f"ordinary work is done for today ({d['used']} of {g['ordinary_cap']})",
            f"Ordinary work has spent its day — {d['used']} of {g['ordinary_cap']}.",
            f"The last {g['reserved']} run(s) of the day are reserved so a status "
            f"review or a re-forecast always has somewhere to run.{queue} Everything "
            "else resumes at 00:00 UTC. Raise 'Runs per day, whole project', or lower "
            "'Reserved governance runs', in Settings.",
            sig="governance_reserve", clears_at=d["resets_at"])

    if gate == "wake_ceiling":
        w = state["wake_ceiling"]
        return brief(
            "serious", "throttled",
            f"every agent with work has hit its hourly wake ceiling, clearing "
            f"{_hhmm(w['frees_at'])}",
            f"Every agent with work has hit its hourly wake ceiling ({w['cap']}/hour).",
            f"The oldest wake ages out at {_hhmm(w['frees_at'])} and work starts again "
            f"on its own — nothing here needs you.{queue} Raise 'Wakes per agent per "
            "hour' in Settings only if the cap is tighter than you meant it to be.",
            sig="wake_ceiling", clears_at=w["frees_at"])

    if gate == "coalescing":
        c = state["coalescing"]
        return brief(
            "warn", "batching",
            f"batching a wake until it is worth a run, due {_hhmm(c['ripe_at'])}",
            f"Holding a wake until it is worth the run — due {_hhmm(c['ripe_at'])}.",
            "Waking an agent costs a full-price run, so one or two notices wait for a "
            f"third or for the oldest to age out.{queue} Nothing is wrong and nothing "
            "here needs you; anything you say yourself skips the wait entirely.",
            sig="coalescing", clears_at=c["ripe_at"])

    if gate == "scheduled":
        return brief(
            "warn", "scheduled",
            f"the ready work is gated until {_hhmm(board['next_gate_at'])}",
            f"The only work on the board is scheduled for {_hhmm(board['next_gate_at'])}.",
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

    if gate == "stalled_requirement":
        keys = _listed(board["stalled_requirements"])
        return brief(
            "serious", "stalled",
            f"{keys} has no live work left and was never met",
            f"{keys}: every task it had was killed.",
            "The requirement is still open, its board is empty, and its acceptance "
            "test will never run because nothing succeeded. It needs new work or it "
            "needs cutting — it will not resolve itself.")

    if gate == "awaiting_chairman":
        wants = []
        if board["plan_open"]:
            wants.append("the plan needs accepting")
        if board["proposed_requirements"]:
            wants.append(f"{_listed(board['proposed_requirements'])} proposed")
        if board["observable_checking"]:
            wants.append(
                f"{_listed(board['observable_checking'])} waiting on your verdict")
        asks = _listed(wants)
        return brief(
            "warn", "your call",
            f"waiting on you — {asks}",
            f"Waiting on you: {asks}.",
            "No work may be created until this is settled, so the company will stay "
            "quiet however much capacity it has. Decide it on the Plan tab.")

    if gate == "blocked_tasks":
        return brief(
            "serious", "blocked",
            f"{board['blocked']} task(s) blocked and nothing else on the board",
            f"{board['blocked']} task(s) are blocked.",
            "An agent stopped and asked a question rather than guessing. Read the "
            "block reason on the Board tab and either answer it or kill the task.")

    if gate == "concurrency":
        c = state["concurrency"]
        return brief(
            "ok", "working",
            f"at the concurrency cap ({c['in_flight']} of {c['cap']}) — working",
            f"At the concurrency cap — {c['in_flight']} of {c['cap']} running.",
            "The company is working flat out. Raise 'Concurrent runs' in Settings to "
            "widen it.", banner=False)

    if gate == "agents_busy":
        return brief(
            "ok", "working",
            "every agent that has work is already running",
            "Every agent with work is already running.",
            "The company is working. Nothing here needs you.", banner=False)

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
        "The board is empty and nobody has unread work. An idle project costs exactly "
        "nothing — that is the point.", banner=False)


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
    in_flight = _in_flight(session, project)
    max_concurrent = _max_concurrent(cons)
    # The same clamp `_dispatch_project` enforces with: past this line ordinary
    # work is done for the day and only governance may still spend.
    reserved = min(cons.reserved_governance_runs, cons.runs_per_project_per_day // 2)
    governance_only = used >= cons.runs_per_project_per_day - reserved

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
                        or _ceo_has_planning_material(session, project)):
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
            "batching_until": held.isoformat() if held else None,
        })

    board = _board_reachability(session, project, now)

    with_work = [r for r in rows if r["pending"]]
    free = [r for r in with_work
            if not r["busy"] and r["wakes_last_hour"] < r["wake_cap"]]
    if used >= cons.runs_per_project_per_day:
        blocked_by = "daily_runs"
    elif used_hour >= cons.runs_per_project_per_hour:
        blocked_by = "pacing"
    elif in_flight >= max_concurrent:
        blocked_by = "concurrency"
    elif not with_work:
        # Nobody has anything to do. That is either correct, or there is work
        # on the board that nobody can reach — and those two used to render
        # identically, as an empty, reassuring sentence.
        blocked_by = board["unreachable"]
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
            "ordinary_cap": max(0, cons.runs_per_project_per_day - reserved),
            "active": governance_only,
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
            "notices": notices_total,
            "agents_with_work": len(with_work),
        },
        "board": board,
        "agents": rows,
    }
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
