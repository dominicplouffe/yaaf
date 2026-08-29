"""Two-phase confirmation for the writes a model must not make in one call.

The preview issues a row; the write spends it. Server-side, because the
alternative is trusting the host client's permission prompt — and a prompt is
a suggestion, which is the thing this platform exists not to rely on.

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing, has_table

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "mcp_confirmations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("client_id", sa.String(40), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("tool", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_index_if_missing("ix_mcp_confirmations_token", "mcp_confirmations",
                            ["token_hash"], unique=True)


def downgrade() -> None:
    if has_table("mcp_confirmations"):
        op.drop_table("mcp_confirmations")
