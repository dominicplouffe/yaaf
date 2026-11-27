"""A run remembers which credential paid for it.

Revision ID: 0087
Revises: 0086
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("runs", sa.Column("paid_by_secret_id", sa.String(40), nullable=True))


def downgrade() -> None:
    from alembic import op

    op.drop_column("runs", "paid_by_secret_id")
