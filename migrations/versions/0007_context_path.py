"""Per-run context visibility: keep what the run actually sent to the model.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("context_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "context_path")
