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
