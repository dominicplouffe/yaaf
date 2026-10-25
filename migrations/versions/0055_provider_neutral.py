"""Provider-neutral by construction (docs/PROVIDERS.md, M7).

- yaaf's own agent loop is one harness, `yaaf_loop`, whose wire is the
  provider's fact; `anthropic_api` and `openai_compatible` were that loop on
  two wires. Stored names move to the new one. Run rows keep the name they ran
  under — history is not rewritten — and the old names stay readable as
  aliases (yaaf.runtimes.harnesses.ALIASES).
- `model_prices.source` — where an install's price came from: NULL for one an
  administrator typed, the feed and its date for one imported (yaaf.core.
  price_feed). The prices table can then say where each number came from.
- `model_prices.context_window` — the model's input window in tokens, which
  yaaf's own loop compacts against (a provider's API does not compact for it).
  Imported with the price, or typed beside it.
- `provider_checks` — one model checked on one credential: probes and small
  agent tasks, graded mechanically (yaaf.core.provider_check).
- `projects.fallback_secret_id` and `credential_breakers` — failover: a
  credential whose provider keeps failing opens its breaker, and the projects
  it bills move their class seats to their fallback credential's provider
  until it closes (yaaf.core.breakers).
- Model columns widen to 128, the width `model_prices.model` already had: a
  Bedrock inference profile or an OpenRouter route does not fit in 64.
- A seat that ran on the Codex or Gemini CLI because an OpenAI or Google API
  key paid it, with no harness named, keeps that CLI as an override. An API
  key of theirs now defaults to yaaf's own loop (CredentialKind.
  default_harness); a running project's seats do not change engine under it
  on deploy. New seats get the loop; the chairman can move these any time.

Revision ID: 0055
Revises: 0054
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import (
    add_column_if_missing,
    create_fk_if_missing,
    create_index_if_missing,
    create_table_if_missing,
)
from yaaf.core.models import JSONVariant, UTCDateTime

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

_RENAMED = ("anthropic_api", "openai_compatible")
#: What an API key of these providers drove a seat with before it defaulted to
#: yaaf's loop.
_FORMER_DEFAULT = {"openai": "codex_cli", "google": "gemini_cli"}
_MODEL_COLUMNS = (("role_versions", "model"), ("agents", "model_override"), ("runs", "model"))


def upgrade() -> None:
    for table, column in (("role_versions", "harness"), ("agents", "harness_override")):
        op.execute(sa.text(f"UPDATE {table} SET {column} = 'yaaf_loop' "
                           f"WHERE {column} IN :names").bindparams(
                               sa.bindparam("names", value=list(_RENAMED), expanding=True)))
    _keep_former_defaults()
    add_column_if_missing("model_prices", sa.Column("source", sa.String(200)))
    add_column_if_missing("model_prices", sa.Column("context_window", sa.Integer))
    create_table_if_missing(
        "provider_checks",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("secret_id", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False),
        sa.Column("outcomes", JSONVariant, nullable=False),
        sa.Column("tasks", sa.Boolean, nullable=False),
        sa.Column("cost_usd", sa.Float),
        sa.Column("started_by", sa.String(40)),
        sa.Column("started_at", UTCDateTime, nullable=False),
        sa.Column("finished_at", UTCDateTime),
        sa.Column("updated_at", UTCDateTime, nullable=False),
    )
    create_index_if_missing("ix_provider_checks_secret_at", "provider_checks",
                            ["secret_id", "started_at"])
    add_column_if_missing("projects", sa.Column("fallback_secret_id", sa.String(40)))
    create_fk_if_missing("fk_projects_fallback_secret", "projects", "secrets",
                         ["fallback_secret_id"], ["id"])
    create_table_if_missing(
        "credential_breakers",
        sa.Column("secret_id", sa.String(40), primary_key=True),
        sa.Column("failures", sa.Integer, nullable=False),
        sa.Column("trips", sa.Integer, nullable=False),
        sa.Column("opened_at", UTCDateTime),
        sa.Column("open_until", UTCDateTime),
        sa.Column("last_error", sa.String(300)),
        sa.Column("updated_at", UTCDateTime, nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        # SQLite does not hold a VARCHAR to its length; Postgres does.
        for table, column in _MODEL_COLUMNS:
            op.alter_column(table, column, type_=sa.String(128))


def _keep_former_defaults() -> None:
    """Pin the CLI an OpenAI or Google API-key seat ran on, where nothing named
    its harness — read with the catalog as this release ships it, which is
    the one that resolves a model name to its provider."""
    from yaaf.runtimes.catalog import provider_of

    bind = op.get_bind()
    seats = bind.execute(sa.text(
        # A team's members carry no role version of their own: they run on
        # their team's (yaaf.server.contextpack.role_version_for).
        "SELECT a.id, a.project_id, a.model_override, rv.model FROM agents a "
        "LEFT JOIN teams t ON t.id = a.team_id "
        "JOIN role_versions rv ON rv.id = COALESCE(a.role_version_id, t.role_version_id) "
        "WHERE a.harness_override IS NULL AND rv.harness IS NULL")).all()
    for agent_id, project_id, override, role_model in seats:
        provider = provider_of(override or role_model or "")
        if provider not in _FORMER_DEFAULT:
            continue
        paid = bind.execute(sa.text(
            "SELECT s.llm_mode FROM projects p JOIN secrets s ON s.id = p.billing_secret_id "
            "WHERE p.id = :project AND s.provider = :provider "
            "UNION ALL "
            "SELECT s.llm_mode FROM project_llm_credentials c JOIN secrets s "
            "ON s.id = c.secret_id WHERE c.project_id = :project AND c.provider = :provider"),
            {"project": project_id, "provider": provider}).first()
        if paid is not None and (paid[0] or "api_key") == "api_key":
            bind.execute(sa.text("UPDATE agents SET harness_override = :harness "
                                 "WHERE id = :id"),
                         {"harness": _FORMER_DEFAULT[provider], "id": agent_id})


def downgrade() -> None:
    op.drop_table("credential_breakers")
    op.drop_column("projects", "fallback_secret_id")
    op.drop_table("provider_checks")
    op.drop_column("model_prices", "context_window")
    op.drop_column("model_prices", "source")
