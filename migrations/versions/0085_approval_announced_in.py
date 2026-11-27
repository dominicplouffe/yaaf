"""A decision remembers the boardroom line that announced it, so the chairman's
note on it is filed under that line as its reply.

Revision ID: 0085
Revises: 0084
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("approvals",
                          sa.Column("announced_in_message_id", sa.String(40), nullable=True))


def downgrade() -> None:
    from alembic import op

    op.drop_column("approvals", "announced_in_message_id")
