"""Provider-neutral by construction (docs/PROVIDERS.md, M7).

- yaaf's own agent loop is one harness, `yaaf_loop`, whose wire is the
  provider's fact; `anthropic_api` and `openai_compatible` were that loop on
  two wires. Stored names move to the new one. Run rows keep the name they ran
  under — history is not rewritten — and the old names stay readable as
  aliases (yaaf.runtimes.harnesses.ALIASES).
- `model_prices.source` — where an install's price came from: NULL for one an
  administrator typed, the feed and its date for one imported (yaaf.core.
  price_feed). The prices table can then say where each number came from.
- Model columns widen to 128, the width `model_prices.model` already had: a
  Bedrock inference profile or an OpenRouter route does not fit in 64.

Revision ID: 0054
Revises: 0053
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

_RENAMED = ("anthropic_api", "openai_compatible")
_MODEL_COLUMNS = (("role_versions", "model"), ("agents", "model_override"), ("runs", "model"))


def upgrade() -> None:
    for table, column in (("role_versions", "harness"), ("agents", "harness_override")):
        op.execute(sa.text(f"UPDATE {table} SET {column} = 'yaaf_loop' "
                           f"WHERE {column} IN :names").bindparams(
                               sa.bindparam("names", value=list(_RENAMED), expanding=True)))
    add_column_if_missing("model_prices", sa.Column("source", sa.String(200)))
    if op.get_bind().dialect.name == "postgresql":
        # SQLite does not hold a VARCHAR to its length; Postgres does.
        for table, column in _MODEL_COLUMNS:
            op.alter_column(table, column, type_=sa.String(128))


def downgrade() -> None:
    op.drop_column("model_prices", "source")
