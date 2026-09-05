"""A question to the chairman becomes a row with a clock on it.

The platform already knew how to hold a decision the chairman owes without
deadlocking on him: an approval carries `expires_at`, a nag, and a default it
takes when the window passes. But an approval BLOCKS, and filing one for a
decision the constitution already delegated is refused on purpose — and the
verb that refusal names is "ask him in the boardroom", which was a message and
nothing more. No clock, no nag, no desk row, no state at all.

So a CEO that had a finished deliverable and a closed cadence slot deferred the
ask to the next slot, and the next slot never came: closing the work that
produced the deliverable emptied the board, and an empty board is exactly what
suppresses the CEO's planning wake. Six hours of silence over a message that
was written, reviewed and ready — invisible from his desk, which said "nothing
waiting", and invisible to the CEO, which was never woken to look.

`asks` is the missing row. It never blocks. It carries the moment the CEO said
it would stop waiting and the action it will take at that moment, written down
before the wait rather than reconstructed after it.

Guarded, as every post-baseline revision must be.

Revision ID: 0030
Revises: 0029
"""

from __future__ import annotations

import sqlalchemy as sa

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing
from yaaf.core.models import UTCDateTime

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "asks",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"),
                  nullable=False),
        sa.Column("asked_by_agent_id", sa.String(40), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("answer_by", UTCDateTime, nullable=False),
        sa.Column("default_action", sa.Text, nullable=False),
        sa.Column("asked_in_message_id", sa.String(40), nullable=False),
        sa.Column("nag_sent_at", UTCDateTime, nullable=True),
        sa.Column("answered_at", UTCDateTime, nullable=True),
        sa.Column("answered_in_message_id", sa.String(40), nullable=True),
        sa.Column("lapsed_at", UTCDateTime, nullable=True),
        sa.Column("closed_at", UTCDateTime, nullable=True),
        sa.Column("closed_in_message_id", sa.String(40), nullable=True),
        sa.Column("created_at", UTCDateTime, nullable=False),
    )
    create_index_if_missing("ix_asks_project_status", "asks", ["project_id", "status"])


def downgrade() -> None:
    # Guarded-forward chain: the baseline owns table drops.
    pass
