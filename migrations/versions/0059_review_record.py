"""The review carries what was done, from the record.

`reviews.record` — requirements made true (and how they were checked), cuts
and their reasons, deliverables, spot-checks, runs, the cycle, the bets, over
the window since the previous meeting. Composed by the janitor at open and
frozen beside `numbers`, so the meeting opens on what happened rather than on
the forecast's inputs. NULL on meetings opened before this; the screen says
so rather than inventing a record after the fact.

Revision ID: 0059
Revises: 0058
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import JSONVariant

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("reviews", sa.Column("record", JSONVariant))


def downgrade() -> None:
    op.drop_column("reviews", "record")
