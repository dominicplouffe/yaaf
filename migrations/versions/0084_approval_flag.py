"""An assist that asks the chairman to answer a flag carries the flag.

Revision ID: 0084
Revises: 0083
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("approvals", sa.Column("flag", sa.JSON(), nullable=True))


def downgrade() -> None:
    from alembic import op

    op.drop_column("approvals", "flag")
