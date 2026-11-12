"""Say who each published file is written for.

`reader` is "people" or "agents"; it decides the form a file is held to and
whether the chairman is told about it. Existing rows stay NULL, which reads as
people, which is how every one of them was treated. `asked_for` holds the
chairman's own words when a file for people is long because he asked for it.

Revision ID: 0072
Revises: 0071
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("artifacts", sa.Column("reader", sa.String(8)))
    add_column_if_missing("artifacts", sa.Column("asked_for", sa.Text))


def downgrade() -> None:
    op.drop_column("artifacts", "asked_for")
    op.drop_column("artifacts", "reader")
