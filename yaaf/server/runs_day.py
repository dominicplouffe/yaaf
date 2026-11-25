"""The Runs page: what used today's runs, who ran them, why, and whether that
is normal.

The chairman who asked for this had three screens telling him three numbers
about one day. The Inbox said 40 of 40. The Runs page said 29. A chart said 42,
above a line at 40. All three were honest about something different:

* the limit's day starts at 00:00 UTC — 7 pm the evening before, in his zone —
  and counts only metered work-lane runs (`dispatcher.daily_runs_used`);
* the Runs page counted from his browser's midnight, everything it was sent
  (chat, checks, reads, refunds), out of the newest 50 rows;
* the chart stacked the uncounted lanes on top of the counted one.

So this module fixes the day and the rule once — the limit's own — and files
every run started in it into exactly one lane: counted, given back (it woke
and changed nothing, so it was refunded), a message read (its own allowance),
a platform check, or the chat with the chairman. The lanes add up to every run
started, and "counted" is the number that stopped the work.

On top of that it answers the questions a reader brings to a full bar: who,
why each one woke (from the trigger the dispatcher stored, in words), how the
day compares with the fortnight before it, and what went wrong. The sentence
at the top is assembled here from the same numbers, never by the CEO, so it
cannot be more cheerful than the arithmetic.

Read-only, like `activity_state` and `vitals`.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.constitution import ConstitutionDoc
from yaaf.core.models import (
    Agent,
    AgentKind,
    AuditLog,
    Channel,
    Project,
    Run,
    RunKind,
    RunQueue,
    RunStatus,
    Task,
    Team,
    utcnow,
)
from yaaf.server.deaths import DEAD_STATUSES, cause_of
from yaaf.server.dispatcher import (
    INBOX_RUN_KINDS,
    NON_WORK_RUN_KINDS,
    TALK_RUN_KINDS,
    day_start,
    run_walls,
)

#: Days of history "usual" is the middle of. Two weeks holds a weekly rhythm
#: twice; the median keeps one bad day from moving it.
HISTORY_DAYS = 14

#: Why a run woke, as the reader would say it. The dispatcher's reason kinds
#: on the left; one family each, in the order a run is filed under when it
#: carries several (the work it did before the chatter that came with it).
REASON_FAMILY = {
    "claimed_task": "task", "team_task_ready": "task", "assignment_ready": "task",
    "review_owed": "review", "review": "review", "review_reply": "review",
    "notices": "notice",
    "messages": "msg",
    "heartbeat": "plan", "daily": "plan", "cycle_plan": "plan",
    "commitment": "other", "ask_lapsed": "other",
}
FAMILY_ORDER = ["task", "review", "notice", "msg", "plan", "other"]
FAMILIES = {
    "task": ("Task work", "picked up or continued a task"),
    "msg": ("Messages", "a message from you or a teammate needed an answer"),
    "plan": ("Planning check-in", "the CEO's timed wake-up to look at the plan"),
    "notice": ("Handed work", "work was handed over or a task became ready"),
    "review": ("Review", "a finished piece, or a status meeting, was waiting to be read"),
    "other": ("Something came due", "a delivery date or an unanswered question"),
}

#: What Claude says when the plan turns a run away (the phrases deaths.CAUSES
#: reads), lowercased.
PLAN_REFUSALS = ("you've hit your 5-hour limit", "you've hit your weekly limit",
                 "you've hit your session limit", "rate_limit_error")


def _lane(run) -> str:
    if run.kind in TALK_RUN_KINDS:
        return "chat"
    if run.kind in (RunKind.SUCCESS_CHECK, RunKind.REQUIREMENT_CHECK):
        return "check"
    if run.kind in INBOX_RUN_KINDS:
        return "read"
    return "counted" if run.metered else "given_back"


def _reasons(trigger) -> list[dict]:
    if isinstance(trigger, dict) and isinstance(trigger.get("reasons"), list):
        return [r for r in trigger["reasons"] if isinstance(r, dict)]
    return []


def _family(trigger) -> str:
    kinds = {REASON_FAMILY.get(r.get("kind"), "other") for r in _reasons(trigger)}
    return next((f for f in FAMILY_ORDER if f in kinds), "other")


def _plan_refused(summary: str | None) -> bool:
    text = (summary or "").lower()
    return any(p in text for p in PLAN_REFUSALS)


def _short_end(status: str, summary: str | None, paused: bool) -> str:
    """A few words for a run that ended early; `cause_of` has the sentence."""
    text = (summary or "").lower()
    if status == RunStatus.KILLED:
        return "Stopped: you paused" if paused else "Stopped"
    if status == RunStatus.REAPED:
        return "Lost: no report"
    if status == RunStatus.TERMINATED_REFUSALS:
        return "Stopped: too many refusals"
    if _plan_refused(summary):
        return "Turned away by Claude"
    if "exit 137" in text or "exited 137" in text:
        return "Failed: ran out of memory"
    if "exit 124" in text or "exited 124" in text:
        return "Failed: timed out"
    if "maximum budget" in text:
        return "Failed: hit its budget"
    if "maximum number of turns" in text:
        return "Failed: too much for one run"
    return "Failed"


def _median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _seconds(run) -> float | None:
    if run.started_at and run.finished_at:
        return (run.finished_at - run.started_at).total_seconds()
    return None


def _tokens(run) -> int:
    return int((run.tokens_in or 0) + (run.tokens_out or 0)
               + (run.cache_creation_tokens or 0) + (run.cache_read_tokens or 0))


def _clock(at: datetime, zone: ZoneInfo) -> str:
    local = at.astimezone(zone)
    return local.strftime("%-I:%M %p").lower().replace(" ", " ")


def _names(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def run_day(session: Session, project: Project, cons: ConstitutionDoc,
            plan_print: str | None = None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    start = day_start(now)
    ends = start + timedelta(days=1)
    since = start - timedelta(days=HISTORY_DAYS)
    wall, _ = run_walls(cons)
    try:
        zone = ZoneInfo(cons.timezone)
    except Exception:
        zone = ZoneInfo("UTC")

    rows = list(session.execute(
        select(Run).where(Run.project_id == project.id, Run.created_at >= since)
        .order_by(Run.created_at)).scalars())
    today = [r for r in rows if r.created_at >= start]
    queued = session.execute(
        select(RunQueue).where(RunQueue.project_id == project.id,
                               RunQueue.created_at >= start)).scalars().all()
    queued_work = [q for q in queued if q.kind not in NON_WORK_RUN_KINDS]

    agents = {a.id: a for a in session.execute(
        select(Agent).where(Agent.project_id == project.id)).scalars()}
    teams = {t.id: t.name for t in session.execute(
        select(Team).where(Team.project_id == project.id)).scalars()}
    task_ids = {r.get("task_id") for run in today for r in _reasons(run.trigger) if r.get("task_id")}
    titles = dict(session.execute(
        select(Task.id, Task.title).where(Task.id.in_(task_ids))).all()) if task_ids else {}
    channels = {c.id: c for c in session.execute(
        select(Channel).where(Channel.project_id == project.id)).scalars()}
    pauses = [{"at": a.at.isoformat(), "action": a.action.removeprefix("project.")}
              for a in session.execute(
                  select(AuditLog).where(AuditLog.project_id == project.id,
                                         AuditLog.action.in_(("project.pause", "project.start",
                                                              "project.stop")),
                                         AuditLog.at >= start)
                  .order_by(AuditLog.at)).scalars()]
    pause_times = [datetime.fromisoformat(p["at"]) for p in pauses if p["action"] == "pause"]

    def name(agent_id: str | None) -> str:
        agent = agents.get(agent_id) if agent_id else None
        return agent.name if agent else "Platform"

    def why(run) -> str:
        lane = _lane(run)
        if lane == "chat":
            return "Chat with you"
        if lane == "check":
            return "Platform check"
        if lane == "read":
            return "Reading messages"
        family = _family(run.trigger)
        for r in _reasons(run.trigger):
            if REASON_FAMILY.get(r.get("kind")) != family:
                continue
            title = titles.get(r.get("task_id")) if r.get("task_id") else None
            if family == "task":
                return f"Task: {title}" if title else "Task work"
            if family == "review":
                return f"Review owed: {title}" if title else "Status meeting"
            if family == "notice":
                return "Work handed over"
            if family == "msg":
                channel = channels.get(r.get("channel_id"))
                if channel is not None and channel.kind == "boardroom":
                    return "Message in the boardroom"
                if channel is not None and channel.team_id in teams:
                    return f"Message in {teams[channel.team_id]}'s channel"
                return "Messages"
            if family == "plan":
                return "Planning check-in" if r.get("kind") == "heartbeat" else "Daily check-in"
            return "Delivery date check" if r.get("kind") == "commitment" else "A question to you timed out"
        return FAMILIES[family][0]

    def result(run) -> tuple[str, str]:
        if run.status == RunStatus.RUNNING:
            return "Running", "running"
        if run.status == RunStatus.DONE:
            return ("Finished", "ok") if run.metered or _lane(run) != "given_back" else ("Changed nothing", "given")
        if run.status in DEAD_STATUSES:
            paused = any(run.started_at and run.started_at <= t <= (run.finished_at or now)
                         for t in pause_times)
            return _short_end(run.status, run.summary, paused), "bad"
        return run.status.replace("_", " ").capitalize(), "running"

    # ── the one count ──────────────────────────────────────────────────────────
    lanes = Counter(_lane(r) for r in today)
    counted_runs = sorted((r for r in today if _lane(r) == "counted"), key=lambda r: r.created_at)
    counted = len(counted_runs) + len(queued_work)
    reached_at = (counted_runs[wall - 1].created_at.isoformat()
                  if wall and len(counted_runs) >= wall else None)

    # ── who and why, today ─────────────────────────────────────────────────────
    by_agent: dict[str, Counter] = defaultdict(Counter)
    for r in counted_runs:
        by_agent[r.agent_id or ""][_family(r.trigger)] += 1
    by_reason = Counter(_family(r.trigger) for r in counted_runs)

    # ── usual: the median of the days before today ─────────────────────────────
    days = [start - timedelta(days=HISTORY_DAYS - 1 - i) for i in range(HISTORY_DAYS)]
    per_day: dict[datetime, list] = defaultdict(list)
    for r in rows:
        per_day[day_start(r.created_at)].append(r)
    history_days = [start - timedelta(days=i) for i in range(1, HISTORY_DAYS + 1)]

    def usual(fn) -> float:
        return round(_median([sum(1 for r in per_day.get(d, []) if fn(r)) for d in history_days]), 1)

    usual_total = usual(lambda r: _lane(r) == "counted")
    usual_agent = {aid: usual(lambda r, aid=aid: _lane(r) == "counted" and (r.agent_id or "") == aid)
                   for aid in set(by_agent) | set(agents)}
    usual_reason = {f: usual(lambda r, f=f: _lane(r) == "counted" and _family(r.trigger) == f)
                    for f in FAMILY_ORDER}
    usual_given = usual(lambda r: _lane(r) == "given_back")
    series = [{"day": d.date().isoformat(),
               "counted": sum(1 for r in per_day.get(d, []) if _lane(r) == "counted")
               + (len(queued_work) if d == start else 0)}
              for d in days]
    last_run = {}
    for r in rows:
        if r.agent_id:
            last_run[r.agent_id] = r.created_at

    who = []
    for aid, agent in agents.items():
        if agent.kind == AgentKind.CEO:
            role = "runs the project"
        else:
            role = teams.get(agent.team_id, "") if agent.team_id else ""
        reasons = dict(by_agent.get(aid, {}))
        seen = last_run.get(aid)
        who.append({"agent_id": aid, "name": agent.name, "role": role,
                    "status": agent.status, "kind": agent.kind,
                    "counted": sum(reasons.values()), "reasons": reasons,
                    "usual": usual_agent.get(aid, 0.0),
                    "days_since_run": (now - seen).days if seen else None})
    who.sort(key=lambda w: (-w["counted"], w["kind"] != AgentKind.CEO, w["name"]))

    # ── anything wrong ─────────────────────────────────────────────────────────
    given = [r for r in today if _lane(r) == "given_back"]
    work_like = [r for r in today if _lane(r) in ("counted", "given_back", "read")]
    early = [r for r in work_like if r.status in DEAD_STATUSES]
    early_groups: dict[str, list] = defaultdict(list)
    for r in early:
        early_groups[result(r)[0]].append(r)
    durations = [s for r in work_like if (s := _seconds(r)) is not None and r.status != RunStatus.RUNNING]
    longest = max((r for r in work_like if _seconds(r) is not None), key=_seconds, default=None)
    refused = [r for r in today if r.status in DEAD_STATUSES and _plan_refused(r.summary)]
    peak = None
    if plan_print:
        from yaaf.core import plan_usage

        readings = plan_usage.history(session, plan_print, start)
        shares = [h["five_hour"] for h in readings if h["five_hour"] is not None]
        peak = max(shares) if shares else None
    problems = {
        "given_back": {
            "count": len(given), "tokens": sum(_tokens(r) for r in given),
            "by_agent": dict(Counter(name(r.agent_id) for r in given)),
            "why": dict(Counter(why(r) for r in given)),
        },
        "ended_early": {
            "count": len(early),
            "causes": [{"label": label, "count": len(rs),
                        "agents": dict(Counter(name(r.agent_id) for r in rs)),
                        "detail": cause_of(rs[0].status, rs[0].summary),
                        "retried_ok": any(o.attempt > 1 and o.status == RunStatus.DONE
                                          and o.prior_run_id in {x.id for x in rs} for o in today)}
                       for label, rs in sorted(early_groups.items(), key=lambda kv: -len(kv[1]))],
        },
        "plan_refused": {"count": len(refused), "peak_five_hour": peak,
                         "metered": plan_print is not None},
        "timeouts": {
            "count": sum(1 for s in durations if s >= cons.run_timeout_s * 0.98),
            "timeout_s": cons.run_timeout_s,
            "longest_s": _seconds(longest) if longest else None,
            "longest_by": name(longest.agent_id) if longest else None,
            "typical_s": _median(durations) if durations else None,
        },
    }

    # ── the sentence ───────────────────────────────────────────────────────────
    story: list[dict] = []

    def say(text: str, strong: bool = False) -> None:
        story.append({"text": text, "strong": strong})

    before = [s["counted"] for s in series[-3:-1]]
    if usual_total >= 2 and len(before) == 2 and all(b < usual_total / 2 for b in before):
        words = ["none" if b == 0 else f"{b} run{'s' if b != 1 else ''}" for b in before]
        labels = [(start - timedelta(days=2 - i)).astimezone(zone).strftime("%A") for i in range(2)]
        say(f"After two quiet days ({words[0]} {labels[0]}, {words[1]} {labels[1]}), ")
    active = [w for w in who if w["counted"]]
    if not active:
        say("Nothing has counted against today's limit yet.")
    else:
        top = active[0]
        parts = sorted(top["reasons"].items(), key=lambda kv: -kv[1])[:2]
        detail = " and ".join(f"{n} {FAMILIES[f][0].lower()}" for f, n in parts)
        say(f"{top['name']} used ")
        say(f"{top['counted']} run{'s' if top['counted'] != 1 else ''}", True)
        say(f": {detail}" + (f", against a usual {top['usual']:g} a day. " if top["usual"] else ". "))
        rest = active[1:]
        if rest:
            total = sum(w["counted"] for w in rest)
            work = [titles[r["task_id"]] for run in counted_runs if (run.agent_id or "") in
                    {w["agent_id"] for w in rest}
                    for r in _reasons(run.trigger) if r.get("task_id") in titles]
            common = [t for t, _ in Counter(work).most_common(2)]
            say(f"{_names([w['name'] for w in rest])} used ")
            say(str(total), True)
            say(f" on {_names([f'“{t}”' for t in common])}. " if common else ". ")
    if reached_at:
        say("The limit was reached at ")
        say(_clock(datetime.fromisoformat(reached_at), zone), True)
        say(f"; nothing counted starts again until {_clock(ends, zone)}.")

    ratio = counted / usual_total if usual_total else None
    if ratio is None:
        verdict = "new" if counted else "quiet"
    elif ratio >= 2:
        verdict = f"{ratio:.0f}× a normal day" if ratio >= 2.5 else f"{ratio:.1f}× a normal day"
    elif ratio < 0.5:
        verdict = "quieter than usual"
    else:
        verdict = "a normal day"

    log = []
    for r in sorted(today, key=lambda r: r.created_at, reverse=True):
        words, tone = result(r)
        log.append({
            "id": r.id, "agent_id": r.agent_id, "agent": name(r.agent_id),
            "lane": _lane(r), "reason": _family(r.trigger) if _lane(r) in ("counted", "given_back") else None,
            "why": why(r), "result": words, "tone": tone,
            "cause": cause_of(r.status, r.summary) if r.status in DEAD_STATUSES else None,
            "created_at": r.created_at.isoformat(),
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "seconds": _seconds(r), "tokens": _tokens(r),
            "has_trace": r.trace_path is not None, "has_context": r.context_path is not None,
        })

    return {
        "as_of": now.isoformat(),
        "timezone": cons.timezone,
        "day": {"starts_at": start.isoformat(), "ends_at": ends.isoformat()},
        "limit": {"wall": wall, "counted": counted, "reached_at": reached_at,
                  "queued": len(queued_work),
                  "at_once": cons.max_concurrent_runs,
                  "reads": lanes.get("read", 0), "reads_cap": cons.inbox_runs_per_project_per_day},
        "started": {"total": len(today) + len(queued_work), "counted": counted,
                    "given_back": lanes.get("given_back", 0), "reads": lanes.get("read", 0),
                    "checks": lanes.get("check", 0), "chat": lanes.get("chat", 0)},
        "verdict": verdict,
        "story": story,
        "families": {k: {"label": v[0], "means": v[1]} for k, v in FAMILIES.items()},
        "who": who,
        "by_reason": dict(by_reason),
        "usual": {"counted": usual_total, "by_reason": usual_reason, "given_back": usual_given},
        "days": series,
        "limit_days": sum(1 for s in series if wall and s["counted"] >= wall),
        "problems": problems,
        "pauses": pauses,
        "runs": log,
    }
