"""Runs remember how the credential that paid them bills.

A project may pay two providers two ways — a Claude subscription for the team,
a DeepSeek key for its CEO — so whether a run moved dollars is the paying
credential's question, not the project's. Answered at finalize, it named
whoever pays *now*: a failover that closed mid-run, or a billing credential
moved mid-run, booked the run to a payer that never paid it. Now it is pinned
at lease beside `runs.provider`. Rows written before this have none and are
read by the project's billing mode, as they always were.

Revision ID: 0058
Revises: 0057
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("runs", sa.Column("paid_in", sa.String(8)))


def downgrade() -> None:
    op.drop_column("runs", "paid_in")
