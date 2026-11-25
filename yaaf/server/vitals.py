"""A project's vital signs, as series a page can draw.

The project home used to answer "how is it going?" in sentences — "Out of runs
for today (40 of 40); work resumes Wed 25 Nov, 7:00 pm EST. 1 task(s) waiting"
— beside a token count printed to the unit. Every fact in that sentence is a
number with a limit, and a number with a limit is a bar, not a clause. The
chairman who asked for this said the page was too wordy to tell what was going
on, and he was right: he had to read the whole sentence to learn the one thing
a glance at a full bar says.

So this reads what the dispatcher, the ledger and the plan meter already
recorded and hands it back as series:

* **today** — work runs per clock hour since the 00:00 UTC refill, against the
  hourly and daily limits;
* **limits** — for the last fourteen days, how many hours each limit held the
  project back. This is the question nobody could answer from inside a
  project: on one install the per-agent wake ceiling was reached on 49% of
  days, the hourly ceiling on 29% and the daily one on 9%, and the screen
  only ever showed whichever was binding at the moment it was opened;
* **published** — results published per day over the same fortnight, the
  rhythm an Operate project is judged on;
* **runs** — the last seven days by outcome, because 16% of runs failing is
  why the daily allowance drains faster than the work moves;
* **spend** — this month by day, with a run-rate projection;
* **plan** — the shared Claude subscription's windows, and which projects are
  drawing on it, because a neighbour emptying the five-hour window makes this
  project look broken when it is not;
* **you** — the chairman as a limit: open questions against their cap, and
  what expired waiting for an answer.

Nothing here decides anything and nothing is written: the page that reads it
must never move governance state, the same rule `activity_state` keeps. The
counts reuse the dispatcher's own run-kind sets, so "runs today" on the chart
is the number that stopped the work, not a near neighbour of it.

The limit history is *reconstructed* from run rows, because the dispatcher
decides its gates thirty times a minute and keeps none of them. Two
approximations follow, and both lean towards under-reporting: the hourly
ceiling is a trailing hour, counted here on clock hours; and the limits are
today's, applied to the whole fortnight. Neither can invent a wall that was
not hit — a clock hour holding the hourly cap's worth of runs was at the cap.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.constitution import ConstitutionDoc
from yaaf.core.models import (
    Approval,
    ApprovalStatus,
    Artifact,
    Ask,
    AskStatus,
    Flag,
    FlagKind,
    FlagStatus,
    LedgerEntry,
    LedgerKind,
    Project,
    Run,
    RunKind,
    RunQueue,
    RunStatus,
    Secret,
    utcnow,
)
from yaaf.server.deaths import DEAD_STATUSES
from yaaf.server.dispatcher import (
    INBOX_RUN_KINDS,
    NON_WORK_RUN_KINDS,
    TALK_RUN_KINDS,
    day_start,
    run_walls,
)

#: How far back the limit grid looks. Two weeks shows a weekly rhythm twice,
#: which is the least that tells a habit from a bad day.
LIMIT_DAYS = 14

#: How far back the outcome bars look.
RUN_DAYS = 7

#: What a Claude plan says when it turns a run away. The same phrases
#: `deaths.CAUSES` reads; kept beside them in spirit, matched lowercased.
PLAN_REFUSALS = ("you've hit your 5-hour limit", "you've hit your weekly limit",
                 "you've hit your session limit", "rate_limit_error")

#: The ordinary runs a project does work in. Checks are the platform's, the
#: talk lane is the chairman's conversation; neither is the work a chairman is
#: asking about when he asks how the runs went.
WORK_KINDS = (RunKind.AGENT_RUN, RunKind.INBOX_RUN)


def _hour(at: datetime) -> datetime:
    return at.replace(minute=0, second=0, microsecond=0)


def _plan_refused(summary: str | None) -> bool:
    text = (summary or "").lower()
    return any(phrase in text for phrase in PLAN_REFUSALS)


def _spent(row, usd_mode: bool) -> float:
    if usd_mode:
        return float(row.usd or 0.0)
    return float((row.tokens_in or 0) + (row.tokens_out or 0)
                 + (row.cache_creation_tokens or 0) + (row.cache_read_tokens or 0))


def _today(session: Session, project: Project, cons: ConstitutionDoc, runs: list,
           now: datetime) -> dict:
    """Work runs per clock hour since the daily refill, counted the way
    `daily_runs_used` counts them — metered work-lane runs plus whatever is
    queued, which lands on the current hour because it has not run yet."""
    start = day_start(now)
    hours = [0] * 24
    for r in runs:
        if r.created_at >= start and r.kind not in NON_WORK_RUN_KINDS and r.metered:
            hours[int((r.created_at - start).total_seconds() // 3600)] += 1
    queued = session.execute(
        select(func.count(RunQueue.id)).where(RunQueue.project_id == project.id,
                                              RunQueue.kind.not_in(NON_WORK_RUN_KINDS),
                                              RunQueue.created_at >= start)
    ).scalar_one()
    current = int((now - start).total_seconds() // 3600)
    hours[current] += queued
    wall, reserved = run_walls(cons)
    return {
        "starts_at": start.isoformat(),
        "resets_at": (start + timedelta(days=1)).isoformat(),
        "current_hour": current,
        "hours": hours,
        "used": sum(hours),
        "daily_cap": cons.runs_per_project_per_day,
        "wall": wall,
        "reserved": reserved,
        "hourly_cap": cons.runs_per_project_per_hour,
    }


def _limits(cons: ConstitutionDoc, runs: list, now: datetime) -> dict:
    """Hours each limit held the project, per UTC day, oldest first.

    * the per-agent wake ceiling: a clock hour in which some agent was woken
      as many times as the ceiling allows — every further wake that hour waited;
    * the hourly ceiling: a clock hour holding that many work runs;
    * the daily wall: the hours from the run that reached it to midnight (or
      now), which is how long the project was stopped;
    * the inbox lane: the same, for reading;
    * the shared plan: hours in which Anthropic turned a run away.
    """
    today = day_start(now)
    days = [today - timedelta(days=LIMIT_DAYS - 1 - i) for i in range(LIMIT_DAYS)]
    index = {d: i for i, d in enumerate(days)}
    wall, _ = run_walls(cons)

    wakes: dict[tuple, int] = defaultdict(int)
    work_per_hour: dict[datetime, int] = defaultdict(int)
    work_per_day: dict[datetime, list] = defaultdict(list)
    inbox_per_day: dict[datetime, list] = defaultdict(list)
    plan_hours: set[datetime] = set()
    for r in runs:
        if r.kind not in TALK_RUN_KINDS and r.attempt == 1 and r.agent_id:
            wakes[(r.agent_id, _hour(r.created_at))] += 1
        if r.kind not in NON_WORK_RUN_KINDS and r.metered:
            work_per_hour[_hour(r.created_at)] += 1
            work_per_day[day_start(r.created_at)].append(r.created_at)
        if r.kind in INBOX_RUN_KINDS:
            inbox_per_day[day_start(r.created_at)].append(r.created_at)
        if r.status in DEAD_STATUSES and _plan_refused(r.summary):
            plan_hours.add(_hour(r.created_at))

    def hours_by_day(hours) -> list[int]:
        out = [0] * LIMIT_DAYS
        for h in hours:
            i = index.get(day_start(h))
            if i is not None:
                out[i] += 1
        return out

    def stopped_after(per_day: dict, cap: int) -> list[float]:
        out = [0.0] * LIMIT_DAYS
        if cap <= 0:
            return out
        for day, stamps in per_day.items():
            i = index.get(day)
            if i is None or len(stamps) < cap:
                continue
            reached = sorted(stamps)[cap - 1]
            end = min(day + timedelta(days=1), now)
            out[i] = round(max(0.0, (end - reached).total_seconds() / 3600), 1)
        return out

    wake_hours = {h for (_, h), n in wakes.items() if n >= cons.wakes_per_agent_per_hour}
    pace_hours = {h for h, n in work_per_hour.items() if n >= cons.runs_per_project_per_hour}
    rows = [
        {"key": "wake_ceiling", "label": "One agent's runs per hour",
         "means": f"each agent may run {cons.wakes_per_agent_per_hour} times an hour, "
                  "so a chain of tasks on one agent goes one step at a time",
         "hours": hours_by_day(wake_hours)},
        {"key": "pacing", "label": "Project runs per hour",
         "means": f"{cons.runs_per_project_per_hour} runs an hour across the project; "
                  "work stops until the hour clears",
         "hours": hours_by_day(pace_hours)},
        {"key": "daily_runs", "label": "Runs per day used up",
         "means": f"{wall} runs a day; nothing new starts until 00:00 UTC",
         "hours": stopped_after(work_per_day, wall)},
        {"key": "inbox_runs", "label": "Message reading used up",
         "means": f"{cons.inbox_runs_per_project_per_day} reads a day; mail waits "
                  "until 00:00 UTC",
         "hours": stopped_after(inbox_per_day, cons.inbox_runs_per_project_per_day)},
        {"key": "plan", "label": "Claude plan limit",
         "means": "the subscription's usage window was full and Claude turned runs away",
         "hours": hours_by_day(plan_hours)},
    ]
    return {"days": [d.date().isoformat() for d in days], "rows": rows}


def _published(session: Session, project: Project, now: datetime) -> dict:
    """Results published per UTC day, oldest first — an Operate project's
    rhythm made visible. A day with nothing published is the gap a chairman
    of a daily desk is looking for, and the one sentence never showed it."""
    today = day_start(now)
    since = today - timedelta(days=LIMIT_DAYS - 1)
    counts = [0] * LIMIT_DAYS
    for (at,) in session.execute(
            select(Artifact.published_at).where(Artifact.project_id == project.id,
                                                Artifact.published_at >= since)).all():
        i = (day_start(at) - since).days
        if 0 <= i < LIMIT_DAYS:
            counts[i] += 1
    return {"days": [(since + timedelta(days=i)).date().isoformat() for i in range(LIMIT_DAYS)],
            "counts": counts}


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def _runs(cons: ConstitutionDoc, runs: list, now: datetime) -> dict:
    """The last seven days by outcome, and how long a run takes."""
    today = day_start(now)
    days = [today - timedelta(days=RUN_DAYS - 1 - i) for i in range(RUN_DAYS)]
    index = {d: i for i, d in enumerate(days)}
    # Counted runs are the ones the daily limit counts (metered, work lane),
    # split by how they ended; given-back runs and message reads are kept
    # apart so a bar of this chart can never pass the limit it is drawn under.
    out = [{"day": d.date().isoformat(), "finished": 0, "failed": 0, "given_back": 0, "reads": 0}
           for d in days]
    durations: list[float] = []
    timed_out = 0
    for r in runs:
        if r.kind not in WORK_KINDS:
            continue
        i = index.get(day_start(r.created_at))
        if i is not None:
            if r.kind in INBOX_RUN_KINDS:
                out[i]["reads"] += 1
            elif not r.metered:
                out[i]["given_back"] += 1
            else:
                out[i]["failed" if r.status in DEAD_STATUSES else "finished"] += 1
        if r.started_at and r.finished_at and r.status in (RunStatus.DONE, *DEAD_STATUSES):
            seconds = (r.finished_at - r.started_at).total_seconds()
            durations.append(seconds)
            if seconds >= cons.run_timeout_s * 0.98:
                timed_out += 1
    ended = sum(d["finished"] + d["failed"] for d in out)
    failed = sum(d["failed"] for d in out)
    return {
        "days": out,
        "failure_share": round(failed / ended, 3) if ended else None,
        "p50_s": _percentile(durations, 0.5),
        "p95_s": _percentile(durations, 0.95),
        "timeout_s": cons.run_timeout_s,
        "timed_out": timed_out,
        "at_once": cons.max_concurrent_runs,
    }


def _spend(session: Session, project: Project, cons: ConstitutionDoc, now: datetime) -> dict:
    """This month by UTC day, in the unit the budget is kept in, with the
    straight-line projection the budget tile states in words."""
    usd_mode = project.billing_mode == "api_key"
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = session.execute(
        select(LedgerEntry.at, LedgerEntry.usd, LedgerEntry.tokens_in, LedgerEntry.tokens_out,
               LedgerEntry.cache_creation_tokens, LedgerEntry.cache_read_tokens)
        .where(LedgerEntry.project_id == project.id, LedgerEntry.at >= month_start,
               LedgerEntry.kind != LedgerKind.REVENUE)
    ).all()
    length = calendar.monthrange(now.year, now.month)[1]
    per_day = [0.0] * length
    for row in rows:
        per_day[row.at.day - 1] += _spent(row, usd_mode)
    spent = sum(per_day)
    elapsed_days = max((now - month_start).total_seconds() / 86400, 1 / 24)
    cap = float(cons.monthly_usd_cap if usd_mode else cons.monthly_token_cap)
    return {
        "unit": "usd" if usd_mode else "tokens",
        "month": month_start.date().isoformat(),
        "days": [round(v, 4) for v in per_day[: now.day]],
        "spent": round(spent, 4),
        "cap": cap,
        "warn_at": round(cap * cons.budget_warn_ratio, 4),
        "pauses_at": round(cap * cons.hard_stop_multiple, 4),
        "projected": round(spent / elapsed_days * length, 4),
    }


def plan_print(session: Session, project: Project) -> tuple[str, str] | None:
    """(fingerprint, token) of the metered Claude plan paying this project, or
    None when it pays some other way. The meter is filed under the fingerprint."""
    from yaaf.core import plan_usage
    from yaaf.core.crypto import decrypt
    from yaaf.runtimes.providers import DEFAULT_PROVIDER, PROVIDERS

    if not project.billing_secret_id:
        return None
    secret = session.get(Secret, project.billing_secret_id)
    if secret is None or secret.llm_mode != "subscription":
        return None
    seller = PROVIDERS.get(secret.provider or DEFAULT_PROVIDER)
    if seller is None or not seller.plan_meter:
        return None
    try:
        token = decrypt(secret.ciphertext)
    except Exception:
        return None
    return plan_usage.fingerprint(token), token


def _plan(session: Session, project: Project, readable: set[str], now: datetime,
          runs: list) -> dict | None:
    """The subscription behind this project, if it is one with a meter.

    Projects are named only when the reader can open them: a co-chair of this
    project has no business learning what else the install runs, so the rest
    are summed into one share. Matched by token fingerprint, the identity the
    meter itself is filed under — several credential rows can be one plan.
    """
    from yaaf.core import plan_usage
    from yaaf.core.crypto import decrypt

    found = plan_print(session, project)
    if found is None:
        return None
    mine, token = found
    reading = plan_usage.stored(session, token)

    # Every project paying with the same plan, found by fingerprint. Decrypting
    # is per distinct credential row, and an install has a handful.
    prints: dict[str, str | None] = {}
    sharing: list[Project] = []
    for other in session.execute(
            select(Project).where(Project.billing_secret_id.is_not(None),
                                  Project.status.in_(("running", "paused")))).scalars():
        sid = other.billing_secret_id
        if sid not in prints:
            row = session.get(Secret, sid)
            try:
                prints[sid] = plan_usage.fingerprint(decrypt(row.ciphertext)) if row else None
            except Exception:
                prints[sid] = None
        if prints[sid] == mine or other.id == project.id:
            sharing.append(other)

    day_ago = now - timedelta(hours=24)
    used = dict(session.execute(
        select(LedgerEntry.project_id,
               func.coalesce(func.sum(LedgerEntry.tokens_in + LedgerEntry.tokens_out
                                      + LedgerEntry.cache_creation_tokens
                                      + LedgerEntry.cache_read_tokens), 0))
        .where(LedgerEntry.project_id.in_([p.id for p in sharing]),
               LedgerEntry.at >= day_ago, LedgerEntry.kind != LedgerKind.REVENUE)
        .group_by(LedgerEntry.project_id)
    ).all())
    total = sum(int(v) for v in used.values())
    shares, others, others_n = [], 0, 0
    for p in sorted(sharing, key=lambda p: -int(used.get(p.id, 0))):
        n = int(used.get(p.id, 0))
        if p.id == project.id or p.id in readable:
            shares.append({"project_id": p.id, "name": p.name, "tokens": n,
                           "share": round(n / total, 3) if total else 0.0,
                           "this": p.id == project.id})
        else:
            others += n
            others_n += 1
    week_ago = now - timedelta(days=7)
    return {
        "available": bool(reading and reading.get("available")),
        "status": reading.get("status") if reading else None,
        "fetched_at": reading.get("fetched_at") if reading else None,
        "windows": [w for w in (reading or {}).get("windows", [])
                    if w.get("key") in ("five_hour", "seven_day")],
        "projects": shares,
        "others": {"count": others_n, "tokens": others,
                   "share": round(others / total, 3) if total else 0.0},
        "refused_7d": sum(1 for r in runs if r.created_at >= week_ago
                          and r.status in DEAD_STATUSES and _plan_refused(r.summary)),
    }


def _you(session: Session, project: Project, cons: ConstitutionDoc, now: datetime) -> dict:
    """The chairman as a limit. Nothing here is a cap the project hits; all of
    it is work that waited on a person, and an expired approval is work refused
    because nobody answered."""
    month = now - timedelta(days=30)
    open_asks = session.execute(
        select(func.count(Ask.id)).where(Ask.project_id == project.id,
                                         Ask.status == AskStatus.OPEN)
    ).scalar_one()
    expired = session.execute(
        select(func.count(Approval.id)).where(Approval.project_id == project.id,
                                              Approval.status == ApprovalStatus.EXPIRED,
                                              Approval.expires_at >= month)
    ).scalar_one()
    lapsed = session.execute(
        select(func.count(Ask.id)).where(Ask.project_id == project.id,
                                         Ask.status == AskStatus.LAPSED,
                                         Ask.answer_by >= month)
    ).scalar_one()
    decided = session.execute(
        select(Approval.created_at, Approval.decided_at)
        .where(Approval.project_id == project.id, Approval.decided_by_user_id.is_not(None),
               Approval.decided_at.is_not(None), Approval.decided_at >= month)
    ).all()
    waits = [(d.decided_at - d.created_at).total_seconds() / 3600 for d in decided]
    overruns = session.execute(
        select(func.count(Flag.id)).where(
            Flag.project_id == project.id,
            Flag.kind.in_((FlagKind.BUDGET_OVERRUN, FlagKind.RUN_OVERRUN)),
            Flag.status != FlagStatus.RESOLVED,
            Flag.acknowledged_at.is_(None))
    ).scalar_one()
    median = _percentile(waits, 0.5)
    return {
        "open_asks": open_asks,
        "ask_cap": cons.max_open_asks,
        "approvals_expired_30d": expired,
        "asks_lapsed_30d": lapsed,
        "reply_hours_median": round(median, 1) if median is not None else None,
        "overruns_unanswered": overruns,
    }


def vitals(session: Session, project: Project, cons: ConstitutionDoc,
           readable: set[str], now: datetime | None = None) -> dict:
    now = now or utcnow()
    since = day_start(now) - timedelta(days=LIMIT_DAYS - 1)
    runs = session.execute(
        select(Run.created_at, Run.kind, Run.metered, Run.status, Run.agent_id, Run.attempt,
               Run.summary, Run.started_at, Run.finished_at)
        .where(Run.project_id == project.id, Run.created_at >= since)
    ).all()
    return {
        "as_of": now.isoformat(),
        "today": _today(session, project, cons, runs, now),
        "limits": _limits(cons, runs, now),
        "runs": _runs(cons, runs, now),
        "published": _published(session, project, now),
        "spend": _spend(session, project, cons, now),
        "plan": _plan(session, project, readable, now, runs),
        "you": _you(session, project, cons, now),
    }
