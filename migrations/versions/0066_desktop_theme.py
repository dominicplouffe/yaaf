"""A person's desktop theme: `users.desktop_theme`.

What their desktop sent with `yaaf theme sync` — a mode and a palette of
#rrggbb colours — so the dashboard can wear the desktop's theme without
leaving the stylesheet's contrast guarantee behind (yaaf.core.desktop_theme
works the tokens out on every read). NULL, which every existing person starts
as, is the stylesheet's own light and dark.

Revision ID: 0066
Revises: 0065
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import JSONVariant

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("users", sa.Column("desktop_theme", JSONVariant))


def downgrade() -> None:
    op.drop_column("users", "desktop_theme")
