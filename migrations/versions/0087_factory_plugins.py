"""Factory plugins: installed plugins and versions, a project's kind, and what core
decided about each plugin artifact.

Revision ID: 0087
Revises: 0086
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from yaaf.core.migration_ops import (
    add_column_if_missing,
    create_fk_if_missing,
    create_table_if_missing,
)

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    create_table_if_missing(
        "plugins",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("kind_key", sa.String(40), nullable=False, unique=True),
        sa.Column("repo_url", sa.String(300)),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_table_if_missing(
        "plugin_versions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("plugin_id", sa.String(40), sa.ForeignKey("plugins.id"), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("ref", sa.String(200)),
        sa.Column("manifest", JSON, nullable=False),
        sa.Column("api_version", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("report", JSON, nullable=False),
        sa.Column("build_log", sa.Text, nullable=False, server_default=""),
        sa.Column("root", sa.String(500), nullable=False, server_default=""),
        sa.Column("installed_by", sa.String(40)),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plugin_id", "version", name="uq_plugin_version"),
    )
    create_table_if_missing(
        "plugin_artifacts",
        sa.Column("artifact_id", sa.String(40), sa.ForeignKey("artifacts.id"),
                  primary_key=True),
        sa.Column("project_id", sa.String(40), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("load_bearing", JSON),
        sa.Column("verdict", sa.String(16)),
        sa.Column("subject_sha256", sa.String(64)),
        sa.Column("reviewed", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    add_column_if_missing("projects", sa.Column("kind", sa.String(64)))
    add_column_if_missing("projects", sa.Column("kind_mode", sa.String(40)))
    add_column_if_missing("projects", sa.Column("plugin_version_id", sa.String(40)))
    add_column_if_missing("projects", sa.Column("kind_state", JSON))
    create_fk_if_missing("fk_projects_plugin_version", "projects", "plugin_versions",
                         ["plugin_version_id"], ["id"])


def downgrade() -> None:
    from alembic import op

    for column in ("kind_state", "plugin_version_id", "kind_mode", "kind"):
        op.drop_column("projects", column)
    op.drop_table("plugin_artifacts")
    op.drop_table("plugin_versions")
    op.drop_table("plugins")
