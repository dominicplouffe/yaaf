"""What a cycle actually did, not just what it was called.

`CycleStatus` is one of three words, and MISSED covers a cycle that made nine
of ten requirements true and one that made none. The flag that obliges the CEO
to explain a missed cycle carried the ordinal, the timestamp and the goal — and
no numbers — even though the enum's own docstring says every flag is arithmetic.

So a desk whose cycle straddled a gameweek deadline spent a run a week
re-explaining the difference by hand: six times in five days, out of its own
allowance, for numbers that were already on the row.

Revision ID: 0033
Revises: 0032
"""

from __future__ import annotations

import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import JSONVariant

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("cycles", sa.Column("scorecard", JSONVariant, nullable=True))


def downgrade() -> None:
    """Deliberately empty. Cycles already closed keep whatever they recorded;
    dropping the column would lose the only per-cycle account of what landed."""
