"""Deliverables reach the chairman where he reads.

`artifacts` gains `slack_ts`, exactly as `messages` has carried one since the
baseline: the marker that this row has been delivered to Slack, and the reason
one that fails is retried on the next tick instead of lost or double-posted.

The gap it closes is not cosmetic. The shelf is a screen you have to go to,
and the record says how that goes — "where are my deliverables?", "where is
the report??", "what are the file names for the deliverables that I wanted?",
and, from the desk whose memo was written and filed but never pushed, "can you
push to me every morning at 8:30 instead of me going to look for a file". A
deliverable nobody can find is indistinguishable from one that was never made.

Guarded, as every post-baseline revision must be.

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing, create_index_if_missing

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing(
        "artifacts", sa.Column("slack_ts", sa.String(40), nullable=True)
    )
    # The mirror sweeps "published here, not yet delivered" every tick, over
    # every project. Without this it is a full scan of the shelf each time.
    create_index_if_missing(
        "ix_artifacts_undelivered", "artifacts", ["project_id", "slack_ts"]
    )
    # Everything already on a shelf predates the feature and was never owed a
    # delivery. Left NULL it would read as a backlog, and the first tick after
    # deploy would empty an entire shelf into a boardroom in one batch — 17
    # files into one channel on the install this was written against, 232 on a
    # project that is merely paused rather than finished. A notification nobody
    # asked for, seventeen times, is not the fix this was written to be.
    #
    # Stamped rather than dated: this is not a real Slack ts and must never be
    # mistaken for one. It only ever answers "does this still owe a delivery?",
    # and the honest answer for a row published before the mechanism existed is
    # no.
    op.execute(
        sa.text("UPDATE artifacts SET slack_ts = :marker WHERE slack_ts IS NULL")
        .bindparams(marker="pre-delivery")
    )


def downgrade() -> None:
    # Guarded-forward chain: the baseline owns table drops, and a column that
    # only ever records "already delivered" is safe to leave in place.
    pass
