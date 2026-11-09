"""Whether a project's job runs through email, and who opened each email thread.

`projects.needs_mail`: 

a project can say it needs its own mailbox — it waits for mail, answers it,
sends its work out by it. Such a project can be created without one and cannot
start until it has one. The flag is design and travels with a setup file; the
mailbox itself stays per install.

`mail_threads.opened_by`: "ceo", or "them" when a listed sender wrote first on
a project that lets them.

Revision ID: 0069
Revises: 0068
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("projects", sa.Column("needs_mail", sa.Boolean, nullable=False,
                                                server_default=sa.false()))
    add_column_if_missing("mail_threads", sa.Column("opened_by", sa.String(8), nullable=False,
                                                    server_default="ceo"))


def downgrade() -> None:
    op.drop_column("mail_threads", "opened_by")
    op.drop_column("projects", "needs_mail")
