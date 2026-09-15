"""What each seat actually did, in a window the caller names.

The platform has recorded this since the first commit — `runs.agent_id`,
`tasks.claimed_by_agent_id`, `ledger.run_id` — and until now only the chairman
could read it. Asked "who did those 15 runs today", a CEO answered "honestly, I
can see the totals but not a name-by-name ledger", estimated six of its own, and
was wrong by half: it had spent twelve of seventeen. It was asking for fifty
percent more runs the same afternoon.

So the numbers come from where they are true, and the two halves are sourced
differently on purpose:

* **Runs and outcomes come from `runs`.** A run that died before booking a
  ledger row still happened, still spent a seat's slot against the ration, and
  is exactly the kind of run somebody asking "where did the day go" needs to
  see.
* **Tokens and money come from the ledger**, joined back through `run_id` — the
  same join, with the same `unattributed` bucket, that `observe.spend` performs
  for the chairman's card. A chat session books a row per turn, so summing the
  runs table instead would count one run's tokens several times over. Two
  numbers in one answer that could not both be true is the failure this shape
  avoids.

Because of that split a seat can show runs with no money (every row died
unbooked) and the project can show money with no seat (`unattributed` — the run
row is gone, but the money was still spent). Both are reported rather than
reconciled away.

`died_of` is the same answer asked one level down. Outcomes alone told a CEO
that eleven of a seat's runs had "failed", which is not a thing anyone can act
on: it reported eleven runs that "died without finishing anything" and moved to
stand the seat down. Seven of the eleven had filed work before a per-run
ceiling cut them off mid-task. So each death is reported with the cause the
platform already wrote (`deaths.py`) and with how many of them produced
something first — because "died" and "bought nothing" are two claims, and only
the first one was ever checked.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import (
    Agent,
    LedgerEntry,
    LedgerKind,
    Project,
    Role,
    RoleVersion,
    Run,
    Task,
    TaskEvent,
    TaskStatus,
    Team,
    utcnow,
)
from yaaf.server import deaths
from yaaf.server.dispatcher import UNMETERED_RUN_KINDS
from yaaf.server.rows import seat_row

#: Relative windows the CEO may name instead of a timestamp. `today` is the one
#: the chairman actually asks for, and the one a rolling `days=1` cannot
#: answer: at 13:00 a rolling day reaches back into yesterday afternoon, which
#: is how "today's runs" and "runs today" became different numbers.
_RELATIVE = re.compile(r"^(\d+)([dh])$")

MAX_WINDOW_DAYS = 365


class WindowError(ValueError):
    """The caller named a window that cannot be resolved."""


def resolve_window(
    timezone: str, since: str | None, until: str | None, *, now: datetime | None = None
) -> tuple[datetime, datetime, str]:
    """Turn what a caller typed into a half-open [start, end) in UTC.

    Accepts `today`, `yesterday`, `Nd`/`Nh` (rolling, back from now), a bare
    date (`2026-09-12`, a whole day in the project's timezone) or an ISO
    timestamp.

    **`today` is the RUN DAY, which starts at midnight UTC** — the same window
    `dispatcher.day_start` uses to count the daily ration, whatever the
    project's timezone is. That is not a detail. The question this whole module
    exists to answer is "who spent today's runs", and `runs` is a rationed
    quantity on a fixed UTC window: resolving `today` on the project's local
    calendar instead put the breakdown and the ration counter in the same
    context pack on different clocks. On the desk that prompted this the
    project runs on America/Toronto, so a local `today` silently dropped every
    run between 00:00 and 04:00 UTC — seventeen runs became ten, and the CEO
    would have been handed two numbers it could not reconcile and no way to
    know why.

    A bare date is still a whole day in the project's timezone. That one is a
    calendar question and has no ration attached to it.
    """
    now = now or utcnow()
    try:
        tz = ZoneInfo(timezone or "UTC")
    except Exception:
        tz = UTC

    def day_bounds(day: date) -> tuple[datetime, datetime]:
        start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
        return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)

    raw = (since or "today").strip().lower()
    if raw in ("today", "yesterday"):
        from yaaf.server.dispatcher import day_start

        start = day_start(now)
        if raw == "yesterday":
            return start - timedelta(days=1), start, "yesterday's run day (00:00 UTC)"
        return start, now, "today's run day (00:00 UTC, when the ration resets)"

    relative = _RELATIVE.match(raw)
    if relative:
        n = int(relative.group(1))
        if n < 1:
            raise WindowError("a window has to be at least 1")
        delta = timedelta(days=n) if relative.group(2) == "d" else timedelta(hours=n)
        if delta > timedelta(days=MAX_WINDOW_DAYS):
            raise WindowError(f"the longest window is {MAX_WINDOW_DAYS}d")
        return now - delta, now, f"the last {raw}"

    start = _parse_point(raw, tz, "since")
    end = _parse_point(until.strip().lower(), tz, "until") if until else now
    if end <= start:
        raise WindowError("`until` has to be after `since`")
    if end - start > timedelta(days=MAX_WINDOW_DAYS):
        raise WindowError(f"the longest window is {MAX_WINDOW_DAYS} days")
    return start, end, f"{start.isoformat()} to {end.isoformat()}"


def _parse_point(raw: str, tz: ZoneInfo, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise WindowError(
            f"{field}={raw!r} is not a window. Use `today`, `yesterday`, `7d`, "
            "`12h`, a date like 2026-09-12, or an ISO timestamp."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(UTC)


# ── attribution ─────────────────────────────────────────────────────────────────


def attribution_index(session: Session, run_ids: set[str]) -> tuple[dict, dict, dict]:
    """`(runs, agents, team_names)` for a set of run ids.

    Shared with `observe.spend` so the chairman's money card and the CEO's
    answer resolve a run to a seat by exactly the same route. A run that no
    longer exists still spent its money; both callers bucket it as
    `unattributed` rather than dropping it.
    """
    runs = {
        r.id: r
        for r in session.execute(select(Run).where(Run.id.in_(run_ids))).scalars()
    } if run_ids else {}
    agent_ids = {r.agent_id for r in runs.values() if r.agent_id}
    agents = {
        a.id: a
        for a in session.execute(select(Agent).where(Agent.id.in_(agent_ids))).scalars()
    } if agent_ids else {}
    team_ids = {a.team_id for a in agents.values() if a.team_id}
    team_names = {
        t.id: t.name
        for t in session.execute(select(Team).where(Team.id.in_(team_ids))).scalars()
    } if team_ids else {}
    return runs, agents, team_names


def _role_slug(session: Session, role_version_id: str | None) -> str | None:
    if not role_version_id:
        return None
    version = session.get(RoleVersion, role_version_id)
    if version is None:
        return None
    role = session.get(Role, version.role_id)
    return role.slug if role else None


def _pseudo_seat(name: str, kind: str) -> dict:
    """A bucket that is not a person. `seat_row` takes an Agent and these have
    none — the platform's own success checks, and money whose run row is gone.
    Same keys so a reader never branches on which kind of row it holds."""
    return {"agent_id": None, "name": name, "kind": kind, "status": None,
            "team_id": None, "team": None, "role_slug": None}


def _zero_death() -> dict:
    """One cause's share of a seat's deaths. `runs` counts runs that STARTED in
    the window; `tokens` is ledger money that LANDED in it, which is the same
    split the rest of this module makes and refuses to reconcile — so a cause
    can show tokens with no runs when a run that began before the window died
    inside it. `produced_work_first` is the one that stops a reader concluding
    a dead run was an empty one: it counts deaths that had already claimed a
    task, written a file or filed work before they were cut."""
    return {"runs": 0, "tokens": 0, "produced_work_first": 0}


def _zero_seat() -> dict:
    return {
        "runs": 0,
        # The subset that actually spent the project's ration. `runs` counts
        # everything that happened; this counts what the daily cap counts, so
        # "who spent today's 15" has an answer that ADDS UP to 15. Without it
        # the breakdown and the counter beside it in the same context pack
        # disagree by however many checks and chat turns the window held, and
        # a CEO reconciling them by hand is back to estimating.
        "runs_metered": 0,
        "runs_by_kind": {},
        "runs_by_outcome": {},
        # Why the dead ones died, keyed by cause in plain words. `runs_by_outcome`
        # says "failed"; a CEO cannot act on "failed", and reading it as "did
        # nothing" is how a seat that shipped five tasks in a night got proposed
        # for stand-down. See `deaths.py`.
        "died_of": {},
        "woke_for": {},
        "tokens_in": 0,
        "tokens_out": 0,
        "cache_creation": 0,
        "cache_read": 0,
        "usd": 0.0,
        "refusals": 0,
        "tasks_claimed": 0,
        "tasks_completed": 0,
        "tasks_killed": 0,
        "holding": 0,
        "last_run_at": None,
    }


def _woke_for(trigger: dict | None) -> list[str]:
    """Why the platform spent this run, in the words the trigger already uses.

    A run that woke for three unread notices and a commitment counts once under
    each, because the honest answer to "what was this run for" is often more
    than one thing and picking a winner would invent a fact.
    """
    if not isinstance(trigger, dict):
        return []
    kinds = [
        str(reason.get("kind"))
        for reason in trigger.get("reasons") or []
        if isinstance(reason, dict) and reason.get("kind")
    ]
    return kinds or [str(trigger.get("type") or "unknown")]


def roster_activity(
    session: Session,
    project: Project,
    start: datetime,
    end: datetime,
    *,
    agent_ids: list[str] | None = None,
) -> dict:
    """Per-seat activity for `[start, end)`.

    `agent_ids` narrows the answer to seats the caller is allowed to see; the
    project totals are narrowed with it, so a lead reading its own team is never
    handed a denominator it cannot account for.
    """
    seats: dict[str, dict] = {}
    order: list[Agent] = []
    for agent in session.execute(
        select(Agent)
        .where(Agent.project_id == project.id)
        .order_by(Agent.kind, Agent.created_at)
    ).scalars():
        if agent_ids is not None and agent.id not in agent_ids:
            continue
        order.append(agent)
        seats[agent.id] = _zero_seat()

    visible = set(seats)
    teams = {
        t.id: t
        for t in session.execute(select(Team).where(Team.project_id == project.id)).scalars()
    }

    # Runs: from the runs table, so a run that died before booking anything is
    # still counted against the seat whose slot it spent.
    runs_in_window = list(
        session.execute(
            select(Run).where(
                Run.project_id == project.id,
                Run.created_at >= start,
                Run.created_at < end,
            )
        ).scalars()
    )
    platform = _zero_seat()
    for run in runs_in_window:
        seat = seats.get(run.agent_id) if run.agent_id else None
        if seat is None:
            # Success and requirement checks carry no agent: the platform ran
            # them. They are part of the day's runs and belong in the answer.
            if run.agent_id and run.agent_id not in visible:
                continue
            seat = platform
        seat["runs"] += 1
        if run.kind not in UNMETERED_RUN_KINDS and run.metered:
            seat["runs_metered"] += 1
        seat["runs_by_kind"][run.kind] = seat["runs_by_kind"].get(run.kind, 0) + 1
        seat["runs_by_outcome"][run.status] = seat["runs_by_outcome"].get(run.status, 0) + 1
        if run.status in deaths.DEAD_STATUSES:
            died = seat["died_of"].setdefault(
                deaths.cause_of(run.status, run.summary), _zero_death())
            died["runs"] += 1
            if run.produced:
                died["produced_work_first"] += 1
        seat["refusals"] += run.refusal_count or 0
        for kind in _woke_for(run.trigger):
            seat["woke_for"][kind] = seat["woke_for"].get(kind, 0) + 1
        at = (run.finished_at or run.started_at or run.created_at).isoformat()
        if seat["last_run_at"] is None or at > seat["last_run_at"]:
            seat["last_run_at"] = at

    # Money: from the ledger, joined back to a seat through the run.
    entries = list(
        session.execute(
            select(LedgerEntry).where(
                LedgerEntry.project_id == project.id,
                LedgerEntry.at >= start,
                LedgerEntry.at < end,
                # Revenue is money coming IN — the same exclusion the meter,
                # the monthly wall and observe.spend apply.
                LedgerEntry.kind != LedgerKind.REVENUE,
            )
        ).scalars()
    )
    ledger_runs, _, _ = attribution_index(session, {e.run_id for e in entries if e.run_id})
    unattributed = _zero_seat()
    for entry in entries:
        run = ledger_runs.get(entry.run_id) if entry.run_id else None
        seat = seats.get(run.agent_id) if run and run.agent_id else None
        if seat is None:
            if run and run.agent_id and run.agent_id not in visible:
                continue
            seat = platform if run is not None else unattributed
        seat["tokens_in"] += entry.tokens_in
        seat["tokens_out"] += entry.tokens_out
        seat["cache_creation"] += entry.cache_creation_tokens
        seat["cache_read"] += entry.cache_read_tokens
        seat["usd"] += entry.usd or 0.0
        # What the deaths cost, taken from the ledger like every other token in
        # this payload. Summing `runs.tokens_*` instead would be a second
        # definition of tokens in the same answer — the thing this module's
        # split exists to avoid.
        if run is not None and run.status in deaths.DEAD_STATUSES:
            died = seat["died_of"].setdefault(
                deaths.cause_of(run.status, run.summary), _zero_death())
            died["tokens"] += (entry.tokens_in + entry.tokens_out
                               + entry.cache_creation_tokens + entry.cache_read_tokens)

    _count_tasks(session, project, start, end, seats)

    rows = []
    for agent in order:
        seat = seats[agent.id]
        team = teams.get(agent.team_id) if agent.team_id else None
        rows.append({
            **seat_row(
                agent,
                team=team,
                role_slug=_role_slug(
                    session,
                    team.role_version_id if team else agent.role_version_id,
                ),
            ),
            **seat,
        })
    rows.sort(key=lambda r: (-r["runs"], r["name"]))

    if platform["runs"] or platform["usd"]:
        rows.append({**_pseudo_seat("(platform checks)", "platform"), **platform})
    if unattributed["usd"] or unattributed["tokens_in"] or unattributed["tokens_out"]:
        rows.append({**_pseudo_seat("(unattributed)", "unattributed"), **unattributed})

    totals = _zero_seat()
    for row in rows:
        for key in ("runs", "runs_metered", "tokens_in", "tokens_out",
                    "cache_creation", "cache_read",
                    "usd", "refusals", "tasks_claimed", "tasks_completed",
                    "tasks_killed", "holding"):
            totals[key] += row[key]
        for key in ("runs_by_kind", "runs_by_outcome", "woke_for"):
            for name, n in row[key].items():
                totals[key][name] = totals[key].get(name, 0) + n
        for cause, bucket in row["died_of"].items():
            into = totals["died_of"].setdefault(cause, _zero_death())
            for key in ("runs", "tokens", "produced_work_first"):
                into[key] += bucket[key]
    totals.pop("last_run_at", None)

    return {
        "since": start.isoformat(),
        "until": end.isoformat(),
        "seats": rows,
        "totals": totals,
    }


def _count_tasks(
    session: Session, project: Project, start: datetime, end: datetime, seats: dict
) -> None:
    """Work each seat moved in the window.

    Completions are read from `tasks.claimed_by_agent_id`, never from a
    `task_events` row with `to_status='done'` — the actor on that row is
    `checker`, the platform, for every command-tested task. Counting the event's
    actor would credit every completion in the project to a machine.
    """
    for event in session.execute(
        select(TaskEvent).where(
            TaskEvent.project_id == project.id,
            TaskEvent.at >= start,
            TaskEvent.at < end,
        )
    ).scalars():
        actor = event.actor or ""
        if not actor.startswith("agent:"):
            continue
        seat = seats.get(actor.split(":", 1)[1])
        if seat is None:
            continue
        if event.to_status == TaskStatus.CLAIMED:
            seat["tasks_claimed"] += 1
        elif event.to_status == TaskStatus.KILLED:
            seat["tasks_killed"] += 1

    for task in session.execute(
        select(Task).where(
            Task.project_id == project.id,
            Task.claimed_by_agent_id.is_not(None),
        )
    ).scalars():
        seat = seats.get(task.claimed_by_agent_id)
        if seat is None:
            continue
        if task.status == TaskStatus.DONE and start <= task.updated_at < end:
            seat["tasks_completed"] += 1
        elif task.status in (TaskStatus.CLAIMED, TaskStatus.CHECKING, TaskStatus.REVIEW):
            seat["holding"] += 1
