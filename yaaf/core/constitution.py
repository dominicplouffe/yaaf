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

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import Amendment, Constitution, utcnow

COOLING_OFF_HOURS = 72


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

    # Runs
    run_timeout_s: int = Field(default=1800, ge=60)

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


def propose_amendment(
    session: Session,
    project_id: str,
    user_id: str,
    reason: str,
    changes: dict[str, Any],
) -> Amendment:
    """Chairman-only (checked by the caller). Validates the overlay now so a
    bad amendment is refused immediately, but it takes effect only after the
    cooling-off period — applied by the janitor, disclosed to the CEO."""
    effective(session, project_id).overlay(changes)  # raises if the result is invalid
    amendment = Amendment(
        id=ids.new_id(ids.AMENDMENT),
        project_id=project_id,
        proposed_by_user_id=user_id,
        reason=reason,
        changes=changes,
        effective_at=utcnow() + timedelta(hours=COOLING_OFF_HOURS),
    )
    session.add(amendment)
    return amendment


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
