"""Requirements become the root object.

The baseline is `create_all`, so a database that ran 0001 *before* these tables
existed will never get them from it — alembic reports head and the API starts
answering against a schema that is missing three tables and four columns. That
is precisely what happened on the first deploy of this stage, which is why this
file exists rather than a re-stamped baseline.

Guarded throughout: on a fresh install create_all has already built all of
this from the models, and an unguarded CREATE would take 0001 down with it.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import (
    add_column_if_missing,
    create_index_if_missing,
    create_table_if_missing,
)

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "requirements",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("key", sa.String(16), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("acceptance_test", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False),
        sa.Column("est_runs", sa.Integer()),
        sa.Column("actual_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_load", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("proposed_by_agent_id", sa.String(40)),
        sa.Column("accepted_by_user_id", sa.String(40)),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("met_at", sa.DateTime(timezone=True)),
        sa.Column("met_by_run_id", sa.String(40)),
        sa.Column("check_output", sa.Text()),
        sa.Column("cut_reason", sa.Text()),
        sa.Column("cut_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "key"),
    )
    create_index_if_missing(
        "ix_requirements_project_status", "requirements", ["project_id", "status"]
    )

    create_table_if_missing(
        "requirement_events",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("requirement_id", sa.String(40), sa.ForeignKey("requirements.id"),
                  nullable=False),
        sa.Column("from_status", sa.String(16)),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    create_index_if_missing(
        "ix_requirement_events_project_at", "requirement_events", ["project_id", "at"]
    )

    create_table_if_missing(
        "requirement_edges",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("from_requirement_id", sa.String(40), sa.ForeignKey("requirements.id"),
                  nullable=False),
        sa.Column("to_requirement_id", sa.String(40), sa.ForeignKey("requirements.id"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("from_requirement_id", "to_requirement_id"),
    )
    create_index_if_missing(
        "ix_requirement_edges_to", "requirement_edges", ["to_requirement_id"]
    )

    # Explore is the default so every project that already exists keeps
    # behaving exactly as it does — the requirement rules only bite in the
    # modes that asked for them.
    add_column_if_missing(
        "projects",
        sa.Column("mode", sa.String(16), nullable=False, server_default="explore"),
    )
    add_column_if_missing("tasks", sa.Column("requirement_id", sa.String(40)))
    add_column_if_missing("runs", sa.Column("requirement_id", sa.String(40)))
    add_column_if_missing("run_queue", sa.Column("requirement_id", sa.String(40)))


def downgrade() -> None:
    op.drop_column("run_queue", "requirement_id")
    op.drop_column("runs", "requirement_id")
    op.drop_column("tasks", "requirement_id")
    op.drop_column("projects", "mode")
    op.drop_table("requirement_edges")
    op.drop_table("requirement_events")
    op.drop_table("requirements")
