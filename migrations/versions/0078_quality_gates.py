"""Quality gates: what a change's diff says, and hidden scenarios.

Revision ID: 0078
Revises: 0077
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import (
    add_column_if_missing,
    create_index_if_missing,
    create_table_if_missing,
)

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("changes", sa.Column("findings", sa.JSON(), nullable=True))
    add_column_if_missing("changes", sa.Column("waivers", sa.JSON(), nullable=True))
    add_column_if_missing("changes", sa.Column("chairman_sha", sa.String(64), nullable=True))
    add_column_if_missing("changes", sa.Column("scenario_results", sa.JSON(), nullable=True))
    create_table_if_missing(
        "scenarios",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("requirement_id", sa.String(40), sa.ForeignKey("requirements.id"),
                  nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("author_agent_id", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    create_index_if_missing("ix_scenarios_requirement", "scenarios", ["requirement_id"])


def downgrade() -> None:
    from alembic import op

    op.drop_index("ix_scenarios_requirement", table_name="scenarios")
    op.drop_table("scenarios")
    for column in ("scenario_results", "chairman_sha", "waivers", "findings"):
        op.drop_column("changes", column)
