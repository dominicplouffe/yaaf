"""A project is front-loaded and then it is not, and the roster never heard.

Every project hires once, at the moment before any work exists, and keeps that
roster until it ends. Planning, requirements, build, validation and quality
assurance all crowd the front half; execution is the back half and needs far
fewer seats. There was no verb for the transition. `agents.status` has always
had `paused`, the dispatcher has always honoured it, and the only door to it
was an admin endpoint reachable by curl — no UI, no CLI, nothing an agent could
call. A CEO watching two of its five seats do nothing for two days could
observe it and could not act on it.

Four columns, and none of them changes how an existing row behaves:

  * `agents.stood_down_at` / `stood_down_reason` — why a seat stopped, in the
    words of whoever stopped it. A stand-down is a staffing decision somebody
    has to be able to read back six weeks later.
  * `teams.stand_down_when` — when this team's job ends, declared while
    planning and evaluated by the platform. Structured, never prose, because
    the platform has to be able to fire it.
  * `teams.stood_down_at` — when it did.

`agents.status` is a plain String(16) and gains a third value, `stood_down`,
with no schema change: every read in the platform filters on `== 'active'`, so
a new value is inert everywhere until something is explicitly taught about it.

Revision ID: 0040
Revises: 0039
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

#: JSONB on Postgres, JSON on SQLite — the variant every other JSON column in
#: this schema uses, so a suite green on SQLite is green about the same shape.
JSON_VARIANT = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # Guarded, like every additive step since 0001: on a fresh install
    # `create_all` has already built these from the models, and an unguarded
    # ADD COLUMN would take the whole chain down with it.
    add_column_if_missing("agents", sa.Column("stood_down_at", sa.DateTime(timezone=True)))
    add_column_if_missing("agents", sa.Column("stood_down_reason", sa.Text()))
    add_column_if_missing("teams", sa.Column("stand_down_when", JSON_VARIANT))
    add_column_if_missing("teams", sa.Column("stood_down_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("teams", "stood_down_at")
    op.drop_column("teams", "stand_down_when")
    op.drop_column("agents", "stood_down_reason")
    op.drop_column("agents", "stood_down_at")
