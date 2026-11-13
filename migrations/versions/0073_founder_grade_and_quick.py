"""The Founder's background grade, and quick founding.

`founding_threads.goal_grade` is the platform's verdict on the draft's goal,
written by the grader's own thread beside the turn rather than by the turn, so
the draft never waits on it. It names the goal it graded; a reader holding a
newer goal ignores it.

`founding_threads.quick` marks a thread founded in quick mode — a brief in, a
running project out, nobody answering questions — and carries how far it got
(`phase`) and what the caller asked for. NULL is the interactive conversation.

Revision ID: 0073
Revises: 0072
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import JSONVariant

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("founding_threads", sa.Column("goal_grade", JSONVariant))
    add_column_if_missing("founding_threads", sa.Column("quick", JSONVariant))


def downgrade() -> None:
    from alembic import op

    op.drop_column("founding_threads", "quick")
    op.drop_column("founding_threads", "goal_grade")
