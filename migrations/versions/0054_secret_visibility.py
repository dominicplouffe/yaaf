"""A secret's visibility, and when it last changed.

`secrets.visibility` lets a plain configuration value (a timezone, an account
id) be shown as is instead of masked like a key. Every existing row stays
"secret", which is what it was.

`secrets.updated_at` is backfilled from `created_at`: a row nobody has touched
since it was stored was last updated when it was stored.

Revision ID: 0054
Revises: 0053
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import UTCDateTime

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("secrets", sa.Column("visibility", sa.String(8), nullable=False,
                                               server_default="secret"))
    add_column_if_missing("secrets", sa.Column("updated_at", UTCDateTime()))
    op.execute("UPDATE secrets SET updated_at = created_at WHERE updated_at IS NULL")


def downgrade() -> None:
    op.drop_column("secrets", "updated_at")
    op.drop_column("secrets", "visibility")
