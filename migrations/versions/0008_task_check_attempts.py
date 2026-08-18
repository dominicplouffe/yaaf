"""Task-level check attempts: the counter the loop breaker runs on.

A task that keeps failing its success test used to bounce between CLAIMED and
CHECKING forever. The counter has to live on the task rather than the claim,
because the janitor releases a stuck claim back to READY and a per-claim
counter would be zeroed by the very loop it exists to catch.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import JSONVariant

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("tasks", sa.Column("check_failures", sa.Integer(),
                                             nullable=False, server_default="0"))
    add_column_if_missing("tasks", sa.Column("check_history", JSONVariant, nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "check_history")
    op.drop_column("tasks", "check_failures")
