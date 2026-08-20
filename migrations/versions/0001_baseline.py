"""Baseline schema, rebuilt.

The chain used to run 0001 → 0009, and 0001 was already `create_all()`. That
combination has one sharp edge: a *fresh* database gets today's columns from
create_all and then 0002 tries to add one that is already there. Every later
migration was written through `add_column_if_missing` for exactly that reason.

Nothing has shipped and no database needs to survive, so the chain collapses
back to one honest step. This is the moment that is free — after a single real
deployment it never is again.

From here the rule holds as before: additive steps use the guarded helpers in
`yaaf.core.migration_ops`, because this file will still be `create_all` on the
next fresh install.
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from yaaf.core.models import Base

    Base.metadata.create_all(op.get_bind())

    if op.get_bind().dialect.name == "postgresql":
        # Full-text search over memory; ILIKE fallback exists for other dialects.
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_memories_fts ON memories "
            "USING gin (to_tsvector('english', subject || ' ' || body))"
        )


def downgrade() -> None:
    from yaaf.core.models import Base

    Base.metadata.drop_all(op.get_bind())
