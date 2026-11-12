"""The status meeting's first half: what was done, from the record.

The review used to open on six tiles — pace, met-since-last, commitment,
budget, audit coverage, worst offender. Those are the forecast's inputs and
the Spend page's month. A chairman who does not live in the project read
"11 a week · 4 still open" and "R-29 · 9 dead runs" and could not say what the
project had actually done that week, because nothing on the page said.

This module answers that at zero tokens, from things the platform wrote down
under its own rules: requirements it made true (and *how* it checked them),
requirements cut and why, deliverables published, spot-checks drawn, runs and
what they cost, the cycle that closed, the bets that lived and died. The CEO
explains it on the reserve run; it never writes it, for the same reason the
numbers are frozen at open — a status report an agent composes about its own
week is an agent grading its own homework.

The rules for what counts are the briefing's (`briefing.py`), re-cut to the
meeting's window: R-00 lifting is not scope landing, a standing obligation met
again is not progress, a deliverable revised four times is one deliverable.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.models import (
    Artifact,
    AuditSample,
    AuditSampleStatus,
    Cycle,
    Evidence,
    EvidenceKind,
    Hypothesis,
    HypothesisStatus,
    Project,
    ProjectMode,
    Recurrence,
    Requirement,
    RequirementEvent,
    RequirementStatus,
    TaskEvent,
    TaskStatus,
    VerificationStatus,
)
from yaaf.server import policy, scorecard, velocity

#: How much of a statement the record carries. The meeting lists things by
#: name; the Roadmap holds the whole text.
STATEMENT_LEN = 200


def compose(session: Session, project: Project, since: datetime, until: datetime) -> dict:
    """Everything the platform can say about the window (since, until] without
    asking anyone. Frozen onto the review row at open, like the numbers."""
    requirements = {
        r.id: r for r in session.execute(
            select(Requirement).where(Requirement.project_id == project.id)
        ).scalars()
    }
    plan = policy.plan_requirement(session, project.id)
    plan_id = plan.id if plan is not None else None
    standing = {rid for rid, r in requirements.items()
                if r.recurrence == Recurrence.STANDING}

    events = list(session.execute(
        select(RequirementEvent).where(
            RequirementEvent.project_id == project.id,
            RequirementEvent.at > since,
            RequirementEvent.at <= until,
        ).order_by(RequirementEvent.at)
    ).scalars())

    def first_seen(to_status: str, keep) -> list[tuple[Requirement, RequirementEvent]]:
        out: list[tuple[Requirement, RequirementEvent]] = []
        seen: set[str] = set()
        for event in events:
            if event.to_status != to_status or event.requirement_id in seen:
                continue
            requirement = requirements.get(event.requirement_id)
            if requirement is None or not keep(requirement):
                continue
            seen.add(event.requirement_id)
            out.append((requirement, event))
        return out

    def is_scope(r: Requirement) -> bool:
        return r.id != plan_id and r.id not in standing

    met = [
        {"key": r.key, "statement": r.statement[:STATEMENT_LEN], "how": checked_how(r)}
        for r, _ in first_seen(RequirementStatus.MET, is_scope)
    ]
    honoured = [
        {"key": r.key, "statement": r.statement[:STATEMENT_LEN]}
        for r, _ in first_seen(RequirementStatus.MET, lambda r: r.id in standing)
    ]
    cut = [
        {"key": r.key, "statement": r.statement[:STATEMENT_LEN], "reason": e.note}
        for r, e in first_seen(RequirementStatus.CUT, is_scope)
    ]

    # By document, not by row — the first sighting of a name, newest first,
    # is its latest version (the briefing's rule, for the briefing's reason).
    deliverables: list[dict] = []
    named: set[str] = set()
    for artifact in session.execute(
        select(Artifact).where(
            Artifact.project_id == project.id,
            Artifact.published_at > since,
            Artifact.published_at <= until,
        ).order_by(Artifact.published_at.desc())
    ).scalars():
        if artifact.name in named:
            continue
        named.add(artifact.name)
        deliverables.append({
            "name": artifact.name, "version": artifact.version,
            "requirements": [requirements[rid].key for rid in artifact.requirement_ids or []
                             if rid in requirements],
        })

    samples = session.execute(
        select(AuditSample.status, func.count(AuditSample.id)).where(
            AuditSample.project_id == project.id,
            AuditSample.drawn_at > since,
            AuditSample.drawn_at <= until,
        ).group_by(AuditSample.status)
    ).all()
    by_status = {status: int(n) for status, n in samples}
    considered = sum(by_status.values())
    spot_checks = {
        "considered": considered,
        "drawn": considered - by_status.get(AuditSampleStatus.SKIPPED, 0),
        "passed": by_status.get(AuditSampleStatus.PASSED, 0),
        "failed": by_status.get(AuditSampleStatus.FAILED, 0),
        "expired": by_status.get(AuditSampleStatus.EXPIRED, 0),
    }

    tasks_closed = session.execute(
        select(func.count(TaskEvent.id)).where(
            TaskEvent.project_id == project.id,
            TaskEvent.at > since,
            TaskEvent.at <= until,
            TaskEvent.to_status == TaskStatus.DONE,
        )
    ).scalar_one()

    runs = velocity.runs_since(session, project.id, since)

    cycle = session.execute(
        select(Cycle).where(
            Cycle.project_id == project.id,
            Cycle.closed_at.is_not(None),
            Cycle.closed_at > since,
            Cycle.closed_at <= until,
        ).order_by(Cycle.ordinal.desc()).limit(1)
    ).scalar_one_or_none()

    proof = session.execute(
        select(Evidence.verification, func.count(Evidence.id)).where(
            Evidence.project_id == project.id,
            Evidence.kind == EvidenceKind.EXTERNAL,
            Evidence.created_at > since,
            Evidence.created_at <= until,
        ).group_by(Evidence.verification)
    ).all()
    proof_by = {status: int(n) for status, n in proof}

    return {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "requirements_met": met,
        "standing_honoured": honoured,
        "requirements_cut": cut,
        "deliverables": deliverables,
        "spot_checks": spot_checks,
        "tasks_closed": int(tasks_closed),
        "runs": {"total": runs["total"], "dead": runs["dead"], "usd": runs["usd"]},
        "cycle": None if cycle is None else {
            "ordinal": cycle.ordinal, "status": cycle.status,
            "goal": (cycle.goal or "")[:STATEMENT_LEN] or None,
            **(cycle.scorecard or {}),
        },
        "bets": _bets(session, project, since, until),
        "evidence": {
            "verified": (proof_by.get(VerificationStatus.VERIFIED, 0)
                         + proof_by.get(VerificationStatus.NOT_APPLICABLE, 0)),
            "failed": proof_by.get(VerificationStatus.FAILED, 0),
            "pending": proof_by.get(VerificationStatus.PENDING, 0),
        },
    }


def checked_how(requirement: Requirement) -> str:
    """"verified" when the platform ran the acceptance test itself; "judged"
    when a person or the CEO looked at an observable. The same line the
    Deliverables tab draws, and never upgraded."""
    kind = (requirement.acceptance_test or {}).get("type")
    return "verified" if kind in policy.MACHINE_CHECKED_TESTS else "judged"


def _bets(session: Session, project: Project, since: datetime, until: datetime) -> dict:
    """Explore's scope. Alive is a state, not a window; added and expired are
    windowed by their own timestamps. A kill has none of its own — the
    Decision row written beside it does — so "killed" is every killed bet
    whose kill decision landed in the window."""
    from yaaf.core.models import Decision

    rows = list(session.execute(
        select(Hypothesis).where(Hypothesis.project_id == project.id)
        .order_by(Hypothesis.created_at)
    ).scalars())
    killed_in_window = {
        d.subject.removeprefix("Killed hypothesis ")
        for d in session.execute(
            select(Decision).where(
                Decision.project_id == project.id,
                Decision.subject.like("Killed hypothesis %"),
                Decision.created_at > since,
                Decision.created_at <= until,
            )
        ).scalars()
    }

    def view(h: Hypothesis) -> dict:
        return {"id": h.id, "statement": h.statement[:STATEMENT_LEN],
                "kill_date": h.kill_date.isoformat(), "status": h.status}

    return {
        "alive": [view(h) for h in rows if h.status == HypothesisStatus.ACTIVE],
        "added": [view(h) for h in rows if since < h.created_at <= until],
        "killed": [view(h) for h in rows
                   if h.status == HypothesisStatus.KILLED and h.id in killed_in_window],
        "expired": [view(h) for h in rows
                    if h.status == HypothesisStatus.EXPIRED and since < h.kill_date <= until],
    }


# ── the headline ────────────────────────────────────────────────────────────────


def headline(mode: str, numbers: dict, record: dict | None) -> str:
    """One sentence in words, from the frozen numbers and the record. Pure, so
    a meeting opened before the record existed still gets one, and so the
    screen, the reader tool and the opening mail cannot say three things."""
    parts: list[str] = []
    numbers = numbers or {}
    kept = record is not None
    record = record or {}
    scope = numbers.get("scope") or {}

    if mode == ProjectMode.OPERATE:
        cycle = record.get("cycle")
        if cycle:
            # Names what it counts. "0 of 4 met" once headlined a week in which
            # the one thing owed — a daily report — landed on time every day:
            # three of the four were cut, and the fourth was never checked.
            parts.append(f"Cycle {cycle['ordinal']}: {scorecard.sentence(cycle)}")
        elif kept:
            parts.append("No cycle closed this period.")
        # A meeting opened before the record existed says nothing about cycles:
        # "No cycle closed" headlined three meetings during which seven did.
        honoured = len(record.get("standing_honoured") or [])
        if honoured:
            parts.append(f"{honoured} standing {_plural(honoured, 'obligation')} honoured.")
    elif mode == ProjectMode.EXPLORE:
        bets = record.get("bets") or {}
        alive = len(bets.get("alive") or [])
        ended = []
        if bets.get("killed"):
            ended.append(f"{len(bets['killed'])} killed")
        if bets.get("expired"):
            ended.append(f"{len(bets['expired'])} expired on {_their(len(bets['expired']))} date")
        parts.append(
            f"{_count(alive, 'bet')} alive"
            + (", " + " and ".join(ended) + " this period." if ended else ".")
        )
        evidence = record.get("evidence") or {}
        if evidence.get("verified") or evidence.get("failed"):
            parts.append(
                f"{_count(evidence.get('verified', 0), 'piece')} of evidence verified"
                + (f", {evidence['failed']} refused." if evidence.get("failed") else "."))
    else:
        commitment = numbers.get("commitment")
        if commitment:
            parts.append(_date_clause(commitment))
        else:
            parts.append("No delivery date is signed.")
        total = scope.get("met", 0) + scope.get("open", 0)
        if total:
            made = len(record.get("requirements_met") or [])
            parts.append(
                f"{scope.get('met', 0)} of {total} requirements true"
                + (f", {made} this period." if made else "."))

    budget = numbers.get("budget") or {}
    if budget.get("cap"):
        pct = round(100 * (budget.get("spent_mtd") or 0) / budget["cap"])
        parts.append(
            f"This month's spend is {pct}% of plan" + (" — over it." if pct > 100 else "."))
    return " ".join(parts)


def _date_clause(commitment: dict) -> str:
    status = commitment.get("status")
    promised = _day(commitment.get("deliver_by"))
    projected = _day(commitment.get("projected_done_at"))
    if status == "met":
        return "Delivered."
    if status == "proposed":
        return "A delivery date is proposed and not yet signed."
    if status == "void":
        return "The delivery date was withdrawn."
    if status == "breached":
        return (f"The promised date ({promised}) has passed"
                + (f"; the projection is now {projected}." if projected else "."))
    late = _days_between(commitment.get("deliver_by"), commitment.get("projected_done_at"))
    if status == "at_risk" and late is not None and late > 0:
        return (f"Behind the promise by {_count(late, 'day')} "
                f"({promised} → {projected}).")
    if projected:
        return f"On track for {promised} (projected {projected})."
    return f"Promised for {promised}; no projection yet."


def _day(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:10]
    return f"{dt:%b} {dt.day}"


def _days_between(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days
    except ValueError:
        return None


def _plural(n: int, one: str, many: str | None = None) -> str:
    return one if n == 1 else (many or one + "s")


def _count(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {_plural(n, one, many)}"


def _their(n: int) -> str:
    return "its" if n == 1 else "their"


# ── the chairman's read ─────────────────────────────────────────────────────────
#
# The headline above is the evidence in a sentence: it counts. A chairman of a
# business that does not live in the project read "Cycle 1: Monday pack with
# audit note: owed this cycle and never checked; 6 of 9 one-off requirements
# met, R-06, R-08, R-10 carried forward. This month's spend is 60% of plan" and
# could not say whether it was good news. He asks three questions, in order,
# and stops at the first good-enough answer:
#
#   1. Are we getting results?
#   2. If so, is the money fine?
#   3. Did we miss anything that matters?
#
# A count of misses is not a severity — one that matters outweighs four that do
# not — so every miss here carries why it does or does not matter, and only the
# ones that do are put in front of him. "Never checked" always matters: an
# unverified result cannot be judged trivial, because it is the answer to the
# first question that it undermines.

GOOD, WATCH, BAD, UNKNOWN = "good", "watch", "bad", "unknown"


def verdict(mode: str, numbers: dict, record: dict | None) -> dict:
    """Results, then money, then the misses that matter — from the same
    frozen facts as the headline, at zero tokens. Pure, for the headline's
    reasons: the screen, the reader tool and the opening mail say one thing."""
    numbers = numbers or {}
    matters: list[dict] = []
    minor: list[dict] = []
    results = _results(mode, numbers, record, matters, minor)
    money = _money(numbers, record)

    spot = (record or {}).get("spot_checks") or {}
    if spot.get("failed"):
        n = spot["failed"]
        matters.append({
            "what": f"{_count(n, 'close')} failed your spot-check",
            "why": "work the platform called done did not hold up when looked at, "
                   "so the results above are less certain than they read"})
        if results["tone"] == GOOD:
            results["tone"] = UNKNOWN

    fine = results["tone"] == GOOD and money["tone"] in (GOOD, UNKNOWN) and not matters
    if fine:
        sentence = ("Getting results"
                    + (" and within budget" if money["tone"] == GOOD else "")
                    + ". Nothing that matters was missed.")
    else:
        sentence = f"{results['says']} {money['says']}"
        if matters:
            sentence += f" {_count(len(matters), 'miss', 'misses')} that "
            sentence += "matters." if len(matters) == 1 else "matter."
    return {"results": results, "money": money, "matters": matters, "minor": minor,
            "fine": fine, "sentence": sentence}


def _results(mode: str, numbers: dict, record: dict | None,
             matters: list[dict], minor: list[dict]) -> dict:
    if record is None:
        return {"tone": UNKNOWN,
                "says": "This meeting opened before the platform kept a record, "
                        "so it cannot say what the period achieved."}
    if mode == ProjectMode.OPERATE:
        return _operate_results(numbers, record, matters, minor)
    if mode == ProjectMode.EXPLORE:
        return _explore_results(record, minor)
    return _deliver_results(numbers, record, matters, minor)


def _operate_results(numbers: dict, record: dict, matters: list[dict],
                     minor: list[dict]) -> dict:
    """An Operate project's results are what it owes on a rhythm — the report
    that lands every Monday, the feed that refreshes daily. One-offs are the
    work that builds it, and slipping one is a schedule fact, not a result."""
    cycle = record.get("cycle")
    if not cycle:
        return {"tone": UNKNOWN, "says": "No cycle closed this period, so there is "
                                         "nothing delivered to judge yet."}
    owed = [r for r in cycle.get("standing") or [] if r.get("verdict") != scorecard.NOT_DUE]
    standing_keys = {r["key"] for r in cycle.get("standing") or []}
    done = [k for k in cycle.get("met_keys") or [] if k not in standing_keys]
    carried = cycle.get("carried_keys") or []
    for key in carried:
        minor.append({"key": key, "what": f"{key} slipped to the next cycle",
                      "why": "a one-off piece of the build, not something owed to "
                             "anyone this cycle"})
    for key in cycle.get("cut_keys") or []:
        minor.append({"key": key, "what": f"{key} was cut", "why": "a decision, with "
                      "its reason on the Roadmap — not a miss"})

    landed, missed, unchecked = [], [], []
    for row in owed:
        label = row.get("title") or row.get("name") or row["key"]
        v = row.get("verdict")
        if v == scorecard.HONOURED:
            landed.append(label)
        elif v == scorecard.MISSED:
            missed.append(label)
            matters.append({"key": row["key"], "what": f"{label} was not delivered",
                            "why": "it is owed every cycle; this is the result itself"})
        else:
            unchecked.append(label)
            matters.append({"key": row["key"], "what": f"{label} was never checked",
                            "why": "it may have gone out, but nobody confirmed it — "
                                   "so whether this cycle got its result is unknown"})
    build = (f" The build behind it: {len(done)} of {len(done) + len(carried)} "
             f"one-off pieces done." if done or carried else "")
    if missed:
        return {"tone": BAD, "says": f"Not getting results: {_and(missed)} "
                f"{'was' if len(missed) == 1 else 'were'} not delivered." + build}
    if unchecked:
        return {"tone": UNKNOWN, "says": f"Results unconfirmed: {_and(unchecked)} "
                f"{'was' if len(unchecked) == 1 else 'were'} owed and never checked." + build}
    if landed:
        return {"tone": GOOD, "says": f"Getting results: {_and(landed)} delivered."}
    # Nothing owed on a rhythm yet: the build is the only result there is.
    if done:
        return {"tone": GOOD, "says": f"Still building, and moving: "
                f"{len(done)} of {len(done) + len(carried)} pieces done this cycle."}
    return {"tone": BAD, "says": "Nothing owed was delivered and nothing was built "
                                 "this cycle."}


def _deliver_results(numbers: dict, record: dict, matters: list[dict],
                     minor: list[dict]) -> dict:
    """A Deliver project's result is the thing on the date. Before there is a
    date, it is whether the period made anything true."""
    for r in record.get("requirements_cut") or []:
        minor.append({"key": r["key"], "what": f"{r['key']} was cut",
                      "why": r.get("reason") or "a decision, not a miss"})
    commitment = numbers.get("commitment") or {}
    status = commitment.get("status")
    if status == "met":
        return {"tone": GOOD, "says": "Delivered."}
    if status in ("at_risk", "breached"):
        clause = _date_clause(commitment)
        matters.append({"what": clause.rstrip("."),
                        "why": "the date was a promise; the period's work does not "
                               "change that it is late"})
        return {"tone": BAD, "says": f"Not on track: {clause[0].lower()}{clause[1:]}"}
    made = record.get("requirements_met") or []
    if status == "accepted":
        return {"tone": GOOD, "says": f"On track: {_date_clause(commitment)[0].lower()}"
                f"{_date_clause(commitment)[1:]}"}
    if made:
        checked = len([r for r in made if r.get("how") == "verified"])
        return {"tone": GOOD, "says": f"Moving: {_count(len(made), 'requirement')} "
                f"made true this period, {checked} checked by the platform itself."}
    return {"tone": BAD, "says": "Nothing was made true this period."}


def _explore_results(record: dict, minor: list[dict]) -> dict:
    """An Explore project's result is learning: evidence the platform verified,
    and bets settled one way or the other."""
    evidence = record.get("evidence") or {}
    bets = record.get("bets") or {}
    for h in bets.get("expired") or []:
        minor.append({"what": f"A bet ran out its clock: {h['statement'][:80]}",
                      "why": "expiry is the rule working; extend or let it go"})
    settled = len(bets.get("killed") or []) + len(bets.get("expired") or [])
    if evidence.get("verified"):
        return {"tone": GOOD, "says": f"Learning: {_count(evidence['verified'], 'piece')} "
                f"of evidence verified" + (f", {settled} bet(s) settled." if settled else ".")}
    if settled:
        return {"tone": GOOD, "says": f"Learning: {settled} bet(s) settled this period."}
    if evidence.get("failed"):
        return {"tone": BAD, "says": "No result: every piece of evidence offered "
                                     "was refused by the verifier."}
    return {"tone": BAD, "says": "No evidence came in and no bet was settled."}


def _money(numbers: dict, record: dict | None) -> dict:
    """On, over, or under pace — in money, not a percentage of an unstated
    cap. The burn rate decides whether a projection may be said at all."""
    budget = numbers.get("budget") or {}
    cap = budget.get("cap")
    if not cap:
        return {"tone": UNKNOWN, "says": "No budget is set."}
    spent = budget.get("spent_mtd") or 0
    unit = budget.get("unit") or ""
    # One format for both sides: "$60.00 of $100" reads as two kinds of number.
    whole = cap >= 100
    amount = f"{_amount(spent, unit, whole)} of {_amount(cap, unit, whole)}"
    waste = _waste(record)
    if spent > cap:
        return {"tone": BAD, "says": f"Over budget: {amount} this month.{waste}"}
    rate = budget.get("rate")
    if rate and rate.get("lands_on_cap_at"):
        return {"tone": BAD, "says": f"Will run out: {amount} spent, on pace to hit "
                f"the budget on {_day(rate['lands_on_cap_at'])}, before the month "
                f"ends.{waste}"}
    if rate and rate.get("projectable"):
        return {"tone": GOOD, "says": f"Within budget: {amount}, on pace to finish "
                                      "the month under it."}
    # No measured rate (too few days, lumpy days, or a meeting frozen before the
    # rate was): the calendar is the only yardstick, and it is said as one.
    elapsed = _month_elapsed(numbers.get("as_of"))
    if elapsed is not None and spent / cap > elapsed + 0.15:
        return {"tone": WATCH, "says": f"Spending ahead of the calendar: {amount} with "
                f"{round(elapsed * 100)}% of the month gone — too early to say where it "
                f"lands.{waste}"}
    return {"tone": GOOD, "says": f"Within budget: {amount} this month."}


def _waste(record: dict | None) -> str:
    """Dead runs are money, so they are said with the money and only when the
    money is a problem — a third of runs dying on budget is somebody's tuning
    question, not the chairman's."""
    runs = (record or {}).get("runs") or {}
    total, dead = runs.get("total") or 0, runs.get("dead") or 0
    if total >= 10 and dead / total >= 0.25:
        return f" {dead} of {total} runs died without a result — that is where to look."
    return ""


def _month_elapsed(iso: str | None) -> float | None:
    import calendar

    if not iso:
        return None
    try:
        at = datetime.fromisoformat(iso)
    except ValueError:
        return None
    days = calendar.monthrange(at.year, at.month)[1]
    return (at.day - 1 + at.hour / 24) / days


def _amount(n: float, unit: str, whole: bool = True) -> str:
    if unit.upper() == "USD":
        return f"${n:,.0f}" if whole else f"${n:,.2f}"
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= size:
            return f"{n / size:.1f}{suffix} tokens"
    return f"{n:,.0f} tokens"


def _and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# ── plan versus actual ──────────────────────────────────────────────────────────

#: What the platform can say about an item the CEO said it would do.
PLAN_MARKS = ("true", "open", "cut", "completed", "missed", "alive", "killed", "expired",
              "unknown")


def plan_marks(session: Session, project: Project, prior) -> list[dict]:
    """The previous meeting's `next` list, each item marked from the record.

    The levers were already accountable — "shown at the next review beside
    what they bought". This is the same mechanism pointed at the CEO's own
    plan: an item that served a requirement reads that requirement's status
    today; one that served the cycle reads the cycle that closed since; one
    that served a bet reads the bet. The platform's column comes first and
    cannot be argued with; the CEO's line beside it is its `last_plan`, and
    "done" against a requirement the platform holds open is refused there.
    Zero tokens, frozen at open like everything else the janitor writes.
    """
    items = ((prior.report or {}).get("next") or []) if prior is not None else []
    if not items:
        return []
    requirements = {
        r.key: r for r in session.execute(
            select(Requirement).where(Requirement.project_id == project.id)
        ).scalars()
    }
    bets = {
        h.id: h for h in session.execute(
            select(Hypothesis).where(Hypothesis.project_id == project.id)
        ).scalars()
    }
    cycle = session.execute(
        select(Cycle).where(Cycle.project_id == project.id,
                            Cycle.closed_at.is_not(None),
                            Cycle.closed_at > prior.opened_at)
        .order_by(Cycle.ordinal.desc()).limit(1)
    ).scalar_one_or_none()

    out: list[dict] = []
    for index, item in enumerate(items):
        serves = item.get("serves") or ""
        kind, mark, says = "unknown", "unknown", "nothing on the record serves this"
        if serves == "cycle":
            kind = "cycle"
            if cycle is None:
                mark, says = "open", "no cycle has closed since"
            else:
                mark = cycle.status
                says = (f"cycle {cycle.ordinal} closed {cycle.status}: "
                        f"{scorecard.sentence(cycle.scorecard)}")
        elif serves in requirements:
            r = requirements[serves]
            kind = "requirement"
            if r.status == RequirementStatus.MET:
                mark, says = "true", f"true · {checked_how(r)}"
            elif r.status == RequirementStatus.CUT:
                mark, says = "cut", "cut"
            else:
                mark = "open"
                dead = getattr(r, "failure_load", 0) or 0
                says = "open" + (f" · {dead} dead runs" if dead else "")
        elif serves in bets:
            h = bets[serves]
            kind = "bet"
            mark = {"active": "alive"}.get(h.status, h.status)
            says = f"bet {mark}"
        out.append({"item": index, "what": item.get("what"), "serves": serves,
                    "by": item.get("by"), "kind": kind, "mark": mark, "says": says})
    return out


# ── when the meeting is due ─────────────────────────────────────────────────────

#: Operate rhythms whose roll IS the status meeting. A daily cycle keeps the
#: interval: seven meetings a week is a feed, not a meeting.
ROLL_IS_THE_MEETING = ("weekly", "monthly", "quarterly")


def meeting_at_the_roll(project: Project, cons) -> bool:
    return (project.mode == ProjectMode.OPERATE
            and cons.cycle_frequency in ROLL_IS_THE_MEETING)


def review_due(session: Session, project: Project, cons, latest) -> tuple[datetime | None, int]:
    """(when the next meeting is due, its number). The janitor opens on it and
    the agenda draws it, from one arithmetic.

    Deliver and Explore meet every `review_interval_days`. An Operate project
    on a weekly or longer rhythm meets at the roll: the roll already closes
    the cycle, writes the scorecard and reopens the plan gate, and a second
    meeting a day later about the same week was the same conversation held
    twice. So the meeting is due the moment a cycle has closed since the
    previous one opened, and not before — None while the cycle is still
    open, and the agenda shows the roll as the meeting's time."""
    from datetime import timedelta

    number = 1 if latest is None else latest.number + 1
    if meeting_at_the_roll(project, cons):
        since = latest.opened_at if latest is not None else project.created_at
        closed = session.execute(
            select(Cycle).where(Cycle.project_id == project.id,
                                Cycle.closed_at.is_not(None),
                                Cycle.closed_at > since)
            .order_by(Cycle.closed_at).limit(1)
        ).scalar_one_or_none()
        if closed is None:
            return None, number
        return closed.closed_at, number
    interval = timedelta(days=cons.review_interval_days)
    if latest is None:
        return project.created_at + interval, number
    return max(latest.scheduled_for + interval,
               latest.closed_at or latest.opened_at), number
