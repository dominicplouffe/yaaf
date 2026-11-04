"""A price carries its long-prompt rates.

Grok, Gemini Pro and OpenAI's GPT-6 bill a higher rate once a call's prompt
passes a size — twice the rate past 200k tokens for Grok — and the price feed
says so. yaaf imported only the base rate, so a run whose prompts grew past the
line was metered at half what it spent, and its budget stopped it late.

Schema: `model_prices.tiers`, the higher rates and the prompt size past which
each applies (yaaf.runtimes.catalog.Tier). Existing rows get none; the daily
upkeep pass refreshes feed-sourced rows and fills them in.

Revision ID: 0065
Revises: 0064
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing
from yaaf.core.models import JSONVariant

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("model_prices", sa.Column("tiers", JSONVariant))


def downgrade() -> None:
    op.drop_column("model_prices", "tiers")
