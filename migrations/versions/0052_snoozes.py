"""Snooze: one person's "not now" on one Inbox row, never past its clock.

`snoozes` holds who, which row (`item_key`), and until when. Personal by
construction: one person's snooze hides nothing from anyone else.

Revision ID: 0052
Revises: 0051
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import has_table
from yaaf.core.models import UTCDateTime

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A fresh install's create_all already built it.
    if has_table("snoozes"):
        return
    op.create_table(
        "snoozes",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("item_key", sa.String(120), nullable=False),
        sa.Column("until", UTCDateTime(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
    )
    op.create_index("ix_snoozes_user_item", "snoozes", ["user_id", "item_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_snoozes_user_item", table_name="snoozes")
    op.drop_table("snoozes")
