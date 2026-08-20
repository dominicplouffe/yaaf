"""The baseline is create_all, so migrations must tolerate being early.

`0001_initial` builds the schema from the models rather than frozen DDL. On a
FRESH database that means today's tables and columns already exist before any
later revision runs — so an unguarded `op.create_table` raises "already
exists", and because Postgres DDL is transactional the failure rolls 0001 back
with it. The server then boots, answers /healthz 200, and 500s every real
request with `relation "projects" does not exist`.

add_column_if_missing has covered the column case since that outage. Nothing
covered the table case, only because every migration since has added columns
and never a table. The next one adds a table.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
import sqlalchemy as sa
from alembic import op
from alembic.migration import MigrationContext
from alembic.operations import Operations

from yaaf.core.migration_ops import (
    add_column_if_missing,
    create_index_if_missing,
    create_table_if_missing,
    has_column,
    has_table,
)


@contextmanager
def _migrating(engine):
    """Bind the module-level `alembic.op` proxy to a real engine.

    The guards call `op.get_bind()`, exactly as they will inside a revision, so
    a mock would prove nothing about what actually runs on deploy. This is the
    same install/remove the alembic runner does around each migration.
    """
    with engine.connect() as connection:
        operations = Operations(MigrationContext.configure(connection))
        Operations._install_proxy(operations)
        try:
            yield
        finally:
            operations._remove_proxy()


def test_creating_a_table_that_create_all_already_made_is_a_no_op(tmp_path):
    """The fresh-install path. Without the guard this raises and takes the
    whole baseline down with it."""
    engine = sa.create_engine(f"sqlite:///{tmp_path}/fresh.db")
    meta = sa.MetaData()
    sa.Table("cycles", meta, sa.Column("id", sa.String(40), primary_key=True))
    meta.create_all(engine)

    with _migrating(engine):
        create_table_if_missing("cycles", sa.Column("id", sa.String(40), primary_key=True))
        assert has_table("cycles")


def test_creating_a_table_that_is_genuinely_missing_still_creates_it(tmp_path):
    """The upgrade path: an existing database really does need the DDL."""
    engine = sa.create_engine(f"sqlite:///{tmp_path}/existing.db")

    with _migrating(engine):
        assert not has_table("cycles")
        create_table_if_missing("cycles", sa.Column("id", sa.String(40), primary_key=True))
        assert has_table("cycles")


def test_an_unguarded_create_table_really_would_fail(tmp_path):
    """The guard is only worth having if the thing it prevents is real. If
    this ever stops raising, the guards are dead weight and can go."""
    engine = sa.create_engine(f"sqlite:///{tmp_path}/boom.db")
    meta = sa.MetaData()
    sa.Table("cycles", meta, sa.Column("id", sa.String(40), primary_key=True))
    meta.create_all(engine)

    with _migrating(engine), pytest.raises(sa.exc.OperationalError, match="already exists"):
        op.create_table("cycles", sa.Column("id", sa.String(40), primary_key=True))


def test_column_and_index_guards_are_idempotent(tmp_path):
    """Running the same revision twice must be survivable — that property is
    what makes re-running a half-finished deploy safe."""
    engine = sa.create_engine(f"sqlite:///{tmp_path}/twice.db")
    meta = sa.MetaData()
    sa.Table("tasks", meta, sa.Column("id", sa.String(40), primary_key=True))
    meta.create_all(engine)

    with _migrating(engine):
        for _ in range(2):
            add_column_if_missing("tasks", sa.Column("cycle_id", sa.String(40)))
            create_index_if_missing("ix_tasks_cycle", "tasks", ["cycle_id"])
        assert has_column("tasks", "cycle_id")
