"""The missing verbs: reopen, restage, reestimate, unsequence.

Stage A of the CEO Charter (docs/AUTONOMY.md). One column: the first
estimate, frozen, so a reestimate can move the plan arithmetic without ever
moving the number the bias multiplier scores against.

Guarded, as every post-baseline revision must be.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("requirements", sa.Column("est_runs_first", sa.Integer()))
    # Every existing estimate becomes its own first estimate: history has no
    # revisions, so the two numbers are the same by definition.
    op.execute(
        "UPDATE requirements SET est_runs_first = est_runs WHERE est_runs_first IS NULL"
    )


def downgrade() -> None:
    op.drop_column("requirements", "est_runs_first")
