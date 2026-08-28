"""A founding turn outlives the request that started it.

The turn moved to a background thread — it takes about five minutes, and a
request held open for that loses the work to any refresh and to any proxy.
Two columns follow it, because the in-memory progress store cannot answer
questions that outlive the process.

Guarded, like the rest of the chain: on a fresh install create_all has already
built these from the models.

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing, has_column

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing(
        "founding_threads",
        sa.Column("turn_started_at", sa.DateTime(timezone=True), nullable=True))
    add_column_if_missing(
        "founding_threads", sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    if has_column("founding_threads", "last_error"):
        op.drop_column("founding_threads", "last_error")
    if has_column("founding_threads", "turn_started_at"):
        op.drop_column("founding_threads", "turn_started_at")
