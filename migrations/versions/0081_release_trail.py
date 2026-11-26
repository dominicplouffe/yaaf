"""A release's environment, SBOM and signed provenance.

Revision ID: 0081
Revises: 0080
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("releases", sa.Column("environment", sa.String(16), nullable=False,
                                                server_default="production"))
    add_column_if_missing("releases", sa.Column("sbom_uri", sa.Text(), nullable=True))
    add_column_if_missing("releases", sa.Column("provenance", sa.JSON(), nullable=True))
    add_column_if_missing("releases", sa.Column("provenance_sig", sa.Text(), nullable=True))


def downgrade() -> None:
    from alembic import op

    for column in ("provenance_sig", "provenance", "sbom_uri", "environment"):
        op.drop_column("releases", column)
