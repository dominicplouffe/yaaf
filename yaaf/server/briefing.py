"""The briefing: what happened while you were not looking.

Every screen this product had was in the present tense. The desk says what
needs you *now*, the overview says what is true *now*, and Records is four
tables you read to reconstruct a weekend by hand. A chairman who comes back
after three days had no way to ask the only question he actually has — what
happened? — and the only mail he ever got was an alarm. A project that writes
to you exclusively with bad news is one you stop opening.

Two properties make this trustworthy rather than decorative:

**It is composed from rows, at zero tokens.** No run, no wake, no model call.
Which means it arrives on a paused project, a broke one, and one whose board
is empty — the three states where a status a *model* wrote would either not
happen or not be affordable, and the three where the chairman most needs to
hear something. Same reasoning as the janitor opening a review with the budget
exhausted.

**It never grades itself.** Every line here counts something the platform
already wrote down under its own rules: a requirement is MET because the
platform ran the test, evidence counts because the verifier fetched it. The
briefing adds no judgement of its own — it is arithmetic over the record, so
it cannot flatter the project the way a self-authored status can.

That second promise is what makes the verdict legal. A panel that says "at
risk" looks like it is grading, and would be, if it worked the answer out. It
does not: `Commitment.status` is stamped by the janitor off its own
re-projection, and the enum's docstring is explicit that no agent verb sits
behind any of its transitions. `_verdict` **reports** that status and the
janitor's own basis note; it never re-derives one. And where nothing has been
promised it says so, because the one thing this panel must never learn to do
is manufacture a green light nobody switched on.

The summary and the detail answer different readers. `verdict`, `movement`,
`money["text"]` and `exceptions` are the board's four questions — will it
land, did it move, what did that cost, what is wrong — and are what the desk
draws. `lines` is the operating record, unchanged and unranked, and is what
the project's own page and the daily mail draw. Both come out of one pass over
the rows, so no two screens can disagree about the same weekend.

The window is the interesting part. "Since you were last here" is not 24
hours: it is `project_members.last_briefed_at`, stamped when the chairman
acknowledges the panel, so a weekend away reads as a weekend. NULL means never
read, which composes from the project's start rather than an empty window — a
first visit should summarise the project, not nothing.

`render_text` is the same content for the daily push. One composer, two
deliveries: a panel and an email that can never disagree about what happened.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.constitution import ConstitutionDoc, effective
from yaaf.core.models import (
    Amendment,
    Approval,
    ApprovalStatus,
    Artifact,
    AuditLog,
    AuditSample,
    AuditSampleStatus,
    Channel,
    Commitment,
    CommitmentStatus,
    Evidence,
    EvidenceKind,
    Flag,
    FlagStatus,
    Hypothesis,
    HypothesisStatus,
    LedgerEntry,
    LedgerKind,
    Message,
    Notice,
    NoticeKind,
    Project,
    ProjectMember,
    ProjectMode,
    ProjectStatus,
    Recurrence,
    Requirement,
    RequirementEvent,
    RequirementStatus,
    Review,
    ReviewStatus,
    Run,
    RunKind,
    RunStatus,
    TaskEvent,
    TaskStatus,
    VerificationStatus,
    utcnow,
)
from yaaf.server import policy

#: A window with no mark falls back to this. Only reached for a member row
#: that predates the column, since a new one composes from the project start.
DEFAULT_WINDOW_HOURS = 24.0

#: Runs that ended without producing anything the project keeps. The same set
#: velocity calls waste, and named identically so the two cannot drift.
DEAD_STATUSES = (RunStatus.FAILED, RunStatus.REAPED, RunStatus.KILLED,
                 RunStatus.TERMINATED_REFUSALS)

#: Notices that are the platform acting on the project rather than telling it
#: something. These are the "decided itself" lines: each one is a wall that
#: moved without anybody's say-so, and the chairman is entitled to hear about
#: it in the same breath as the work.
SELF_ACTING_NOTICES = {
    NoticeKind.BUDGET_EXHAUSTED: "the platform paused the project — the monthly budget ran out",
    NoticeKind.BUDGET_WARNING: "the budget passed its warning line and the CEO was told to cut burn",
    NoticeKind.COMMITMENT_AT_RISK: "the delivery date moved to AT RISK — new scope is frozen",
    NoticeKind.COMMITMENT_BREACHED: "the delivery date passed with scope still open",
    NoticeKind.COMMITMENT_RECOVERED: "the delivery date came back into reach",
    NoticeKind.COMMITMENT_MET: "every requirement in the commitment settled true",
    NoticeKind.CYCLE_ROLLED: "the cycle rolled — unfinished scope carried forward",
    NoticeKind.EXPLORE_DEADLINE: "the deadline you set has passed",
    NoticeKind.STALLED: "the project had work and started nothing for hours",
    NoticeKind.MEMORY_FULL: "a memory scope hit its cap and is refusing writes",
    NoticeKind.REVENUE: "money came in, booked by a signed webhook",
}


#: The verdict vocabulary, worst first — so the same tuple that names the state
#: also sorts the desk, and no caller has to re-derive which of two projects is
#: in more trouble. `severity` is the app's existing clock vocabulary (the
#: `.chip` classes), not a second colour system.
VERDICT_LABELS: dict[str, tuple[str, str]] = {
    "stopped":    ("stopped", "critical"),
    "paused":     ("paused", "critical"),
    "breached":   ("past due", "critical"),
    "at_risk":    ("at risk", "serious"),
    "unpromised": ("no date promised", "flat"),
    "proposed":   ("a date is on your desk", "warn"),
    "on_track":   ("on track", "ok"),
    "delivered":  ("delivered", "ok"),
}

#: What a chairman is entitled to be interrupted by, worst first. Membership is
#: the editorial decision — a `kind` absent here is operating detail, and still
#: reaches the project's own page and the daily mail — and the order IS the
#: severity, so nothing anywhere has to work out which of two facts is worse a
#: second time. The server sorts with this and no renderer re-sorts: a panel and
#: an email that disagreed about which line was the alarming one would be worse
#: than either alone.
#:
#: The argument for the order, in one clause each: the project is off > a
#: promise already broken > it is not working at all > a promise about to break
#: > the money wall > decisions taken in your absence > waste with a cause >
#: everything else.
EXCEPTION_RANK: tuple[str, ...] = (
    "notice_budget_exhausted",
    "notice_commitment_breached",
    "notice_stalled",
    "notice_commitment_at_risk",
    "plan_gate_reopened",
    "approvals_expired",
    "runs_dead",
    "evidence_failed",
    "tasks_blocked",
    "bets_expired",
    "samples_expired",
    "requirement_cut",
    "threads_frozen",
    "notice_memory_full",
    "notice_explore_deadline",
)

#: `notice_budget_warning` is deliberately absent, and it is the one exclusion
#: here that is a bug elsewhere rather than an editorial call. Five things now
#: write a BUDGET_WARNING notice — `janitor.enforce_budget`, its overrun notice,
#: `spendwindow.sweep`, and three admin routes — while `SELF_ACTING_NOTICES`
#: still renders every one of them as "the budget passed its warning line and
#: the CEO was told to cut burn". Opening a measurement window on a project at
#: 5% of budget would put that sentence in front of the chairman and spend one
#: of his three slots asserting something that did not happen. It stays in
#: `lines`, where it is one bullet among many rather than a headline. Split the
#: notice kind by producer and it belongs back in this table, around rank 4.

#: Deliberately NOT merged with `dispatcher.URGENT_NOTICE_KINDS`, which answers
#: a different question — does this deserve a paid wake for the CEO. A memory
#: scope refusing writes is urgent for the agent and nearly irrelevant to a
#: board; a requirement cut is the reverse.

#: What the verdict sentence already says, so the exceptions do not say it
#: again. A `stalled` bullet under a stalled verdict is one fact twice, and
#: repetition is how a list of three stops being read.
VERDICT_COVERS: dict[str, frozenset[str]] = {
    "paused": frozenset({"notice_budget_exhausted", "notice_stalled"}),
    "stopped": frozenset({"notice_budget_exhausted", "notice_stalled"}),
    "breached": frozenset({"notice_commitment_breached", "notice_commitment_at_risk"}),
    "at_risk": frozenset({"notice_commitment_at_risk"}),
}

#: At most this many exceptions reach the chairman. Three is the number a
#: person reads; a fourth is where a list becomes a table and stops being one.
MAX_EXCEPTIONS = 3

#: Dead runs are metered like live ones, so a window can throw a third of itself
#: away while its spend still looks reasonable. These two are the only bare
#: thresholds in the briefing — everything else it reports is a row's existence
#: or a constitution number. The floor exists because 1-in-3 of three runs is
#: noise, and the share because 11 dead out of 900 is not news.
DEAD_RUN_SHARE = 0.10
DEAD_RUN_FLOOR = 3


def exception_rank(kind: str) -> int:
    """One definition of how bad a thing is. Unknown kinds sort last rather
    than raising: a new line builder must not be able to break the desk."""
    try:
        return EXCEPTION_RANK.index(kind)
    except ValueError:
        return len(EXCEPTION_RANK)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _member(session: Session, project: Project) -> ProjectMember | None:
    return session.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == project.chairman_user_id,
        )
    ).scalar_one_or_none()


def window_start(session: Session, project: Project) -> tuple[datetime, str]:
    """Where this project's briefing starts, and why — the `why` is shown.

    A window the reader cannot see the edges of is a window he cannot trust:
    "3 tasks closed" means nothing until you know whether that is since Friday
    or since an hour ago. So the source travels with the timestamp and the
    panel prints it.
    """
    member = _member(session, project)
    marked = _aware(member.last_briefed_at) if member is not None else None
    if marked is not None:
        return marked, "since you last read this"
    created = _aware(project.created_at)
    if created is not None:
        return created, "since this project started"
    return utcnow() - timedelta(hours=DEFAULT_WINDOW_HOURS), "the last 24 hours"


def mark_read(session: Session, project: Project, at: datetime | None = None) -> bool:
    """Stamp the chairman's mark. Returns False when the project has no member
    row to stamp — an install seeded before members existed, which must not
    500 a page that is only trying to be helpful."""
    member = _member(session, project)
    if member is None:
        return False
    member.last_briefed_at = at or utcnow()
    return True


def _line(kind: str, text: str, *, tone: str = "flat", count: int | None = None,
          where: str | None = None) -> dict:
    """One fact. `tone` is what it means, never how loud to be about it:
    `good` landed, `bad` cost something, `flat` merely happened."""
    return {"kind": kind, "text": text, "tone": tone, "count": count, "where": where}


def _plural(n: int, one: str, many: str | None = None) -> str:
    return one if n == 1 else (many or f"{one}s")


# ── the window's facts ──────────────────────────────────────────────────────────


def _scope_lines(session: Session, project: Project, since, until) -> list[dict]:
    """Requirement transitions in the window, by what they mean rather than by
    status name.

    MET leads because it is the only line here no agent could have caused by
    asserting it — the platform ran the acceptance test itself. But two kinds
    of MET are deliberately kept out of that count, on exactly the grounds
    velocity keeps them out of pace:

    **R-00 is the plan gate lifting, not scope landing.** Counting it would
    hand every new project a "requirement settled true" in its first hour, and
    the first briefing a chairman ever reads would be a false one. It gets its
    own line, because the plan starting to add up is real news.

    **A standing obligation is met again every cycle by design.** The weekly
    memo going out is worth reporting and is not progress; conflating the two
    would make an Operate project look like it was finishing three things a
    week forever.
    """
    events = list(session.execute(
        select(RequirementEvent).where(
            RequirementEvent.project_id == project.id,
            RequirementEvent.at > since,
            RequirementEvent.at <= until,
        ).order_by(RequirementEvent.at)
    ).scalars())
    if not events:
        return []
    requirements = {
        r.id: r
        for r in session.execute(
            select(Requirement).where(Requirement.project_id == project.id)
        ).scalars()
    }
    plan = policy.plan_requirement(session, project.id)
    plan_id = plan.id if plan is not None else None
    standing = {
        rid for rid, r in requirements.items()
        if r.recurrence == Recurrence.STANDING
    }

    def named(to_status: str, include=None) -> list[str]:
        seen: list[str] = []
        for event in events:
            if event.to_status != to_status:
                continue
            if include is not None and not include(event.requirement_id):
                continue
            requirement = requirements.get(event.requirement_id)
            if requirement and requirement.key not in seen:
                seen.append(requirement.key)
        return seen

    def is_scope(rid: str) -> bool:
        return rid != plan_id and rid not in standing

    lines: list[dict] = []
    met = named(RequirementStatus.MET, is_scope)
    if met:
        lines.append(_line(
            "requirement_met",
            f"{len(met)} {_plural(len(met), 'requirement')} settled true "
            f"({', '.join(met[:6])}{'…' if len(met) > 6 else ''}) — "
            "the platform ran the acceptance test itself",
            tone="good", count=len(met), where="plan",
        ))
    honoured = named(RequirementStatus.MET, lambda rid: rid in standing)
    if honoured:
        lines.append(_line(
            "standing_honoured",
            f"{len(honoured)} standing {_plural(len(honoured), 'obligation')} honoured "
            f"({', '.join(honoured[:6])}{'…' if len(honoured) > 6 else ''}) — "
            "recurring work, not scope landing",
            tone="good", count=len(honoured), where="plan",
        ))
    if plan_id is not None:
        gate = [e for e in events if e.requirement_id == plan_id]
        closed = [e for e in gate if e.to_status == RequirementStatus.MET]
        reopened = [e for e in gate if e.from_status == RequirementStatus.MET
                    and e.to_status != RequirementStatus.MET]
        if closed:
            lines.append(_line(
                "plan_gate_closed",
                "the plan gate closed — the plan is staged, estimated and fits the "
                "money, so work may be created",
                tone="good", count=len(closed), where="plan",
            ))
        if reopened:
            lines.append(_line(
                "plan_gate_reopened",
                "the plan gate reopened — the plan stopped adding up and no new work "
                "may be created until it does",
                tone="bad", count=len(reopened), where="plan",
            ))
    accepted = named(RequirementStatus.PENDING, is_scope)
    if accepted:
        lines.append(_line(
            "requirement_accepted",
            f"{len(accepted)} {_plural(len(accepted), 'requirement')} entered scope "
            f"({', '.join(accepted[:6])}{'…' if len(accepted) > 6 else ''})",
            count=len(accepted), where="plan",
        ))
    cut = named(RequirementStatus.CUT, is_scope)
    if cut:
        lines.append(_line(
            "requirement_cut",
            f"{len(cut)} {_plural(len(cut), 'requirement')} cut from scope "
            f"({', '.join(cut[:6])}{'…' if len(cut) > 6 else ''})",
            tone="bad", count=len(cut), where="plan",
        ))
    proposed = named(RequirementStatus.PROPOSED, is_scope)
    if proposed:
        lines.append(_line(
            "requirement_proposed",
            f"the CEO proposed {len(proposed)} new "
            f"{_plural(len(proposed), 'requirement')} for you to accept",
            count=len(proposed), where="plan",
        ))
    return lines


def _work_lines(session: Session, project: Project, since, until) -> list[dict]:
    """Tasks closing is activity, not progress — the distinction velocity.py
    exists to hold. It belongs in the briefing anyway, *below* scope: a week
    with nine tasks closed and no requirement met is a specific and legible
    kind of bad week, and only visible if both numbers are printed."""
    rows = session.execute(
        select(TaskEvent.to_status, func.count(TaskEvent.id)).where(
            TaskEvent.project_id == project.id,
            TaskEvent.at > since,
            TaskEvent.at <= until,
            TaskEvent.to_status.in_([TaskStatus.DONE, TaskStatus.BLOCKED]),
        ).group_by(TaskEvent.to_status)
    ).all()
    counts = {status: int(n) for status, n in rows}
    lines: list[dict] = []
    done = counts.get(TaskStatus.DONE, 0)
    if done:
        lines.append(_line(
            "tasks_done", f"{done} {_plural(done, 'task')} closed",
            tone="good", count=done, where="work",
        ))
    blocked = counts.get(TaskStatus.BLOCKED, 0)
    if blocked:
        lines.append(_line(
            "tasks_blocked",
            f"{blocked} {_plural(blocked, 'task')} blocked — an agent said it could not "
            "finish rather than closing it anyway",
            tone="bad", count=blocked, where="work",
        ))
    return lines


def _proof_lines(session: Session, project: Project, since, until) -> list[dict]:
    """Evidence, split the way the verifier splits it. An agent writes its own
    evidence row, so the only number worth printing is the one the platform
    stood behind — and a failed check is worth printing louder than a passed
    one, because it is the case where the project believed itself."""
    rows = session.execute(
        select(Evidence.verification, func.count(Evidence.id)).where(
            Evidence.project_id == project.id,
            Evidence.kind == EvidenceKind.EXTERNAL,
            Evidence.created_at > since,
            Evidence.created_at <= until,
        ).group_by(Evidence.verification)
    ).all()
    counts = {status: int(n) for status, n in rows}
    verified = (counts.get(VerificationStatus.VERIFIED, 0)
                + counts.get(VerificationStatus.NOT_APPLICABLE, 0))
    failed = counts.get(VerificationStatus.FAILED, 0)
    pending = counts.get(VerificationStatus.PENDING, 0)
    lines: list[dict] = []
    if verified:
        lines.append(_line(
            "evidence_verified",
            f"{verified} {_plural(verified, 'piece')} of external proof verified — "
            "the platform fetched and snapshotted the source itself",
            tone="good", count=verified, where="work",
        ))
    if failed:
        lines.append(_line(
            "evidence_failed",
            f"{failed} evidence {_plural(failed, 'claim')} failed its check and counts "
            "toward nothing",
            tone="bad", count=failed, where="work",
        ))
    if pending:
        lines.append(_line(
            "evidence_pending",
            f"{pending} evidence {_plural(pending, 'claim')} still waiting on the verifier",
            count=pending, where="work",
        ))
    return lines


def _deliverable_lines(session: Session, project: Project, since, until) -> list[dict]:
    artifacts = list(session.execute(
        select(Artifact).where(
            Artifact.project_id == project.id,
            Artifact.published_at > since,
            Artifact.published_at <= until,
        ).order_by(Artifact.published_at.desc())
    ).scalars())
    if not artifacts:
        return []
    names = []
    for artifact in artifacts:
        label = f"{artifact.name} v{artifact.version}"
        if label not in names:
            names.append(label)
    return [_line(
        "published",
        f"{len(artifacts)} {_plural(len(artifacts), 'deliverable')} published "
        f"({', '.join(names[:4])}{'…' if len(names) > 4 else ''})",
        tone="good", count=len(artifacts), where="deliverables",
    )]


def _money(session: Session, project: Project, cons: ConstitutionDoc,
           since, until) -> dict:
    """What the window cost, in the project's native unit, against the cap the
    month is measured on. Spend on its own is a vanity number — the desk makes
    the same point pairing it with evidence — so the caller prints it beside
    what it bought."""
    usd, tokens_in, tokens_out, cache_creation, cache_read = session.execute(
        select(
            func.coalesce(func.sum(LedgerEntry.usd), 0.0),
            func.coalesce(func.sum(LedgerEntry.tokens_in), 0),
            func.coalesce(func.sum(LedgerEntry.tokens_out), 0),
            func.coalesce(func.sum(LedgerEntry.cache_creation_tokens), 0),
            func.coalesce(func.sum(LedgerEntry.cache_read_tokens), 0),
        ).where(
            LedgerEntry.project_id == project.id,
            LedgerEntry.at > since,
            LedgerEntry.at <= until,
            LedgerEntry.kind != LedgerKind.REVENUE,
        )
    ).one()
    usd_mode = project.billing_mode == "api_key"
    from yaaf.server.janitor import month_to_date

    mtd_usd, mtd_tokens = month_to_date(session, project.id)
    return {
        "unit": "usd" if usd_mode else "tokens",
        "window": (round(float(usd), 4) if usd_mode
                   else int(tokens_in) + int(tokens_out)
                   + int(cache_creation) + int(cache_read)),
        "month_to_date": round(float(mtd_usd), 4) if usd_mode else int(mtd_tokens),
        "cap": cons.monthly_usd_cap if usd_mode else cons.monthly_token_cap,
    }


def _run_lines(session: Session, project: Project, since, until) -> list[dict]:
    """Runs, and specifically the ones that bought nothing. A dead run is
    metered exactly like a live one, so a window whose spend looks reasonable
    can still have thrown a third of itself away."""
    rows = list(session.execute(
        select(Run).where(
            Run.project_id == project.id,
            Run.created_at > since,
            Run.created_at <= until,
            Run.kind != RunKind.CHAT_SESSION,
        )
    ).scalars())
    if not rows:
        return []
    dead = [r for r in rows if r.status in DEAD_STATUSES]
    lines = [_line(
        "runs", f"{len(rows)} {_plural(len(rows), 'run')} in all",
        count=len(rows), where="records/runs",
    )]
    if dead:
        refusals = [r.last_refusal for r in rows if r.last_refusal]
        detail = f" — the last wall hit was “{refusals[-1][:90]}”" if refusals else ""
        lines.append(_line(
            "runs_dead",
            f"{len(dead)} of them died (failed, reaped, killed or cut off for "
            f"refusals){detail}",
            tone="bad", count=len(dead), where="records/runs",
        ))
    return lines


def _self_acting_lines(session: Session, project: Project, since, until) -> list[dict]:
    """What decided itself. The platform's whole promise is that things resolve
    without the chairman — approvals auto-deny, bets die on their date, budgets
    pause the project. The cost of that promise is that he can miss all of it,
    so every automatic resolution in the window is named here.
    """
    lines: list[dict] = []

    expired_approvals = list(session.execute(
        select(Approval).where(
            Approval.project_id == project.id,
            Approval.decided_at > since,
            Approval.decided_at <= until,
            Approval.decided_by_user_id.is_(None),
        ).order_by(Approval.decided_at)
    ).scalars())
    if expired_approvals:
        titles = ", ".join(f"“{a.title[:60]}”" for a in expired_approvals[:3])
        lines.append(_line(
            "approvals_expired",
            f"{len(expired_approvals)} {_plural(len(expired_approvals), 'approval')} "
            f"timed out unanswered and {_plural(len(expired_approvals), 'was', 'were')} "
            f"decided by the constitution's default ({titles})",
            tone="bad", count=len(expired_approvals), where="records/decisions",
        ))

    expired_bets = list(session.execute(
        select(Hypothesis).where(
            Hypothesis.project_id == project.id,
            Hypothesis.status == HypothesisStatus.EXPIRED,
            Hypothesis.kill_date > since,
            Hypothesis.kill_date <= until,
        )
    ).scalars())
    if expired_bets:
        lines.append(_line(
            "bets_expired",
            f"{len(expired_bets)} {_plural(len(expired_bets), 'bet')} hit "
            f"{_plural(len(expired_bets), 'its', 'their')} kill date — work behind "
            f"{_plural(len(expired_bets), 'it', 'them')} is blocked until you extend or "
            "the CEO pivots",
            tone="bad", count=len(expired_bets), where="work",
        ))

    unreviewed = int(session.execute(
        select(func.count(AuditSample.id)).where(
            AuditSample.project_id == project.id,
            AuditSample.status == AuditSampleStatus.EXPIRED,
            AuditSample.decided_at > since,
            AuditSample.decided_at <= until,
        )
    ).scalar_one())
    if unreviewed:
        lines.append(_line(
            "samples_expired",
            f"{unreviewed} spot-{_plural(unreviewed, 'check')} expired unreviewed — "
            "those closes stand unaudited",
            tone="bad", count=unreviewed, where="work",
        ))

    frozen = int(session.execute(
        select(func.count(Message.id))
        .join(Channel, Channel.id == Message.channel_id)
        .where(
            Channel.project_id == project.id,
            Message.frozen_at > since,
            Message.frozen_at <= until,
        )
    ).scalar_one())
    if frozen:
        lines.append(_line(
            "threads_frozen",
            f"the pair breaker froze {frozen} {_plural(frozen, 'thread')} — two agents "
            "were talking without moving any task",
            tone="bad", count=frozen, where="records/messages",
        ))

    for notice in session.execute(
        select(Notice).where(
            Notice.project_id == project.id,
            Notice.created_at > since,
            Notice.created_at <= until,
            Notice.kind.in_(list(SELF_ACTING_NOTICES)),
        ).order_by(Notice.created_at)
    ).scalars():
        tone = "good" if notice.kind in (
            NoticeKind.COMMITMENT_RECOVERED, NoticeKind.COMMITMENT_MET,
            NoticeKind.REVENUE,
        ) else "bad"
        lines.append(_line(
            f"notice_{notice.kind}", SELF_ACTING_NOTICES[notice.kind], tone=tone,
        ))
    return lines


def _your_decisions_lines(session: Session, project: Project, since, until) -> list[dict]:
    """What the chairman himself did in the window. Present so the brief reads
    as a record of the period rather than a list of things done to him — and
    because after four days away, what you decided on Tuesday is not something
    you reliably remember either."""
    lines: list[dict] = []
    decided = list(session.execute(
        select(Approval).where(
            Approval.project_id == project.id,
            Approval.decided_at > since,
            Approval.decided_at <= until,
            Approval.decided_by_user_id.is_not(None),
        )
    ).scalars())
    if decided:
        granted = sum(1 for a in decided if a.status == ApprovalStatus.APPROVED)
        lines.append(_line(
            "you_decided",
            f"you decided {len(decided)} {_plural(len(decided), 'approval')} "
            f"({granted} approved, {len(decided) - granted} denied)",
            count=len(decided), where="records/decisions",
        ))
    amendments = list(session.execute(
        select(Amendment).where(
            Amendment.project_id == project.id,
            Amendment.created_at > since,
            Amendment.created_at <= until,
            Amendment.cancelled_at.is_(None),
        ).order_by(Amendment.created_at)
    ).scalars())
    if amendments:
        changed = sorted({key for a in amendments for key in (a.changes or {})})
        lines.append(_line(
            "rules_changed",
            f"you changed {len(changed)} {_plural(len(changed), 'rule')} "
            f"({', '.join(changed[:6])}{'…' if len(changed) > 6 else ''})",
            count=len(amendments), where="records/decisions",
        ))
    return lines


def _ceo_authority_lines(session: Session, project: Project, since, until) -> list[dict]:
    """What the CEO decided on its own authority, where the dial gave it one.

    These rows already exist and are already emailed; they are here because a
    brief that omitted them would be describing a different project than the
    one the autonomy dial actually built. Under the Supervised dial this is
    empty by construction.
    """
    rows = list(session.execute(
        select(AuditLog).where(
            AuditLog.project_id == project.id,
            AuditLog.at > since,
            AuditLog.at <= until,
            AuditLog.action.in_(["scope.self", "commitment.self_accept",
                                 "commitment.self_move"]),
        ).order_by(AuditLog.at)
    ).scalars())
    if not rows:
        return []
    moved_date = sum(1 for r in rows if r.action == "commitment.self_move")
    line = (f"the CEO exercised {len(rows)} {_plural(len(rows), 'decision')} on its own "
            "authority")
    if moved_date:
        line += f", including moving the delivery date {moved_date}×"
    return [_line("ceo_authority", line, count=len(rows), where="records/decisions")]


def _meeting_lines(session: Session, project: Project, since, until) -> list[dict]:
    lines: list[dict] = []
    for review in session.execute(
        select(Review).where(
            Review.project_id == project.id,
            Review.opened_at > since,
            Review.opened_at <= until,
        ).order_by(Review.number)
    ).scalars():
        filed = "the CEO has filed its variance narrative" if review.narrative else \
            "the CEO has not filed its narrative yet"
        lines.append(_line(
            "review_opened",
            f"review #{review.number} opened — {filed}",
            count=1, where="review",
        ))
    return lines


# ── what is waiting now (not window-scoped) ─────────────────────────────────────


def _waiting(session: Session, project: Project, cons: ConstitutionDoc) -> dict:
    """The present tense, deliberately kept apart from the past tense above.

    The desk owns this question and owns it well; repeating its ranking here
    would be a second definition to drift. What the brief carries is the
    *count* and the soonest clock, so the last line of "what happened" can hand
    off to "and here is what that leaves you" without pretending to be the desk.
    """
    waiting = policy.awaiting_chairman(session, project, cons)
    pending = list(session.execute(
        select(Approval).where(
            Approval.project_id == project.id,
            Approval.status == ApprovalStatus.PENDING,
        ).order_by(Approval.expires_at)
    ).scalars())
    raised_flags = int(session.execute(
        select(func.count(Flag.id)).where(
            Flag.project_id == project.id,
            Flag.status == FlagStatus.RAISED,
        )
    ).scalar_one())
    open_review = session.execute(
        select(func.count(Review.id)).where(
            Review.project_id == project.id,
            Review.status == ReviewStatus.OPEN,
        )
    ).scalar_one()
    soonest = _aware(pending[0].expires_at) if pending else None
    return {
        "approvals": len(pending),
        "soonest_expiry": soonest.isoformat() if soonest else None,
        "plan": len(waiting["plan"]),
        "verdicts": len(waiting["verdicts"]),
        "commitment": waiting["commitment"] is not None,
        "flags_raised": raised_flags,
        "open_reviews": int(open_review),
        "total": (len(pending) + len(waiting["plan"]) + len(waiting["verdicts"])
                  + (1 if waiting["commitment"] else 0) + raised_flags + int(open_review)),
    }


# ── proof of life ───────────────────────────────────────────────────────────────


def _pulse(session: Session, project: Project, since, until) -> dict:
    """*Nothing is wrong* and *nothing is happening* are different screens.

    The desk already refuses to congratulate a chairman whose projects are all
    idle, and a brief with no lines has exactly the same duty: say whether the
    silence is a quiet week or a dead one. So an empty window still ships the
    three facts that tell them apart.
    """
    last_run_at = _aware(session.execute(
        select(func.max(Run.started_at)).where(Run.project_id == project.id)
    ).scalar_one())
    running = int(session.execute(
        select(func.count(Run.id)).where(
            Run.project_id == project.id,
            Run.status == RunStatus.RUNNING,
            Run.kind != RunKind.CHAT_SESSION,
        )
    ).scalar_one())
    open_tasks = len(policy.open_top_level_tasks(session, project.id))
    return {
        "last_run_at": last_run_at.isoformat() if last_run_at else None,
        "running": running,
        "open_tasks": open_tasks,
    }


# ── the board's four questions ──────────────────────────────────────────────────
#
# What follows is the summary the desk shows: a verdict, what moved toward it,
# what that cost, and the exceptions. The detail lines above are not deleted —
# they are the project's own page and the daily mail — but a chairman arriving
# after three days is not reading thirteen bullets, and a panel that gives a
# healthy project and a stalling one the same thirteen has told him nothing
# about which is which.


#: Below this a grouped integer is still readable, so it is left alone. The
#: same floor and the same one decimal place as `fmtUnit` in web/src/ui.tsx,
#: which formats the very same numbers two inches away on the desk: a briefing
#: saying 74M beside a row saying 73.7M is one number wearing two faces.
COMPACT_FLOOR = 10_000


def _human(amount: float, unit: str) -> str:
    """A quantity a person can hold in their head. `73,738,051 tokens` is not
    one: it is nine glyphs the eye slides off, and the desk printed it beside
    every project. Dollars are already readable and are left alone."""
    if unit == "usd":
        return f"${amount:,.2f}"
    amount = float(amount)
    if abs(amount) < COMPACT_FLOOR:
        return f"{amount:,.0f}"
    for cut, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(amount) >= cut:
            return f"{amount / cut:,.1f}".removesuffix(".0") + suffix
    return f"{amount:,.0f}"


def _money_text(money: dict) -> str:
    """The window's spend, in one clause, against the month it is capped on.

    Spend on its own is a vanity number, so it never travels alone; but it is
    printed on every window and not only the alarming ones, because a figure
    that appears only when it is bad teaches the reader to fear the sight of it.
    """
    unit = money["unit"]
    noun = "" if unit == "usd" else " tokens"
    spent = (f"{_human(money['window'], unit)}{noun} this window"
             if money["window"] else "Nothing spent this window")
    if not money["cap"]:
        return f"{spent}. No monthly cap is set."
    share = money["month_to_date"] / money["cap"]
    return (f"{spent} — {_human(money['month_to_date'], unit)} of the month's "
            f"{_human(money['cap'], unit)} budget ({share:.0%}).")


def _movement(session: Session, project: Project, lines: list[dict]) -> dict:
    """How far along this is, with the denominator attached.

    "12 requirements settled" is a number a chairman cannot judge, because
    twelve of forty and twelve of thirteen are opposite weeks wearing the same
    sentence. The denominator is the whole content.

    Measured per mode, because the modes do not share a unit. A DELIVER project
    has a finish line to count toward. An OPERATE project's obligations recur by
    design, so a fraction of them is meaningless and its record — met in N of
    the last M cycles — is the honest shape. An EXPLORE project has no scope to
    divide at all, and inventing a ratio for it would be the exact failure this
    function exists to fix.
    """
    from yaaf.server import velocity

    in_window = next((line["count"] for line in lines
                      if line["kind"] == "requirement_met"), 0) or 0
    published = next((line for line in lines if line["kind"] == "published"), None)
    latest = None
    if published is not None:
        latest = published["text"].split("(", 1)[-1].split(",")[0].rstrip(")…")
    tail = ""
    if published is not None:
        n = published["count"]
        tail = (f" {n} {_plural(n, 'deliverable')} published"
                + (f", latest {latest}." if latest else "."))

    if project.mode == ProjectMode.OPERATE:
        record = velocity.standing_record(session, project.id)
        worst = min(record, key=lambda r: (r["met"] - r["judged"], r["key"]),
                    default=None) if record else None
        if worst and worst["judged"]:
            text = (f"The standing obligations are being honoured: {worst['key']} — "
                    f"the weakest of them — was met in {worst['met']} of the last "
                    f"{worst['judged']} {_plural(worst['judged'], 'cycle')}.")
        else:
            text = "No cycle has been judged yet, so there is no record to report."
        return {"unit": "cycles", "settled": None, "total": None, "open": None,
                "in_window": in_window, "per_week": None,
                "published": published["count"] if published else 0,
                "latest": latest, "text": text + tail}

    if project.mode == ProjectMode.EXPLORE:
        proved = next((line["count"] for line in lines
                       if line["kind"] == "evidence_verified"), 0) or 0
        text = (f"{proved} {_plural(proved, 'piece')} of external proof landed this "
                "window. An Explore project has no scope to be a fraction of — "
                "what it has is evidence."
                if proved else
                "No external proof landed this window. An Explore project has no "
                "scope to be a fraction of — what it has is evidence.")
        return {"unit": "evidence", "settled": None, "total": None, "open": None,
                "in_window": proved, "per_week": None,
                "published": published["count"] if published else 0,
                "latest": latest, "text": text + tail}

    position = velocity.scope_position(session, project.id)
    pace = velocity.project_velocity(session, project.id)
    total, settled = position["total"], position["settled"]
    if total == 0:
        text = "Nothing is in scope yet, so there is nothing to be a fraction of."
    else:
        moved = (f" {in_window} of them since you last looked."
                 if in_window else " None of them moved this window.")
        text = (f"{settled} of {total} {_plural(total, 'requirement')} settled "
                f"true — {position['open']} still open.{moved}")
    return {
        "unit": "requirements", "settled": settled, "total": total,
        "open": position["open"], "in_window": in_window,
        "per_week": pace["per_week_3w"] or None,
        "published": published["count"] if published else 0,
        "latest": latest, "text": text + tail,
    }


def _verdict(session: Session, project: Project, movement: dict) -> dict:
    """Will this deliver what it promised, by when? — and the platform's answer
    to it, never the briefing's.

    This is the one question a chairman asks first and the one thing the desk
    never said. The answer already existed: `Commitment.status` is stamped by
    the janitor off its own re-projection, and the enum's docstring is explicit
    that no agent verb sits behind any of its transitions. So the verdict here
    is a **lookup**, and the module's promise not to grade itself survives
    intact.

    It is a lookup in a stricter sense than it may look. It reads the stamped
    `projected_done_at` and the janitor's own basis note rather than calling
    `commitment_projection` afresh — a live projection can disagree with the
    stamped status between two janitor ticks, and choosing which of them to
    believe would be exactly the judgement this function must not make.

    Where nothing has been promised there is no verdict to report, and the
    briefing says so. It must never manufacture "on track" out of a project
    nobody committed to: a green light nobody switched on is the worst thing
    this panel could learn to do.
    """
    def out(state: str, reason: str, source: str, **extra) -> dict:
        label, severity = VERDICT_LABELS[state]
        return {"state": state, "label": label, "severity": severity,
                "source": source, "reason": reason,
                "deliver_by": None, "projected_done_at": None,
                "days_late": None, "basis": None, **extra}

    if project.status in (ProjectStatus.PAUSED, ProjectStatus.STOPPED,
                          ProjectStatus.COMPLETED):
        state = "delivered" if project.status == ProjectStatus.COMPLETED else (
            "stopped" if project.status == ProjectStatus.STOPPED else "paused")
        return out(state, project.status_reason or "no reason was recorded",
                   "project_status")

    row = policy.open_commitment(session, project.id) or session.execute(
        select(Commitment).where(
            Commitment.project_id == project.id,
            Commitment.status == CommitmentStatus.MET,
        ).order_by(Commitment.created_at.desc()).limit(1)
    ).scalar_one_or_none()

    if row is None:
        settled = movement.get("settled")
        total = movement.get("total")
        where = (f" {settled} of {total} {_plural(total, 'requirement')} settled."
                 if total else "")
        return out(
            "unpromised",
            "Nothing has been promised here, so nothing can be on or off "
            "track." + where,
            "none")

    deliver_by = _aware(row.deliver_by)
    projected = _aware(row.projected_done_at)
    basis = (row.projection or {}).get("basis")
    days = _days_late(projected, deliver_by)
    dates = {"deliver_by": deliver_by.isoformat() if deliver_by else None,
             "projected_done_at": projected.isoformat() if projected else None,
             "days_late": days, "basis": basis}

    if row.status == CommitmentStatus.PROPOSED:
        return out("proposed",
                   f"The CEO has asked to commit to {_day(deliver_by)}. Until you "
                   "sign it nothing here is on track or late.",
                   "commitment", **dates)
    if row.status == CommitmentStatus.MET:
        return out("delivered",
                   f"Every requirement in the promise settled true. Promised "
                   f"{_day(deliver_by)}.", "commitment", **dates)
    if row.status == CommitmentStatus.BREACHED:
        return out("breached",
                   row.status_reason or f"{_day(deliver_by)} passed with scope "
                                        "still open.",
                   "commitment", **dates)
    if row.status == CommitmentStatus.AT_RISK:
        gap = (f", {days} {_plural(days, 'day')} past the date you signed"
               if days and days > 0 else "")
        return out("at_risk",
                   f"Promised {_day(deliver_by)}, projected {_day(projected)}{gap}. "
                   "New scope is frozen until you cut something or move the date."
                   + (f" ({basis}.)" if basis else ""),
                   "commitment", **dates)
    inside = (f" — {abs(days)} {_plural(abs(days), 'day')} inside it"
              if days is not None and days < 0 else "")
    return out("on_track",
               f"Promised {_day(deliver_by)}, projected {_day(projected)}{inside}."
               + (f" ({basis}.)" if basis else ""),
               "commitment", **dates)


def _day(dt: datetime | None) -> str:
    return dt.strftime("%-d %b").lstrip() if dt else "an unstated date"


def _days_late(projected: datetime | None, deliver_by: datetime | None) -> int | None:
    """How late the projection is, rounded so the answer is never flattering.

    `timedelta.days` floors toward negative infinity, which gets this wrong in
    both directions and always in the project's favour: a projection two hours
    BEFORE the date reads `-1` and printed "1 day inside it" for a promise two
    hours from missing, while one twenty hours PAST the date reads `0` and
    dropped the "days past the date you signed" clause altogether.

    Ceiling instead. It rounds lateness up and margin down, so the number a
    chairman is given is the pessimistic one — which is the only direction a
    delivery forecast may ever be wrong in on the page he decides from.
    """
    if projected is None or deliver_by is None:
        return None
    return math.ceil((projected - deliver_by).total_seconds() / 86_400)


def _exceptions(session: Session, project: Project, cons: ConstitutionDoc,
                lines: list[dict], verdict: dict, since, until) -> list[dict]:
    """The two or three facts worth interrupting a chairman with, worst first.

    Exception reporting, which is the shape a board actually runs on: when
    nothing is wrong this returns nothing, and the absence is the report. The
    old panel gave a healthy project and a stalling one thirteen bullets each,
    in the order the builders happened to be concatenated, which put "the
    project had work and started nothing for hours" underneath a list of file
    versions.

    Two lines are rewritten on the way out rather than passed through, because
    what serves the project's own page does not serve this one:

    * a dead-run count without its denominator is unreadable, and its cause is
      already known — one requirement usually ate them, and `worst_failure`
      names it;
    * the raw refusal text ("not_before is 8.6h out and the limit is 6h") is
      the platform talking to itself, and belongs in Records rather than in
      front of the board.
    """
    from yaaf.server import velocity

    covered = VERDICT_COVERS.get(verdict["state"], frozenset())
    out: list[dict] = []
    for line in lines:
        kind = line["kind"]
        if kind not in EXCEPTION_RANK or kind in covered:
            continue
        text, cause = line["text"], None
        if kind == "runs_dead":
            total = next((x["count"] for x in lines if x["kind"] == "runs"), 0) or 0
            dead = line["count"] or 0
            if total and (dead < DEAD_RUN_FLOOR
                          or dead / total < DEAD_RUN_SHARE):
                continue
            share = f" ({dead / total:.0%})" if total else ""
            text = (f"{dead} of {total} {_plural(total, 'run')} died without "
                    f"producing anything{share}")
            worst = velocity.project_velocity(session, project.id)["worst_failure"]
            if worst and worst["failure_load"] >= cons.commitment_failure_reforecast:
                cause = (f"one requirement, {worst['key']}, has absorbed "
                         f"{worst['failure_load']} of them")
        elif kind == "notice_stalled":
            text = ("it had work and started nothing for "
                    f"{cons.stall_hours:g} hours")
        out.append({"kind": kind, "rank": exception_rank(kind), "text": text,
                    "cause": cause, "where": line["where"],
                    "severity": "serious" if line["tone"] == "bad" else "warn"})
    out.sort(key=lambda e: e["rank"])
    return out[:MAX_EXCEPTIONS]


def _headline(project: Project, verdict: dict, exceptions: list[dict]) -> str:
    """The whole panel in one sentence: the verdict, and the worst thing under
    it. This is also the subject line of the daily mail.

    It used to be a concatenation of counts — "12 requirements settled true, 21
    tasks closed, 12 proofs verified — 73,738,051 tokens spent." Every clause of
    that was activity, none of it said whether the project would deliver, and
    the two facts that mattered were thirteen lines further down.

    The verdict leads because it is the question. The top exception follows it
    because the verdict word is a promise status and a promise status is silent
    about a project that has stopped working: "no date promised" alone would let
    a fourteen-hour stall pass as calm. Naming both is how neither is invented
    and neither is buried.
    """
    reason = verdict["reason"].rstrip()
    # `status_reason` is the chairman's or the janitor's own words and does not
    # reliably end in a stop, so one is added rather than assumed.
    if reason and reason[-1] not in ".!?":
        reason += "."
    sentence = f"{project.name} — {verdict['label']}. {reason}"
    if exceptions:
        first = exceptions[0]
        sentence += f" And {first['text']}"
        sentence += f" ({first['cause']})." if first["cause"] else "."
    return sentence


# ── the composers ───────────────────────────────────────────────────────────────


def _next_up(session: Session, project: Project, cons: ConstitutionDoc) -> dict | None:
    """The next thing this project is scheduled to do, and when.

    The briefing answered four of the five questions a chairman actually asks —
    what shipped, what is running, what is waiting on me, what it cost — and
    was silent on the fifth: *when does something happen next*. The arithmetic
    existed the whole time in :mod:`yaaf.server.agenda`, drawn on one screen
    nobody visits at the moment they want it.

    That silence is what a quiet window sounds like from the outside. A
    chairman looking at a board that finished everything it knew to queue has
    no way to tell "resting between things" from "stopped", and the record of
    that ambiguity is a run of messages — "why are we idle?", "are we on
    track?", "nothing has been done on the project in 13 hours. What is going
    on?" — every one of which cost a full model turn to answer from a screen
    that could have said it for nothing.

    Composed from rows like everything else here: no run, no tokens, and it
    still arrives on a project the platform has paused.
    """
    from yaaf.server.agenda import project_agenda

    agenda = project_agenda(session, project, cons, days=14)
    events = agenda.get("events") or []
    rhythms = [r for r in agenda.get("rhythms") or [] if r.get("next")]
    soonest_rhythm = min(rhythms, key=lambda r: r["next"]) if rhythms else None

    # A dated event beats a recurring wake when it lands first: "the delivery
    # date is Friday" is a different sentence from "the CEO wakes hourly".
    candidates: list[tuple[str, str, str]] = []
    if events:
        candidates.append((events[0]["at"], events[0]["label"], events[0]["kind"]))
    if soonest_rhythm is not None:
        candidates.append(
            (soonest_rhythm["next"], soonest_rhythm["label"], soonest_rhythm["kind"]))
    if not candidates:
        return {
            "at": None, "label": None, "kind": None,
            "why": (agenda["notes"][0] if agenda.get("notes") else
                    "Nothing is scheduled in the next fourteen days."),
            "live": agenda["live"],
        }
    at, label, kind = min(candidates)
    return {"at": at, "label": label, "kind": kind, "why": None,
            "live": agenda["live"]}


def project_briefing(session: Session, project: Project,
                     cons: ConstitutionDoc | None = None,
                     since: datetime | None = None,
                     until: datetime | None = None) -> dict:
    """One project's briefing over one window. Pure read — the mark is only
    ever stamped by :func:`mark_read`, so polling this page can never lose the
    chairman the window he has not looked at yet."""
    cons = cons or effective(session, project.id)
    until = until or utcnow()
    if since is None:
        since, source = window_start(session, project)
    else:
        source = "the window you asked for"
    since = min(_aware(since) or until, until)

    lines: list[dict] = []
    lines += _scope_lines(session, project, since, until)
    lines += _work_lines(session, project, since, until)
    lines += _proof_lines(session, project, since, until)
    lines += _deliverable_lines(session, project, since, until)
    lines += _self_acting_lines(session, project, since, until)
    lines += _ceo_authority_lines(session, project, since, until)
    lines += _your_decisions_lines(session, project, since, until)
    lines += _meeting_lines(session, project, since, until)
    lines += _run_lines(session, project, since, until)

    money = _money(session, project, cons, since, until)
    money["text"] = _money_text(money)
    pulse = _pulse(session, project, since, until)
    waiting = _waiting(session, project, cons)
    next_up = _next_up(session, project, cons)
    movement = _movement(session, project, lines)
    verdict = _verdict(session, project, movement)
    exceptions = _exceptions(session, project, cons, lines, verdict, since, until)
    # The run tally is bookkeeping, not an event: a window whose only lines are
    # "12 runs, 0 died" did nothing, and saying so is the point.
    substantive = [line for line in lines if line["kind"] not in ("runs",)]
    return {
        "project_id": project.id,
        "slug": project.slug,
        "name": project.name,
        "status": project.status,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "window_source": source,
        "window_hours": round((until - since).total_seconds() / 3600.0, 2),
        "quiet": not substantive,
        # A window can be quiet and the project still be on fire — a stall that
        # started before the window logs nothing inside it. So `quiet` keeps its
        # meaning (nothing happened) and `hushable` answers the different
        # question the panel actually asks: may this be collapsed to one line?
        "hushable": (not substantive and not exceptions
                     and verdict["severity"] in ("ok", "flat")),
        "headline": _headline(project, verdict, exceptions),
        "verdict": verdict,
        "movement": movement,
        "exceptions": exceptions,
        "lines": lines,
        "money": money,
        "pulse": pulse,
        "waiting": waiting,
        "next_up": next_up,
    }


def desk_briefing(session: Session, since: datetime | None = None,
                  hours: float | None = None) -> dict:
    """Every live project, each on its own window.

    Per-project windows rather than one shared clock, because the mark is per
    project: reading one brief must not consume another's. A caller may force a
    single window with `since`/`hours` — the CLI and a "show me the last 24
    hours" control both want that — and then every project is read on it.
    """
    now = utcnow()
    forced = since if since is not None else (
        now - timedelta(hours=hours) if hours else None)
    TERMINAL = (ProjectStatus.STOPPED, ProjectStatus.COMPLETED)
    out = []
    for project in session.execute(
        select(Project).where(Project.status.not_in(TERMINAL))
        .order_by(Project.created_at)
    ).scalars():
        out.append(project_briefing(session, project, since=forced, until=now))
    # Worst first, and sorted HERE rather than in the panel: the desk, the mail
    # and the MCP view must agree about which project is in the most trouble,
    # and creation order — which is what this used to be — put a stalled project
    # underneath three healthy ones for no reason but its age.
    order = list(VERDICT_LABELS)
    out.sort(key=lambda b: (order.index(b["verdict"]["state"]),
                            b["exceptions"][0]["rank"] if b["exceptions"] else 99))
    return {
        "as_of": now.isoformat(),
        "forced_window": forced.isoformat() if forced else None,
        "quiet": all(brief["quiet"] for brief in out) if out else True,
        "projects": out,
    }


# ── the same content, as text ───────────────────────────────────────────────────


def render_text(brief: dict) -> str:
    """The daily push. One composer, two deliveries — a panel and an email that
    cannot disagree about what happened, because there is no second query.

    The mail leads with the same summary the desk draws and then keeps the full
    detail below it, which the desk does not. That asymmetry is deliberate: a
    reader on the desk is one click from the project's own page, and a reader in
    their inbox is not. Dropping the detail here would make the mail worse at
    the only thing it is better at.
    """
    body = [
        brief["headline"],
        "",
        f"Window: {brief['window_source']} — {brief['since'][:16].replace('T', ' ')} "
        f"to {brief['until'][:16].replace('T', ' ')} UTC ({brief['window_hours']}h)",
    ]

    # In the order the payload gave them, never re-sorted here: the whole point
    # of ranking on the server is that every delivery leads with the same line.
    if brief.get("exceptions"):
        body += ["", "Needs attention"]
        for exc in brief["exceptions"]:
            body.append(f"  ! {exc['text']}"
                        + (f" — {exc['cause']}" if exc["cause"] else ""))

    movement = brief.get("movement")
    if movement:
        body += ["", "What moved", f"  {movement['text']}"]

    body += ["", f"Money: {brief['money']['text']}"]

    waiting = brief["waiting"]
    body.append("")
    if waiting["total"]:
        body.append(f"Waiting on you: {waiting['total']}")
        if waiting["approvals"]:
            when = (waiting["soonest_expiry"][:16].replace("T", " ") + " UTC"
                    if waiting["soonest_expiry"] else "unknown")
            body.append(f"  · {waiting['approvals']} approval(s); the soonest "
                        f"auto-decides {when}")
        if waiting["plan"]:
            body.append(f"  · {waiting['plan']} proposed requirement(s) to accept "
                        "— proposed scope is not scope")
        if waiting["verdicts"]:
            body.append(f"  · {waiting['verdicts']} observable close(s) only you can judge")
        if waiting["commitment"]:
            body.append("  · a delivery date to sign")
        if waiting["flags_raised"]:
            body.append(f"  · {waiting['flags_raised']} flag(s) the CEO raised with a "
                        "proposal")
        if waiting["open_reviews"]:
            body.append(f"  · {waiting['open_reviews']} review(s) open")
    else:
        body.append("Waiting on you: nothing.")
    # The fifth question, and the one that used to go unanswered: a quiet
    # window reads as a dead project unless something says when it moves next.
    next_up = brief.get("next_up")
    if next_up:
        body.append("")
        if next_up["at"]:
            when = next_up["at"][:16].replace("T", " ") + " UTC"
            body.append(f"Next: {next_up['label']} — {when}")
        else:
            body.append(f"Next: nothing scheduled. {next_up['why']}")

    body.append("")
    if brief["lines"]:
        body.append("What happened, in full")
        for line in brief["lines"]:
            marker = {"good": "+", "bad": "!"}.get(line["tone"], "·")
            body.append(f"  {marker} {line['text']}")
    else:
        pulse = brief["pulse"]
        body.append("What happened, in full")
        body.append("  · nothing — no scope settled, no work closed, no proof landed")
        body.append(f"  · {pulse['open_tasks']} task(s) open, {pulse['running']} run(s) "
                    f"in flight, last run "
                    + (pulse["last_run_at"][:16].replace("T", " ") + " UTC"
                       if pulse["last_run_at"] else "never"))
    body += [
        "",
        "This briefing was composed from the record — no agent wrote it and no run "
        "was spent on it.",
    ]

    return "\n".join(body)
