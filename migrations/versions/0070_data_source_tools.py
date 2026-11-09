"""A data source's tools, each with a permission, and the CEO's request to move one.

`data_source_tools`: one row per tool a project's data source offers — what it
is, whether it reads or writes, and whether an agent's call to it is forwarded.
Every agent call now goes through the server, which checks this row immediately
before the call leaves; the sandbox no longer holds a source's address or token.

`approvals.tool_permission`: a `tool_permission` approval's move,
{"source", "tool", "from", "to"}. Approving it is what moves the tool.

Revision ID: 0070
Revises: 0069
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import (
    add_column_if_missing,
    create_index_if_missing,
    create_table_if_missing,
)
from yaaf.core.models import JSONVariant, UTCDateTime

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "data_source_tools",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("title", sa.String(200)),
        sa.Column("description", sa.Text),
        sa.Column("input_schema", JSONVariant),
        sa.Column("annotations", JSONVariant),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("kind_reason", sa.String(300), nullable=False),
        sa.Column("permission", sa.String(8), nullable=False),
        sa.Column("decided_by", sa.String(16), nullable=False),
        sa.Column("decided_by_user_id", sa.String(40)),
        sa.Column("decided_at", UTCDateTime, nullable=False),
        sa.Column("first_seen_at", UTCDateTime, nullable=False),
        sa.Column("last_seen_at", UTCDateTime, nullable=False),
        sa.Column("gone_at", UTCDateTime),
    )
    create_index_if_missing("ix_data_source_tools_key", "data_source_tools",
                            ["project_id", "source", "name"], unique=True)
    add_column_if_missing("approvals", sa.Column("tool_permission", JSONVariant))


def downgrade() -> None:
    op.drop_column("approvals", "tool_permission")
    op.drop_index("ix_data_source_tools_key", table_name="data_source_tools")
    op.drop_table("data_source_tools")
