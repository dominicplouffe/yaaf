"""A question's options and recommendation, kept apart from its prose.

`asks.options` and `asks.recommendation` let the owner answer with one click.
Existing asks keep neither: their options stay in the body, as before.

Revision ID: 0053
Revises: 0052
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import JSONVariant

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("asks", sa.Column("options", JSONVariant))
    add_column_if_missing("asks", sa.Column("recommendation", sa.Text()))


def downgrade() -> None:
    op.drop_column("asks", "recommendation")
    op.drop_column("asks", "options")
