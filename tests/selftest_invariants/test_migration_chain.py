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

import contextlib
import os
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
STAGE_6_TABLES = ("artifacts", "audit_samples")
STAGE_7_TABLES = ("reviews",)
STAGE_8_TABLES = ("cycles",)
STAGE_8_COLUMNS = (
    ("projects", "deadline"),
    ("requirements", "cycle_id"),
)
# Stage 9's own tables (`experiments`, `experiment_arms`) are absent on purpose:
# 0007 creates them and 0012 drops them again, so they exist only in the middle
# of the chain and never in the models. `projects.topology` outlived them.
STAGE_9_COLUMNS = (("projects", "topology"),)
# 0013 + 0014: outcome metering, cycle goals and due-bys, standing
# requirements, and the flag. Listed here for the reason the header gives —
# without the entry, the fixture builds these from today's models, the guarded
# create_table finds them already present, and the upgrade path they exist to
# defend is never walked.
STAGE_10_TABLES = ("flags", "requirement_cycle_results")
STAGE_10_COLUMNS = (
    ("runs", "produced"),
    ("runs", "metered"),
    ("cycles", "goal"),
    ("cycles", "due_at"),
    ("cycles", "due_source"),
    ("requirements", "recurrence"),
)

# 0041: what a run executed on, and what a credential pays (docs/RUNTIMES.md).
# `secrets.provider` is NOT NULL with a server default — the shape most likely
# to break an ALTER on a table that already has rows.
RUNTIMES_COLUMNS = (
    ("secrets", "provider"),
    ("runs", "provider"),
    ("runs", "harness"),
    ("runs", "error_kind"),
    # 0042: a seat chooses what drives its model.
    ("role_versions", "harness"),
    ("agents", "harness_override"),
    # 0043: every run is priced from the catalog, whoever bills it.
    ("runs", "priced_usd"),
    # 0044: the first providers that are not Anthropic.
    ("secrets", "base_url"),
)
RUNTIMES_TABLES = ("project_llm_credentials", "model_prices")
# 0046: people (docs/AUTH.md).
PEOPLE_TABLES = ("user_sessions", "user_tokens", "user_setup_links", "login_failures")
PEOPLE_COLUMNS = (("users", "deactivated_at"),)
# 0055: provider-neutral by construction — where a price came from and the
# model's window, the provider check, failover.
PROVIDERS_TABLES = ("provider_checks", "credential_breakers")
PROVIDERS_COLUMNS = (
    ("model_prices", "source"),
    ("model_prices", "context_window"),
    ("projects", "fallback_secret_id"),
)
# 0056: a person's sidebar pins, and a project's setup checklist.
SHORTCUTS_COLUMNS = (("users", "pins"), ("projects", "setup"))

REMOVED_TABLES = (STAGE_1_TABLES + STAGE_5_TABLES + STAGE_6_TABLES
                  + STAGE_7_TABLES + STAGE_8_TABLES + STAGE_10_TABLES + RUNTIMES_TABLES
                  + PEOPLE_TABLES + PROVIDERS_TABLES)
REMOVED_COLUMNS = (STAGE_1_COLUMNS + STAGE_8_COLUMNS + STAGE_9_COLUMNS
                   + STAGE_10_COLUMNS + RUNTIMES_COLUMNS + PEOPLE_COLUMNS + PROVIDERS_COLUMNS
                   + SHORTCUTS_COLUMNS
                   + (("secrets", "llm_mode"),))


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

    # mkstemp, not mktemp: the name is reserved rather than merely guessed, and
    # the file is removed afterwards. The old form leaked one database per test
    # per run — 185 of them had accumulated in /tmp on the box this was found
    # on, which is a slow way to fill a disk with schemas nobody reads.
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    os.unlink(path)  # SQLite wants to create it itself
    url = f"sqlite:///{path}"
    monkeypatch.setenv("YAAF_DATABASE_URL", url)
    settings.cache_clear()
    try:
        yield url
    finally:
        settings.cache_clear()
        for leftover in (path, f"{path}-wal", f"{path}-shm"):
            with contextlib.suppress(OSError):
                os.unlink(leftover)


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


def test_0055_keeps_an_api_key_seat_on_the_cli_it_ran_on(client, monkeypatch):
    """An OpenAI or Google API key now defaults to yaaf's own loop; on main it
    drove the Codex or Gemini CLI. A running project's seats must not change
    engine under it on deploy, so 0055 pins the CLI each such seat ran on —
    team members included, whose role version is their team's — and leaves
    every other seat, and every seat that named a harness, alone."""
    import importlib.util

    from tests.conftest import ADMIN, ROLE_MIN
    from yaaf.core.db import db_session, engine
    from yaaf.core.models import Agent

    for slug, model in (("chief", "gpt-5.5"), ("writer", "gpt-5.5"), ("claude", "claude-sonnet-5-5")):
        assert client.post("/admin/roles", headers=ADMIN, json={
            "slug": slug, "name": slug.title(), **ROLE_MIN, "model": model,
            "is_ceo_role": slug != "writer"}).status_code == 200
    client.put("/admin/settings/model-prices/gpt-5.5", headers=ADMIN,
               json={"input": 1.25, "output": 10})
    client.post("/admin/users", headers=ADMIN, json={"email": "chair@example.com",
                                                     "display_name": "Chair"})

    def credential(name, provider, value):
        return client.post("/admin/settings/llm-credentials", headers=ADMIN, json={
            "name": name, "mode": "api_key", "provider": provider, "value": value}).json()["id"]

    def project(slug, ceo, billing, teams):
        created = client.post("/admin/projects", headers=ADMIN, json={
            "slug": slug, "name": slug, "goal": "Prove the framework works.",
            "chairman_email": "chair@example.com", "ceo_role_slug": ceo, "ceo_name": "CEO",
            "billing_secret_id": billing, "teams": teams})
        assert created.status_code == 200, created.text
        return created.json()["id"]

    on_openai = project("on-openai", "chief", credential("OpenAI", "openai", "sk-proj-mig"),
                        [{"role_slug": "writer", "name": "W", "employees": ["Ola"]}])
    on_claude = project("on-claude", "claude",
                        credential("Claude", "anthropic", "sk-ant-api03-" + "m" * 40),
                        [{"role_slug": "claude", "name": "C", "employees": ["Cy"]}])
    spec = importlib.util.spec_from_file_location(
        "m0055", REPO / "migrations" / "versions" / "0055_provider_neutral.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine().begin() as connection:
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection, raising=False)
        migration._keep_former_defaults()
    with db_session() as session:
        overrides = {(a.project_id, a.name): a.harness_override
                     for a in session.query(Agent).filter(
                         Agent.project_id.in_((on_openai, on_claude)))}
    assert overrides == {(on_openai, "CEO"): "codex_cli", (on_openai, "Ola"): "codex_cli",
                         (on_claude, "CEO"): None, (on_claude, "Cy"): None}
