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

import re
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
    LedgerEntry,
    LedgerKind,
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
from yaaf.server import policy, scorecard, velocity, writing

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

    # `title` is what the owner calls it (the CEO's `meta.title`), present only
    # when written — the board names and groups by it; the statement stays.
    met = [
        {"key": r.key, "statement": r.statement[:STATEMENT_LEN], **_title(r),
         "how": checked_how(r)}
        for r, _ in first_seen(RequirementStatus.MET, is_scope)
    ]
    honoured = [
        {"key": r.key, "statement": r.statement[:STATEMENT_LEN], **_title(r)}
        for r, _ in first_seen(RequirementStatus.MET, lambda r: r.id in standing)
    ]
    cut = [
        {"key": r.key, "statement": r.statement[:STATEMENT_LEN], **_title(r),
         "reason": e.note}
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
    # Spend in the window, in tokens whatever the billing unit: the meeting
    # speaks one currency, and tokens are the one every seat books (a
    # subscription seat books no dollars at all). Raw sum, cache included —
    # `budget.month_to_date`'s rule, so the two can be compared.
    tokens = session.execute(
        select(func.coalesce(func.sum(
            LedgerEntry.tokens_in + LedgerEntry.tokens_out
            + LedgerEntry.cache_creation_tokens + LedgerEntry.cache_read_tokens), 0))
        .where(LedgerEntry.project_id == project.id,
               LedgerEntry.at > since,
               LedgerEntry.at <= until,
               LedgerEntry.kind != LedgerKind.REVENUE)
    ).scalar_one()

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
        "tokens": int(tokens),
        "cycle": None if cycle is None else {
            "ordinal": cycle.ordinal, "status": cycle.status,
            "goal": (cycle.goal or "")[:STATEMENT_LEN] or None,
            **(cycle.scorecard or {}),
        },
        "bets": _bets(session, project, since, until),
        # How long the team's files ran, by who they were for, and whether a
        # person opened his — what the CEO coaches writing from (`writing`).
        "writing": writing.compose(session, project, since, until),
        "evidence": {
            "verified": (proof_by.get(VerificationStatus.VERIFIED, 0)
                         + proof_by.get(VerificationStatus.NOT_APPLICABLE, 0)),
            "failed": proof_by.get(VerificationStatus.FAILED, 0),
            "pending": proof_by.get(VerificationStatus.PENDING, 0),
        },
    }


def _title(requirement: Requirement) -> dict:
    title = ((requirement.meta or {}).get("title") or "").strip()
    return {"title": title[:STATEMENT_LEN]} if title else {}


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
            parts.append(f"Cycle {cycle['ordinal']}: {scorecard.sentence(cycle, cuts=False)}")
        elif kept:
            parts.append("No cycle closed this period.")
        # A meeting opened before the record existed says nothing about cycles:
        # "No cycle closed" headlined three meetings during which seven did.
        honoured = len(record.get("standing_honoured") or [])
        # The cycle's sentence already named each obligation and its verdict;
        # "2 standing obligations honoured" after it said the same thing twice.
        if honoured and not (cycle and cycle.get("standing")):
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


# ── the board: where it stands, against plan, by exception ──────────────────────
#
# The meeting used to open on the record itself: five equal columns of counts
# ("30 requirements made true", "2 standing obligations honoured", "11
# deliverables published") over checker sentences. A chairman reading it could
# not say whether the period went well. One daily post was counted three times
# — as thirty dated requirements, as a standing obligation and as eleven
# files — and the headline's "1 cut" and the column's "4 cut" came from two
# windows, so the page appeared to contradict itself.
#
# A board report reads the other way round: a status, a short scorecard of
# plan against actual, what did not go to plan, and the full record behind a
# fold. `board` builds the platform's half of that at zero tokens and from the
# same frozen facts as the headline, so it cannot drift from it; the CEO's
# report still says what it meant and what is next.

OK, WARN, BAD, NEUTRAL = "ok", "warn", "bad", "neutral"
STATUS_LABEL = {OK: "On track", WARN: "Watch", BAD: "Off track"}

#: How far over the month's elapsed share spend may run before it is a watch.
#: 96% of plan with a fifth of the month left is the case this exists for.
SPEND_PACE_SLACK = 0.10

#: How many characters of a normalised name decide whether two requirements are
#: the same recurring thing on different days.
SERIES_PREFIX = 60

_DAY_WORDS = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}(?:T[\d:.]+Z?)?"
    r"|(?:mon|tues|wednes|thurs|fri|satur|sun)day"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?"
    r"|today|tomorrow|tonight)\b",
    re.IGNORECASE,
)
_DAY_MARK = "[day]"
_DAY_RUNS = re.compile(r"\[day\](?:[\s,]+\[day\])+")


def _undated(text: str) -> str:
    """The name with its dates taken out — "a WordPress draft dated
    2026-11-12" and "… dated 2026-11-13" are one thing done twice."""
    out = _DAY_RUNS.sub(_DAY_MARK, _DAY_WORDS.sub(_DAY_MARK, text))
    return re.sub(r"\s+", " ", out).strip()


#: "Before 18:00 America/Toronto on Tuesday 2026-11-10, " — the checker's
#: deadline, which a statement leads with and a person does not.
_DEADLINE = re.compile(
    r"^(?:before|by)\s+(\d{1,2}:\d{2})(?:\s+[A-Za-z_]+/[A-Za-z_]+)?\s+on\s+"
    r"(?:\[day\]|(?:[A-Za-z]+day\s+)?\d{4}-\d{2}-\d{2})[\s,]*",
    re.IGNORECASE,
)


def _label(row: dict) -> str:
    return row.get("title") or row["statement"]


def _plain(text: str) -> tuple[str, str | None]:
    """(the thing, its deadline) — the deadline moved out of the name, and the
    checker's detail after a colon dropped. A title the CEO wrote is left as
    written; this only tidies a statement."""
    deadline = None
    match = _DEADLINE.match(text)
    if match:
        deadline, text = match.group(1), text[match.end():]
    head, sep, _ = text.partition(": ")
    if sep and len(head) >= 20:
        text = head
    text = text.strip().rstrip(".:,;")
    first = text.split(" ", 1)[0]
    if first.isalpha():   # "an independent fact-check", not "dplouffe.ca"
        text = text[:1].upper() + text[1:]
    return text, deadline


def series(met: list[dict], cut: list[dict]) -> list[dict]:
    """The window's one-off requirements, grouped into the recurring things they
    are. Two or more whose names match once their dates are taken out are one
    series ("[day] post on the site: 15 delivered, 1 cut"); the rest are listed
    as one-offs. Order: series first, largest first, then the one-offs."""
    groups: dict[str, dict] = {}
    for kind, rows in (("met", met), ("cut", cut)):
        for row in rows:
            name = _undated(_label(row))
            key = name[:SERIES_PREFIX].lower()
            group = groups.setdefault(key, {"name": name, "met": [], "cut": []})
            group[kind].append(row)
    recurring = [g for g in groups.values() if len(g["met"]) + len(g["cut"]) > 1]
    one_off = [g for g in groups.values() if len(g["met"]) + len(g["cut"]) == 1]
    recurring.sort(key=lambda g: -(len(g["met"]) + len(g["cut"])))
    out = [{"name": _plain(g["name"])[0], "deadline": _plain(g["name"])[1], "recurring": True,
            "met": [r["key"] for r in g["met"]], "cut": [r["key"] for r in g["cut"]]}
           for g in recurring]
    if one_off:
        out.append({"name": None, "deadline": None, "recurring": False,
                    "met": [r["key"] for g in one_off for r in g["met"]],
                    "cut": [r["key"] for g in one_off for r in g["cut"]]})
    return out


def _tokens(n: float | int | None) -> str:
    n = float(n or 0)
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= size:
            value = n / size
            return f"{value:.1f}{suffix}".replace(".0" + suffix, suffix)
    return f"{int(n)}"


def _month_elapsed(as_of: str | None) -> float | None:
    """How much of the month had gone by when the numbers froze, 0..1."""
    import calendar

    if not as_of:
        return None
    try:
        at = datetime.fromisoformat(as_of)
    except ValueError:
        return None
    days = calendar.monthrange(at.year, at.month)[1]
    return (at.day - 1 + at.hour / 24) / days


def _spend_row(numbers: dict, record: dict) -> dict | None:
    budget = numbers.get("budget") or {}
    if not budget:
        return None
    cap = budget.get("cap") or 0
    unit = budget.get("unit")
    pct = round(100 * (budget.get("spent_mtd") or 0) / cap) if cap else None
    tokens_mtd = budget.get("tokens_mtd")
    if tokens_mtd is None and unit == "tokens":
        tokens_mtd = budget.get("spent_mtd")
    elapsed = _month_elapsed(numbers.get("as_of"))

    actual = []
    if tokens_mtd is not None:
        actual.append(f"{_tokens(tokens_mtd)} this month")
    if pct is not None:
        actual.append(f"{pct}% of plan")
    tone, note = OK, None
    if pct is not None and pct > 100:
        tone, note = BAD, "over this month's plan"
    elif pct is not None and elapsed is not None and pct / 100 > elapsed + SPEND_PACE_SLACK:
        tone = WARN
        note = (f"{pct}% used with {round(100 * (1 - elapsed))}% of the month left")
    period = record.get("tokens")
    detail = f"{_tokens(period)} since the last meeting" if period is not None else None
    return {
        "what": "Tokens",
        "plan": (f"{_tokens(cap)} a month" if unit == "tokens" and cap
                 else "this month's plan" if cap else "no plan set"),
        "actual": " · ".join(actual) or "—",
        "tone": tone,
        "note": note or detail,
        "detail": detail if note else None,
    }


def _standing_rows(cycle: dict) -> list[dict]:
    rows = []
    for row in cycle.get("standing") or []:
        if row["verdict"] == scorecard.NOT_DUE:
            continue
        due = row.get("due_daily_at")
        if "days_due" in row:
            actual = f"{row.get('days_met', 0)} of {row['days_due']} days on time"
            if row["verdict"] == scorecard.UNCHECKED:
                actual = f"not checked on any of {row['days_due']} days"
        else:
            actual = {scorecard.HONOURED: "done", scorecard.MISSED: "not done",
                      scorecard.UNCHECKED: "never checked"}[row["verdict"]]
        rows.append({
            "what": row.get("title") or row["name"],
            "plan": f"every day by {due}" if due else "every cycle",
            "actual": actual,
            "tone": {scorecard.HONOURED: OK, scorecard.MISSED: BAD,
                     scorecard.UNCHECKED: WARN}[row["verdict"]],
            "note": f"cycle {cycle['ordinal']}",
            "keys": [row["key"]],
        })
    return rows


def _series_rows(record: dict) -> list[dict]:
    rows = []
    for s in series(record.get("requirements_met") or [], record.get("requirements_cut") or []):
        done, cut = len(s["met"]), len(s["cut"])
        actual = f"{done} done" + (f", {cut} cut" if cut else "")
        rows.append({
            "what": s["name"] if s["recurring"] else "One-off requirements",
            "plan": f"{done + cut} due" + (f", each by {s['deadline']}" if s["deadline"] else ""),
            "actual": actual,
            # A cut is a decision with a reason, listed under what did not go
            # to plan — not a failure to colour.
            "tone": OK if done else NEUTRAL,
            "note": "recurring" if s["recurring"] else None,
            "keys": s["met"] + s["cut"],
        })
    return rows


def _misses(mode: str, numbers: dict, record: dict) -> list[dict]:
    misses: list[dict] = []
    cycle = record.get("cycle") or {}
    for row in _standing_rows(cycle) if cycle else []:
        if row["tone"] != OK:
            misses.append({"what": f"{row['what']}: {row['actual']}", "why": None,
                           "keys": row["keys"]})
    # Cuts by reason: four posts cut for one reason are one line, not four.
    by_reason: dict[str, list[dict]] = {}
    for row in record.get("requirements_cut") or []:
        by_reason.setdefault((row.get("reason") or "").strip(), []).append(row)
    for reason, rows in by_reason.items():
        names = sorted({_plain(_undated(_label(r)))[0] for r in rows})
        what = (f"Cut {len(rows)} × {names[0]}" if len(names) == 1 and len(rows) > 1
                else f"Cut: {_plain(_label(rows[0]))[0]}" if len(rows) == 1
                else f"Cut {len(rows)} requirements")
        misses.append({"what": what, "why": reason or "no reason recorded",
                       "keys": [r["key"] for r in rows]})
    commitment = numbers.get("commitment") or {}
    if commitment.get("status") in ("at_risk", "breached"):
        misses.append({"what": _date_clause(commitment), "why": commitment.get("basis"),
                       "keys": []})
    checks = record.get("spot_checks") or {}
    if checks.get("failed"):
        misses.append({"what": f"{_count(checks['failed'], 'spot-check')} failed",
                       "why": "a close the platform drew for review did not hold up",
                       "keys": []})
    if checks.get("expired"):
        misses.append({"what": f"{_count(checks['expired'], 'spot-check')} expired unanswered",
                       "why": None, "keys": []})
    evidence = record.get("evidence") or {}
    if mode == ProjectMode.EXPLORE and evidence.get("failed"):
        misses.append({"what": f"{_count(evidence['failed'], 'piece')} of evidence refused",
                       "why": "the platform could not verify it", "keys": []})
    runs = record.get("runs") or {}
    if runs.get("total") and runs.get("dead", 0) / runs["total"] >= 0.25:
        misses.append({"what": f"{runs['dead']} of {runs['total']} runs died",
                       "why": None, "keys": []})
    return misses


def _pretty_file(name: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name)
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}[-_ ]*", "", stem)
    return re.sub(r"[-_]+", " ", stem).strip() or name


def board(mode: str, numbers: dict | None, record: dict | None) -> dict:
    """The platform's half of a board report, from the frozen numbers and the
    record: a status, a scorecard of plan against actual, what did not go to
    plan, and up to three things published. Pure, like `headline`, so a
    meeting opened before any of this existed still gets one."""
    numbers = numbers or {}
    kept = record is not None
    record = record or {}
    rows: list[dict] = []

    if mode == ProjectMode.EXPLORE:
        bets = record.get("bets") or {}
        if kept:
            rows.append({
                "what": "Bets",
                "plan": f"{_count(len(bets.get('added') or []), 'new bet')} this period",
                "actual": (f"{len(bets.get('alive') or [])} alive, "
                           f"{len(bets.get('killed') or [])} killed, "
                           f"{len(bets.get('expired') or [])} expired"),
                "tone": OK if bets.get("alive") else NEUTRAL, "note": None, "keys": []})
        evidence = record.get("evidence") or {}
        if evidence.get("verified") or evidence.get("failed"):
            rows.append({
                "what": "Evidence",
                "plan": "verified by the platform",
                "actual": (f"{evidence.get('verified', 0)} verified"
                           + (f", {evidence['failed']} refused" if evidence.get("failed") else "")),
                "tone": BAD if evidence.get("failed") else OK, "note": None, "keys": []})
    else:
        commitment = numbers.get("commitment")
        if mode != ProjectMode.OPERATE:
            if commitment:
                status = commitment.get("status")
                rows.append({
                    "what": "Delivery date",
                    "plan": _day(commitment.get("deliver_by")) or "—",
                    "actual": (_day(commitment.get("projected_done_at"))
                               or {"met": "delivered"}.get(status, "no projection yet")),
                    "tone": {"breached": BAD, "at_risk": WARN, "proposed": NEUTRAL,
                             "void": NEUTRAL}.get(status, OK),
                    "note": {"at_risk": "behind", "breached": "date passed",
                             "proposed": "not signed"}.get(status),
                    "keys": []})
            scope = numbers.get("scope") or {}
            total = scope.get("met", 0) + scope.get("open", 0)
            if total:
                rows.append({
                    "what": "Requirements true",
                    "plan": f"{total} in scope",
                    "actual": f"{scope.get('met', 0)} of {total}",
                    "tone": OK, "note": None, "keys": []})
        if record.get("cycle"):
            rows.extend(_standing_rows(record["cycle"]))
        rows.extend(_series_rows(record))

    checks = record.get("spot_checks") or {}
    if checks.get("considered"):
        rows.append({
            "what": "Spot-checks",
            "plan": f"{checks.get('drawn', 0)} of {checks['considered']} closes drawn",
            "actual": (f"{checks.get('passed', 0)} passed"
                       + (f", {checks['failed']} failed" if checks.get("failed") else "")
                       + (f", {checks['expired']} unanswered" if checks.get("expired") else "")),
            "tone": BAD if checks.get("failed") else WARN if checks.get("expired") else OK,
            "note": None, "keys": []})
    spend = _spend_row(numbers, record)
    if spend:
        rows.append(spend)

    toned = [r for r in rows if r["tone"] in (WARN, BAD)]
    tone = BAD if any(r["tone"] == BAD for r in rows) else WARN if toned else OK
    published = record.get("deliverables") or []
    return {
        "status": {
            "tone": tone, "label": STATUS_LABEL[tone],
            "why": [f"{r['what']}: {r['note'] or r['actual']}" for r in rows
                    if r["tone"] == tone and tone != OK],
        },
        "scorecard": rows,
        "misses": _misses(mode, numbers, record),
        "highlights": {
            "published": len(published),
            "top": [{"name": _pretty_file(d["name"]), "file": d["name"],
                     "version": d.get("version")} for d in published[:3]],
        },
    }


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
