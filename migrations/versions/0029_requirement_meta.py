"""A requirement can carry what its owner would call it.

A requirement's `statement` is written by the CEO for the platform: precise, and
phrased for the person running the project. The owner who hired the project wants
a title, a one-line description in spreadsheet terms, when it lands and where.
Those are not something the platform checks — the acceptance test is — so they
live in their own nullable column rather than inside `acceptance_test`, which the
plan gate and the checker read.

Nullable with no default, so every requirement that already exists reads as
"carries no owner-facing copy".

Revision ID: 0029
Revises: 0028
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("requirements", sa.Column("meta", sa.JSON()))


def downgrade() -> None:
    op.drop_column("requirements", "meta")
