"""A credential can be a file, not only a string.

Revision ID: 0038
Revises: 0037
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL means what every existing row means: a string credential, injected
    # under its own name. Nothing to backfill.
    add_column_if_missing("secrets", sa.Column("filename", sa.String(120), nullable=True))


def downgrade() -> None:
    # Dropping this does not lose a value, but it does lose the fact that the
    # value is base64 of a file — those rows read back as gibberish strings.
    from alembic import op

    op.drop_column("secrets", "filename")
