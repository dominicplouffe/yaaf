"""A Code Factory holds a second credential: what its repo seats code with.

Revision ID: 0086
Revises: 0085
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing, create_fk_if_missing

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("projects", sa.Column("coding_secret_id", sa.String(40)))
    create_fk_if_missing("fk_projects_coding_secret", "projects", "secrets",
                         ["coding_secret_id"], ["id"])


def downgrade() -> None:
    from alembic import op

    op.drop_column("projects", "coding_secret_id")
