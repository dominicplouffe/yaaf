"""Plan readings keep their past.

`plan_readings` holds one row per subscription, overwritten by every run, so
"how full did the five-hour window get last night?" had no answer. This table
keeps each reading for a month (yaaf.core.plan_usage.HISTORY_DAYS).

Revision ID: 0074
Revises: 0073
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "plan_reading_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token_fingerprint", sa.String(64), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("five_hour", sa.Float(), nullable=True),
        sa.Column("seven_day", sa.Float(), nullable=True),
        sa.Column("status", sa.String(24), nullable=True),
    )
    create_index_if_missing("ix_plan_history_print_at", "plan_reading_history",
                            ["token_fingerprint", "at"])


def downgrade() -> None:
    # Only history: the latest reading stays in plan_readings.
    from alembic import op

    op.drop_index("ix_plan_history_print_at", table_name="plan_reading_history")
    op.drop_table("plan_reading_history")
