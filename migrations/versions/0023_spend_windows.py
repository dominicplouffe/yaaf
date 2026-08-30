"""A funded period for finding out what something actually costs.

Every budget conversation on this platform was an argument between two guesses.
A CEO with no cost model and no lever offered its chairman 3M a day, then 5M,
then 35M, inside two hours — and the chairman's reply was the design:

    "It's okay to say I don't know, so let's add 1 billion tokens for the next
    30 days and let's evaluate what we are doing right, what we are doing
    wrong, and adjust as time goes on. Then we refine the budget together, the
    CEO and chairman."

`spend_windows` is that object. An allowance, an end date, a question in
writing, and findings the PLATFORM writes from the ledger at close — the
commitment doctrine applied to money. The CEO may ask the question; only the
arithmetic answers it.

Guarded, as every post-baseline revision must be.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

import sqlalchemy as sa

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing
from yaaf.core.models import JSONVariant, UTCDateTime

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "spend_windows",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"),
                  nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("allowance", sa.Float, nullable=False),
        sa.Column("unit", sa.String(8), nullable=False),
        sa.Column("started_at", UTCDateTime, nullable=False),
        sa.Column("ends_at", UTCDateTime, nullable=False),
        sa.Column("checkpoint_hours", sa.Float, nullable=False, server_default="168"),
        sa.Column("last_checkpoint_at", UTCDateTime, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("opened_by_user_id", sa.String(40), nullable=False),
        sa.Column("findings", JSONVariant, nullable=True),
        sa.Column("closed_at", UTCDateTime, nullable=True),
        sa.Column("close_reason", sa.Text, nullable=True),
        sa.Column("adjustments", JSONVariant, nullable=False),
        sa.Column("created_at", UTCDateTime, nullable=False),
    )
    create_index_if_missing(
        "ix_spend_windows_project_status", "spend_windows", ["project_id", "status"]
    )


def downgrade() -> None:
    # Guarded-forward chain: the baseline owns table drops.
    pass
