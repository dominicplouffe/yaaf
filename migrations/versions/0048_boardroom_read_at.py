"""The boardroom's unread count.

`project_members.boardroom_read_at` is how far into the boardroom a member has
read; everything after it, written by somebody else, is that member's unread
count on the sidebar.

Existing members are stamped "now". Left NULL they would read as never having
opened the boardroom, and the first page load after deploy would put a
project's entire history on the badge — a count of hundreds that means nothing.

Revision ID: 0048
Revises: 0047
"""
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import UTCDateTime

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guarded, like every additive step since 0001: on a fresh install
    # `create_all` has already built it from the model.
    add_column_if_missing("project_members", sa.Column("boardroom_read_at", UTCDateTime()))
    # Bound through the column's own type, not CURRENT_TIMESTAMP: SQLite would
    # store that in a shape the model does not write.
    op.execute(
        sa.text("UPDATE project_members SET boardroom_read_at = :now "
                "WHERE boardroom_read_at IS NULL")
        .bindparams(sa.bindparam("now", datetime.now(UTC), type_=UTCDateTime()))
    )


def downgrade() -> None:
    op.drop_column("project_members", "boardroom_read_at")
