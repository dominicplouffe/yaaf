"""Where the chairman's note was said in the boardroom.

`chairman_notes.message_id` is the boardroom message his note became. A note
written in the Inbox used to live only in this table, which the chat session
never read: it answered "still waiting on you" to a reply he had sent two hours
earlier. Now the note is also his line in the boardroom, and this column links
the two, so an Inbox reply is not mistaken for an answer to the CEO's own open
questions. Rows written before this have no message, and need none.

Revision ID: 0056
Revises: 0055
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("chairman_notes", sa.Column("message_id", sa.String(40)))


def downgrade() -> None:
    op.drop_column("chairman_notes", "message_id")
