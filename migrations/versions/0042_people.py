"""People: sessions, personal tokens, setup links, and the login throttle.

The admin token authenticated a client and never a person, so everything a
person did through the dashboard or the CLI was recorded as the project's
chairman. This revision is the schema half of replacing it (docs/AUTH.md):

  * `user_sessions` — a browser signed in as a person, revocable by row
  * `user_tokens` — a personal token for the CLI, always expiring
  * `user_setup_links` — single-use links that set a password: invites, resets,
    and the first admin on a box that has none
  * `login_failures` — the throttle, keyed by a hash of the address
  * `users.deactivated_at` — leaving without the record losing its name

And two data steps an existing box needs before any of that is usable:

  * **Emails are lower-cased**, because sign-in now matches on them and the old
    New-project form stored whatever was typed. Two rows that differ only by
    case are left alone rather than merged — merging people is a decision, and
    the instance admin can make it.
  * **Every project gets the chairman row in `project_members`** that creation
    has written since that table existed. Older projects predate it, and
    membership is now what decides who may open a project at all.

Revision ID: 0042
Revises: 0041
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import (
    add_column_if_missing,
    create_index_if_missing,
    create_table_if_missing,
)

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    add_column_if_missing("users", sa.Column("deactivated_at", TS))

    create_table_if_missing(
        "user_sessions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_agent", sa.String(255)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS),
    )
    create_index_if_missing("ix_user_sessions_hash", "user_sessions", ["token_hash"],
                            unique=True)

    create_table_if_missing(
        "user_tokens",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("last_used_at", TS),
        sa.Column("revoked_at", TS),
    )
    create_index_if_missing("ix_user_tokens_hash", "user_tokens", ["token_hash"], unique=True)

    create_table_if_missing(
        "user_setup_links",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("created_by_user_id", sa.String(40)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS),
        sa.Column("revoked_at", TS),
    )
    create_index_if_missing("ix_user_setup_links_hash", "user_setup_links", ["token_hash"],
                            unique=True)

    create_table_if_missing(
        "login_failures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column("at", TS, nullable=False),
    )
    create_index_if_missing("ix_login_failures_email_at", "login_failures",
                            ["email_hash", "at"])

    bind = op.get_bind()
    # Lower-case every address whose lower-cased form nobody else holds.
    bind.execute(sa.text(
        "UPDATE users SET email = lower(email) "
        "WHERE email <> lower(email) AND NOT EXISTS ("
        "  SELECT 1 FROM users AS other "
        "  WHERE other.id <> users.id AND lower(other.email) = lower(users.email))"
    ))
    # The chairman row every project created since 0001 already has.
    bind.execute(sa.text(
        "INSERT INTO project_members (project_id, user_id, role) "
        "SELECT p.id, p.chairman_user_id, 'chairman' FROM projects AS p "
        "WHERE NOT EXISTS (SELECT 1 FROM project_members AS m "
        "                  WHERE m.project_id = p.id AND m.user_id = p.chairman_user_id)"
    ))


def downgrade() -> None:
    op.drop_table("login_failures")
    op.drop_table("user_setup_links")
    op.drop_table("user_tokens")
    op.drop_table("user_sessions")
    op.drop_column("users", "deactivated_at")
