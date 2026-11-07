"""Where a run found the read cursors it moved: `runs.read_from`.

A context pack marks mail read by moving the agent's cursor, and a run can die
before any model reads the pack. Retries re-showed "recent" mail instead —
which, after the slice became the oldest pending, meant the channel's ten
oldest messages ever — and a wake the runner gave up on kept the cursor where
the dead run left it, so the message that woke it was read by nobody. This is
the position each cursor had before, so a retry can show exactly what the
first attempt was shown and a given-up wake can put it back. NULL, which every
existing run starts as, is a run that moved no cursor.

Revision ID: 0067
Revises: 0066
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import JSONVariant

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("runs", sa.Column("read_from", JSONVariant))


def downgrade() -> None:
    op.drop_column("runs", "read_from")
