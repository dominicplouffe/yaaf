"""How a chairman finds a role: a category, and the worlds it knows.

The library reached thirty roles while the only classification in the system
was the boolean `is_ceo_role`, so the picker could offer nothing but an
alphabetical dropdown. `category` is the aisle (one of six) and `domains` is
the filter — both on `roles`, not `role_versions`, because they are identity:
editing a prompt must never silently refile a role, and a team pinned to an
old version must never show a category the role has since left.

Both are nullable/empty by default. An install that upgrades before it
re-seeds keeps every role, filed under "Uncategorised" until
`yaaf seed-roles roles/` carries the values in.

Guarded, as every post-baseline revision must be.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import JSONVariant

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("roles", sa.Column("category", sa.String(32)))
    add_column_if_missing("roles", sa.Column("domains", JSONVariant))


def downgrade() -> None:
    op.drop_column("roles", "category")
    op.drop_column("roles", "domains")
