"""Health-card suggestions a member has put away.

`project_members.health_acks` holds, per member, the health-card warnings that
member has read and set aside ({sig: {"key", "at"}}). Additive and nullable:
NULL is "nothing put away", which is what every existing member has done.

Revision ID: 0047
Revises: 0046
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from yaaf.core.migration_ops import add_column_if_missing

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

#: JSONB on Postgres, JSON on SQLite — the variant every other JSON column uses.
JSON_VARIANT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # Guarded, like every additive step since 0001: on a fresh install
    # `create_all` has already built it from the model.
    add_column_if_missing("project_members", sa.Column("health_acks", JSON_VARIANT))


def downgrade() -> None:
    op.drop_column("project_members", "health_acks")
