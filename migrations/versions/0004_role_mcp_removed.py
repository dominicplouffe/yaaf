"""Roles no longer own MCP servers — data sources are project-owned.

Scrubs the legacy ``tool_permissions.mcp_servers`` key from every stored role
version, pinned ones included: a server line frozen into a role template
follows every project that pins the role, which is exactly the ghost this
removal kills. The API refuses the shape from now on.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, tool_permissions FROM role_versions")
    ).fetchall()
    for row_id, perms in rows:
        data = perms if isinstance(perms, dict) else json.loads(perms or "{}")
        if "mcp_servers" not in data:
            continue
        data.pop("mcp_servers")
        bind.execute(
            sa.text("UPDATE role_versions SET tool_permissions = :perms WHERE id = :id"),
            {"perms": json.dumps(data), "id": row_id},
        )


def downgrade() -> None:
    # The scrubbed lines are gone deliberately; there is nothing to restore.
    pass
