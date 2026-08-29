"""Tokens rotate; grants and attribution do not.

M0 kept one hash on the grant row, which is all a pasted bearer needs. OAuth
needs refresh, so the string a client holds changes — and the audit trail names
that client forever, so the name cannot live on a row a refresh replaces. The
secret moves to `mcp_tokens`; `mcp_clients` stays the durable grant.

Existing CLI-minted tokens are carried across as `kind='static'` rows with no
expiry, so a token minted before this migration keeps working after it.

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import (
    add_column_if_missing,
    create_index_if_missing,
    create_table_if_missing,
    has_column,
    has_table,
)

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_table_if_missing(
        "mcp_tokens",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("client_id", sa.String(40), sa.ForeignKey("mcp_clients.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("resource", sa.String(500), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_to", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    create_index_if_missing("ix_mcp_tokens_hash", "mcp_tokens", ["token_hash"], unique=True)

    create_table_if_missing(
        "oauth_clients",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("client_secret_hash", sa.String(64), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("redirect_uris", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="dcr"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    create_index_if_missing("ix_oauth_clients_client_id", "oauth_clients",
                            ["client_id"], unique=True)

    create_table_if_missing(
        "oauth_codes",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("oauth_client_id", sa.String(40), nullable=False),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("project_ids", sa.JSON(), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("redirect_uri", sa.String(500), nullable=False),
        sa.Column("resource", sa.String(500), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    create_index_if_missing("ix_oauth_codes_hash", "oauth_codes", ["code_hash"], unique=True)

    add_column_if_missing("mcp_clients", sa.Column("oauth_client_id", sa.String(40), nullable=True))

    # Carry the M0 tokens over before the column holding them goes away. A
    # chairman who minted one last week should not have to notice this ran.
    if has_column("mcp_clients", "token_hash"):
        bind = op.get_bind()
        rows = bind.execute(sa.text(
            "SELECT id, token_hash, created_at, revoked_at FROM mcp_clients "
            "WHERE token_hash IS NOT NULL"
        )).fetchall()
        for row in rows:
            bind.execute(
                sa.text(
                    "INSERT INTO mcp_tokens "
                    "(id, client_id, kind, token_hash, resource, expires_at, created_at,"
                    " revoked_at) "
                    "VALUES (:id, :client, 'static', :hash, '', NULL, :created, :revoked)"
                ),
                {"id": f"mcpt_{row[0].split('_', 1)[1]}", "client": row[0], "hash": row[1],
                 "created": row[2], "revoked": row[3]},
            )
        with op.batch_alter_table("mcp_clients") as batch:
            batch.drop_column("token_hash")


def downgrade() -> None:
    add_column_if_missing("mcp_clients", sa.Column("token_hash", sa.String(64), nullable=True))
    for table in ("oauth_codes", "oauth_clients", "mcp_tokens"):
        if has_table(table):
            op.drop_table(table)
