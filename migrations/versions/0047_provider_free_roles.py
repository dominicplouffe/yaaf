"""A role says how hard; the project says on what.

A role used to name its model — `claude-sonnet-5-5` — and a seat's provider was
read off that name, so every role in the library was an Anthropic role and a
project on any other vendor's key was refused seat by seat. Now a role names a
tier (light, standard, strong) and an effort, and nothing about a vendor; the
project answers the tier with its provider's lineup, or its own
(`projects.seat_models`). docs/RUNTIMES.md, "Three axes, one resolution".

  * `role_versions.tier`, read off the model each version named.
  * `agents.tier_override` — what `stakes: high` now stamps instead of a model,
    so a lifted seat follows the project to whatever provider it pays.
  * `projects.seat_models` — a project's own tier → model, NULL for "the
    provider's lineup".
  * `role_versions.model` and `role_versions.harness` go: both named a vendor.

**No seat changes model on upgrade.** Each seat is resolved the old way and the
new way, and where they differ the old answer is stamped on the seat as an
override — a custom role on Fable, a DeepSeek role hired onto a Claude project.
A seat that was already overridden onto exactly what its tier now resolves to
is turned into a tier override, so it keeps following the project. A role
version's harness is stamped on its seats the same way.

The lineups and ranks are frozen here, as they stood when this ran, rather than
imported: a later change to the catalog must not change what this migration
did to a database that ran it.

Revision ID: 0047
Revises: 0046
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing, drop_column_if_present, has_column
from yaaf.core.models import JSONVariant

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

TIERS = ("light", "standard", "strong")
LINEUPS = {
    "anthropic": ("claude-haiku-4-5", "claude-sonnet-5-5", "claude-opus-5-5"),
    "deepseek": ("deepseek-chat", "deepseek-chat", "deepseek-reasoner"),
    "google": ("gemini-3-flash-preview", "gemini-3.8-flash", "gemini-3.1-pro-preview"),
    "openai": ("gpt-5.6-luna", "gpt-6-sol", "gpt-6-astra"),
}
#: model → (provider, rank within it), as the catalog listed them.
RANKS = {
    "claude-haiku-4-5": ("anthropic", 1), "claude-sonnet-5": ("anthropic", 2),
    "claude-sonnet-5-5": ("anthropic", 3), "claude-opus-5": ("anthropic", 4),
    "claude-opus-5-5": ("anthropic", 5), "claude-fable-5-1": ("anthropic", 6),
    "deepseek-chat": ("deepseek", 1), "deepseek-reasoner": ("deepseek", 2),
    "gpt-5.5": ("openai", 1), "gpt-5.6-luna": ("openai", 2), "gpt-5.6-terra": ("openai", 3),
    "gpt-5.6-sol": ("openai", 4), "gpt-6-luna": ("openai", 5), "gpt-6-sol": ("openai", 6),
    "gpt-6.1-sol": ("openai", 7), "gpt-6-astra": ("openai", 8),
    "gemini-2.5-flash": ("google", 1), "gemini-3-flash-preview": ("google", 2),
    "gemini-3.8-flash": ("google", 3), "gemini-2.5-pro": ("google", 4),
    "gemini-3.1-pro-preview": ("google", 5),
}
PROVIDERS = ("anthropic", "deepseek", "openrouter", "custom", "moonshot", "qwen", "mistral",
             "xai", "openai", "google")


def _provider_of(model: str | None) -> str:
    if model in RANKS:
        return RANKS[model][0]
    prefix, _, rest = (model or "").partition("/")
    return prefix if rest and prefix in PROVIDERS else "anthropic"


def _tier_of(model: str | None) -> str:
    """yaaf.runtimes.catalog.tier_of, frozen."""
    shipped = LINEUPS.get(_provider_of(model))
    if not shipped:
        return "standard"
    held = [t for t, m in zip(TIERS, shipped, strict=True) if m == model]
    if held:
        return "standard" if "standard" in held else held[0]
    rank = RANKS.get(model or "", (None, 0))[1]
    if not rank:
        return "standard"
    return min(zip(TIERS, shipped, strict=True),
               key=lambda rung: (abs(RANKS[rung[1]][1] - rank), -TIERS.index(rung[0])))[0]


def upgrade() -> None:
    add_column_if_missing("role_versions", sa.Column(
        "tier", sa.String(16), nullable=False, server_default="standard"))
    add_column_if_missing("agents", sa.Column("tier_override", sa.String(16)))
    add_column_if_missing("projects", sa.Column("seat_models", JSONVariant))

    bind = op.get_bind()
    named_model = has_column("role_versions", "model")
    named_harness = has_column("role_versions", "harness")
    if named_model:
        for row in bind.execute(sa.text("SELECT id, model FROM role_versions")).mappings():
            bind.execute(sa.text("UPDATE role_versions SET tier = :tier WHERE id = :id"),
                         {"tier": _tier_of(row["model"]), "id": row["id"]})

        seats = bind.execute(sa.text(
            "SELECT a.id, a.model_override, a.harness_override, rv.model, rv.tier, "
            + ("rv.harness, " if named_harness else "NULL AS harness, ")
            + "s.provider AS pays "
            "FROM agents a "
            "LEFT JOIN teams t ON t.id = a.team_id "
            "JOIN role_versions rv ON rv.id = COALESCE(a.role_version_id, t.role_version_id) "
            "JOIN projects p ON p.id = a.project_id "
            "LEFT JOIN secrets s ON s.id = p.billing_secret_id"
        )).mappings().all()
        for seat in seats:
            ships = LINEUPS.get(seat["pays"] or "anthropic")
            lineup = dict(zip(TIERS, ships, strict=True)) if ships else {}
            changes: dict = {}
            override = seat["model_override"]
            if override:
                # Already where its tier would put it: keep it following the project.
                held = [t for t, m in lineup.items() if m == override]
                if held:
                    changes = {"model_override": None,
                               "tier_override": None if seat["tier"] in held else held[-1]}
            elif seat["model"] and lineup.get(seat["tier"]) != seat["model"]:
                changes["model_override"] = seat["model"]
            if seat["harness"] and not seat["harness_override"]:
                changes["harness_override"] = seat["harness"]
            if changes:
                sets = ", ".join(f"{column} = :{column}" for column in changes)
                bind.execute(sa.text(f"UPDATE agents SET {sets} WHERE id = :id"),
                             {**changes, "id": seat["id"]})

    drop_column_if_present("role_versions", "harness")
    drop_column_if_present("role_versions", "model")


def downgrade() -> None:
    add_column_if_missing("role_versions", sa.Column("model", sa.String(64)))
    add_column_if_missing("role_versions", sa.Column("harness", sa.String(32)))
    bind = op.get_bind()
    anthropic = dict(zip(TIERS, LINEUPS["anthropic"], strict=True))
    for row in bind.execute(sa.text("SELECT id, tier FROM role_versions")).mappings():
        bind.execute(sa.text("UPDATE role_versions SET model = :model WHERE id = :id"),
                     {"model": anthropic.get(row["tier"], anthropic["standard"]),
                      "id": row["id"]})
    op.drop_column("projects", "seat_models")
    op.drop_column("agents", "tier_override")
    op.drop_column("role_versions", "tier")
