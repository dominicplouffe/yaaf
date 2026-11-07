"""A desktop install's database, brought to the models every time it starts.

The server's deploy migrates (deploy/entrypoint.sh runs `alembic upgrade
head`). `yaaf up` built its SQLite file with `create_all`, which makes missing
tables and never touches one that exists — so the first release that added a
column to a table answered 500 on every desktop that updated: `users` had no
`desktop_theme`, and the dashboard's first read of it failed.

So `yaaf up` and `yaaf setup` call `prepare` instead, which meets three kinds
of database:

- **Empty**: built by the migration chain itself (0001 is the baseline), so it
  carries its revision from the first day and every update after is an
  ordinary `alembic upgrade head`.
- **Versioned**: upgraded to head, like a server.
- **Built by create_all before this** (no revision recorded): adopted, once.
  Its schema was the models of whatever day it was made, but which revision
  that was is not written anywhere, and replaying the chain from the start
  over it would rerun data migrations written for an older shape — 0055 would
  pin OpenAI seats back onto the Codex CLI. So it gets only what is missing:
  tables, then columns, each nullable or carrying its own default; then it is
  stamped at head, and is versioned from then on. A missing column that is
  required and has no default cannot be added honestly, and stops it with the
  reason rather than guessing a value.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

REPO = Path(__file__).resolve().parents[2]


def alembic_config(url: str):
    from alembic.config import Config

    config = Config(str(REPO / "alembic.ini"))
    config.set_main_option("script_location", str(REPO / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def missing_columns(engine: sa.Engine) -> list[sa.Column]:
    """Model columns the live database lacks, in tables it has."""
    from yaaf.core.models import Base

    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    missing = []
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        missing += [column for column in table.columns if column.name not in have]
    return missing


def _add(engine: sa.Engine, column: sa.Column) -> None:
    if not column.nullable and column.server_default is None:
        raise RuntimeError(
            f"{column.table.name}.{column.name} is required and has no default, so it "
            "cannot be added to this database without a value nobody chose. Back up "
            "the database and start from a fresh one, or run the migration chain by hand.")
    preparer = engine.dialect.identifier_preparer
    kind = column.type.compile(dialect=engine.dialect)
    default = ""
    if column.server_default is not None:
        default = " DEFAULT " + str(sa.schema.DDLElement.compile(
            column.server_default.arg, dialect=engine.dialect)
            if hasattr(column.server_default.arg, "compile") else column.server_default.arg)
    with engine.begin() as connection:
        connection.execute(sa.text(
            f"ALTER TABLE {preparer.quote(column.table.name)} "
            f"ADD COLUMN {preparer.quote(column.name)} {kind}{default}"))


def prepare(url: str) -> str:
    """Bring the database at `url` to the models; say which of the three it was."""
    from alembic import command

    from yaaf.core.models import Base

    engine = sa.create_engine(url)
    try:
        tables = set(sa.inspect(engine).get_table_names())
        config = alembic_config(url)
        if "alembic_version" in tables:
            command.upgrade(config, "head")
            return "migrated"
        if not tables:
            command.upgrade(config, "head")
            return "created"
        Base.metadata.create_all(engine)
        added = missing_columns(engine)
        for column in added:
            _add(engine, column)
        command.stamp(config, "head")
        return f"adopted ({len(added)} column{'' if len(added) == 1 else 's'} added)"
    finally:
        engine.dispose()
