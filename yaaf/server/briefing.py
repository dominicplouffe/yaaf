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

The window is the interesting part. "Since you were last here" is not 24
hours: it is `project_members.last_briefed_at`, stamped when the chairman
acknowledges the panel, so a weekend away reads as a weekend. NULL means never
read, which composes from the project's start rather than an empty window — a
first visit should summarise the project, not nothing.

`render_text` is the same content for the daily push. One composer, two
deliveries: a panel and an email that can never disagree about what happened.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.constitution import (
    ConstitutionDoc,
    bar_name,
    effective,
    preset_name,
    tempo_name,
)
from yaaf.core.models import (
    Amendment,
    Approval,
    ApprovalStatus,
    Artifact,
    Ask,
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
from yaaf.server.dispatcher import heartbeat_only

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
#: Each entry is (what happened, why it matters). The second half is the same
#: sentence every time it appears, which is exactly why it is not part of the
#: first: a fact the reader is scanning for should not be buried behind a
#: clause he has already read forty times.
SELF_ACTING_NOTICES = {
    NoticeKind.BUDGET_EXHAUSTED: ("the platform paused the project",
                                  "the monthly budget ran out"),
    NoticeKind.BUDGET_WARNING: ("the budget passed its warning line",
                                "the CEO was told to cut burn"),
    NoticeKind.COMMITMENT_AT_RISK: ("the delivery date moved to AT RISK",
                                    "new scope is frozen"),
    NoticeKind.COMMITMENT_BREACHED: ("the delivery date passed with scope still open",
                                     None),
    NoticeKind.COMMITMENT_RECOVERED: ("the delivery date came back into reach", None),
    NoticeKind.COMMITMENT_MET: ("every requirement in the commitment settled true",
                                None),
    NoticeKind.CYCLE_ROLLED: ("the cycle rolled",
                              "unfinished scope carried forward"),
    NoticeKind.EXPLORE_DEADLINE: ("the deadline you set has passed", None),
    NoticeKind.STALLED: ("the project had work and started nothing for hours", None),
    NoticeKind.MEMORY_FULL: ("a memory scope hit its cap and is refusing writes", None),
    NoticeKind.REVENUE: ("money came in", "booked by a signed webhook"),
}

#: The three questions a briefing answers, in the order a chairman asks them.
#: Every line belongs to exactly one, and the panel prints them under these
#: headings rather than as one undifferentiated list — which is what made a
#: stall read like a tally of runs.
GROUPS = ("attention", "moved", "delegated")

#: What each group is called where the reader sees it. One table, so the panel
#: and the email cannot end up naming the same three groups differently.
GROUP_HEADINGS = {
    "attention": "Worth your eye",
    "moved": "What moved",
    "delegated": "The CEO handled this itself",
}

#: Lines that need the chairman even though nothing about them is *bad*: he is
#: being asked for something. Tone stays honest; the grouping is about who has
#: to do the next thing.
ASKS = {"requirement_proposed"}

#: A line that is the chairman's at every setting of the autonomy dial, because
#: no authority he could hand over makes it go away: money in or out, a promise
#: to the outside world breaking, the project stopping, what he actually gets,
#: and a signature only he can give.
MATERIAL = "material"

#: A line that was never his at any setting. How the work got done.
OPS = "ops"

#: Whose problem each kind of line is.
#:
#: The briefing used to print the same thirteen lines whether the chairman had
#: kept every authority or handed over all five — which makes the autonomy dial
#: a lie on the one screen where it should be loudest. A chairman who set the
#: dial to autonomous said *deal with it, I have given it to you*; answering him
#: with a management report is arguing with the setting.
#:
#: So each line names either MATERIAL, OPS, or the constitution rule that
#: governs it. A rule still reading `chairman` means he kept that authority and
#: therefore still wants the news; a rule reading anything else means he handed
#: it over, and the line folds into the CEO's own business.
#:
#: The split inside scope is the one worth stating outright: **an outcome is
#: material, its management is delegated.** A requirement settling true is what
#: he gets. A requirement entering scope, the plan gate opening, the CEO
#: exercising authority the dial gave it — that is how he gets it, and he said
#: he did not want to watch.
LINE_OWNER: dict[str, str] = {
    # Money, liveness, and what he actually ends up with.
    "notice_budget_exhausted": MATERIAL,
    "notice_budget_warning": MATERIAL,
    "notice_revenue": MATERIAL,
    "notice_stalled": MATERIAL,
    "notice_memory_full": MATERIAL,
    "notice_explore_deadline": MATERIAL,
    "notice_commitment_breached": MATERIAL,
    "notice_commitment_met": MATERIAL,
    "requirement_met": MATERIAL,
    "requirement_cut": MATERIAL,
    # The objective, measured. Material at every dial setting and not
    # negotiable by it: the split above is "an outcome is material, its
    # management is delegated", and this line IS the outcome. A chairman who
    # delegated the running of the project did not thereby delegate being told
    # whether it is winning — that is the one thing delegation is FOR.
    "metric": MATERIAL,
    "published": MATERIAL,
    # Only he can sign these, so only he can be at fault for them.
    "approvals_expired": MATERIAL,
    # And the mirror of it: a question whose window ran out, where the CEO then
    # did the thing it had told him it would do. Material at every dial setting
    # — it asked BECAUSE it wanted his read, and he did not give one.
    "asks_lapsed": MATERIAL,
    "rules_changed": MATERIAL,
    # Governed by the dial.
    "plan_gate_closed": "plan_authority",
    "plan_gate_reopened": "plan_authority",
    "tasks_done": "plan_authority",
    "tasks_blocked": "plan_authority",
    "requirement_accepted": "scope_authority",
    "requirement_proposed": "scope_authority",
    "standing_honoured": "scope_authority",
    "ceo_authority": "scope_authority",
    "bets_expired": "scope_authority",
    "notice_commitment_at_risk": "commitment_authority",
    "notice_commitment_recovered": "commitment_authority",
    "evidence_verified": "verdict_policy",
    "evidence_failed": "verdict_policy",
    "evidence_pending": "verdict_policy",
    "samples_expired": "verdict_policy",
    # Who did the work. The CEO staffs the project itself, so a hire or a
    # stand-down is a decision it made and not one he owes an answer on — but
    # he is told, because a roster that changed while he was away is the first
    # thing that makes a brief unreadable.
    "staffing": OPS,
    "seats_idle": MATERIAL,
    # How the work got done. His at no setting.
    "runs": OPS,
    "runs_dead": OPS,
    "threads_frozen": OPS,
    "review_opened": OPS,
    "you_decided": OPS,
    "notice_cycle_rolled": OPS,
}

#: Which setback earns the headline when several compete. A paused project
#: outranks a cut requirement; anything unlisted sorts last.
SEVERITY = (
    "notice_budget_exhausted",
    "notice_commitment_breached",
    "notice_stalled",
    "plan_gate_reopened",
    "notice_commitment_at_risk",
    "approvals_expired",
    "asks_lapsed",
    "evidence_failed",
    "bets_expired",
    "notice_memory_full",
    "samples_expired",
    "tasks_blocked",
    "threads_frozen",
    "notice_budget_warning",
    "requirement_cut",
    "notice_cycle_rolled",
    "notice_explore_deadline",
    "runs_dead",
)


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
          where: str | None = None, note: str | None = None) -> dict:
    """One fact, and separately the standing explanation of what that kind of
    fact means.

    `text` is the news — the part that is different this window. `note` is the
    part that is identical in every briefing this project will ever get: *the
    platform ran the acceptance test itself*, *an agent said it could not
    finish rather than closing it anyway*. Both are worth having; welding them
    into one sentence with an em-dash is what turned thirteen facts into a
    paragraph nobody finishes. Kept apart, the reader scans the news and reads
    the explanation only when a line surprises him.

    `tone` is what it means, never how loud to be about it: `good` landed,
    `bad` cost something, `flat` merely happened. `group` and `yours` are not
    settled here — they depend on the autonomy dial, which this function has no
    business reading. :func:`_triage` fills them in.
    """
    return {"kind": kind, "text": text, "tone": tone, "count": count,
            "where": where, "note": note, "group": None, "yours": None}


def _plural(n: int, one: str, many: str | None = None) -> str:
    return one if n == 1 else (many or f"{one}s")


def _keys(keys: list[str], show: int = 4) -> str:
    """A list of requirement keys the reader can hold in his head, and an
    honest count of the ones he cannot. `R-30, R-33, R-34, R-35, R-36, R-38…`
    was six opaque ids and an ellipsis that hid how many more there were."""
    if len(keys) <= show:
        return ", ".join(keys)
    return f"{', '.join(keys[:show])} and {len(keys) - show} more"


# ── the window's facts ──────────────────────────────────────────────────────────


def _metric_lines(session: Session, project: Project, since, until) -> list[dict]:
    """The objective, measured. First, because it is the only line here that is
    about whether the project is winning rather than about whether it is busy.

    This module adds no judgement of its own — it is arithmetic over the record,
    at zero tokens — and a metric reading is exactly that: a number the platform
    fetched from a source the project does not author, compared with a target
    the chairman accepted. It qualifies on the same grounds `MET` leads
    `_scope_lines`: no agent could have caused this line by asserting it.

    Only measured requirements produce lines, so a project whose finish line is
    still prose sees nothing new here, which is every project that exists today.
    """
    from yaaf.core.models import Measurement
    from yaaf.server import metrics

    # Windowed like every other line in this module. Without this the same
    # scoreboard line repeats in every brief forever, including briefs
    # regenerated for a past window, which is what trains a reader to skip a
    # section. A metric read once a day therefore appears on the days it was
    # read and not on the others.
    measured_in_window = {
        rid for (rid,) in session.execute(
            select(Measurement.requirement_id).where(
                Measurement.project_id == project.id,
                Measurement.at > since,
                Measurement.at <= until,
            )
        )
    }
    lines = []
    for requirement in metrics.metric_requirements(session, project.id):
        if requirement.id not in measured_in_window:
            continue
        board = metrics.scoreboard(session, requirement)
        if board["verdict"] == "unknown":
            continue
        tone = {"losing": "bad", "met": "good", "gaining": "good"}.get(board["verdict"], "flat")
        arrow = " -> ".join(str(v) for v in board["trend"][-3:])
        stale = " Last read failed; this number is older than it looks." if board["stale"] else ""
        lines.append(_line(
            "metric",
            f"{board['requirement_key']}: {board['value']} against a target of "
            f"{board['target']} ({board['verdict']}). Recent: {arrow}.{stale}",
            tone=tone,
        ))
    return lines


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
            f"({_keys(met)})",
            tone="good", count=len(met), where="plan",
            note="the platform ran the acceptance test itself",
        ))
    honoured = named(RequirementStatus.MET, lambda rid: rid in standing)
    if honoured:
        lines.append(_line(
            "standing_honoured",
            f"{len(honoured)} standing {_plural(len(honoured), 'obligation')} honoured "
            f"({_keys(honoured)})",
            tone="good", count=len(honoured), where="plan",
            note="recurring work, not scope landing",
        ))
    if plan_id is not None:
        gate = [e for e in events if e.requirement_id == plan_id]
        closed = [e for e in gate if e.to_status == RequirementStatus.MET]
        reopened = [e for e in gate if e.from_status == RequirementStatus.MET
                    and e.to_status != RequirementStatus.MET]
        if closed:
            lines.append(_line(
                "plan_gate_closed",
                "the plan gate closed — work may now be created",
                tone="good", count=len(closed), where="plan",
                note="the plan is staged, estimated and fits the money",
            ))
        if reopened:
            lines.append(_line(
                "plan_gate_reopened",
                "the plan gate reopened — no new work may be created",
                tone="bad", count=len(reopened), where="plan",
                note="the plan stopped adding up, and nothing starts until it does",
            ))
    accepted = named(RequirementStatus.PENDING, is_scope)
    if accepted:
        lines.append(_line(
            "requirement_accepted",
            f"{len(accepted)} {_plural(len(accepted), 'requirement')} entered scope "
            f"({_keys(accepted)})",
            count=len(accepted), where="plan",
        ))
    cut = named(RequirementStatus.CUT, is_scope)
    if cut:
        lines.append(_line(
            "requirement_cut",
            f"{len(cut)} {_plural(len(cut), 'requirement')} cut from scope "
            f"({_keys(cut)})",
            tone="bad", count=len(cut), where="plan",
        ))
    proposed = named(RequirementStatus.PROPOSED, is_scope)
    if proposed:
        lines.append(_line(
            "requirement_proposed",
            f"the CEO proposed {len(proposed)} new "
            f"{_plural(len(proposed), 'requirement')} ({_keys(proposed)})",
            count=len(proposed), where="plan",
            # Not welded into the fact: under a dial that gave scope to the CEO
            # it accepts these itself, and "for you to accept" would be the
            # panel telling him to do a job he handed over.
            note="proposed scope is not scope until it is accepted",
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
            f"{blocked} {_plural(blocked, 'task')} blocked",
            tone="bad", count=blocked, where="work",
            note="an agent said it could not finish rather than closing it anyway",
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
            f"{verified} {_plural(verified, 'piece')} of external proof verified",
            tone="good", count=verified, where="work",
            note="the platform fetched and snapshotted the source itself",
        ))
    if failed:
        lines.append(_line(
            "evidence_failed",
            f"{failed} evidence {_plural(failed, 'claim')} failed its check",
            tone="bad", count=failed, where="work",
            note="a failed claim counts toward nothing",
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
    # By document, not by row. A ticket revised four times is one deliverable
    # that moved, and printing "ticket.md v4, ticket.md v3, ticket.md v2" as
    # three of twenty published things overstated the week and buried the four
    # documents that were actually new. Rows are ordered newest first, so the
    # first sighting of a name is its latest version.
    latest: dict[str, str] = {}
    for artifact in artifacts:
        latest.setdefault(artifact.name, f"{artifact.name} v{artifact.version}")
    names = list(latest.values())
    revisions = len(artifacts) - len(names)
    return [_line(
        "published",
        f"{len(names)} {_plural(len(names), 'deliverable')} published "
        f"({_keys(names, show=3)})",
        tone="good", count=len(names), where="deliverables",
        note=(f"{revisions} of the {len(artifacts)} versions in the window were "
              "revisions of these" if revisions else None),
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


def _listed(names: list[str]) -> str:
    """The dispatcher's own joiner, so a list of teams reads the same in the
    brief as it does in the idle banner."""
    from yaaf.server.dispatcher import _listed as joined

    return joined(sorted(set(names)))


def _staffing_lines(session: Session, project: Project, cons, since, until) -> list[dict]:
    """Who joined, who finished, and who is costing without moving anything.

    The roster is the one thing on this page that can change while the chairman
    is away without any of his decisions being involved — the CEO hires and
    stands roles down itself. A brief that reported the work and not the people
    doing it would leave him reading Tuesday's numbers against Monday's
    assumption about who was on the project.
    """
    from yaaf.core.models import AuditLog

    lines: list[dict] = []
    moves = list(session.execute(
        select(AuditLog).where(
            AuditLog.project_id == project.id,
            AuditLog.action.in_(["team.hire", "team.stand_down", "team.recall"]),
            AuditLog.at > since,
            AuditLog.at <= until,
        ).order_by(AuditLog.at)
    ).scalars())
    if moves:
        named = {
            "team.hire": [], "team.stand_down": [], "team.recall": [],
        }
        for row in moves:
            named[row.action].append((row.detail or {}).get("team") or "a team")
        parts = [
            f"hired {_listed(named['team.hire'])}" if named["team.hire"] else "",
            f"stood down {_listed(named['team.stand_down'])}"
            if named["team.stand_down"] else "",
            f"recalled {_listed(named['team.recall'])}" if named["team.recall"] else "",
        ]
        said = ", ".join(part for part in parts if part)
        lines.append(_line(
            "staffing", f"The roster changed: {said}",
            count=len(moves), where="overview",
            note="Your CEO staffs this project itself, inside the `max_teams` rule. "
                 "Stand a role down or recall one yourself on the Overview roster.",
        ))

    # And the seats nobody has decided about. Read from the open flags rather
    # than recomputed, so the brief and the flag cannot disagree about which
    # seats are idle.
    from yaaf.server import flags as flag_lib

    idle = [f for f in flag_lib.open_flags(session, project.id)
            if f.kind == "seat_idle"]
    if idle:
        who = _listed([(f.arithmetic or {}).get("team") or "a team" for f in idle])
        lines.append(_line(
            "seats_idle",
            f"{who} spent runs and closed no work in {cons.staffing_review_days} days",
            tone="bad", count=len(idle), where="overview",
            note="A seat that is awake is not free: it reads its channels, buys inbox "
                 "runs, and counts as a mover in the capacity forecast. Your CEO owes "
                 "you either a stand-down or a reason.",
        ))
    return lines


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
    # What the runs were FOR. The dispatcher classifies every wake at the
    # moment it decides to spend one, and the answer was thrown away at the UI
    # boundary: it reached a run-kind filter on one screen and the spend CLI,
    # and never the chairman. So "this is costing too much" had no next
    # question attached to it — a bill is not a lever, and the difference
    # between forty runs that produced deliverables and forty runs of agents
    # acknowledging each other's messages is the whole of what to do about it.
    reading = sum(1 for r in rows if r.kind == RunKind.INBOX_RUN)
    planning = sum(1 for r in rows
                   if r.kind != RunKind.INBOX_RUN and heartbeat_only(r.trigger))
    working = len(rows) - reading - planning
    split = ", ".join(part for part in (
        f"{working} work" if working else "",
        f"{reading} reading each other" if reading else "",
        f"{planning} waking up to look around" if planning else "",
    ) if part)
    lines = [_line(
        "runs", f"{len(rows)} {_plural(len(rows), 'run')} in all",
        count=len(rows), where="records/runs",
        note=split if reading or planning else None,
    )]
    if dead:
        refusals = [r.last_refusal for r in rows if r.last_refusal]
        note = "failed, reaped, killed or cut off for refusals"
        if refusals:
            note += f"; the last wall hit was “{refusals[-1][:90]}”"
        lines.append(_line(
            # "of {len(rows)}", not "of them": this line is read under a
            # different heading from the tally, so it carries its denominator.
            "runs_dead", f"{len(dead)} of {len(rows)} runs died and bought nothing",
            tone="bad", count=len(dead), where="records/runs", note=note,
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

    lapsed_asks = list(session.execute(
        select(Ask).where(
            Ask.project_id == project.id,
            Ask.lapsed_at > since,
            Ask.lapsed_at <= until,
        ).order_by(Ask.lapsed_at)
    ).scalars())
    if lapsed_asks:
        titles = ", ".join(f"“{a.title[:60]}”" for a in lapsed_asks[:3])
        lines.append(_line(
            "asks_lapsed",
            f"{len(lapsed_asks)} {_plural(len(lapsed_asks), 'question')} to you ran out "
            f"of time and the CEO did what it had told you it would ({titles})",
            tone="bad", count=len(lapsed_asks), where="decisions",
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
            f"{_plural(len(expired_bets), 'its', 'their')} kill date",
            tone="bad", count=len(expired_bets), where="work",
            note=f"work behind {_plural(len(expired_bets), 'it', 'them')} is blocked "
                 "until you extend or the CEO pivots",
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
            f"{unreviewed} spot-{_plural(unreviewed, 'check')} expired unreviewed",
            tone="bad", count=unreviewed, where="work",
            note="those closes stand unaudited",
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
            f"the pair breaker froze {frozen} {_plural(frozen, 'thread')}",
            tone="bad", count=frozen, where="records/messages",
            note="two agents were talking without moving any task",
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
        text, note = SELF_ACTING_NOTICES[notice.kind]
        lines.append(_line(f"notice_{notice.kind}", text, tone=tone, note=note))
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


def _yours(kind: str, cons: ConstitutionDoc) -> bool:
    """Whether this line is still the chairman's news, per the dial he set.

    An unknown kind is his. A line nobody has classified should arrive loudly
    and get classified, never disappear into a fold because a table was not
    updated — the failure mode of a filter is silence, and silence is the one
    thing this panel exists to prevent.
    """
    owner = LINE_OWNER.get(kind, MATERIAL)
    if owner == MATERIAL:
        return True
    if owner == OPS:
        return False
    return getattr(cons, owner, "chairman") == "chairman"


def _triage(lines: list[dict], cons: ConstitutionDoc) -> list[dict]:
    """File every line by whose problem it is, then by what it means.

    Two passes that used to be neither. Ownership first, because a chairman who
    handed the CEO all five authorities does not want a line about scope
    entering the plan under any heading — the dial *is* his instruction about
    what to stop telling him. Then, among the lines that are still his, tone:
    a setback he owns can never be folded away.
    """
    for line in lines:
        line["yours"] = _yours(line["kind"], cons)
        if not line["yours"]:
            line["group"] = "delegated"
        elif line["tone"] == "bad" or line["kind"] in ASKS:
            line["group"] = "attention"
        else:
            line["group"] = "moved"
    return _ordered(lines)


def _worst(lines: list[dict]) -> dict | None:
    """The setback that earns the headline. Ranked, because a window can hold
    six of them and "the platform paused the project" is not interchangeable
    with "one requirement was cut"."""
    setbacks = [line for line in lines if line["group"] == "attention"
                and line["tone"] == "bad"]
    if not setbacks:
        return None
    def rank(line: dict) -> int:
        try:
            return SEVERITY.index(line["kind"])
        except ValueError:
            return len(SEVERITY)
    return min(setbacks, key=rank)


def _headline(project: Project, lines: list[dict], pulse: dict, waiting: dict,
              delegated: int = 0) -> str:
    """One sentence: what landed, and the worst thing that did not.

    It used to be three throughput counts and a token total — "15 requirements
    settled true, 26 tasks closed, 13 proofs verified — 101,638,431 tokens
    spent" — which is the least decision-relevant content on the panel, sitting
    in the one position everybody reads. Worse, it promoted a setback only when
    *nothing* landed, so a week that shipped fifteen requirements AND stalled
    for hours announced the fifteen and left the stall as grey line ten.

    So: progress, then the worst setback, then what that leaves waiting. Money
    is not here at all — the money row prints it directly underneath, and a
    number the reader is about to see twice is a number that stops being read.

    And it is composed only from the lines that are still the chairman's. A
    headline that names something he cannot find on the panel is a headline
    that sent him looking, which on an autonomous project means sending him
    into work he explicitly delegated.
    """
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
    lines = [line for line in lines if line["group"] != "delegated"]
    if not lines:
        # He handed over everything this window touched. Saying so in one
        # sentence is the whole promise of the dial — the alternative is a
        # management report he asked not to receive, and a blank panel that
        # cannot be told apart from a dead project.
        sentence = "Nothing needed you"
        if delegated:
            sentence += (f" — the CEO handled {delegated} "
                         f"{_plural(delegated, 'thing')} itself")
        elif pulse["running"]:
            sentence += f", and {pulse['running']} {_plural(pulse['running'], 'run')} " \
                        "are in flight right now"
        if waiting["total"]:
            sentence += f". {waiting['total']} now waiting on you"
        return sentence + "."
    counts = {line["kind"]: (line["count"] or 0) for line in lines}
    parts: list[str] = []
    if counts.get("requirement_met"):
        n = counts["requirement_met"]
        parts.append(f"{n} {_plural(n, 'requirement')} settled true")
    if counts.get("published"):
        n = counts["published"]
        parts.append(f"{n} {_plural(n, 'deliverable')} published")
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
    setback = _worst(lines)
    if parts:
        # Two, not four. "15 requirements settled true, 26 tasks closed, 13
        # proofs verified" spends the one line everybody reads on throughput;
        # the rest of the tally is four inches below under "What moved".
        sentence = ", ".join(parts[:2])
        # The "but" is the whole change: a good week with a stall in it is a
        # different week from a good week, and the reader learns that in the
        # first line rather than on line ten.
        if setback:
            sentence += f" — but {setback['text']}"
    else:
        # Nothing landed. Rather than a shrug, lead with the loudest thing that
        # DID happen: burying a stall under "activity, but nothing settled" is
        # how a bad week reads as a dull one.
        #
        # But only say it when it is true. The ladder above counts four kinds
        # of progress, and a dial that delegates all four still leaves lines
        # under "What moved" on the panel — so "Nothing settled" was printed
        # over a window that had visibly settled something, two inches below.
        # A headline may be brief; it may not contradict the list beneath it.
        landed = next((line for line in lines if line["group"] == "moved"), None)
        opener = landed["text"][0].upper() + landed["text"][1:] if landed \
            else "Nothing settled"
        sentence = opener + (f" — but {setback['text']}" if setback and landed
                             else f" — {setback['text']}" if setback else "")
    if waiting["total"]:
        sentence += f". {waiting['total']} now waiting on you"
    return sentence + "."


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


def _ordered(lines: list[dict]) -> list[dict]:
    """Lines in the order they should be read, not the order they were queried.

    The composer runs scope, then work, then proof, then deliverables, then
    self-acting, then decisions, then runs — which is a sensible order to write
    the code in and a useless one to read. It put a stall between "20
    deliverables published" and "the CEO exercised 26 decisions", at the same
    weight as both.

    Within a group the composer's order is kept, because it is already
    meaningful (scope before work before proof). Attention is the exception:
    there the worst thing goes first, since that group exists to be triaged.
    """
    def rank(line: dict) -> tuple[int, int]:
        group = GROUPS.index(line["group"])
        if line["group"] != "attention":
            return (group, 0)
        try:
            return (group, SEVERITY.index(line["kind"]))
        except ValueError:
            return (group, len(SEVERITY))
    return sorted(lines, key=rank)


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
    lines += _metric_lines(session, project, since, until)
    lines += _scope_lines(session, project, since, until)
    lines += _work_lines(session, project, since, until)
    lines += _proof_lines(session, project, since, until)
    lines += _deliverable_lines(session, project, since, until)
    lines += _self_acting_lines(session, project, since, until)
    lines += _ceo_authority_lines(session, project, since, until)
    lines += _your_decisions_lines(session, project, since, until)
    lines += _meeting_lines(session, project, since, until)
    lines += _run_lines(session, project, since, until)
    lines += _staffing_lines(session, project, cons, since, until)

    lines = _triage(lines, cons)
    money = _money(session, project, cons, since, until)
    pulse = _pulse(session, project, since, until)
    waiting = _waiting(session, project, cons)
    next_up = _next_up(session, project, cons)
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
        "headline": _headline(
            project, substantive, pulse, waiting,
            # Counted off `lines`, not `substantive`: the sentence names a
            # number the reader is about to see on the fold beside it.
            delegated=sum(1 for line in lines if line["group"] == "delegated")),
        "lines": lines,
        "groups": {group: sum(1 for line in lines if line["group"] == group)
                   for group in GROUPS},
        # How many of this window's lines the chairman still owns. Zero is the
        # answer an autonomous project should usually give, and the Desk uses
        # it rather than `quiet` to decide who gets a full block: "nothing
        # happened" and "nothing happened that is yours" are different
        # sentences, and only the second one scales with the dial.
        "needs_you": sum(1 for line in lines if line["yours"]),
        "autonomy": preset_name(cons.model_dump()),
        # Which cards this project runs on, or None for rules set one at a
        # time. Beside the autonomy dial because together they answer "what
        # kind of project is this": who holds the verbs, how often it wakes to
        # use them, and what the work is worth.
        "tempo": tempo_name(cons.model_dump()),
        "bar": bar_name(cons.model_dump()),
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
        # Under the same three headings the panel uses, and in the same order:
        # an email that groups differently from the screen is a second story
        # about the same window.
        for group, heading in GROUP_HEADINGS.items():
            rows = [line for line in brief["lines"] if line["group"] == group]
            if not rows:
                continue
            body.append(heading)
            for line in rows:
                marker = {"good": "+", "bad": "!"}.get(line["tone"], "·")
                body.append(f"  {marker} {line['text']}")
                if line.get("note"):
                    body.append(f"      {line['note']}")
            body.append("")
        body.pop()
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
    body += [
        "",
        "This briefing was composed from the record — no agent wrote it and no run "
        "was spent on it.",
    ]
    return "\n".join(body)
