"""Promised credentials are rows, not a sentence in the brief.

Revision ID: 0075
Revises: 0074
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "credential_needs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("service", sa.String(64), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(16), nullable=False, server_default="creation"),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("check", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="needed"),
        sa.Column("last_check", sa.JSON(), nullable=True),
        sa.Column("last_use", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_index_if_missing("ix_credential_needs_project", "credential_needs", ["project_id"])


def downgrade() -> None:
    from alembic import op

    op.drop_index("ix_credential_needs_project", table_name="credential_needs")
    op.drop_table("credential_needs")
