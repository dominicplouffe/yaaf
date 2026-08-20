"""The status meeting, as a table the janitor owns.

Stage 7 of the rebuild. One table: the review — numbered, opened with zero
tokens, carrying the platform's frozen numbers, the CEO's variance narrative,
and the chairman's six levers with their reasons.

Guarded, as every post-baseline revision must be.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "reviews",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("numbers", sa.JSON(), nullable=False),
        sa.Column("narrative", sa.Text()),
        sa.Column("recommended", sa.Text()),
        sa.Column("narrative_run_id", sa.String(40)),
        sa.Column("narrative_filed_at", sa.DateTime(timezone=True)),
        sa.Column("decisions", sa.JSON(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_by_user_id", sa.String(40)),
        sa.Column("nag_sent_at", sa.DateTime(timezone=True)),
        sa.Column("escalated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "number"),
    )
    create_index_if_missing(
        "ix_reviews_project_status", "reviews", ["project_id", "status"]
    )


def downgrade() -> None:
    op.drop_table("reviews")
