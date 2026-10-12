"""The models a seat can be hired on: how they rank, what they cost, and what
the API accepts for each.

Data, not logic. A model this file does not list can still be named on a role.
It ranks 0, so `stakes: high` lifts it rather than leaving it, and it is
served by the provider its name starts with — `openrouter/qwen/qwen3-coder`,
`custom/llama3.1:8b` — or by the default provider when it names none. A harness
that *prices* runs itself refuses one with no price (R3, yaaf.runtimes.seats):
a run nobody can price is a run the budget cannot stop.

Prices here are Anthropic's published list prices, per million tokens, as of
the date beside them — the only ones yaaf could check against the vendor's own
list. Every other provider's models ship unpriced, and an install prices them
in Settings (yaaf.core.prices), which wins over this table for any model. They
are checked rather than trusted: every run on a harness that reports its own
bill is also priced, and a model whose two figures drift apart is named on the
health checklist of every project the price meters (docs/RUNTIMES.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from yaaf.runtimes.providers import DEFAULT_PROVIDER, PROVIDERS


@dataclass(frozen=True)
class Price:
    """USD per million tokens. Cache writes are priced by TTL: the 5-minute
    entry a loop writes and the 1-hour entry Claude Code writes are different
    prices for the same tokens."""
    input: float
    output: float
    cache_read: float
    cache_write_5m: float
    cache_write_1h: float
    as_of: str

    def as_dict(self) -> dict:
        """What a sandbox prices a run by: it cannot import this module, so the
        row travels to it as JSON (`YAAF_PRICE`)."""
        return {"input": self.input, "output": self.output, "cache_read": self.cache_read,
                "cache_write_5m": self.cache_write_5m, "cache_write_1h": self.cache_write_1h,
                "web_search": WEB_SEARCH_USD, "as_of": self.as_of}


#: Per web search on the Messages API's server tool. Web fetch is not metered.
WEB_SEARCH_USD = 0.01


@dataclass(frozen=True)
class Model:
    id: str
    provider: str
    #: Weakest to strongest within the provider; 0 is "never heard of it". Only
    #: ever compared within one provider — a tier is not a cross-vendor claim.
    tier: int
    price: Price | None = None
    #: Whether the API takes `output_config.effort` for this model. Claude
    #: Haiku 4.5 rejects it, so a harness that builds requests itself omits it.
    effort: bool = True
    #: The server web tools this model takes: "20260209" (dynamic filtering)
    #: or "basic" (`web_search_20250305` / `web_fetch_20250910`).
    web_tools: str = "basic"


_AS_OF = "2026-10-12"

MODELS: tuple[Model, ...] = (
    Model("claude-haiku-4-5", "anthropic", 1,
          Price(1.00, 5.00, 0.10, 1.25, 2.00, _AS_OF), effort=False),
    Model("claude-sonnet-5", "anthropic", 2,
          Price(2.00, 10.00, 0.20, 2.50, 4.00, _AS_OF), web_tools="20260209"),
    Model("claude-sonnet-5-5", "anthropic", 3,
          Price(2.00, 10.00, 0.20, 2.50, 4.00, _AS_OF),
          web_tools="20260209"),
    Model("claude-opus-5", "anthropic", 4,
          Price(5.00, 25.00, 0.50, 6.25, 10.00, _AS_OF),
          web_tools="20260209"),
    Model("claude-opus-5-5", "anthropic", 5,
          Price(4.00, 20.00, 0.20, 5.00, 8.00, _AS_OF),
          web_tools="20260209"),
    Model("claude-fable-5-1", "anthropic", 6,
          Price(10.00, 50.00, 0.25, 12.50, 20.00, _AS_OF)),
    # The names DeepSeek's API keeps pointed at its current chat and reasoning
    # models. Unpriced, for the reason above.
    Model("deepseek-chat", "deepseek", 1, effort=False),
    Model("deepseek-reasoner", "deepseek", 2, effort=False),
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
    if found:
        return found.provider
    prefix, _, rest = (model_id or "").partition("/")
    return prefix if rest and prefix in PROVIDERS else DEFAULT_PROVIDER


def api_name(model_id: str) -> str:
    """What the provider's own API calls the model: the name without the
    provider in front of it, where the name carries one."""
    prefix, _, rest = model_id.partition("/")
    return rest if rest and prefix in PROVIDERS and model(model_id) is None else model_id


def price(model_id: str | None) -> Price | None:
    found = model(model_id)
    return found.price if found else None
