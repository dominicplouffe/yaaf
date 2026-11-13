"""Who a task's result is written for, and whether a person opened a file.

`tasks.result_for` is "team", "chairman" or "outside"; NULL reads as the team.
It shapes how the result is written, never who is told about it.
`artifacts.opens` / `first_opened_at` count a person opening a published file,
which nothing recorded before. `artifacts.opening` is the file's opening
answer, lifted at publish so its announcements can quote it.
`artifacts.reader` is who the file was written for, and `announced_at` when the
chairman was told: a person's files at once, the team's in one roundup.

Revision ID: 0072
Revises: 0071
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import UTCDateTime

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("tasks", sa.Column("result_for", sa.String(12)))
    add_column_if_missing("artifacts", sa.Column("opens", sa.Integer, nullable=False,
                                                 server_default="0"))
    add_column_if_missing("artifacts", sa.Column("first_opened_at", UTCDateTime))
    add_column_if_missing("artifacts", sa.Column("opening", sa.Text))
    add_column_if_missing("artifacts", sa.Column("reader", sa.String(12)))
    add_column_if_missing("artifacts", sa.Column("announced_at", UTCDateTime))


def downgrade() -> None:
    from alembic import op

    op.drop_column("artifacts", "announced_at")
    op.drop_column("artifacts", "reader")
    op.drop_column("artifacts", "opening")
    op.drop_column("artifacts", "first_opened_at")
    op.drop_column("artifacts", "opens")
    op.drop_column("tasks", "result_for")
