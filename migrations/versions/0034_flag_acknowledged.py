"""A flag closes by being answered, not by being raised.

`_resolve` refuses the historical kinds by design — a missed cycle cannot
un-miss — and was never called at all for `spend_window` or `budget_overrun`.
So a raised flag of those kinds had no path to RESOLVED anywhere in the repo,
and sat on the chairman's "needs you" list permanently, under a link that took
him to the boardroom and changed nothing.

The chairman had no verb over a flag at all: the only flag-mutating route was
the CEO's own `POST /agent/flags/raise`. These two columns are where his answer
goes.

Revision ID: 0034
Revises: 0033
"""

from __future__ import annotations

import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import UTCDateTime

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("flags", sa.Column("acknowledged_at", UTCDateTime, nullable=True))
    add_column_if_missing("flags", sa.Column("acknowledgement", sa.Text(), nullable=True))


def downgrade() -> None:
    """Deliberately empty. Dropping these loses the record of what the chairman
    said about news the platform obliged the CEO to bring him."""
