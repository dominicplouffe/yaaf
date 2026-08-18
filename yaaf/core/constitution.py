"""The constitution: the limits a project's agents cannot talk their way out of.

Every rule here is enforced by :mod:`yaaf.server.policy` as a refusal or an
automatic state change, server-side. That is what makes it binding: an agent
cannot be argued out of a rule it never held, however persuasive the argument.
Defaults derive from lana-project's numbers, the ones paid for in burned cycles.

The constraint runs on the *agents*, not on the chairman. He changes any rule
whenever he likes and it binds from the next call — the delay that used to sit
in front of behavioural changes protected nobody in practice, and it made
being wrong about a number cost three days. What survives is the accounting: a
written reason on every change, an audit entry, and mandatory disclosure to the
CEO in its next context pack. The company still cannot move its own goalposts;
the man who owns it never had to be stopped from moving them.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import Amendment, Constitution, utcnow


def parse_report_window(raw: object) -> tuple[time, time] | None:
    """``"09:00-20:00"`` → (09:00, 20:00). Returns None for absent, malformed,
    or midnight-wrapping windows — the validator below turns None-from-a-value
    into a refusal, while callers read None as "no window"."""
    if not raw:
        return None
    try:
        start_s, end_s = str(raw).split("-", 1)
        start = time(*(int(part) for part in start_s.strip().split(":", 1)))
        end = time(*(int(part) for part in end_s.strip().split(":", 1)))
    except (TypeError, ValueError):
        return None
    return (start, end) if start < end else None


def _rule(default: Any, *, group: str, label: str, help: str, **constraints: Any) -> Any:
    """A constitution field that can describe itself.

    Every rule carries its own group, label and help text, so the settings page
    is *generated* from this schema rather than from a hand-kept list beside it.
    That list existed and went stale exactly as you would expect: six of
    twenty-four rules were editable, and the memory caps — the ones a full
    company actually needs — were not among them.
    """
    return Field(default=default, title=label, description=help,
                 json_schema_extra={"group": group}, **constraints)


class ConstitutionDoc(BaseModel):
    """Schema for the frozen policy document (stored as JSON)."""

    # Work discipline (lana §3, §5)
    queue_ceiling: int = _rule(
        8, ge=1, group="Work discipline", label="Open tasks at once",
        help="Top-level tasks the company may have open at once. Forces finishing over "
             "starting.")
    subtask_ceiling: int = _rule(
        10, ge=1, group="Work discipline", label="Open subtasks per task",
        help="How far a lead may decompose one parent task before it must land something.")
    external_ratio_min: float = _rule(
        0.5, ge=0, le=1, group="Work discipline", label="Minimum share of outward-facing work",
        help="Fraction of open tasks that must produce evidence from outside the company. "
             "Stops a company that only talks to itself.")
    external_ratio_applies_from: int = _rule(
        3, ge=1, group="Work discipline", label="Board size before that share is enforced",
        help="Below this many open tasks the ratio is not checked — three tasks cannot be "
             "half of anything useful.")
    min_success_test_len: int = _rule(
        15, ge=1, group="Work discipline", label="Shortest allowed success test",
        help="Characters. A success test shorter than this is not falsifiable, so the "
             "platform refuses the task.")

    # Evidence discipline (lana §4) — drought counts verified/attested external only
    drought_hours: int = _rule(
        24, ge=1, group="Evidence", label="Hours without proof before a drought",
        help="With no verified external evidence for this long, only evidence-producing "
             "tasks may be created until proof lands.")
    require_evidence_verification: bool = _rule(
        True, group="Evidence", label="External evidence must be verified",
        help="Evidence counts only once the platform has fetched and checked it. Evidence "
             "nobody can check is opinion.")

    # Autonomy thresholds (lana §6)
    approval_threshold_usd: float = _rule(
        250.0, ge=0, group="Autonomy", label="Spending that needs your approval",
        help="USD. Spends at or above this, and anything legally ambiguous, wait for you.")
    approval_timeout_days: int = _rule(
        7, ge=1, group="Autonomy", label="Days before an approval expires",
        help="An unanswered approval decides itself at this point, so a vacationing "
             "chairman degrades the company instead of deadlocking it.")
    approval_timeout_decision: str = _rule(
        "deny", group="Autonomy", label="What an expired approval decides",
        help="'deny' or 'approve'. Default-deny: silence is not consent.")

    # Budgets — native unit of the project's billing mode
    monthly_usd_cap: float = _rule(
        1000.0, ge=0, group="Budgets", label="Monthly budget (USD)",
        help="Hard stop: the project pauses itself at 100%. The unit under API-key billing.")
    monthly_token_cap: int = _rule(
        200_000_000, ge=0, group="Budgets", label="Monthly token cap",
        help="The budget unit under subscription billing. Same hard stop at 100%.")
    budget_warn_ratio: float = _rule(
        0.8, gt=0, lt=1, group="Budgets", label="Warn at this share of the budget",
        help="The CEO is told to cut burn at this point — before the platform pauses the "
             "project for it.")

    # Memory (lana §6b) — caps are refusals; curation is explicit, never auto-evict
    memory_cap_project: int = _rule(
        60, ge=1, group="Memory", label="Project memory slots",
        help="Live entries the CEO may keep. At the cap, new entries are refused until it "
             "compacts or forgets one.")
    memory_cap_team: int = _rule(
        40, ge=1, group="Memory", label="Team memory slots",
        help="Live entries per team. Any member may write one; only the team's lead may "
             "compact or forget. Memory-heavy roles need more room than this.")
    memory_warn_ratio: float = _rule(
        0.85, gt=0, lt=1, group="Memory", label="Warn at this share of the slots",
        help="The scope's curator is told to compact at this point, so it curates on a wake "
             "it was having anyway instead of discovering the wall mid-write.")

    # Wake-loop prevention — the #1 way agent systems burn money
    wakes_per_agent_per_hour: int = _rule(
        6, ge=1, group="Wake-loop brakes", label="Wakes per agent per hour",
        help="The runaway-loop brake. Every wake is a paid model call.")
    runs_per_project_per_day: int = _rule(
        150, ge=1, group="Wake-loop brakes", label="Runs per day, whole project",
        help="The daily activity ceiling across every agent.")
    runs_per_project_per_hour: int = _rule(
        15, ge=1, group="Wake-loop brakes", label="Runs per hour, whole project",
        help="The pacing brake. The daily ceiling refills all at once, so without this a "
             "company can spend a whole day's allowance in the first hour and then sit "
             "dead until midnight with work stranded on the board. This is a trailing "
             "hour, not a clock hour, so it clears continuously rather than in a lump.")
    pair_breaker_messages: int = _rule(
        10, ge=2, group="Wake-loop brakes", label="Messages before a thread freezes",
        help="Two agents talking this many times in one thread with nothing landing are "
             "talking, not working; the thread freezes.")
    refusal_cap_per_run: int = _rule(
        5, ge=1, group="Wake-loop brakes", label="Refusals a run may hit",
        help="Past this the run's token is revoked. Rephrasing a refused request is not a "
             "strategy.")
    task_check_failures_before_block: int = _rule(
        3, ge=1, group="Wake-loop brakes", label="Failed checks before a task blocks",
        help="A task whose success test fails this many times is not one the claimant can "
             "finish. It blocks and becomes a question for whoever planned it, instead of "
             "buying another wake to fail the same way. Counts across re-claims; only a "
             "passing check resets it.")

    # The chairman's reporting cadence. A wake ceiling stops the company
    # burning money on itself; this stops it burning money on *him* — the CEO
    # reported thirteen times in 2h20m before it existed. Enforced by
    # policy.check_chairman_report as a refusal on unprompted boardroom posts;
    # 0 hours means the chairman never asked for one and nothing is refused.
    chairman_report_interval_hours: float = _rule(
        0.0, ge=0, group="Reporting to you", label="Hours between unprompted status reports",
        help="0 means you never asked for a cadence and nothing is refused. Replying to "
             "something you actually said is always allowed and is not a status.")
    chairman_report_window_local: str | None = _rule(
        None, group="Reporting to you", label="Only report during these hours",
        help="'09:00-20:00' in your local time, or blank for any hour.")
    chairman_report_timezone: str = _rule(
        "UTC", group="Reporting to you", label="Your timezone",
        help="IANA name, e.g. 'America/New_York'. The window above is read in this zone.")

    # Runs
    run_timeout_s: int = _rule(
        1800, ge=60, group="Runs", label="Run timeout (seconds)",
        help="A run past this is reaped and its task claim released. The CEO's "
             "resident chat session is exempt — it is meant to stay open for "
             "hours and dies only when its heartbeat stops.")

    @field_validator("chairman_report_window_local")
    @classmethod
    def _window_is_readable(cls, raw: str | None) -> str | None:
        if raw is not None and parse_report_window(raw) is None:
            raise ValueError(
                f"{raw!r} is not a reporting window: write it 'HH:MM-HH:MM' in the "
                "chairman's local time, with the start before the end")
        return raw

    @field_validator("chairman_report_timezone")
    @classmethod
    def _timezone_exists(cls, raw: str) -> str:
        try:
            ZoneInfo(raw)
        except Exception as exc:
            raise ValueError(f"{raw!r} is not an IANA timezone (e.g. 'America/New_York')") from exc
        return raw

    def overlay(self, changes: dict[str, Any]) -> ConstitutionDoc:
        """Apply a partial amendment overlay, re-validating the result."""
        merged = self.model_dump()
        merged.update(changes)
        return ConstitutionDoc.model_validate(merged)


def effective(session: Session, project_id: str, at: datetime | None = None) -> ConstitutionDoc:
    """The constitution in force for a project right now: the highest version
    whose effective_at has passed."""
    at = at or utcnow()
    row = session.execute(
        select(Constitution)
        .where(Constitution.project_id == project_id, Constitution.effective_at <= at)
        .order_by(Constitution.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise LookupError(f"project {project_id} has no effective constitution")
    return ConstitutionDoc.model_validate(row.body)


def create_initial(session: Session, project_id: str, doc: ConstitutionDoc) -> Constitution:
    row = Constitution(
        id=ids.new_id(ids.CONSTITUTION),
        project_id=project_id,
        version=1,
        body=doc.model_dump(),
        effective_at=utcnow(),
    )
    session.add(row)
    return row


# The fields a CEO may set itself via `directive_file` — the chairman's
# reporting preferences, which are instructions *to* the company about how to
# treat him rather than limits *on* it. Everything else in the constitution is
# the chairman's alone to change.
CADENCE_FIELDS = frozenset({"chairman_report_interval_hours",
                            "chairman_report_window_local",
                            "chairman_report_timezone"})


def propose_amendment(
    session: Session,
    project_id: str,
    user_id: str,
    reason: str,
    changes: dict[str, Any],
) -> list[Amendment]:
    """Chairman-only (checked by the caller). Validates the whole overlay — so a
    bad amendment is refused rather than half-applied — records it with its
    written reason, and puts it in force in this very call. Returns the
    recorded amendment in a list, since callers still iterate for auditing and
    disclosure.

    There is no delay. A rule the chairman has decided on is in force for the
    next agent call, and the record of who changed what, when and why is what
    makes it accountable — not a waiting period he was always able to wait out
    or work around.
    """
    effective(session, project_id).overlay(changes)  # raises if the result is invalid
    if not changes:
        return []
    amendment = Amendment(
        id=ids.new_id(ids.AMENDMENT),
        project_id=project_id,
        proposed_by_user_id=user_id,
        reason=reason,
        changes=dict(changes),
        effective_at=utcnow(),
    )
    session.add(amendment)
    session.flush()
    apply_due_amendments(session, project_id)  # in force NOW
    return [amendment]


def apply_due_amendments(session: Session, project_id: str) -> list[Amendment]:
    """Turn every amendment whose time has come into a new constitution version.
    Idempotent.

    New amendments are due the instant they are proposed, so in practice this
    runs once, inline, from ``propose_amendment``. The janitor still calls it
    each tick: amendments written before the cooling-off was removed may still
    be sitting in the table with a future ``effective_at``, and they land on
    schedule rather than being stranded.
    """
    now = datetime.now(UTC)
    due = (
        session.execute(
            select(Amendment)
            .where(
                Amendment.project_id == project_id,
                Amendment.applied_constitution_id.is_(None),
                Amendment.cancelled_at.is_(None),
                Amendment.effective_at <= now,
            )
            .order_by(Amendment.effective_at)
        )
        .scalars()
        .all()
    )
    applied = []
    for amendment in due:
        current = session.execute(
            select(Constitution)
            .where(Constitution.project_id == project_id)
            .order_by(Constitution.version.desc())
            .limit(1)
        ).scalar_one()
        doc = ConstitutionDoc.model_validate(current.body).overlay(amendment.changes)
        new = Constitution(
            id=ids.new_id(ids.CONSTITUTION),
            project_id=project_id,
            version=current.version + 1,
            body=doc.model_dump(),
            effective_at=now,
        )
        session.add(new)
        session.flush()
        amendment.applied_constitution_id = new.id
        applied.append(amendment)
    return applied
