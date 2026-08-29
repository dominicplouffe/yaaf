"""A third principal: model clients that speak MCP on the chairman's behalf.

One table. It is not a credential store and holds no secret value — only a
token hash, the scopes that token may reach, and the projects it may see. The
point of the row is that the chairman's own bearer never leaves the chairman's
hands.

Guarded, like the rest of the chain: on a fresh install create_all has already
built this from the models.

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import (
    create_index_if_missing,
    create_table_if_missing,
    has_column,
    has_table,
)

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "mcp_clients",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("project_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Unique, because two clients sharing a hash would make attribution a lie.
    #
    # Guarded on the column, not just the index: 0019 moves the hash out to
    # `mcp_tokens`, so on a fresh install create_all builds this table from
    # today's models — without `token_hash` — and indexing it would take the
    # whole chain down on a database that has nothing wrong with it.
    if has_column("mcp_clients", "token_hash"):
        create_index_if_missing("ix_mcp_clients_hash", "mcp_clients",
                                ["token_hash"], unique=True)


def downgrade() -> None:
    if has_table("mcp_clients"):
        op.drop_table("mcp_clients")
