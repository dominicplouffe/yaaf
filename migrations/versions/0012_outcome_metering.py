"""Outcome metering: a run that changed nothing stops spending the work allowance.

Two columns on `runs`. `produced` counts accepted writes as they happen, so the
platform can tell a wake that moved the company from one that woke, thought and
left everything as it found it. `metered` is the refund: true at birth, set
false at finalize when nothing was produced.

Both are backfilled to the pre-change behaviour — every existing run keeps
counting — because reclassifying history would move the caps under a running
company retroactively. The split starts at deploy.

Guarded, as every post-baseline revision must be.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing, has_column

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing(
        "runs", sa.Column("produced", sa.Integer(), nullable=False, server_default="0")
    )
    add_column_if_missing(
        "runs",
        sa.Column("metered", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    if has_column("runs", "metered"):
        op.drop_column("runs", "metered")
    if has_column("runs", "produced"):
        op.drop_column("runs", "produced")
