"""Integrations: MCP servers connected once, for every project.

- `integrations` — the server, its sign-in and its allowed tools, held above
  the projects. Its credentials live encrypted in its own `vault`, never as
  `Secret` rows: a platform-scope secret granted to a role slug reaches every
  project with that role, and an integration reaches only the projects that
  chose it.
- `project_integrations` — one project's use or decline of one integration,
  and the roles that get its tools.
- `task_mirror_items` — one task as last written to an external task manager.
- `approvals.integration` — an agent's request to use one, which approving
  attaches.

Revision ID: 0058
Revises: 0057
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing, create_table_if_missing
from yaaf.core.models import JSONVariant, UTCDateTime

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "integrations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(32), nullable=False, unique=True),
        sa.Column("description", sa.Text),
        sa.Column("covers", JSONVariant),
        sa.Column("entry", JSONVariant),
        sa.Column("vault", JSONVariant),
        sa.Column("task_mirror", JSONVariant),
        sa.Column("created_at", UTCDateTime),
        sa.Column("updated_at", UTCDateTime),
    )
    create_table_if_missing(
        "project_integrations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("integration_id", sa.String(40), sa.ForeignKey("integrations.id"),
                  nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("roles", JSONVariant),
        sa.Column("reason", sa.Text),
        sa.Column("mirror_tasks", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("decided_by", sa.String(80)),
        sa.Column("created_at", UTCDateTime),
        sa.Column("updated_at", UTCDateTime),
        sa.UniqueConstraint("project_id", "integration_id"),
    )
    create_table_if_missing(
        "task_mirror_items",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("integration_id", sa.String(40), sa.ForeignKey("integrations.id"),
                  nullable=False),
        sa.Column("external_id", sa.String(200)),
        sa.Column("synced_status", sa.String(32)),
        sa.Column("error", sa.Text),
        sa.Column("attempts", sa.Integer),
        sa.Column("updated_at", UTCDateTime),
        sa.UniqueConstraint("task_id", "integration_id"),
    )
    add_column_if_missing("approvals", sa.Column("integration", JSONVariant))


def downgrade() -> None:
    op.drop_column("approvals", "integration")
    op.drop_table("task_mirror_items")
    op.drop_table("project_integrations")
    op.drop_table("integrations")
