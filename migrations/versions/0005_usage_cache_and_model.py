"""Usage metering: cache token columns and per-run model attribution.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("runs", sa.Column("cache_creation_tokens", sa.Integer(),
                                            nullable=False, server_default="0"))
    add_column_if_missing("runs", sa.Column("cache_read_tokens", sa.Integer(),
                                            nullable=False, server_default="0"))
    add_column_if_missing("runs", sa.Column("model", sa.String(64), nullable=True))
    add_column_if_missing("runs", sa.Column("effort", sa.String(16), nullable=True))
    add_column_if_missing("ledger", sa.Column("cache_creation_tokens", sa.Integer(),
                                              nullable=False, server_default="0"))
    add_column_if_missing("ledger", sa.Column("cache_read_tokens", sa.Integer(),
                                              nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("ledger", "cache_read_tokens")
    op.drop_column("ledger", "cache_creation_tokens")
    op.drop_column("runs", "effort")
    op.drop_column("runs", "model")
    op.drop_column("runs", "cache_read_tokens")
    op.drop_column("runs", "cache_creation_tokens")
