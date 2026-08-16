"""The constitution: a project's frozen forcing functions.

A forcing function you can edit in a settings page is a default, not a forcing
function. So this document is written once at project start and changes only by
chairman amendment — written reason, cooling-off delay, audit entry, and
mandatory disclosure to the CEO agent in its next context pack.

Everything here is enforced by :mod:`yaaf.server.policy` as a refusal or an
automatic state change. Defaults derive from lana-project's numbers, the ones
that were paid for in burned cycles.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import Amendment, Constitution, utcnow

COOLING_OFF_HOURS = 72


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


class ConstitutionDoc(BaseModel):
    """Schema for the frozen policy document (stored as JSON)."""

    # Work discipline (lana §3, §5)
    queue_ceiling: int = Field(default=8, ge=1)
    subtask_ceiling: int = Field(default=10, ge=1)  # open subtasks per parent task
    external_ratio_min: float = Field(default=0.5, ge=0, le=1)
    external_ratio_applies_from: int = Field(default=3, ge=1)
    min_success_test_len: int = Field(default=15, ge=1)

    # Evidence discipline (lana §4) — drought counts verified/attested external only
    drought_hours: int = Field(default=24, ge=1)
    require_evidence_verification: bool = True

    # Autonomy thresholds (lana §6)
    approval_threshold_usd: float = Field(default=250.0, ge=0)
    approval_timeout_days: int = Field(default=7, ge=1)
    approval_timeout_decision: str = "deny"  # a vacationing chairman degrades, never deadlocks

    # Budgets — native unit of the project's billing mode
    monthly_usd_cap: float = Field(default=1000.0, ge=0)
    monthly_token_cap: int = Field(default=200_000_000, ge=0)
    budget_warn_ratio: float = Field(default=0.8, gt=0, lt=1)

    # Memory (lana §6b) — caps are refusals; curation is explicit, never auto-evict
    memory_cap_project: int = Field(default=60, ge=1)
    memory_cap_team: int = Field(default=40, ge=1)

    # Wake-loop prevention — the #1 way agent systems burn money
    wakes_per_agent_per_hour: int = Field(default=6, ge=1)
    runs_per_project_per_day: int = Field(default=150, ge=1)
    pair_breaker_messages: int = Field(default=10, ge=2)
    refusal_cap_per_run: int = Field(default=5, ge=1)

    # The chairman's reporting cadence. A wake ceiling stops the company
    # burning money on itself; this stops it burning money on *him* — the CEO
    # reported thirteen times in 2h20m before it existed. Enforced by
    # policy.check_chairman_report as a refusal on unprompted boardroom posts;
    # 0 hours means the chairman never asked for one and nothing is refused.
    chairman_report_interval_hours: float = Field(default=0.0, ge=0)
    chairman_report_window_local: str | None = Field(default=None)  # "09:00-20:00"
    chairman_report_timezone: str = Field(default="UTC")

    # Runs
    run_timeout_s: int = Field(default=1800, ge=60)

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


# Budget fields are exempt from the cooling-off (chairman's ruling,
# 2026-08-15): budgets are business reality — a customer changes their mind,
# a mistake needs correcting NOW, and a spending cap that lags reality by
# three days is itself a hazard. Everything else stays a forcing function:
# for behavioral rules, the delay is the point.
BUDGET_FIELDS = frozenset({"monthly_usd_cap", "monthly_token_cap", "budget_warn_ratio"})

# The reporting cadence is exempt on the same argument, earned the same way
# (2026-08-16): a chairman who says "stop, every four hours, this is costing
# me money" means the next run. Three days of the old cadence is precisely the
# hazard he is trying to end, and unlike a queue ceiling this rule constrains
# the company's demands on *him* — there is nobody it could be used against.
CADENCE_FIELDS = frozenset({"chairman_report_interval_hours",
                            "chairman_report_window_local",
                            "chairman_report_timezone"})

IMMEDIATE_FIELDS = BUDGET_FIELDS | CADENCE_FIELDS


def propose_amendment(
    session: Session,
    project_id: str,
    user_id: str,
    reason: str,
    changes: dict[str, Any],
) -> list[Amendment]:
    """Chairman-only (checked by the caller). Validates the whole overlay now
    so a bad amendment is refused immediately, then splits it: budget and
    cadence fields take effect in this very call; everything else waits out the
    cooling-off (applied by the janitor). Both halves are recorded and
    disclosed to the CEO. Returns the recorded amendments (one or two)."""
    effective(session, project_id).overlay(changes)  # raises if the result is invalid
    immediate = {k: v for k, v in changes.items() if k in IMMEDIATE_FIELDS}
    cooled = {k: v for k, v in changes.items() if k not in IMMEDIATE_FIELDS}
    amendments = []
    for subset, delay_hours in ((immediate, 0), (cooled, COOLING_OFF_HOURS)):
        if not subset:
            continue
        amendment = Amendment(
            id=ids.new_id(ids.AMENDMENT),
            project_id=project_id,
            proposed_by_user_id=user_id,
            reason=reason,
            changes=subset,
            effective_at=utcnow() + timedelta(hours=delay_hours),
        )
        session.add(amendment)
        amendments.append(amendment)
    if immediate:
        session.flush()
        apply_due_amendments(session, project_id)  # these are in force NOW
    return amendments


def apply_due_amendments(session: Session, project_id: str) -> list[Amendment]:
    """Called by the janitor: turn every amendment past its cooling-off into a
    new constitution version. Idempotent."""
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
