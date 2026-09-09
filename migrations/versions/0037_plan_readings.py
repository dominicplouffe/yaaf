"""The plan meter becomes something we are told, not something we ask for.

Revision ID: 0037
Revises: 0036
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import create_table_if_missing

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "plan_readings",
        sa.Column("token_fingerprint", sa.String(64), primary_key=True),
        sa.Column("windows", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    # Harmless to drop: every row is re-harvested from the next run on that
    # credential, because the reading was never ours to begin with.
    from alembic import op

    op.drop_table("plan_readings")
