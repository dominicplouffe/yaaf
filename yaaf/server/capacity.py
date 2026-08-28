"""Can the work on the board actually happen before a given moment?

The CEO used to answer this in prose, dividing the one number it had. Asked
whether a deadline would hold it said "ten ordinary runs, four needed, six
spare to absorb a retry" — and was wrong sixty seconds after the allowance
reset, because three of the first four runs went to agents acknowledging a
message and the hourly ceiling closed behind them.

Four separate limits decide whether a run starts, and only one of them is a
daily total:

* the ordinary daily allowance (the daily cap minus the governance reserve),
  which refills in a lump at 00:00 UTC;
* the trailing-hour ceiling, which clears continuously;
* one wake per agent per hour, which is what makes a chain of work SERIAL —
  three tasks owned by one agent take three hours whatever the other caps say;
* how many runs may be in flight at once.

So this module answers with four numbers rather than one, and never with a
point estimate: what is available, what is needed, what to hold back for the
retries the measured failure rate predicts, and the slack between them. A
negative slack is the trip condition for a flag — the arithmetic says the date
does not hold, which is a fact the chairman should hear from his CEO rather
than discover.
"""

from __future__ import annotations

import math
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.constitution import ConstitutionDoc
from yaaf.core.models import (
    Agent,
    AgentKind,
    AgentStatus,
    Project,
    Run,
    RunStatus,
    Task,
    TaskStatus,
    Team,
    utcnow,
)
from yaaf.server import policy

#: Trailing window for the measured failure rate.
FAILURE_WINDOW_DAYS = 7

#: What to assume before there is any history to measure. A project with no
#: failures yet is not a project that cannot fail, and a reserve of zero on day
#: one is exactly the optimism this module exists to replace.
DEFAULT_FAILURE_RATE = 0.15

#: Statuses that mean the run did not deliver and its work still needs doing.
LOST_RUN_STATUSES = (
    RunStatus.FAILED,
    RunStatus.REAPED,
    RunStatus.KILLED,
    RunStatus.TERMINATED_REFUSALS,
)


def _owner_of(session: Session, task: Task) -> str | None:
    """Which agent will actually burn a wake on this task.

    Assignee if one is named; otherwise the team's lead, because a team task is
    claimed by its lead. Ownership is what makes the per-agent wake cap bite:
    three tasks on three teams run in an hour, three tasks on one desk take
    three.
    """
    if task.assignee_agent_id:
        return task.assignee_agent_id
    if task.claimed_by_agent_id:
        return task.claimed_by_agent_id
    if task.team_id:
        team = session.get(Team, task.team_id)
        if team is not None:
            lead = policy.effective_lead(session, team)
            if lead is not None:
                return lead.id
    return None


def failure_rate(session: Session, project: Project) -> tuple[float, str]:
    """How often a run has to be done twice, measured rather than assumed.

    Counts runs that ended without delivering and runs that were themselves a
    retry, over the trailing window. Returns the rate and the sentence that
    explains where it came from — a reserve that cannot say why it is that size
    is a padding number, not a forecast.
    """
    from yaaf.server.dispatcher import NON_WORK_RUN_KINDS

    since = utcnow() - timedelta(days=FAILURE_WINDOW_DAYS)
    base = select(func.count(Run.id)).where(
        Run.project_id == project.id,
        Run.kind.not_in(NON_WORK_RUN_KINDS),
        Run.created_at >= since,
    )
    total = session.execute(base).scalar_one()
    if not total:
        return DEFAULT_FAILURE_RATE, (
            f"no work runs in the last {FAILURE_WINDOW_DAYS} days to measure, so "
            f"{DEFAULT_FAILURE_RATE:.0%} is assumed"
        )
    lost = session.execute(
        base.where(Run.status.in_(LOST_RUN_STATUSES))
    ).scalar_one()
    retried = session.execute(base.where(Run.attempt > 1)).scalar_one()
    # A retry and the failure that caused it are the same incident counted from
    # both ends; taking the larger avoids double-charging the reserve for it.
    spoiled = max(lost, retried)
    rate = spoiled / total
    return rate, (
        f"{spoiled} of {total} work runs in the last {FAILURE_WINDOW_DAYS} days "
        f"did not deliver ({rate:.0%})"
    )


def runs_available_before(session: Session, project: Project, cons: ConstitutionDoc,
                          until, movers: int) -> tuple[int, str]:
    """How many work runs can physically start between now and ``until``.

    Walked hour by hour rather than divided, because the two ceilings behave
    differently: the daily allowance refills in a lump at 00:00 UTC and the
    hourly one clears continuously. Dividing the daily cap by anything is how
    the forecast this replaces went wrong.

    ``movers`` is how many distinct agents actually have work. Capacity is
    never more than ``movers × wakes_per_agent_per_hour`` in any hour, however
    much daily allowance is left — one desk with three tasks cannot go faster
    than its own wake slot.
    """
    from yaaf.server.dispatcher import (
        daily_runs_used,
        day_start,
        hourly_runs_used,
    )

    now = utcnow()
    if until <= now:
        return 0, "the deadline has already passed"

    reserved = min(cons.reserved_governance_runs, cons.runs_per_project_per_day // 2)
    ordinary_cap = max(0, cons.runs_per_project_per_day - reserved)
    day_left = max(0, ordinary_cap - daily_runs_used(session, project, day_start(now)))
    hour_used = hourly_runs_used(session, project, now)

    per_hour_ceiling = min(cons.runs_per_project_per_hour,
                           max(0, movers) * cons.wakes_per_agent_per_hour)

    available = 0
    cursor = now
    first = True
    resets = 0
    # Bounded so a deadline set years out cannot spin: past this the answer is
    # "more than enough" and the precise figure stops being interesting.
    for _ in range(24 * 90):
        if cursor >= until:
            break
        chunk_end = min(until, cursor + timedelta(hours=1))
        if day_start(chunk_end) > day_start(cursor):
            day_left = ordinary_cap
            resets += 1
        room = per_hour_ceiling
        if first:
            # The trailing hour is already partly spent.
            room = max(0, room - hour_used)
            first = False
        take = max(0, min(room, day_left))
        available += take
        day_left -= take
        cursor = chunk_end

    hours = (until - now).total_seconds() / 3600
    basis = (
        f"{available} run(s) can start in the {hours:.1f}h before the deadline: "
        f"at most {cons.runs_per_project_per_hour}/hour company-wide and "
        f"{cons.wakes_per_agent_per_hour} wake/hour for each of {movers} agent(s) "
        f"with work, against {ordinary_cap} ordinary runs a day"
        + (f" with {resets} daily reset(s) in the window" if resets else
           " and no reset before then")
    )
    return available, basis


def capacity_forecast(session: Session, project: Project, cons: ConstitutionDoc,
                      until, extra_runs: int = 0) -> dict:
    """The four numbers, and the sentence that explains them.

    ``extra_runs`` is work the CEO knows about that is not on the board yet —
    the memo it will write once the analysis lands. The platform can only count
    what has been filed, and saying so is better than quietly forecasting a
    chain one run shorter than it is.
    """
    now = utcnow()

    # What has to happen. Claimed work still needs a run to finish; ready work
    # needs one to start; gated work only counts if its gate opens in time.
    needed_by_agent: dict[str, int] = {}
    unowned = 0
    for task in session.execute(
        select(Task).where(Task.project_id == project.id,
                           Task.status.in_([TaskStatus.READY, TaskStatus.CLAIMED]))
    ).scalars():
        gate = task.not_before
        if gate is not None and gate > until:
            continue
        owner = _owner_of(session, task)
        if owner is None:
            # Nobody can claim it, so it is not capacity that is short — the
            # board reachability block is where that shows up.
            unowned += 1
            continue
        needed_by_agent[owner] = needed_by_agent.get(owner, 0) + 1

    if extra_runs:
        ceo = session.execute(
            select(Agent).where(Agent.project_id == project.id,
                                Agent.kind == AgentKind.CEO)
        ).scalars().first()
        if ceo is not None:
            needed_by_agent[ceo.id] = needed_by_agent.get(ceo.id, 0) + extra_runs

    needed = sum(needed_by_agent.values())

    # Everyone who could move, not only those already holding work: an idle
    # agent is capacity the moment a task lands on its team.
    active = session.execute(
        select(func.count(Agent.id)).where(Agent.project_id == project.id,
                                           Agent.status == AgentStatus.ACTIVE)
    ).scalar_one()
    movers = max(len(needed_by_agent), 1) if needed else active

    available, basis = runs_available_before(session, project, cons, until, movers)

    rate, rate_basis = failure_rate(session, project)
    retry_reserve = round(needed * rate, 1)

    # The serial floor. One desk holding three tasks needs three hours at one
    # wake an hour, whatever the project-wide ceilings allow — this is the
    # constraint the old prose arithmetic had no way to express.
    wake_cap = max(1, cons.wakes_per_agent_per_hour)
    chain_hours = max((math.ceil(n / wake_cap) for n in needed_by_agent.values()),
                      default=0)
    hours_left = max(0.0, (until - now).total_seconds() / 3600)

    slack = round(available - needed - retry_reserve, 1)
    fits_in_time = chain_hours <= hours_left
    feasible = slack >= 0 and fits_in_time

    if not needed:
        why = "nothing is on the board that needs a run before then"
    elif not fits_in_time:
        busiest = max(needed_by_agent.values())
        why = (
            f"one agent holds {busiest} of the {needed} run(s) needed and may wake "
            f"{wake_cap} time(s) an hour, so the chain alone takes {chain_hours}h of "
            f"the {hours_left:.1f}h left"
        )
    elif slack < 0:
        why = (
            f"{needed} run(s) needed plus {retry_reserve} held back for retries "
            f"exceeds the {available} that can start — short by {abs(slack)}"
        )
    else:
        why = f"{available} available against {needed} needed, {slack} spare after retries"

    return {
        "until": until.isoformat(),
        "hours_left": round(hours_left, 1),
        "available": available,
        "needed": needed,
        "retry_reserve": retry_reserve,
        "slack": slack,
        "chain_hours": chain_hours,
        "feasible": feasible,
        "movers": movers,
        "unowned_tasks": unowned,
        "why": why,
        "basis": basis,
        "retry_basis": rate_basis,
        "note": (
            "Four limits gate a run and only one is a daily total. Do not divide "
            "the daily cap: this is the same arithmetic the dispatcher uses to "
            "decide whether a run may start. If `feasible` is false, say so to the "
            "chairman with these numbers and propose what to cut or raise — a date "
            "that cannot hold is worth more to him early than a reassurance is."
        ),
    }
