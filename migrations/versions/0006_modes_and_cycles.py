"""Explore's window, Operate's rhythm, and cycle-scoped requirements.

Stage 8 of the rebuild: the `cycles` table (one turn of an Operate project's
rhythm, carrying the learning-loop narrative), `projects.deadline` (Explore's
bounded window on open scope), and `requirements.cycle_id` (each cycle plans
fresh; unsettled scope carries its id forward when the janitor rolls).

Guarded, as every post-baseline revision must be.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import (
    add_column_if_missing,
    create_index_if_missing,
    create_table_if_missing,
)

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "cycles",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("narrative", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "ordinal"),
    )
    create_index_if_missing(
        "ix_cycles_project_status", "cycles", ["project_id", "status"]
    )
    add_column_if_missing("projects", sa.Column("deadline", sa.DateTime(timezone=True)))
    add_column_if_missing("requirements", sa.Column("cycle_id", sa.String(40)))


def downgrade() -> None:
    op.drop_column("requirements", "cycle_id")
    op.drop_column("projects", "deadline")
    op.drop_table("cycles")
