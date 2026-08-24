"""Crews: a named job with a team already on it.

The role taxonomy (0010) made thirty roles findable. It did nothing about the
harder question — who do I actually need? — which is answered by starting from
the work rather than the role. A crew carries the same TeamSpec shape the
create-project body already accepts, so applying one is a copy the chairman
then edits.

Reference data, seeded like roles (`yaaf seed-crews crews/`). Nothing in a
running project ever points back at a crew.

Guarded, as every post-baseline revision must be.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import create_table_if_missing
from yaaf.core.models import JSONVariant

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "crews",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("ceo_role_slug", sa.String(64), nullable=False),
        sa.Column("domains", JSONVariant),
        sa.Column("teams", JSONVariant),
        sa.Column("source", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("crews")
