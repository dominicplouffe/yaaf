"""What a run executed on, and what a credential pays — the first of runtimes.

docs/RUNTIMES.md separates four facts that used to be one word and one vendor:
the provider, the credential kind, the harness and the model. This is the
schema half of its M0, and none of it changes how an existing row behaves:

  * `secrets.provider` — which provider an LLM credential pays; `llm_mode` is
    the kind within it. NOT NULL with a server default, so every existing row
    reads `anthropic`, which is what every one of them is.
  * `runs.provider` / `runs.harness` — pinned at lease time beside `model`,
    for the same reason `model` is: a reaped run stays attributed to what
    actually ran. NULL on history, which was all the Agent SDK on Anthropic;
    nothing is backfilled, because "not recorded" is the truth about it.
  * `runs.error_kind` — how a failed run failed, as its harness classified it.
    The credential-failure pause reads the last three runs, so it lives on the
    row rather than only on the result in flight.

Revision ID: 0041
Revises: 0040
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guarded, like every additive step since 0001: on a fresh install
    # `create_all` has already built these from the models.
    add_column_if_missing("secrets", sa.Column(
        "provider", sa.String(32), nullable=False, server_default="anthropic"))
    add_column_if_missing("runs", sa.Column("provider", sa.String(32)))
    add_column_if_missing("runs", sa.Column("harness", sa.String(32)))
    add_column_if_missing("runs", sa.Column("error_kind", sa.String(16)))


def downgrade() -> None:
    op.drop_column("runs", "error_kind")
    op.drop_column("runs", "harness")
    op.drop_column("runs", "provider")
    op.drop_column("secrets", "provider")
