"""The briefing mark: where the chairman last read up to.

One nullable column. `project_members.last_briefed_at` is stamped when the
chairman acknowledges a project's briefing, and the briefing composes its
window from it — so "since you were last here" means something the platform
can actually answer rather than a fixed 24 hours that is wrong every time he
is away for a weekend.

Per member and per project deliberately: a box-wide mark would be stamped by
opening one project and would silently swallow the three he did not open.

Guarded, as every post-baseline revision must be: on a fresh install
`create_all` has already built this from the models.

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing, has_column

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing(
        "project_members",
        sa.Column("last_briefed_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    if has_column("project_members", "last_briefed_at"):
        op.drop_column("project_members", "last_briefed_at")
