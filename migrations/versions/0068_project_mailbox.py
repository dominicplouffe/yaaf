"""A project mailbox: the CEO's correspondence with people outside the project.

The chairman gives a project its own address, SMTP server and IMAP server; the
CEO emails a deliverable from it and the replies come back to it. Three tables:
`mail_threads` (one per correspondence), `mail_messages` (the outbox and the
accepted replies) and `mailbox_states` (how far the inbox has been read). The
configuration itself is `projects.integrations["mail"]` and its password an
ordinary project Secret, so nothing here holds a credential.

Revision ID: 0068
Revises: 0067
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import create_index_if_missing, create_table_if_missing
from yaaf.core.models import JSONVariant, UTCDateTime

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "mail_threads",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("counterpart", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(300), nullable=False),
        sa.Column("task_id", sa.String(40)),
        sa.Column("frozen_at", UTCDateTime),
        sa.Column("reopened_at", UTCDateTime),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.Column("last_at", UTCDateTime, nullable=False),
    )
    create_index_if_missing("ix_mail_threads_project", "mail_threads",
                            ["project_id", "last_at"])
    create_table_if_missing(
        "mail_messages",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("thread_id", sa.String(40), sa.ForeignKey("mail_threads.id"),
                  nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("rfc_message_id", sa.String(400), nullable=False),
        sa.Column("in_reply_to", sa.String(400)),
        sa.Column("from_addr", sa.String(320), nullable=False),
        sa.Column("to_addr", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(300), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("artifact_id", sa.String(40)),
        sa.Column("attachments", JSONVariant),
        sa.Column("idempotency_key", sa.String(80)),
        sa.Column("run_id", sa.String(40)),
        sa.Column("side_effect_id", sa.String(40)),
        sa.Column("evidence_id", sa.String(40)),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("last_error", sa.Text),
        sa.Column("next_attempt_at", UTCDateTime),
        sa.Column("read_at", UTCDateTime),
        sa.Column("sent_at", UTCDateTime),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("project_id", "rfc_message_id"),
        sa.UniqueConstraint("project_id", "idempotency_key"),
    )
    create_index_if_missing("ix_mail_messages_thread", "mail_messages",
                            ["thread_id", "created_at"])
    create_index_if_missing("ix_mail_messages_outbox", "mail_messages",
                            ["project_id", "status"])
    create_table_if_missing(
        "mailbox_states",
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"),
                  primary_key=True),
        sa.Column("uidvalidity", sa.String(40)),
        sa.Column("last_uid", sa.Integer, nullable=False),
        sa.Column("polled_at", UTCDateTime),
        sa.Column("ok_at", UTCDateTime),
        sa.Column("last_error", sa.Text),
        sa.Column("ignored", sa.Integer, nullable=False),
        sa.Column("last_test", JSONVariant),
    )


def downgrade() -> None:
    op.drop_table("mailbox_states")
    op.drop_index("ix_mail_messages_outbox", table_name="mail_messages")
    op.drop_index("ix_mail_messages_thread", table_name="mail_messages")
    op.drop_table("mail_messages")
    op.drop_index("ix_mail_threads_project", table_name="mail_threads")
    op.drop_table("mail_threads")
