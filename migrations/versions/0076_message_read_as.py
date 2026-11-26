"""What the chairman's boardroom line was read as, kept on the line.

A question, a vent, a hold or a fact gets a read-only reply; only an order or
an approval lets the CEO act (yaaf.server.intent). The reading is made once,
by a model or by the chairman's own correction, and every gate reads the same
one.

Revision ID: 0076
Revises: 0075
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("messages", sa.Column("read_as", sa.String(16), nullable=True))
    add_column_if_missing("messages", sa.Column("read_confidence", sa.Float(), nullable=True))
    add_column_if_missing("messages", sa.Column("read_by", sa.String(80), nullable=True))


def downgrade() -> None:
    from alembic import op

    for column in ("read_by", "read_confidence", "read_as"):
        op.drop_column("messages", column)
