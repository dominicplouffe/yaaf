"""A finish line the project does not author.

`Project.goal` was a Text column read by five callers, every one of them a
prompt or a screen. No policy check, no gate, no re-grade, no derived metric —
so the only thing the platform could actually verify was a shell command run
against the project's own workspace, and every requirement on every desk became
a grep for a heading in a document the project wrote itself.

`measurements` is one row per reading of one metric, from a source outside the
project. A row per reading rather than a column on the requirement, because the
trend is the information: "we are losing" is a fact about a sequence, and a
platform holding only the latest value can report a position but never a
direction.

Nothing is backfilled and nothing changes shape. A project with no metric
requirement behaves exactly as before, which is every project that exists on
the day this lands.

Revision ID: 0031
Revises: 0030
"""

from __future__ import annotations

import sqlalchemy as sa

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing
from yaaf.core.models import UTCDateTime

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "measurements",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"),
                  nullable=False),
        sa.Column("requirement_id", sa.String(40), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("at", UTCDateTime, nullable=False),
        # What was fetched, so a reading that looks wrong can be argued with.
        sa.Column("source_note", sa.Text(), nullable=True),
        # A reading that could not be taken is still a row: a metric that
        # silently stops measuring looks exactly like one reporting no change.
        sa.Column("error", sa.Text(), nullable=True),
    )
    create_index_if_missing(
        "ix_measurements_requirement_at", "measurements", ["requirement_id", "at"]
    )
    create_index_if_missing(
        "ix_measurements_project_at", "measurements", ["project_id", "at"]
    )


def downgrade() -> None:
    """Deliberately empty. Dropping `measurements` would discard the only
    record of what the outside world said, which no schema rollback needs and
    no operator can get back."""
