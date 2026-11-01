"""Approvals carry the filer's recommendation, in one sentence.

The decision card showed the case and what a no costs, but never what the CEO
actually recommended: the screen either said nothing or guessed from the
case's first sentence. A guess on the card he decides from is an invented
fact, so the recommendation is now the filer's own words, stored.

Revision ID: 0062
Revises: 0061
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("approvals", sa.Column("recommendation", sa.Text))


def downgrade() -> None:
    op.drop_column("approvals", "recommendation")
