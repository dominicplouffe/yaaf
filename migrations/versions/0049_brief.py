"""The brief: standing orders the chairman confirms.

`projects.standing_orders` and `projects.retired_orders` sit beside `goal` and
are pushed to every agent with it. `projects.brief_confirmed_at` is when he last
confirmed them. `approvals.brief` carries a proposed brief until he does.

Existing projects start with no standing orders: whatever their chairman said
since founding lives in chat, memory and files, and the CEO's first brief is
what gathers it.

Revision ID: 0049
Revises: 0048
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import JSONVariant, UTCDateTime

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("projects", sa.Column("standing_orders", JSONVariant))
    add_column_if_missing("projects", sa.Column("retired_orders", JSONVariant))
    add_column_if_missing("projects", sa.Column("brief_confirmed_at", UTCDateTime()))
    add_column_if_missing("approvals", sa.Column("brief", JSONVariant))


def downgrade() -> None:
    op.drop_column("approvals", "brief")
    op.drop_column("projects", "brief_confirmed_at")
    op.drop_column("projects", "retired_orders")
    op.drop_column("projects", "standing_orders")
