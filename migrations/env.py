from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from yaaf.core.config import settings
from yaaf.core.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings().database_url)
target_metadata = Base.metadata

# The api and the runner container each run `alembic upgrade head` from their
# entrypoint at start, and the whole chain runs inside ONE transaction whose
# first act on a fresh database takes an ACCESS EXCLUSIVE lock on
# alembic_version. Racing them means one wins and the other dies on the lock;
# since a failed migration is deliberately fatal, compose then restarts it, and
# a perfectly normal deploy shows a container flapping once for a reason
# nobody can act on. Worse, anything asking `alembic current` at that moment —
# which is exactly what the deploy scripts do next — waits behind the same
# lock and looks hung.
#
# An advisory lock makes the loser wait for the winner rather than fail, and by
# the time it gets in there is nothing left to do. Transaction-scoped, so the
# commit that writes alembic_version releases it and a crash cannot strand it.
MIGRATION_LOCK_KEY = 0x7961_6146  # "yaaF" — one lock, shared by every process

# Long enough to sit through any real migration, short enough that a genuinely
# stuck holder ends as a loud failure instead of a container waiting forever.
# Failing is recoverable here: compose restarts us and we try again.
MIGRATION_LOCK_TIMEOUT_MS = 300_000


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            # Postgres only: SQLite (the test database) has no advisory locks
            # and no concurrent migrator to serialize against.
            if connection.dialect.name == "postgresql":
                connection.execute(text(f"SET LOCAL lock_timeout = '{MIGRATION_LOCK_TIMEOUT_MS}ms'"))
                connection.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                                   {"key": MIGRATION_LOCK_KEY})
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
