"""Submissions to a library source.

`publications` records each project or role version this install submitted to
a registry: the listing, the hash of what was sent, the pull request and what
the moderators last said.

Revision ID: 0075
Revises: 0074
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import create_table_if_missing
from yaaf.core.models import UTCDateTime

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "publications",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("project_id", sa.String(40)),
        sa.Column("role_slug", sa.String(64)),
        sa.Column("listing_id", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("repo", sa.String(200), nullable=False),
        sa.Column("pr_number", sa.Integer()),
        sa.Column("pr_url", sa.String(500)),
        sa.Column("state", sa.String(24), nullable=False, server_default="submitted"),
        sa.Column("review_note", sa.Text()),
        sa.Column("submitted_by", sa.String(40)),
        sa.Column("created_at", UTCDateTime()),
        sa.Column("checked_at", UTCDateTime()),
    )


def downgrade() -> None:
    from alembic import op

    op.drop_table("publications")
