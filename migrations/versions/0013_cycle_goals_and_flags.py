"""Cycle goals, standing requirements, and the flag.

Three changes that together let an Operate company say when its work is due,
whether it is honouring what recurs, and — the part no mechanism existed for —
tell its chairman when the answer is no.

* `cycles` gains a goal, a due-by, and the PROVENANCE of that due-by. A
  deadline the world imposes is a fact to be discovered, not a promise to be
  made, so the CEO may record one and must cite where it came from.
* `requirements` gains `recurrence`. Everything existing is TERMINAL, which is
  what it already was; STANDING is the shape Operate needed and never had.
* `requirement_cycle_results` is the operating record: one standing obligation,
  judged for one cycle, so "met in 23 of the last 24" becomes a fact rather
  than a claim.
* `flags` is the obligation to speak.

Guarded, as every post-baseline revision must be.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import (
    add_column_if_missing,
    create_index_if_missing,
    create_table_if_missing,
    has_column,
    has_table,
)
from yaaf.core.models import JSONVariant

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("cycles", sa.Column("goal", sa.Text()))
    add_column_if_missing("cycles", sa.Column("due_at", sa.DateTime(timezone=True)))
    add_column_if_missing("cycles", sa.Column("due_source", JSONVariant))

    add_column_if_missing(
        "requirements",
        sa.Column("recurrence", sa.String(16), nullable=False,
                  server_default="terminal"),
    )

    create_table_if_missing(
        "requirement_cycle_results",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("requirement_id", sa.String(40), nullable=False),
        sa.Column("cycle_id", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("met_at", sa.DateTime(timezone=True)),
        sa.Column("met_by_run_id", sa.String(40)),
        sa.Column("check_output", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("requirement_id", "cycle_id"),
    )
    create_index_if_missing(
        "ix_reqcycle_project_cycle", "requirement_cycle_results",
        ["project_id", "cycle_id"],
    )

    create_table_if_missing(
        "flags",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("subject_id", sa.String(40)),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("arithmetic", JSONVariant),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True)),
        sa.Column("raised_in_message_id", sa.String(40)),
        sa.Column("proposal", sa.Text()),
        sa.Column("resolution", sa.Text()),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "kind", "subject_id", "status"),
    )
    create_index_if_missing("ix_flags_project_status", "flags",
                            ["project_id", "status"])


def downgrade() -> None:
    if has_table("flags"):
        op.drop_table("flags")
    if has_table("requirement_cycle_results"):
        op.drop_table("requirement_cycle_results")
    if has_column("requirements", "recurrence"):
        op.drop_column("requirements", "recurrence")
    for column in ("due_source", "due_at", "goal"):
        if has_column("cycles", column):
            op.drop_column("cycles", column)
