"""Retire the one-call approvals that let a call through a denied data source tool.

A TOOL_REQUEST approval carrying a `grant` used to let ONE call through to a
tool whose permission said deny. A denied tool now fails every time; the only
way past is the chairman allowing the tool. Pending grants could no longer do
anything if approved, so they are closed here rather than left on his desk
looking like they would. No schema change: decided rows keep their history.

Revision ID: 0071
Revises: 0070
"""
import sqlalchemy as sa
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None

NOTE = ("retired: one-call approvals no longer let a call through a denied data source "
        "tool — allow the tool in Settings → Data sources instead")


def upgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE approvals SET status = 'expired', decision_note = :note, "
                "decided_at = CURRENT_TIMESTAMP "
                "WHERE kind = 'tool_request' AND status = 'pending' AND \"grant\" IS NOT NULL"),
        {"note": NOTE})


def downgrade() -> None:
    pass  # what was closed stays closed
