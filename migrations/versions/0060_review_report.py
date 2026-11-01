"""The CEO's status report, structured.

`reviews.report` — what the period bought, what moved against the plan and
its largest cause, what it will do next and what each item serves, and the
ask. Filed through `review_report`; filing it also renders the free-text
`narrative` and `recommended` the older readers use, so one filing feeds
both. NULL on meetings filed the old way, which the screen renders the old
way rather than synthesising a plan the CEO never wrote.

Revision ID: 0060
Revises: 0059
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import JSONVariant

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("reviews", sa.Column("report", JSONVariant))


def downgrade() -> None:
    op.drop_column("reviews", "report")
