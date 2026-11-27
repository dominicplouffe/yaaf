"""A Code Factory's home page, as data: what needs the chairman, every change in
flight and what it is waiting on, what each seat is doing, how close the build
is to done, what it costs, and how the factory is doing.

Every figure is counted from rows the platform wrote — changes, their gates
and review events, releases, incidents, runs and the ledger — so the page is
the platform's account, not the crew's. Each number that summarises several
changes says how many it is based on, and is marked early below
`EARLY_BELOW`: four of five sent back is a fact, "80%" of five is a trend that
does not exist yet.

The shape follows what tools for supervising coding agents converged on: a
review queue sorted by what each change waits on (review became the
bottleneck once agents write the code), each agent's live state, delivery
numbers in speed/quality pairs (DORA plus review pickup and change size), and
cost per merged change against the budget.
"""

from __future__ import annotations

from datetime import UTC, timedelta
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.constitution import effective
from yaaf.core.models import (
    Agent,
    AgentKind,
    AgentStatus,
    AuditLog,
    Change,
    ChangeStatus,
    Cycle,
    CycleStatus,
    Incident,
    LedgerEntry,
    LedgerKind,
    Project,
    Release,
    ReleaseStatus,
    Requirement,
    RequirementStatus,
    Role,
    RoleVersion,
    Run,
    RunKind,
    Task,
    TaskStatus,
    Team,
    utcnow,
)
from yaaf.server import policy, upkeep
from yaaf.server.rows import requirement_name

#: Below this many changes (or reviews, or releases), a number is shown but
#: marked early.
EARLY_BELOW = 5

#: What a change in flight is waiting on, in the order the page lists them:
#: a person before a machine, and the chairman first of all.
WAITING_ORDER = ("you", "fix", "review", "tests", "checks", "ready", "merging")

#: Gates only the chairman's own yes can pass (changes.CHAIRMAN_GATES).
CHAIRMAN_GATES = ("protected", "ci_config")

#: Gates the platform or CI is still working out, rather than failing — the
#: gates' own wording (changes.py): "no check has reported", "running: …",
#: "has not scanned/measured/reported", "not yet run", "not up yet". "lacks" is
#: a runner without the scanners, which nobody on the change can fix either.
_RUNNING_WORDS = ("running", "not yet", "has reported", "has not", "lacks")


def _aware(at):
    return at if at is None or at.tzinfo else at.replace(tzinfo=UTC)


def _hours(a, b) -> float:
    return (_aware(b) - _aware(a)).total_seconds() / 3600


def _stat(values: list[float], digits: int = 1) -> dict:
    """A median with the count it rests on."""
    return {"value": round(median(values), digits) if values else None, "n": len(values),
            "early": len(values) < EARLY_BELOW}


def waiting_on(change: Change) -> str:
    """The one thing this change is waiting on now, read from its gates.

    Author fixes outrank everything: a change that fails something its author
    must change is waiting on the author whatever else is pending. Then the
    machines still running, then the hidden tests nobody has written, then a
    reader, then the chairman — who is asked only once nothing else stands in
    the way."""
    if change.status == ChangeStatus.READY:
        return "ready"
    if change.status == ChangeStatus.MERGING:
        return "merging"
    gates = change.gates or {}
    failing = {name: g for name, g in gates.items() if not g.get("ok")}
    if not failing:
        return "checks"  # gates not evaluated yet

    def running(g: dict) -> bool:
        detail = str(g.get("detail") or "").lower()
        return any(word in detail for word in _RUNNING_WORDS)

    if change.review_verdict == "returned" and change.reviewed_sha == change.head_sha:
        return "fix"
    if any(name not in (*CHAIRMAN_GATES, "review") and not running(g)
           and not (name == "scenarios" and "written" in str(g.get("detail")))
           for name, g in failing.items()):
        return "fix"
    if any(running(g) for g in failing.values()):
        return "checks"
    if "scenarios" in failing:
        return "tests"
    if "review" in failing:
        return "review"
    return "you"


def summary(session: Session, project: Project) -> dict:
    now = utcnow()
    month = now - timedelta(days=30)
    week = now - timedelta(days=7)
    day = now - timedelta(hours=24)
    cons = effective(session, project.id)

    changes = list(session.execute(
        select(Change).where(Change.project_id == project.id)).scalars())
    merged = [c for c in changes if c.status == ChangeStatus.MERGED and c.merged_at]
    merged_week = [c for c in merged if _aware(c.merged_at) >= week]
    merged_month = [c for c in merged if _aware(c.merged_at) >= month]
    live = [c for c in changes if c.status in (ChangeStatus.OPEN, ChangeStatus.READY,
                                               ChangeStatus.MERGING)]

    agents = list(session.execute(select(Agent).where(
        Agent.project_id == project.id)).scalars())
    names = {a.id: a.name for a in agents}

    # ── progress ──────────────────────────────────────────────────────────
    reqs = [r for r in session.execute(select(Requirement).where(
        Requirement.project_id == project.id)).scalars()
        if r.key != policy.PLAN_KEY and not upkeep.is_upkeep_promise(r)]
    accepted = [r for r in reqs if r.status not in (RequirementStatus.PROPOSED,
                                                    RequirementStatus.CUT)]
    proposed = [r for r in reqs if r.status == RequirementStatus.PROPOSED]
    met = [r for r in accepted if r.status == RequirementStatus.MET]
    working = [r for r in accepted if r.status in (RequirementStatus.IN_PROGRESS,
                                                   RequirementStatus.CHECKING)]
    by_req = {r.id: r for r in reqs}

    # ── releases and where the project is ─────────────────────────────────
    releases = list(session.execute(select(Release).where(
        Release.project_id == project.id)).scalars())
    ever_live = any(r.status in (ReleaseStatus.LIVE, ReleaseStatus.ROLLED_BACK)
                    for r in releases)
    in_flight_release = any(r.status in (ReleaseStatus.PROPOSED, ReleaseStatus.APPROVED,
                                         ReleaseStatus.DEPLOYING, ReleaseStatus.WATCHING)
                            for r in releases)
    if project.factory_phase == "upkeep":
        stage = "kept"
    elif ever_live:
        stage = "running"
    elif in_flight_release:
        stage = "first_release"
    elif not accepted and not changes:
        stage = "spec"
    else:
        stage = "build"

    # ── changes in flight ─────────────────────────────────────────────────
    in_flight = []
    for c in live:
        req = by_req.get(c.requirement_id)
        gates = c.gates or {}
        in_flight.append({
            "id": c.id, "number": c.number, "title": c.title,
            "author": names.get(c.author_agent_id, "a seat"),
            "requirement": ({"key": req.key, "name": requirement_name(req)} if req else None),
            "waiting_on": waiting_on(c),
            "lines": (c.lines_added or 0) + (c.lines_removed or 0),
            "added": c.lines_added or 0, "removed": c.lines_removed or 0,
            "gates_green": sum(1 for g in gates.values() if g.get("ok")),
            "gates_total": len(gates),
            "returns": c.returns or 0,
            "opened_at": _aware(c.created_at).isoformat(),
        })
    in_flight.sort(key=lambda r: (WAITING_ORDER.index(r["waiting_on"]), r["opened_at"]))
    counts = {k: sum(1 for r in in_flight if r["waiting_on"] == k) for k in WAITING_ORDER}

    # ── the crew ──────────────────────────────────────────────────────────
    role_of: dict[str, str] = {}
    for agent_id, role_name in session.execute(
        select(Agent.id, Role.name)
        .join(Team, Team.id == Agent.team_id)
        .join(RoleVersion, RoleVersion.id == Team.role_version_id)
        .join(Role, Role.id == RoleVersion.role_id)
        .where(Agent.project_id == project.id)
    ).all():
        role_of[agent_id] = role_name
    for agent_id, role_name in session.execute(
        select(Agent.id, Role.name)
        .join(RoleVersion, RoleVersion.id == Agent.role_version_id)
        .join(Role, Role.id == RoleVersion.role_id)
        .where(Agent.project_id == project.id, Agent.kind == AgentKind.CEO)
    ).all():
        role_of[agent_id] = role_name
    runs_day = list(session.execute(select(Run).where(
        Run.project_id == project.id, Run.kind != RunKind.CHAT_SESSION,
        Run.created_at >= day)).scalars())
    running = {r.agent_id: r for r in session.execute(select(Run).where(
        Run.project_id == project.id, Run.status == "running",
        Run.kind != RunKind.CHAT_SESSION)).scalars()}
    tasks = {t.id: t for t in session.execute(select(Task).where(
        Task.project_id == project.id,
        Task.status.in_(policy.OPEN_TASK_STATUSES))).scalars()}
    claimed = {}
    for t in tasks.values():
        if t.claimed_by_agent_id and t.status == TaskStatus.CLAIMED:
            claimed.setdefault(t.claimed_by_agent_id, t)
    crew = []
    for a in agents:
        if a.status != AgentStatus.ACTIVE:
            continue
        run = running.get(a.id)
        task = tasks.get(run.task_id) if run and run.task_id else claimed.get(a.id)
        mine = [r for r in runs_day if r.agent_id == a.id]
        crew.append({
            "id": a.id, "name": a.name, "lead": a.kind == AgentKind.CEO,
            "role": role_of.get(a.id),
            "state": "working" if run else "idle",
            "since": _aware(run.started_at or run.created_at).isoformat() if run else None,
            "on": task.title if task else None,
            "runs_today": len(mine),
            "tokens_today": sum((r.tokens_in or 0) + (r.tokens_out or 0) for r in mine),
        })
    crew.sort(key=lambda s: (not s["lead"], s["state"] != "working", s["name"]))

    # ── cost ──────────────────────────────────────────────────────────────
    from yaaf.server.budget import month_start

    start = month_start(now)
    usd, tokens_in, tokens_out = session.execute(
        select(func.coalesce(func.sum(LedgerEntry.usd), 0.0),
               func.coalesce(func.sum(LedgerEntry.tokens_in), 0),
               func.coalesce(func.sum(LedgerEntry.tokens_out), 0))
        .where(LedgerEntry.project_id == project.id, LedgerEntry.at >= start,
               LedgerEntry.kind != LedgerKind.REVENUE)).one()
    usd_mode = project.billing_mode == "api_key"
    month_runs = list(session.execute(select(Run).where(
        Run.project_id == project.id, Run.created_at >= start)).scalars())

    def run_usd(r: Run) -> float:
        return float(r.cost_usd or 0.0) if usd_mode else float(r.priced_usd or 0.0)

    est_usd = sum(run_usd(r) for r in month_runs)
    spent_usd = float(usd) if usd_mode else est_usd
    # The same total, said as what it is made of. A factory's coding key and
    # its billing credential may bill differently, and a subscription's runs
    # move no dollars: on a dollar budget they are counted at list price
    # (budget.counted), which is a number about the cap and not about the
    # invoice. The cap's arithmetic is untouched; this only names the halves,
    # the way every spend screen does (spend_rollup.project_split).
    from yaaf.server.spend_rollup import project_split

    split = project_split(session, project.id, start)
    billed_usd = split["billed_usd"]
    # On a token budget nothing on the plan is counted in dollars at all; the
    # list-price figure is the estimate the card already shows.
    by_plan = split["plan_usd"] if usd_mode else sum(
        run_usd(r) for r in month_runs if r.paid_in != "usd")
    per_seat: dict[str, float] = {}
    for r in month_runs:
        if r.agent_id:
            per_seat[r.agent_id] = per_seat.get(r.agent_id, 0.0) + run_usd(r)
    top = max(per_seat.items(), key=lambda kv: kv[1]) if per_seat else None
    merged_this_month = [c for c in merged if _aware(c.merged_at) >= start]
    cost = {
        "unit": "usd" if usd_mode else "tokens",
        "estimate": not usd_mode,
        "spent_usd": round(spent_usd, 2),
        # Dollars a provider actually billed, and what the subscription's runs
        # come to at list price. On a dollar budget the two sum to spent_usd.
        "billed_usd": round(billed_usd, 2),
        "subscription_usd": round(by_plan, 2),
        "tokens": int(tokens_in) + int(tokens_out),
        "cap": cons.monthly_usd_cap if usd_mode else cons.monthly_token_cap,
        "per_merged_change_usd": (round(spent_usd / len(merged_this_month), 2)
                                  if merged_this_month else None),
        # The same figure in billed dollars alone, for a reader asking what a
        # merge cost him rather than what it counted for.
        "per_merged_change_billed_usd": (round(billed_usd / len(merged_this_month), 2)
                                         if merged_this_month else None),
        "merged": len(merged_this_month),
        "top_seat": ({"name": names.get(top[0], "a seat"), "usd": round(top[1], 2),
                      "billed_usd": round(
                          split["by_agent"].get(top[0], {}).get("billed_usd", 0.0), 2)}
                     if top and top[1] > 0 else None),
    }

    # ── how the factory is doing ──────────────────────────────────────────
    reviewed_month = [c for c in changes if _aware(c.created_at) >= month
                      and (c.review_verdict or c.returns)]
    first_reads = {}
    for row in session.execute(select(AuditLog).where(
            AuditLog.project_id == project.id, AuditLog.action == "change.review",
            AuditLog.at >= month)).scalars():
        if row.entity_id not in first_reads or _aware(row.at) < first_reads[row.entity_id]:
            first_reads[row.entity_id] = _aware(row.at)
    opened = {c.id: c for c in changes}
    shipped = [r for r in releases if r.status in (ReleaseStatus.LIVE, ReleaseStatus.ROLLED_BACK)
               and r.deployed_at and _aware(r.deployed_at) >= month]
    rolled = [r for r in shipped if r.status == ReleaseStatus.ROLLED_BACK]
    recover = [_hours(i.opened_at, i.resolved_at) for i in session.execute(
        select(Incident).where(Incident.project_id == project.id,
                               Incident.resolved_at.is_not(None))).scalars()]
    numbers = {
        "time_to_merge_hours": _stat([_hours(c.created_at, c.merged_at)
                                      for c in merged_month if c.created_at]),
        "sent_back": {"count": sum(1 for c in reviewed_month if c.returns),
                      "n": len(reviewed_month), "early": len(reviewed_month) < EARLY_BELOW},
        "first_review_minutes": _stat([_hours(opened[cid].created_at, at) * 60
                                       for cid, at in first_reads.items() if cid in opened],
                                      digits=0),
        "change_lines": {**_stat([(c.lines_added or 0) + (c.lines_removed or 0)
                                  for c in changes if _aware(c.created_at) >= month
                                  and c.number is not None], digits=0),
                         "limit": cons.max_change_lines},
        "deploys": {"count": len(shipped), "early": len(shipped) < EARLY_BELOW},
        "rolled_back": {"count": len(rolled), "n": len(shipped),
                        "early": len(shipped) < EARLY_BELOW},
        "recover_hours": _stat(recover),
        "early_below": EARLY_BELOW,
    }

    # ── promises, once it is kept ─────────────────────────────────────────
    last_cycle = session.execute(select(Cycle).where(
        Cycle.project_id == project.id, Cycle.status != CycleStatus.OPEN)
        .order_by(Cycle.ordinal.desc())).scalars().first()
    standing = ((last_cycle.scorecard or {}).get("standing") or []) if last_cycle else []
    promise_keys = {r.key for r in session.execute(
        select(Requirement).where(Requirement.project_id == project.id)).scalars()
        if upkeep.is_upkeep_promise(r)}
    promises = [row for row in standing if row.get("key") in promise_keys]

    return {
        "phase": project.factory_phase,
        "stage": stage,
        "progress": {"accepted": len(accepted), "proposed": len(proposed), "met": len(met),
                     "working": len(working), "merged_week": len(merged_week),
                     "promises_kept": (sum(1 for row in promises
                                           if row.get("verdict") == "honoured")
                                       if promises else None),
                     "promises": len(promises)},
        "in_flight": in_flight,
        "waiting_counts": counts,
        "crew": crew,
        "cost": cost,
        "numbers": numbers,
    }
