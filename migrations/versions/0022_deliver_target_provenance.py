"""A Deliver project may hold a target date — if it says where the date is from.

`projects` gains `deadline_source`, the same provenance `cycles.due_source` has
carried since 0014, and Deliver stops refusing a deadline outright.

The refusal it replaces was well-meant and backwards. Deliver rejected a bare
deadline because "Deliver promises through a commitment" — so the founder, who
still had a date in mind, wrote it into the goal PROSE instead, where nothing
validates it, nothing re-projects it, and nothing can compare it to the
platform's own arithmetic. The date did not go away; it went somewhere
unreadable, and then paced the work anyway:

    CEO: Only R-07 remains open — the dogfooding weekly-note cadence. It's
         calendar-gated … Given the 6 Nov commitment, I'm not pulling that
         timeline forward artificially; it accrues honestly week by week.

Thirteen silent hours later the chairman asked what was going on. The measured
record is that every commitment on this install was met the day it was made,
against promises of up to 127 days.

So the date becomes a first-class field with provenance behind it, and the
doctrine `set_cycle_goal` already states is extended to Deliver: a deadline the
world imposes is a FACT and cites its source; anything else is a wish, and is
recorded as one rather than dressed as a promise.

Guarded, as every post-baseline revision must be.

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import JSONVariant

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing(
        "projects", sa.Column("deadline_source", JSONVariant, nullable=True)
    )


def downgrade() -> None:
    # Guarded-forward chain: the baseline owns table drops, and a nullable
    # provenance column is inert to any code that does not read it.
    pass
