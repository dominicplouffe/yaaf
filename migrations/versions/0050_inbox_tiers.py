"""Decide, acknowledge, or neither: what the CEO puts in front of the chairman.

`approvals.capacity` carries a capacity request's dial, so approving it is
what raises the dial. `heads_ups` holds what he asked to be told, which he
acknowledges rather than approves. `chairman_notes` holds every note he writes
on a decision until the CEO answers it.

Revision ID: 0050
Revises: 0049
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing, has_table
from yaaf.core.models import JSONVariant, UTCDateTime

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("approvals", sa.Column("capacity", JSONVariant))
    # A fresh install's create_all already built both tables and their indexes.
    if has_table("heads_ups"):
        return
    op.create_table(
        "heads_ups",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("agent_id", sa.String(40), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("standing_order", sa.Text(), nullable=False),
        sa.Column("message_id", sa.String(40)),
        sa.Column("acknowledged_at", UTCDateTime()),
        sa.Column("created_at", UTCDateTime(), nullable=False),
    )
    op.create_table(
        "chairman_notes",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_id", sa.String(40), nullable=False),
        sa.Column("about", sa.String(400), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("answered_at", UTCDateTime()),
        sa.Column("reply", sa.Text()),
        sa.Column("standing", sa.Boolean()),
    )
    op.create_index("ix_heads_ups_project", "heads_ups", ["project_id", "acknowledged_at"])
    op.create_index("ix_chairman_notes_open", "chairman_notes",
                    ["project_id", "answered_at"])
    op.create_index("ix_chairman_notes_source", "chairman_notes",
                    ["source_kind", "source_id"])


def downgrade() -> None:
    op.drop_table("chairman_notes")
    op.drop_table("heads_ups")
    op.drop_column("approvals", "capacity")
