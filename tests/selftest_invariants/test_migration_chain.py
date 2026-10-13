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
    # 0047: a role says how hard, the project says on what.
    ("role_versions", "tier"),
    ("agents", "tier_override"),
    ("projects", "seat_models"),
)
RUNTIMES_TABLES = ("project_llm_credentials", "model_prices")
# 0046: people (docs/AUTH.md).
PEOPLE_TABLES = ("user_sessions", "user_tokens", "user_setup_links", "login_failures")
PEOPLE_COLUMNS = (("users", "deactivated_at"),)

REMOVED_TABLES = (STAGE_1_TABLES + STAGE_5_TABLES + STAGE_6_TABLES
                  + STAGE_7_TABLES + STAGE_8_TABLES + STAGE_10_TABLES + RUNTIMES_TABLES
                  + PEOPLE_TABLES)
REMOVED_COLUMNS = (STAGE_1_COLUMNS + STAGE_8_COLUMNS + STAGE_9_COLUMNS
                   + STAGE_10_COLUMNS + RUNTIMES_COLUMNS + PEOPLE_COLUMNS
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


def test_roles_lose_their_models_and_no_seat_changes_what_it_runs_on(scratch_db):
    """0047 takes the model and the harness off the role version and reads a
    tier off the model instead. The promise is that no running seat moves: a
    seat whose role named something its tier does not resolve to keeps it as
    an override, a role's harness lands on its seats, and an override that
    already equals what its tier resolves to becomes a tier override so it
    follows the project from now on."""
    from sqlalchemy.orm import Session

    from yaaf.core.models import Agent, Project, Role, RoleVersion, Secret, Team, User

    engine = sa.create_engine(scratch_db)
    Base.metadata.create_all(engine)
    # Flushed group by group, parents first: Postgres holds the foreign keys
    # SQLite lets slide, and the models declare no relationships to order by.
    with Session(engine) as session, session.begin():
        session.add(User(id="u1", email="chair@example.com", display_name="Chair"))
        session.flush()
        session.add_all([Secret(id="s_ant", name="Claude key", ciphertext="x", provider="anthropic"),
                         Secret(id="s_ds", name="DeepSeek key", ciphertext="x",
                                provider="deepseek")])
        session.flush()
        session.add_all([Project(id="p1", slug="claude", name="Claude", goal="g" * 12,
                                 chairman_user_id="u1", billing_secret_id="s_ant"),
                         Project(id="p2", slug="deep", name="Deep", goal="g" * 12,
                                 chairman_user_id="u1", billing_secret_id="s_ds")])
        session.add(Role(id="r1", slug="r", name="R"))
        session.flush()
        for version, rv in enumerate(("opus", "fable", "deepseek", "cli", "sonnet"), start=1):
            session.add(RoleVersion(id=f"rv_{rv}", role_id="r1", version=version,
                                    description="d", system_prompt="p"))
        session.flush()
        session.add_all([Team(id=f"t_{p}_{rv}", project_id=p, role_version_id=f"rv_{rv}",
                              name=rv)
                         for p, rv in (("p1", "fable"), ("p1", "deepseek"), ("p1", "cli"),
                                       ("p1", "sonnet"), ("p2", "sonnet"))])
        session.flush()
        session.add_all([
            Agent(id="a_ceo", project_id="p1", kind="ceo", name="CEO", role_version_id="rv_opus"),
            Agent(id="a_fable", project_id="p1", team_id="t_p1_fable", kind="employee", name="F"),
            Agent(id="a_ds", project_id="p1", team_id="t_p1_deepseek", kind="employee", name="D"),
            Agent(id="a_cli", project_id="p1", team_id="t_p1_cli", kind="employee", name="C"),
            Agent(id="a_lifted", project_id="p1", team_id="t_p1_sonnet", kind="employee",
                  name="L", model_override="claude-opus-5-5"),
            Agent(id="a_moved", project_id="p2", team_id="t_p2_sonnet", kind="employee", name="M"),
        ])
    # Back to the shape 0046 left: models and harnesses on the role version.
    with engine.begin() as conn:
        conn.execute(sa.text("ALTER TABLE role_versions DROP COLUMN tier"))
        conn.execute(sa.text("ALTER TABLE agents DROP COLUMN tier_override"))
        conn.execute(sa.text("ALTER TABLE projects DROP COLUMN seat_models"))
        conn.execute(sa.text("ALTER TABLE role_versions ADD COLUMN model VARCHAR(64)"))
        conn.execute(sa.text("ALTER TABLE role_versions ADD COLUMN harness VARCHAR(32)"))
        for rv, model, harness in (("opus", "claude-opus-5-5", None),
                                   ("fable", "claude-fable-5-1", None),
                                   ("deepseek", "deepseek-chat", None),
                                   ("cli", "claude-sonnet-5-5", "claude_cli"),
                                   ("sonnet", "claude-sonnet-5-5", None)):
            conn.execute(sa.text("UPDATE role_versions SET model = :m, harness = :h "
                                 "WHERE id = :id"), {"m": model, "h": harness, "id": f"rv_{rv}"})

    config = _alembic_config(scratch_db)
    command.stamp(config, "0046")
    command.upgrade(config, "head")

    with engine.connect() as conn:
        assert {"model", "harness"}.isdisjoint(
            c["name"] for c in sa.inspect(conn).get_columns("role_versions"))
        tiers = dict(conn.execute(sa.text("SELECT id, tier FROM role_versions")).all())
        assert tiers == {"rv_opus": "strong", "rv_fable": "strong", "rv_deepseek": "standard",
                         "rv_cli": "standard", "rv_sonnet": "standard"}
        seats = {row.id: (row.model_override, row.tier_override, row.harness_override)
                 for row in conn.execute(sa.text(
                     "SELECT id, model_override, tier_override, harness_override FROM agents"))}
    assert seats == {
        "a_ceo": (None, None, None),                      # Opus is the strong tier already
        "a_fable": ("claude-fable-5-1", None, None),      # off the lineup: kept by name
        "a_ds": ("deepseek-chat", None, None),            # another vendor: kept by name
        "a_cli": (None, None, "claude_cli"),              # the role's harness, on the seat
        "a_lifted": (None, "strong", None),               # now follows the project
        "a_moved": ("claude-sonnet-5-5", None, None),     # a DeepSeek project's Claude seat
    }
