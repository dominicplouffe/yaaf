"""A decision may decide itself only once it reached somebody.

Silence is a decision here, and it was a decision taken whether or not anyone
had been told: an install with no mail configured — every desktop install —
only logged its notices, and a laptop that slept through a clock expired it the
moment it woke. The rule now in force (docs/ux-redesign/decide-anywhere) needs
a record of who was reached, and decisions committed from surfaces other than
the dashboard need a preview ticket.

Schema: `reach_receipts` (delivered · seen · extended, per item) and
`commit_tickets` (a previewed decision, committable once). No data: an open
approval or question filed before this has no receipt, and is extended once if
its deadline arrives before anyone reads it — the rule, applied honestly.

Revision ID: 0066
Revises: 0065
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing
from yaaf.core.models import UTCDateTime

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "reach_receipts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("item_key", sa.String(120), nullable=False),
        sa.Column("user_id", sa.String(40)),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("channel", sa.String(24), nullable=False),
        sa.Column("at", UTCDateTime, nullable=False),
    )
    create_index_if_missing("ix_reach_receipts_project_item", "reach_receipts",
                            ["project_id", "item_key"])
    create_table_if_missing(
        "commit_tickets",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("item_key", sa.String(120), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("expires_at", UTCDateTime, nullable=False),
        sa.Column("used_at", UTCDateTime),
        sa.Column("created_at", UTCDateTime, nullable=False),
    )
    create_index_if_missing("ix_commit_tickets_token", "commit_tickets", ["token_hash"],
                            unique=True)


def downgrade() -> None:
    op.drop_table("commit_tickets")
    op.drop_table("reach_receipts")
