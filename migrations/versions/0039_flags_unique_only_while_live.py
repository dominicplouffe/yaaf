"""A flag's uniqueness is about live conditions, not about history.

`flags` carried `UNIQUE (project_id, kind, subject_id, status)` to stop the
janitor opening a fresh flag every tick for a condition already flagged. That
is the right rule for a LIVE flag and the wrong rule for a resolved one: a
resolved row is a thing that happened, and the same condition can happen
twice.

So the second close on any subject collided with the first one's row. Two ways
in, both reachable and both seen:

  * A missed cycle is flagged, the chairman presses "noted" (which resolves it,
    because a rolled cycle has nothing left to clear), the janitor opens a new
    flag for the same cycle on its next tick, and his second "noted" is an
    HTTP 500.
  * A forecast that flickers in and out of feasible opens, resolves, opens and
    resolves again — and the second resolve is an IntegrityError inside the
    janitor, where nobody is catching it.

The replacement is the same tuple with the resolved rows excluded, so nothing
about live flags changes and no existing row can violate it.

Revision ID: 0039
Revises: 0038
"""
import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

INDEX = "uq_flags_live_condition"
COLUMNS = ["project_id", "kind", "subject_id", "status"]
LIVE_ONLY = "status <> 'resolved'"

#: Postgres named the constraint for us, because 0014 declared it inline and
#: unnamed. `IF EXISTS` covers a database whose table came from `create_all`
#: against the current models and so never had it.
PG_CONSTRAINT = "flags_project_id_kind_subject_id_status_key"

#: The table as it must look afterwards. Spelled out here rather than imported
#: from `yaaf.core.models`, because a migration that reads today's models is a
#: migration that changes meaning every time somebody edits them.
FLAGS = sa.Table(
    "flags", sa.MetaData(),
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("project_id", sa.String(40), nullable=False),
    sa.Column("kind", sa.String(24), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("subject_id", sa.String(40)),
    sa.Column("summary", sa.Text(), nullable=False),
    sa.Column("arithmetic", sa.JSON()),
    sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("raised_at", sa.DateTime(timezone=True)),
    sa.Column("raised_in_message_id", sa.String(40)),
    sa.Column("proposal", sa.Text()),
    sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
    sa.Column("acknowledgement", sa.Text()),
    sa.Column("resolution", sa.Text()),
    sa.Column("closed_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)


def _has_the_old_constraint(inspector) -> bool:
    return any(set(u["column_names"]) == set(COLUMNS)
               for u in inspector.get_unique_constraints("flags"))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "flags" not in inspector.get_table_names():
        return  # a database that has not reached 0014 yet

    if bind.dialect.name == "postgresql":
        op.execute(f"ALTER TABLE flags DROP CONSTRAINT IF EXISTS {PG_CONSTRAINT}")
    elif _has_the_old_constraint(inspector):
        # SQLite cannot drop a constraint in place, and this one has no name to
        # drop it by. `copy_from` rebuilds the table from the definition above,
        # which is the same columns without it; the data is copied across.
        with op.batch_alter_table("flags", copy_from=FLAGS, recreate="always"):
            pass

    if INDEX not in {i["name"] for i in sa.inspect(bind).get_indexes("flags")}:
        op.create_index(INDEX, "flags", COLUMNS, unique=True,
                        sqlite_where=sa.text(LIVE_ONLY),
                        postgresql_where=sa.text(LIVE_ONLY))


def downgrade() -> None:
    # Deliberately not restoring the old constraint: rows this index allows are
    # rows that one forbids, so a database that has run for any length of time
    # on the new rule cannot go back without deleting a chairman's answers.
    op.drop_index(INDEX, table_name="flags")
