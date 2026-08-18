"""A failed run gave its notices back but destroyed its suggestions.

Pack construction consumes notices and suggestions alike, and _retry_failed_run
un-consumes the notices by the run that ate them. Suggestions carried no such
column, so a CEO run that died after building its context permanently deleted
every suggestion it had been handed — an employee's only authority over the
backlog, lost to a crash it had no part in.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing(
        "suggestions", sa.Column("consumed_by_run_id", sa.String(40), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("suggestions", "consumed_by_run_id")
