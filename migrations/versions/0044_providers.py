"""The first providers that are not Anthropic.

docs/RUNTIMES.md M3. Three things a second provider needs that one never did:

- `project_llm_credentials` — a project whose seats run on two providers pays
  each with its own credential. `projects.billing_secret_id` stays the billing
  credential, whichever provider it pays; this holds the others, one per
  provider. Empty on every existing project, which is exactly right: until now
  there was one provider and the billing credential paid it.
- `model_prices` — an install's own price for a model, over the shipped
  catalog. yaaf ships no price it could not check against the vendor's own
  list, so every non-Anthropic model is priced here or not run on a harness
  that meters by price.
- `secrets.base_url` — where a custom OpenAI-compatible server lives, for the
  credential that pays it.

Revision ID: 0044
Revises: 0043
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing, create_table_if_missing
from yaaf.core.models import UTCDateTime

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("secrets", sa.Column("base_url", sa.String(500)))
    create_table_if_missing(
        "project_llm_credentials",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("secret_id", sa.String(40), sa.ForeignKey("secrets.id"), nullable=False),
        sa.Column("created_at", UTCDateTime),
        sa.UniqueConstraint("project_id", "provider"),
    )
    create_table_if_missing(
        "model_prices",
        sa.Column("model", sa.String(128), primary_key=True),
        sa.Column("input", sa.Float, nullable=False),
        sa.Column("output", sa.Float, nullable=False),
        sa.Column("cache_read", sa.Float),
        sa.Column("cache_write", sa.Float),
        sa.Column("updated_at", UTCDateTime),
    )


def downgrade() -> None:
    op.drop_table("model_prices")
    op.drop_table("project_llm_credentials")
    op.drop_column("secrets", "base_url")
