"""What doctrine a project was founded under.

The Founder runs once. A fix to it reaches the projects founded after it and no
others, which is the whole of why the same problem kept coming back: six
well-aimed fixes landed between 30 August and 3 September, and the desk the
chairman was actually watching was founded on 26 August. It received none of
them and would have run nine more months on that day's doctrine, with the fact
visible only in `git log`.

A content fingerprint rather than a version number, because a version number is
a promise to do a chore later and the chore is what was skipped. NULL on every
project founded before this column existed — deliberately, because that is the
population it exists to find, and treating "unstamped" as current would hide
exactly the projects that matter.

Revision ID: 0032
Revises: 0031
"""

from __future__ import annotations

import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("projects", sa.Column("doctrine_stamp", sa.String(16),
                                                nullable=True))


def downgrade() -> None:
    """Deliberately empty: dropping the stamp would silently re-classify every
    project as unknown-doctrine, which is not a rollback anyone wants."""
