"""Let an LLM credential be retired from the menu without being destroyed.

Revision ID: 0036
Revises: 0035
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

# The name index moves with the column. 0034 reserved every managed
# credential's name for as long as the row existed; hiding is worth nothing if
# the name a chairman can no longer see still refuses the one they want to
# type. Rebuilt rather than added because a partial index's predicate is part
# of its identity, and a fresh database already built the new one from the
# models.
INDEX = ("CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_credential_name ON secrets (lower(name)) "
         "WHERE llm_mode IS NOT NULL AND hidden_at IS NULL")


def upgrade() -> None:
    add_column_if_missing("secrets", sa.Column("hidden_at", sa.DateTime(timezone=True),
                                               nullable=True))
    op.execute("DROP INDEX IF EXISTS uq_llm_credential_name")
    op.execute(INDEX)


def downgrade() -> None:
    # Going back means two credentials may again differ only by being hidden,
    # and the old index would refuse to build over them. Un-hide first.
    op.execute("DROP INDEX IF EXISTS uq_llm_credential_name")
    op.execute("UPDATE secrets SET hidden_at = NULL WHERE hidden_at IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_credential_name "
               "ON secrets (lower(name)) WHERE llm_mode IS NOT NULL")
