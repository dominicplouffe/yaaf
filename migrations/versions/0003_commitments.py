"""The commitment: a delivery date the platform owns.

Stage 5 of the rebuild. One table — the projection, the frozen basis and the
status walk all live on it; everything else stage 5 added is behaviour
(policy refusals, the janitor's re-validation, notices) and needs no schema.

Guarded, as every post-baseline revision must be: on a fresh install
`create_all` has already built this from the models, and an unguarded CREATE
would roll 0001 back with it.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "commitments",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("deliver_by", sa.DateTime(timezone=True), nullable=False),
        sa.Column("proposed_by_agent_id", sa.String(40)),
        sa.Column("note", sa.Text()),
        sa.Column("accepted_by_user_id", sa.String(40)),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("constitution_version", sa.Integer()),
        sa.Column("scope_snapshot", sa.JSON(), nullable=False),
        sa.Column("projected_done_at", sa.DateTime(timezone=True)),
        sa.Column("projection", sa.JSON()),
        sa.Column("status_reason", sa.Text()),
        sa.Column("met_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_index_if_missing(
        "ix_commitments_project_status", "commitments", ["project_id", "status"]
    )


def downgrade() -> None:
    op.drop_table("commitments")
