"""What a Code Factory's repo reported, for its upkeep promises.

Revision ID: 0081
Revises: 0080
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "factory_signals",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    create_index_if_missing("ix_factory_signals", "factory_signals",
                            ["project_id", "kind", "at"])


def downgrade() -> None:
    from alembic import op

    op.drop_index("ix_factory_signals", table_name="factory_signals")
    op.drop_table("factory_signals")
