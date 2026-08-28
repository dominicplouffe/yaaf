"""The briefing: what happened while you were not looking.

Every screen this product had was in the present tense. The desk says what
needs you *now*, the overview says what is true *now*, and Records is four
tables you read to reconstruct a weekend by hand. A chairman who comes back
after three days had no way to ask the only question he actually has — what
happened? — and the only mail he ever got was an alarm. A company that writes
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
it cannot flatter the company the way a self-authored status can.

The window is the interesting part. "Since you were last here" is not 24
hours: it is `project_members.last_briefed_at`, stamped when the chairman
acknowledges the panel, so a weekend away reads as a weekend. NULL means never
read, which composes from the project's start rather than an empty window — a
first visit should summarise the company, not nothing.

`render_text` is the same content for the daily push. One composer, two
deliveries: a panel and an email that can never disagree about what happened.
"""

from __future__ import annotations

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

#: Runs that ended without producing anything the company keeps. The same set
#: velocity calls waste, and named identically so the two cannot drift.
DEAD_STATUSES = (RunStatus.FAILED, RunStatus.REAPED, RunStatus.KILLED,
                 RunStatus.TERMINATED_REFUSALS)

#: Notices that are the platform acting on the company rather than telling it
#: something. These are the "decided itself" lines: each one is a wall that
#: moved without anybody's say-so, and the chairman is entitled to hear about
#: it in the same breath as the work.
SELF_ACTING_NOTICES = {
    NoticeKind.BUDGET_EXHAUSTED: "the platform paused the company — the monthly budget ran out",
    NoticeKind.BUDGET_WARNING: "the budget passed its warning line and the CEO was told to cut burn",
    NoticeKind.COMMITMENT_AT_RISK: "the delivery date moved to AT RISK — new scope is frozen",
    NoticeKind.COMMITMENT_BREACHED: "the delivery date passed with scope still open",
    NoticeKind.COMMITMENT_RECOVERED: "the delivery date came back into reach",
    NoticeKind.COMMITMENT_MET: "every requirement in the commitment settled true",
    NoticeKind.CYCLE_ROLLED: "the cycle rolled — unfinished scope carried forward",
    NoticeKind.EXPLORE_DEADLINE: "the deadline you set has passed",
    NoticeKind.STALLED: "the company had work and started nothing for hours",
    NoticeKind.MEMORY_FULL: "a memory scope hit its cap and is refusing writes",
    NoticeKind.REVENUE: "money came in, booked by a signed webhook",
}


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
        return created, "since this company started"
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
    hand every new company a "requirement settled true" in its first hour, and
    the first briefing a chairman ever reads would be a false one. It gets its
    own line, because the plan starting to add up is real news.

    **A standing obligation is met again every cycle by design.** The weekly
    memo going out is worth reporting and is not progress; conflating the two
    would make an Operate company look like it was finishing three things a
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
    one, because it is the case where the company believed itself."""
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
    pause the company. The cost of that promise is that he can miss all of it,
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
    brief that omitted them would be describing a different company than the
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

    The desk already refuses to congratulate a chairman whose companies are all
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


def _headline(project: Project, lines: list[dict], money: dict, pulse: dict,
              waiting: dict) -> str:
    """One sentence, composed from the facts above rather than written about
    them. Order is what a chairman asks in order: is it alive, did it finish
    anything, what did that cost."""
    if project.status in (ProjectStatus.PAUSED, ProjectStatus.STOPPED,
                          ProjectStatus.COMPLETED):
        reason = project.status_reason or "no reason recorded"
        return f"{project.name} is {project.status} — {reason}"
    if not lines:
        if pulse["running"]:
            return (f"Nothing settled, but {pulse['running']} "
                    f"{_plural(pulse['running'], 'run')} in flight right now.")
        if pulse["open_tasks"] == 0:
            return "Nothing happened, and there is nothing on the board to happen."
        return (f"Nothing happened — {pulse['open_tasks']} "
                f"{_plural(pulse['open_tasks'], 'task')} open and none of them moved.")
    counts = {line["kind"]: (line["count"] or 0) for line in lines}
    parts: list[str] = []
    if counts.get("requirement_met"):
        n = counts["requirement_met"]
        parts.append(f"{n} {_plural(n, 'requirement')} settled true")
    if counts.get("tasks_done"):
        n = counts["tasks_done"]
        parts.append(f"{n} {_plural(n, 'task')} closed")
    if counts.get("standing_honoured"):
        n = counts["standing_honoured"]
        parts.append(f"{n} standing {_plural(n, 'obligation')} honoured")
    if counts.get("evidence_verified"):
        n = counts["evidence_verified"]
        parts.append(f"{n} {_plural(n, 'proof')} verified")
    if not parts and counts.get("plan_gate_closed"):
        parts.append("the plan gate closed and the board opened")
    if not parts:
        # Nothing landed. Rather than a shrug, promote the loudest thing that
        # DID happen — a stall, a frozen thread, an approval that timed out is
        # the headline of that window, and burying it under "activity, but
        # nothing settled" is how a bad week reads as a dull one.
        setback = next((line for line in lines if line["tone"] == "bad"), None)
        parts.append("Nothing settled" + (f" — {setback['text']}" if setback else ""))
    sentence = ", ".join(parts)
    if money["window"]:
        unit = "$" if money["unit"] == "usd" else ""
        suffix = "" if money["unit"] == "usd" else " tokens"
        amount = (f"{money['window']:,.2f}" if money["unit"] == "usd"
                  else f"{money['window']:,.0f}")
        sentence += f" — {unit}{amount}{suffix} spent"
    if waiting["total"]:
        sentence += f". {waiting['total']} now waiting on you"
    return sentence + "."


# ── the composers ───────────────────────────────────────────────────────────────


def project_briefing(session: Session, project: Project,
                     cons: ConstitutionDoc | None = None,
                     since: datetime | None = None,
                     until: datetime | None = None) -> dict:
    """One company's briefing over one window. Pure read — the mark is only
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
    pulse = _pulse(session, project, since, until)
    waiting = _waiting(session, project, cons)
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
        "headline": _headline(project, substantive, money, pulse, waiting),
        "lines": lines,
        "money": money,
        "pulse": pulse,
        "waiting": waiting,
    }


def desk_briefing(session: Session, since: datetime | None = None,
                  hours: float | None = None) -> dict:
    """Every live company, each on its own window.

    Per-company windows rather than one shared clock, because the mark is per
    company: reading one brief must not consume another's. A caller may force a
    single window with `since`/`hours` — the CLI and a "show me the last 24
    hours" control both want that — and then every company is read on it.
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
    return {
        "as_of": now.isoformat(),
        "forced_window": forced.isoformat() if forced else None,
        "quiet": all(brief["quiet"] for brief in out) if out else True,
        "projects": out,
    }


# ── the same content, as text ───────────────────────────────────────────────────


def render_text(brief: dict) -> str:
    """The daily push. One composer, two deliveries — a panel and an email that
    cannot disagree about what happened, because there is no second query."""
    money = brief["money"]
    spent = (f"${money['window']:,.2f}" if money["unit"] == "usd"
             else f"{money['window']:,.0f} tokens")
    mtd = (f"${money['month_to_date']:,.2f}" if money["unit"] == "usd"
           else f"{money['month_to_date']:,.0f} tokens")
    cap = (f"${money['cap']:,.0f}" if money["unit"] == "usd"
           else f"{money['cap']:,.0f} tokens")
    body = [
        brief["headline"],
        "",
        f"Window: {brief['window_source']} — {brief['since'][:16].replace('T', ' ')} "
        f"to {brief['until'][:16].replace('T', ' ')} UTC ({brief['window_hours']}h)",
        "",
    ]
    if brief["lines"]:
        body.append("What happened")
        for line in brief["lines"]:
            marker = {"good": "+", "bad": "!"}.get(line["tone"], "·")
            body.append(f"  {marker} {line['text']}")
    else:
        pulse = brief["pulse"]
        body.append("What happened")
        body.append("  · nothing — no scope settled, no work closed, no proof landed")
        body.append(f"  · {pulse['open_tasks']} task(s) open, {pulse['running']} run(s) "
                    f"in flight, last run "
                    + (pulse["last_run_at"][:16].replace("T", " ") + " UTC"
                       if pulse["last_run_at"] else "never"))
    body += ["", f"Money: {spent} this window; {mtd} of {cap} this month."]

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
    body += [
        "",
        "This briefing was composed from the record — no agent wrote it and no run "
        "was spent on it.",
    ]
    return "\n".join(body)
