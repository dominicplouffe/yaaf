"""M3 completion: approval nags, amendment cancellation, completion audits.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("approvals", sa.Column("nag_sent_at", sa.DateTime(timezone=True), nullable=True))
    add_column_if_missing("amendments", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    add_column_if_missing("tasks", sa.Column("audit_status", sa.String(16), nullable=True))
    add_column_if_missing("tasks", sa.Column("audit_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "audit_note")
    op.drop_column("tasks", "audit_status")
    op.drop_column("amendments", "cancelled_at")
    op.drop_column("approvals", "nag_sent_at")
