"""The topology experiment: the suspicion, made answerable.

Stage 9 of the rebuild — the last one. `experiments` freezes the shared goal,
requirement set and constitution so "the arms were identical" is a checkable
fact; `experiment_arms` binds each topology to its project; and
`projects.topology` records the org shape as a fact instead of an inference.

Guarded, as every post-baseline revision must be.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing, create_table_if_missing

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "experiments",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("requirement_set", sa.JSON(), nullable=False),
        sa.Column("constitution", sa.JSON(), nullable=False),
        sa.Column("chairman_user_id", sa.String(40), sa.ForeignKey("users.id"),
                  nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_table_if_missing(
        "experiment_arms",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("experiment_id", sa.String(40), sa.ForeignKey("experiments.id"),
                  nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"),
                  nullable=False),
        sa.Column("topology", sa.String(24), nullable=False),
        sa.Column("stopped_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("experiment_id", "topology"),
    )
    add_column_if_missing(
        "projects",
        sa.Column("topology", sa.String(24), nullable=False,
                  server_default="ceo_leads_workers"),
    )


def downgrade() -> None:
    op.drop_column("projects", "topology")
    op.drop_table("experiment_arms")
    op.drop_table("experiments")
