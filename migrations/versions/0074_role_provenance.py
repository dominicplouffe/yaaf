"""Where a role came from.

`roles.source` and `roles.source_index` name the listing and the index a role
was installed from; NULL is a role written or seeded on this install.
`role_versions.source_version` and `role_versions.content_sha256` record which
listing version a role version is and the hash it was checked against.

Revision ID: 0074
Revises: 0073
"""
import sqlalchemy as sa

from yaaf.core.migration_ops import add_column_if_missing

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("roles", sa.Column("source", sa.String(200)))
    add_column_if_missing("roles", sa.Column("source_index", sa.String(500)))
    add_column_if_missing("role_versions", sa.Column("source_version", sa.Integer()))
    add_column_if_missing("role_versions", sa.Column("content_sha256", sa.String(64)))


def downgrade() -> None:
    from alembic import op

    op.drop_column("role_versions", "content_sha256")
    op.drop_column("role_versions", "source_version")
    op.drop_column("roles", "source_index")
    op.drop_column("roles", "source")
