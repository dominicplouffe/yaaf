"""A flag is told in parts: headline, why, plan, and what the chairman does.

One proposal paragraph, under a title that named the flag's category, was not
readable cold: the chairman had to ask what two cards on his own screen meant.
`covered_by_flag_id` lets one telling cover several flags with one cause.

Rows raised before this keep their `proposal` and read as they did.

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

COLUMNS = ("headline", "why", "plan", "from_you")


def upgrade() -> None:
    for name in COLUMNS:
        add_column_if_missing("flags", sa.Column(name, sa.Text, nullable=True))
    add_column_if_missing("flags", sa.Column("covered_by_flag_id", sa.String(40),
                                             nullable=True))


def downgrade() -> None:
    op.drop_column("flags", "covered_by_flag_id")
    for name in reversed(COLUMNS):
        op.drop_column("flags", name)
