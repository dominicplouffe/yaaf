"""Shortcuts: a person's sidebar pins, and a project's setup checklist.

- `users.pins` — the places one person pinned under a project in the sidebar.
  NULL means the default (Deliverables), so nobody who never touched their pins
  is frozen on today's default.
- `projects.setup` — what people decided about the setup checklist: the steps
  they skipped, and whether they closed it. Whether a step is done is read from
  the project itself, never stored.

Every project that has already started is closed here. The checklist is for a
project being set up; one that has been running for weeks would otherwise
greet its chairman with "Finish setting up" on the morning this ships.

Revision ID: 0056
Revises: 0055
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import JSONVariant, utcnow

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("users", sa.Column("pins", JSONVariant))
    add_column_if_missing("projects", sa.Column("setup", JSONVariant))
    _close_started(op.get_bind())


def _close_started(connection) -> None:
    projects = sa.table("projects", sa.column("setup", JSONVariant), sa.column("status", sa.String))
    connection.execute(projects.update()
                       .where(projects.c.setup.is_(None), projects.c.status != "draft")
                       .values(setup={"skipped": [], "closed_at": utcnow().isoformat(),
                                      "closed_by": "migration"}))


def downgrade() -> None:
    op.drop_column("projects", "setup")
    op.drop_column("users", "pins")
