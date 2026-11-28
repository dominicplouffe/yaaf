"""A requirement remembers the commit its acceptance test last failed at, and
a blocked task what it is waiting on.

Revision ID: 0087
Revises: 0086
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from yaaf.core.migration_ops import add_column_if_missing

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None

#: JSONB on Postgres, JSON on SQLite — the variant every other JSON column uses.
JSON_VARIANT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    add_column_if_missing("requirements", sa.Column("failed_at_sha", sa.String(64)))
    add_column_if_missing("tasks", sa.Column("blocked_on", JSON_VARIANT))


def downgrade() -> None:
    from alembic import op

    op.drop_column("tasks", "blocked_on")
    op.drop_column("requirements", "failed_at_sha")
