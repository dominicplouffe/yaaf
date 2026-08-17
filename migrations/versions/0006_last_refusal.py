"""Refusal storms name the wall: keep the last refusal message on the run.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("runs", sa.Column("last_refusal", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "last_refusal")
