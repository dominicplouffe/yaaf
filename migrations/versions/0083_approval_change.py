"""An assist that asks the chairman's yes on a change carries the change.

Revision ID: 0083
Revises: 0082
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("approvals", sa.Column("change", sa.JSON(), nullable=True))


def downgrade() -> None:
    from alembic import op

    op.drop_column("approvals", "change")
