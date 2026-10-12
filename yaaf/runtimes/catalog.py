"""The models a seat can be hired on, and how they rank.

Data, not logic. A model this file does not list can still be named on a role —
nothing here refuses one yet — but it ranks 0, so `stakes: high` lifts it
rather than leaving it, and it is served by the default provider, which until a
second provider exists is the only answer there is.

Prices arrive with the harness that needs them to stop a run (docs/RUNTIMES.md,
M2). Until then the Agent SDK reports each run's cost itself, as it always has.
"""

from __future__ import annotations

from dataclasses import dataclass

from yaaf.runtimes.providers import DEFAULT_PROVIDER


@dataclass(frozen=True)
class Model:
    id: str
    provider: str
    #: Weakest to strongest within the provider; 0 is "never heard of it". Only
    #: ever compared within one provider — a tier is not a cross-vendor claim.
    tier: int


MODELS: tuple[Model, ...] = (
    Model("claude-haiku-4-5", "anthropic", 1),
    Model("claude-sonnet-5", "anthropic", 2),
    Model("claude-sonnet-5-5", "anthropic", 3),
    Model("claude-opus-5", "anthropic", 4),
    Model("claude-opus-5-5", "anthropic", 5),
    Model("claude-fable-5-1", "anthropic", 6),
)

_BY_ID = {m.id: m for m in MODELS}

#: What a seat with no role version, and a role nobody gave a model, runs on.
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "high"


def model(model_id: str | None) -> Model | None:
    return _BY_ID.get(model_id or "")


def tier(model_id: str | None) -> int:
    found = model(model_id)
    return found.tier if found else 0


def provider_of(model_id: str | None) -> str:
    found = model(model_id)
    return found.provider if found else DEFAULT_PROVIDER
