"""The account registry, and wakes for everyone.

Stage E of the CEO Charter (docs/AUTONOMY.md). `accounts` records every
identity the company creates in the world — record-only, so wind-down is a
reading, not an archaeology. `agents.daily_at` generalizes the CEO's standup
to any agent, through the same heartbeat dedup and wake-rate brakes.

Guarded, as every post-baseline revision must be.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import (
    add_column_if_missing,
    create_index_if_missing,
    create_table_if_missing,
)

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "accounts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"),
                  nullable=False),
        sa.Column("service", sa.String(200), nullable=False),
        sa.Column("username", sa.String(200), nullable=False),
        sa.Column("owner", sa.String(16), nullable=False),
        sa.Column("credential_name", sa.String(120)),
        sa.Column("url", sa.String(500)),
        sa.Column("requirement_id", sa.String(40)),
        sa.Column("note", sa.Text()),
        sa.Column("created_by_agent_id", sa.String(40)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "service", "username"),
    )
    create_index_if_missing("ix_accounts_project", "accounts", ["project_id"])
    add_column_if_missing("agents", sa.Column("daily_at", sa.String(8)))


def downgrade() -> None:
    op.drop_table("accounts")
    op.drop_column("agents", "daily_at")
