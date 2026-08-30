"""An MCP grant sees every project its chairman chairs, and stops holding a list.

The per-client allowlist asked, at grant time, a question only the chairman's
project list can answer. A project founded after consent fell outside a list
frozen weeks earlier and read as one that does not exist, and the only way back
in was a second trip through the browser — which minted a *second* grant beside
the first, so the audit trail grew a name for every project the chairman added.

Chairmanship was always the gate behind it and is evaluated on every call, so
dropping the column widens nothing a chairman had not already decided by
chairing the project. Grants issued against a narrower list widen to it here,
which is the point: nobody re-authorizes to see what they already own.

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing, has_column, has_table

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

TABLES = ("mcp_clients", "oauth_codes")


def upgrade() -> None:
    for table in TABLES:
        # Guarded, as every post-baseline revision must be: a database built
        # from today's models by create_all never had the column.
        if has_table(table) and has_column(table, "project_ids"):
            with op.batch_alter_table(table) as batch:
                batch.drop_column("project_ids")


def downgrade() -> None:
    # Nullable on the way back: the lists themselves are gone, and an empty one
    # meant "every project this chairman chairs" — which is now what every
    # grant means anyway.
    for table in TABLES:
        if has_table(table):
            add_column_if_missing(table, sa.Column("project_ids", sa.JSON(), nullable=True))
