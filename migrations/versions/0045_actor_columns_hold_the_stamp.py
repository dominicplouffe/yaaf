"""The attribution columns are wide enough for the attribution.

`audit_log.actor` was VARCHAR(80) and the two event logs' `actor` VARCHAR(64).
The MCP layer writes `user:<id> via mcp:<client name>`, and a client name may
be 80 characters on its own, so a client named past about 37 had every write it
attempted rejected by Postgres as "value too long" — a 500 on the database
people deploy, and nothing at all on SQLite, which does not enforce a VARCHAR
length and is where the suite runs by default.

Widened in place. SQLite needs nothing: its declared length is advisory, and
the models' new width is what `create_all` builds on a fresh file.

Revision ID: 0045
Revises: 0044
"""
import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

#: Spelled out rather than imported from `yaaf.core.models`, so this revision
#: keeps meaning what it meant when it was written.
ACTOR_LEN = 200
COLUMNS = (("audit_log", 80), ("requirement_events", 64), ("task_events", 64))


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, _old in COLUMNS:
        op.alter_column(table, "actor", type_=sa.String(ACTOR_LEN),
                        existing_type=sa.String(), existing_nullable=False)


def downgrade() -> None:
    # Narrowing would fail on any row written by a long-named client, which is
    # every row this revision exists to admit; it is left wide on the way down.
    pass
