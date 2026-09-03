"""A seat may be given more thinking, not only a better model.

`agents.model_override` has existed since the baseline; effort never had a
twin. So a project could put the writer on a stronger model and could not give
it more reasoning, because effort was readable only from the role version —
which is global, shared by every project on the install, and raising it there
raises it for all of them.

That asymmetry landed on exactly the wrong seats. The critic roles ship at
opus/xhigh; the roles that produce what the chairman actually reads ship at
sonnet/high, and no project could lift the second half without editing the
library out from under everybody else.

Nullable, no backfill, no server default: null means "whatever the role says",
which is what every existing seat already meant.

Guarded, as every post-baseline revision must be.

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing(
        "agents", sa.Column("effort_override", sa.String(16), nullable=True)
    )


def downgrade() -> None:
    # Guarded-forward chain: the baseline owns column drops.
    pass
