"""A refusal that the next try got past: `runs.last_refusal_*`.

`runs.last_refusal_route` is the call the last wall was hit on and
`runs.last_refusal_at` when; `runs.last_refusal_cleared_at` is when the same
call then succeeded in the same run. The boardroom stops warning about a
refusal the CEO already got past.

Existing rows start with none of the three: their refusal stays shown as it
always was.

Revision ID: 0051
Revises: 0050
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import UTCDateTime

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("runs", sa.Column("last_refusal_route", sa.String(200)))
    add_column_if_missing("runs", sa.Column("last_refusal_at", UTCDateTime()))
    add_column_if_missing("runs", sa.Column("last_refusal_cleared_at", UTCDateTime()))


def downgrade() -> None:
    op.drop_column("runs", "last_refusal_cleared_at")
    op.drop_column("runs", "last_refusal_at")
    op.drop_column("runs", "last_refusal_route")
