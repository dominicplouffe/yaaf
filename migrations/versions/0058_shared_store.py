"""The shared store: secrets, variables and data sources above every project.

- `secrets.shared_with` marks a platform-scope row as being in the store
  ("all" or "listed"). NULL on every existing row, which none of them is.
- `shared_data_sources` holds a data source many projects may mount.
- `shared_access` is one project's access to one shared item, and the roles
  on that project that receive it (docs/SHARED_STORE.md).

Revision ID: 0058
Revises: 0057
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import (
    add_column_if_missing,
    create_index_if_missing,
    create_table_if_missing,
    has_table,
)
from yaaf.core.models import JSONVariant, UTCDateTime

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("secrets", sa.Column("shared_with", sa.String(8)))
    create_table_if_missing(
        "shared_data_sources",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(32), nullable=False, unique=True),
        sa.Column("url_cipher", sa.Text(), nullable=False),
        sa.Column("bearer_cipher", sa.Text()),
        sa.Column("entry", JSONVariant, nullable=False),
        sa.Column("shared_with", sa.String(8), nullable=False, server_default="listed"),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime()),
    )
    create_table_if_missing(
        "shared_access",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("item_kind", sa.String(16), nullable=False),
        sa.Column("item_id", sa.String(40), nullable=False),
        sa.Column("role_scopes", JSONVariant, nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime()),
        sa.UniqueConstraint("project_id", "item_kind", "item_id"),
    )
    create_index_if_missing("ix_shared_access_item", "shared_access", ["item_kind", "item_id"])


def downgrade() -> None:
    if has_table("shared_access"):
        op.drop_table("shared_access")
    if has_table("shared_data_sources"):
        op.drop_table("shared_data_sources")
    op.drop_column("secrets", "shared_with")
