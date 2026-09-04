"""A data source can name the tools it may be called with, and an approval can carry one.

Two halves of the same rule. `mcp_servers[].tools` on a project's integrations is
an allowlist: with it set, data_fetch refuses any other tool on that source
instead of passing the name through to the vendor. That lives in an existing JSON
column, so nothing here touches it.

What needs columns is the way past the allowlist: a TOOL_REQUEST approval whose
`grant` names the exact source, tool and argument digest the chairman decided on,
and a `spent_at` that makes it good for exactly one call.

Both are nullable with no default, so every approval that already exists reads as
"carries no grant" — which is what an approval filed as prose has always been.

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("approvals", sa.Column("grant", sa.JSON()))
    add_column_if_missing("approvals", sa.Column("spent_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("approvals", "spent_at")
    op.drop_column("approvals", "grant")
