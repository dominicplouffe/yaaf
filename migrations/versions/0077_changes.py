"""A Code Factory's pull requests are rows the platform owns.

Revision ID: 0077
Revises: 0076
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "changes",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("requirement_id", sa.String(40), sa.ForeignKey("requirements.id"),
                  nullable=True),
        sa.Column("author_agent_id", sa.String(40), nullable=False),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("branch", sa.String(255), nullable=False),
        sa.Column("base", sa.String(255), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("head_sha", sa.String(64), nullable=True),
        sa.Column("lines_added", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_removed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_changed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("gates", sa.JSON(), nullable=False),
        sa.Column("reviewer_agent_id", sa.String(40), nullable=True),
        sa.Column("review_verdict", sa.String(16), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_sha", sa.String(64), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("returns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("merged_sha", sa.String(64), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_index_if_missing("ix_changes_project_status", "changes", ["project_id", "status"])


def downgrade() -> None:
    from alembic import op

    op.drop_index("ix_changes_project_status", table_name="changes")
    op.drop_table("changes")
