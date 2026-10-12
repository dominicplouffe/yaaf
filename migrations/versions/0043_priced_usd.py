"""Every run is priced from the catalog, whoever bills it.

docs/RUNTIMES.md M2: `anthropic_api` is the first harness yaaf prices itself,
from the model catalog — so the catalog is now a meter, and a meter is checked
against something. Runs on the harnesses that report their own bill carry both
figures, and the health checklist compares them. NULL on every existing row:
nothing priced them.

Revision ID: 0043
Revises: 0042
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("runs", sa.Column("priced_usd", sa.Float))


def downgrade() -> None:
    op.drop_column("runs", "priced_usd")
