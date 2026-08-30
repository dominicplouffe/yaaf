"""The budget stops being a wall, and gains a conversation.

One number was doing two jobs: the plan you make before the work starts, and
the wall that stops a runaway. Because crossing the plan paused the project,
the plan became a thing to stay well back from — and every CEO on this
platform lowballed, refused money it was offered, and reported comfortable
figures. That is not timidity; it is the rational play when crossing your own
budget is fatal. The cost was that a safe number and an honest number look
identical from outside, so no number could be trusted.

`budget_overruns` is the move that did not exist: spend it, and report. The
platform writes the facts from the ledger, the CEO writes why, the chairman
writes what happens next — the same division that makes commitments work.

The two new constitution rules (`hard_stop_multiple`, `stakes`) need no
migration: the constitution is a JSON document with schema defaults, so
existing projects pick them up on read. Which means every live project's cap
becomes its BUDGET and gains 3x headroom before anything pauses — the
behaviour change is deliberate and is the point of the release.

Guarded, as every post-baseline revision must be.

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

import sqlalchemy as sa

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing
from yaaf.core.models import JSONVariant, UTCDateTime

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "budget_overruns",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"),
                  nullable=False),
        sa.Column("period", UTCDateTime, nullable=False),
        sa.Column("unit", sa.String(8), nullable=False),
        sa.Column("budget", sa.Float, nullable=False),
        sa.Column("spent_at_crossing", sa.Float, nullable=False),
        sa.Column("final_spent", sa.Float, nullable=True),
        sa.Column("crossed_at", UTCDateTime, nullable=False),
        sa.Column("breakdown", JSONVariant, nullable=True),
        sa.Column("ceo_account", sa.Text, nullable=True),
        sa.Column("accounted_at", UTCDateTime, nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("chairman_note", sa.Text, nullable=True),
        sa.Column("decided_at", UTCDateTime, nullable=True),
        sa.Column("decided_by_user_id", sa.String(40), nullable=True),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("project_id", "period", name="uq_overrun_project_period"),
    )
    create_index_if_missing(
        "ix_overruns_project_outcome", "budget_overruns", ["project_id", "outcome"]
    )


def downgrade() -> None:
    # Guarded-forward chain: the baseline owns table drops.
    pass
