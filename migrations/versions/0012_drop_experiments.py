"""Drop the topology experiment.

0007 added `experiments` and `experiment_arms` to compare three org shapes on
one goal. The feature is gone — the dashboard page, the admin and observe
endpoints, the stop rule and the models with it — so the tables go too rather
than linger as schema nothing reads.

`projects.topology` stays: the org shape is a fact about a company, recorded at
creation and read by the clone path, and it long outlived what it was first
added for.

Guarded, as every post-baseline revision must be: an install that never ran
0007 has nothing to drop.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import has_table

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Arms first: they carry the foreign key into experiments.
    for table in ("experiment_arms", "experiments"):
        if has_table(table):
            op.drop_table(table)


def downgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("requirement_set", sa.JSON(), nullable=False),
        sa.Column("constitution", sa.JSON(), nullable=False),
        sa.Column("chairman_user_id", sa.String(40), sa.ForeignKey("users.id"),
                  nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "experiment_arms",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("experiment_id", sa.String(40), sa.ForeignKey("experiments.id"),
                  nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"),
                  nullable=False),
        sa.Column("topology", sa.String(24), nullable=False),
        sa.Column("stopped_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("experiment_id", "topology"),
    )
