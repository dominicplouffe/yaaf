"""A Code Factory remembers whether it is being built or being kept.

Revision ID: 0077
Revises: 0076
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("projects", sa.Column("factory_phase", sa.String(16), nullable=True))


def downgrade() -> None:
    from alembic import op

    op.drop_column("projects", "factory_phase")
