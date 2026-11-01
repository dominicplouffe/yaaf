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
from yaaf.server import policy, velocity

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
    record = record or {}
    scope = numbers.get("scope") or {}

    if mode == ProjectMode.OPERATE:
        cycle = record.get("cycle")
        if cycle:
            parts.append(
                f"Cycle {cycle['ordinal']} closed with {cycle.get('met', 0)} of "
                f"{cycle.get('accepted', 0)} met"
                + (f", {cycle['carried']} carried forward." if cycle.get("carried")
                   else "."))
        else:
            parts.append("No cycle closed this period.")
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
                card = cycle.scorecard or {}
                says = (f"cycle {cycle.ordinal} closed {cycle.status}, "
                        f"{card.get('met', 0)} of {card.get('accepted', 0)} met")
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
