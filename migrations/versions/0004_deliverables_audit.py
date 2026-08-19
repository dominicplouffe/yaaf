"""Deliverables as rows, and the sampled audit.

Stage 6 of the rebuild. Two tables and one constraint:

- ``artifacts`` — the deliverable as a receipt: name, version, sha256, size,
  a real publish time, the author, and the requirements it claims to satisfy.
- ``audit_samples`` — one row per considered observable close, drawn or
  skipped by the janitor, so the draw is never re-rolled and coverage has a
  denominator.
- ``evidence.task_id`` becomes a real foreign key — it was an un-FK'd string
  the CEO's scoreboard joined on, so any pasted id inflated it.

Guarded throughout, as every post-baseline revision must be.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import (
    create_fk_if_missing,
    create_index_if_missing,
    create_table_if_missing,
)

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "artifacts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("published_by_agent_id", sa.String(40)),
        sa.Column("run_id", sa.String(40)),
        sa.Column("requirement_ids", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "name", "version"),
    )
    create_index_if_missing(
        "ix_artifacts_project_published", "artifacts", ["project_id", "published_at"]
    )

    create_table_if_missing(
        "audit_samples",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("drawn_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.Text()),
        sa.UniqueConstraint("task_id"),
    )
    create_index_if_missing(
        "ix_audit_samples_project_status", "audit_samples", ["project_id", "status"]
    )

    create_fk_if_missing("fk_evidence_task_id", "evidence", "tasks",
                         ["task_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_evidence_task_id", "evidence", type_="foreignkey")
    op.drop_table("audit_samples")
    op.drop_table("artifacts")
