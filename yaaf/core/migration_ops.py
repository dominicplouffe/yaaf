"""Migration operations that tolerate the baseline already being current.

`0001_initial` builds the schema with `Base.metadata.create_all()` — the models
are the baseline rather than a frozen DDL script, which is what let the shape
settle quickly early on. The cost only shows up on a *first* install: 0001
creates today's columns, and every later migration then tries to add something
that is already there.

A deployment that upgraded one revision at a time never sees it, because each
migration ran when its column genuinely was missing. A fresh database fails on
0002 — and because Postgres DDL is transactional, the failure rolls 0001 back
too, leaving no tables at all. The server then boots (migrations are the only
thing that failed, and it is not fatal to the process), answers /healthz with
200, authenticates fine, and 500s on every real request with
`relation "projects" does not exist`.

So additive steps are written to no-op when the column is already present.
This keeps one chain honest for both cases: existing databases still get the
real ALTER, fresh ones skip what create_all already did.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


def has_column(table: str, column: str) -> bool:
    return any(c["name"] == column for c in sa.inspect(op.get_bind()).get_columns(table))


def add_column_if_missing(table: str, column: sa.Column) -> None:
    """`op.add_column`, skipped when 0001's create_all already made it."""
    if not has_column(table, column.name):
        op.add_column(table, column)


def has_table(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def create_table_if_missing(table: str, *columns, **kwargs) -> None:
    """`op.create_table`, skipped when 0001's create_all already made it.

    The table-level twin of add_column_if_missing, and needed for exactly the
    reason in this module's docstring — it just has not bitten yet, because
    every migration since the baseline has only ever added columns. The first
    one to add a *table* hits it: create_all on a fresh database already built
    that table from the models, `op.create_table` raises "already exists", and
    the transactional DDL takes 0001 down with it.
    """
    if not has_table(table):
        op.create_table(table, *columns, **kwargs)


def create_fk_if_missing(name: str, source: str, referent: str,
                         local_cols: list[str], remote_cols: list[str]) -> None:
    """`op.create_foreign_key`, skipped when the constraint (or an equivalent
    one on the same columns) already exists — a fresh install's create_all
    built it from the models — and skipped entirely on SQLite, which cannot
    ALTER TABLE ADD CONSTRAINT. SQLite only ever runs the chain in tests,
    where the baseline builds every FK from the models anyway."""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    existing = sa.inspect(bind).get_foreign_keys(source)
    if any(fk.get("constrained_columns") == local_cols for fk in existing):
        return
    op.create_foreign_key(name, source, referent, local_cols, remote_cols)


def create_index_if_missing(name: str, table: str, columns: list[str], **kwargs) -> None:
    """`op.create_index`, skipped when create_all already made it. Same story:
    an index declared in __table_args__ exists on a fresh database before the
    migration that adds it ever runs."""
    if not any(ix["name"] == name for ix in sa.inspect(op.get_bind()).get_indexes(table)):
        op.create_index(name, table, columns, **kwargs)
