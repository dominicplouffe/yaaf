"""The founding moment gets a project: platform settings and founding threads.

Two tables, both new, both guarded — on a fresh install `create_all` has
already built them from the models.

`platform_settings` is the first configuration in this system that is not
project-scoped. `founding_threads` holds a conversation and the draft bundle
it is producing; a draft is not a project, and nothing here can spend money or
bind an agent.

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "platform_settings",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("key", sa.String(120), nullable=False, unique=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_table_if_missing(
        "founding_threads",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("transcript", sa.JSON(), nullable=False),
        sa.Column("draft_bundle", sa.JSON(), nullable=True),
        sa.Column("created_project_id", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_index_if_missing(
        "ix_founding_user_at", "founding_threads", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("founding_threads")
    op.drop_table("platform_settings")
