"""Code Factory releases and incidents.

Revision ID: 0079
Revises: 0078
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "releases",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("sha", sa.String(64), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("change_ids", sa.JSON(), nullable=False),
        sa.Column("proposed_by_agent_id", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("approval_id", sa.String(40), nullable=True),
        sa.Column("previous_release_id", sa.String(40), nullable=True),
        sa.Column("probes", sa.JSON(), nullable=False),
        sa.Column("deploy_output", sa.Text(), nullable=True),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_index_if_missing("ix_releases_project", "releases", ["project_id", "status"])
    create_table_if_missing(
        "incidents",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("release_id", sa.String(40), nullable=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("opened_by", sa.String(40), nullable=False),
        sa.Column("timeline", sa.JSON(), nullable=False),
        sa.Column("postmortem", sa.Text(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    create_index_if_missing("ix_incidents_project", "incidents", ["project_id", "status"])


def downgrade() -> None:
    from alembic import op

    op.drop_index("ix_incidents_project", table_name="incidents")
    op.drop_table("incidents")
    op.drop_index("ix_releases_project", table_name="releases")
    op.drop_table("releases")
