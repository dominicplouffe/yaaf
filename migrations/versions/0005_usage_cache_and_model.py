"""Usage metering: cache token columns and per-run model attribution.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("cache_creation_tokens", sa.Integer(), nullable=False,
                                    server_default="0"))
    op.add_column("runs", sa.Column("cache_read_tokens", sa.Integer(), nullable=False,
                                    server_default="0"))
    op.add_column("runs", sa.Column("model", sa.String(64), nullable=True))
    op.add_column("runs", sa.Column("effort", sa.String(16), nullable=True))
    op.add_column("ledger", sa.Column("cache_creation_tokens", sa.Integer(), nullable=False,
                                      server_default="0"))
    op.add_column("ledger", sa.Column("cache_read_tokens", sa.Integer(), nullable=False,
                                      server_default="0"))


def downgrade() -> None:
    op.drop_column("ledger", "cache_read_tokens")
    op.drop_column("ledger", "cache_creation_tokens")
    op.drop_column("runs", "effort")
    op.drop_column("runs", "model")
    op.drop_column("runs", "cache_read_tokens")
    op.drop_column("runs", "cache_creation_tokens")
