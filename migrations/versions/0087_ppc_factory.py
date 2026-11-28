"""An Amazon PPC Factory remembers whether it audits an account or runs one.

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
    add_column_if_missing("projects", sa.Column("ppc_mode", sa.String(16), nullable=True))


def downgrade() -> None:
    from alembic import op

    op.drop_column("projects", "ppc_mode")
