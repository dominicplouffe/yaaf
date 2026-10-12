"""A seat chooses what drives its model.

docs/RUNTIMES.md M1: with a second harness (the Claude CLI, driven directly)
there is something to choose, so the choice gets a column — on the role
version, beside the model it drives, and on the agent, beside the model
override, for the seat a project wants run differently from the library.
Both NULL on every existing row, which resolves to the provider's default
harness: exactly what every seat ran on before.

Revision ID: 0042
Revises: 0041
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("role_versions", sa.Column("harness", sa.String(32)))
    add_column_if_missing("agents", sa.Column("harness_override", sa.String(32)))


def downgrade() -> None:
    op.drop_column("agents", "harness_override")
    op.drop_column("role_versions", "harness")
