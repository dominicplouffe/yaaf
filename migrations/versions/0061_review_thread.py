"""The meeting has a thread.

`reviews.thread_id` — the boardroom message that opened this meeting's "say
something back" exchange, set when the chairman first speaks on it. One
thread per meeting, so the governance-reserve replies the meeting may spend
have an edge, and the record of what was said at the meeting is one thread
rather than the whole room.

Revision ID: 0061
Revises: 0060
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("reviews", sa.Column("thread_id", sa.String(40)))


def downgrade() -> None:
    op.drop_column("reviews", "thread_id")
