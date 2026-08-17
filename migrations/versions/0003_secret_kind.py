"""Secrets get a kind: service vs billing, enforced both directions.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing(
        "secrets",
        sa.Column("kind", sa.String(16), nullable=False, server_default="service"),
    )
    # Anything already attached as a project's billing credential IS one.
    op.execute(
        "UPDATE secrets SET kind = 'billing' WHERE id IN "
        "(SELECT billing_secret_id FROM projects WHERE billing_secret_id IS NOT NULL)"
    )


def downgrade() -> None:
    op.drop_column("secrets", "kind")
