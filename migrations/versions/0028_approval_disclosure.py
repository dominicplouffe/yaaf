"""An approval can carry a disclosure: what a chairman needs to decide well.

A TOOL_REQUEST filed as a title and a paragraph is a paragraph with buttons. What
makes a confident yes — and a confident no — possible is structure: the change
set before and after, the rows it rests on with their window and currency, the
estimated effect and its basis, whether it can be undone, and the case against,
written by the agent asking.

Nullable with no default, so every approval that already exists reads as
"carries no disclosure". The digest in `grant` is untouched: it binds the
arguments, and prose about them is not something an approval is spent on.

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("approvals", sa.Column("disclosure", sa.JSON()))


def downgrade() -> None:
    op.drop_column("approvals", "disclosure")
