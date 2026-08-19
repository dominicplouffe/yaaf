"""Invariant selftests: the migration chain reaches the models it ships with.

The baseline is `create_all`, which hides a whole class of mistake. Adding a
table to the models and no migration for it looks fine on a fresh database —
0001 builds it from the models — and does nothing at all on a database that
already ran 0001. Alembic then reports head, the API starts, and every request
touching the new table 500s.

That is not hypothetical: stage 1 of the rebuild shipped exactly that way and
was caught by looking at the live database after the deploy, not in review.

So two things are pinned here:

  * an EMPTY database migrated to head matches the models exactly
  * an EXISTING database at an older revision reaches the same place — which
    is the case create_all cannot cover and the only one that ever breaks
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from yaaf.core.models import Base

REPO = Path(__file__).resolve().parents[2]

# The tables and columns each stage introduced. A database stamped at an older
# revision before they existed is the shape this file exists to defend, so the
# lists are built by hand rather than derived from today's metadata — deriving
# them from the models would make the test agree with whatever the models
# happen to say. Every stage that adds schema adds its entries here.
STAGE_1_TABLES = ("requirement_edges", "requirement_events", "requirements")
STAGE_1_COLUMNS = (
    ("projects", "mode"),
    ("tasks", "requirement_id"),
    ("runs", "requirement_id"),
    ("run_queue", "requirement_id"),
)
STAGE_5_TABLES = ("commitments",)

REMOVED_TABLES = STAGE_1_TABLES + STAGE_5_TABLES
REMOVED_COLUMNS = STAGE_1_COLUMNS


def _alembic_config(url: str) -> Config:
    config = Config(str(REPO / "alembic.ini"))
    config.set_main_option("script_location", str(REPO / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _model_schema() -> dict[str, set[str]]:
    return {
        table.name: {column.name for column in table.columns}
        for table in Base.metadata.sorted_tables
    }


def _live_schema(engine) -> dict[str, set[str]]:
    inspector = sa.inspect(engine)
    return {
        name: {c["name"] for c in inspector.get_columns(name)}
        for name in inspector.get_table_names()
        if name != "alembic_version"
    }


def _create_pre_stage_1_schema(engine) -> None:
    """The schema as it stood before requirements existed.

    Built as a skeleton — names, types, primary keys — rather than by creating
    everything and dropping bits, because SQLite refuses to drop a column named
    in a foreign key. The migration guards only ever ask `has_table` and
    `has_column`, so names are the whole of what this needs to get right.
    """
    removed = set(REMOVED_COLUMNS)
    old = sa.MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name in REMOVED_TABLES:
            continue
        columns = [
            sa.Column(column.name, column.type, primary_key=column.primary_key)
            for column in table.columns
            if (table.name, column.name) not in removed
        ]
        sa.Table(table.name, old, *columns)
    old.create_all(engine)


@pytest.fixture
def scratch_db(monkeypatch):
    """A database of this test's own.

    `migrations/env.py` deliberately reads the URL from settings rather than
    from the alembic config, so pointing the config at a scratch file is not
    enough — without clearing the cache all three tests would migrate whichever
    database the harness happened to configure first, and pass or fail on the
    order they ran in.
    """
    from yaaf.core.config import settings

    path = tempfile.mktemp(suffix=".db")
    url = f"sqlite:///{path}"
    monkeypatch.setenv("YAAF_DATABASE_URL", url)
    settings.cache_clear()
    try:
        yield url
    finally:
        settings.cache_clear()


def test_an_empty_database_migrates_to_exactly_the_models(scratch_db):
    engine = sa.create_engine(scratch_db)
    command.upgrade(_alembic_config(scratch_db), "head")

    live, models = _live_schema(engine), _model_schema()
    assert set(live) == set(models), (
        f"tables only in the database: {set(live) - set(models)}; "
        f"tables only in the models: {set(models) - set(live)}"
    )
    for table, columns in models.items():
        assert columns <= live[table], f"{table} is missing {columns - live[table]}"


def test_a_database_stamped_before_stage_1_still_reaches_the_models(scratch_db):
    """The case create_all cannot cover, and the one that actually broke.

    Build the schema as it looked *before* requirements existed, stamp it at
    0001 the way the live box was, then upgrade. If the later revision is
    missing or unguarded, this is where it shows — not on a production deploy.
    """
    engine = sa.create_engine(scratch_db)
    _create_pre_stage_1_schema(engine)

    before = _live_schema(engine)
    for table in REMOVED_TABLES:
        assert table not in before, "the fixture must actually remove the table"

    config = _alembic_config(scratch_db)
    command.stamp(config, "0001")
    command.upgrade(config, "head")

    live, models = _live_schema(engine), _model_schema()
    missing_tables = set(models) - set(live)
    assert not missing_tables, (
        f"upgrading an existing database left these tables uncreated: {missing_tables}. "
        "The baseline is create_all, so a model added without its own revision only "
        "appears on fresh installs."
    )
    for table, columns in models.items():
        missing = columns - live[table]
        assert not missing, f"{table} never got {missing} on the upgrade path"


def test_the_guarded_helpers_make_the_chain_rerunnable(scratch_db):
    """A fresh database has everything from create_all before any later
    revision runs. Running the whole chain over it must be a no-op, not a
    collision that rolls the baseline back."""
    engine = sa.create_engine(scratch_db)
    Base.metadata.create_all(engine)
    config = _alembic_config(scratch_db)
    command.stamp(config, "0001")
    command.upgrade(config, "head")   # every later revision runs against a full schema

    live, models = _live_schema(engine), _model_schema()
    assert set(models) <= set(live), "the chain destroyed tables the baseline had built"
