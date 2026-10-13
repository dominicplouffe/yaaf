"""The models a seat can run on: how they rank, what they cost, what the API
accepts for each — and the three tiers a role asks for instead of a model.

A role names no model and no provider. It names a tier — light, standard or
strong — and the project's provider answers it: each provider ships a lineup,
one model per tier, and a project may name its own (`Project.seat_models`).
That is what lets one role library serve a project on any vendor's key; a role
that named `claude-opus-5-5` was an Anthropic role, whatever its prompt said.

A model this file does not list can still be named in a lineup or on a seat.
It ranks 0, and it is served by the provider its name starts with —
`openrouter/qwen/qwen3-coder`, `custom/llama3.1:8b` — or by the default
provider when it names none. A harness that *prices* runs itself refuses one
with no price (R3, yaaf.runtimes.seats): a run nobody can price is a run the
budget cannot stop.

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

from yaaf.runtimes.providers import DEFAULT_PROVIDER, PROVIDERS, provider


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
    #: ever compared within one provider — a rank is not a cross-vendor claim.
    rank: int
    price: Price | None = None
    #: Whether the API takes `output_config.effort` for this model. Claude
    #: Haiku 4.5 rejects it, so a harness that builds requests itself omits it.
    effort: bool = True
    #: The server web tools this model takes: "20260209" (dynamic filtering)
    #: or "basic" (`web_search_20250305` / `web_fetch_20250910`).
    web_tools: str = "basic"
    #: The efforts the model takes, where its maker publishes the list; None
    #: when it does not, and then only the harness's own list is held (R6).
    efforts: tuple[str, ...] | None = None


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
    # OpenAI's, as the Codex CLI the sandbox pins (0.162.1) lists them in its
    # own model presets, best last here, with the reasoning efforts each
    # preset declares. Unpriced: OpenAI's list was not reachable to check.
    Model("gpt-5.5", "openai", 1, efforts=("low", "medium", "high", "xhigh")),
    Model("gpt-5.6-luna", "openai", 2, efforts=("low", "medium", "high", "xhigh", "max")),
    Model("gpt-5.6-terra", "openai", 3, efforts=("low", "medium", "high", "xhigh", "max")),
    Model("gpt-5.6-sol", "openai", 4, efforts=("low", "medium", "high", "xhigh", "max")),
    Model("gpt-6-luna", "openai", 5, efforts=("low", "medium", "high", "xhigh", "max")),
    Model("gpt-6-sol", "openai", 6, efforts=("low", "medium", "high", "xhigh", "max")),
    Model("gpt-6.1-sol", "openai", 7, efforts=("low", "medium", "high", "xhigh", "max")),
    Model("gpt-6-astra", "openai", 8, efforts=("low", "medium", "high", "xhigh", "max")),
    # Google's, as the Gemini CLI the sandbox pins (0.63.0) names them, and
    # only the names it sends as given: it swaps others for a newer model on
    # the wire (yaaf/runner/gemini_cli_entry.py). The 3 family takes a
    # thinking level, low or high; the 2.5 family keeps the CLI's own
    # thinking budget and has no dial. Unpriced: Google's list was not
    # reachable to check.
    Model("gemini-2.5-flash", "google", 1, efforts=("high",)),
    Model("gemini-3-flash-preview", "google", 2, efforts=("low", "high")),
    Model("gemini-3.8-flash", "google", 3, efforts=("low", "high")),
    Model("gemini-2.5-pro", "google", 4, efforts=("high",)),
    Model("gemini-3.1-pro-preview", "google", 5, efforts=("low", "high")),
)

_BY_ID = {m.id: m for m in MODELS}

#: What a role asks for instead of a model, weakest first. Provider-neutral:
#: "strong" is Opus on an Anthropic key and gpt-6-astra on an OpenAI one.
TIERS = ("light", "standard", "strong")
DEFAULT_TIER = "standard"

#: The efforts a role or a seat may name, cheapest first. One ladder for every
#: provider; a model that takes fewer runs at the nearest it does (fit_effort).
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = "high"

#: What the default provider's standard tier is, and what a run that somehow
#: reached the sandbox naming nothing falls back to (sandbox_entry keeps a
#: literal a test holds to this).
DEFAULT_MODEL = "claude-sonnet-5-5"


def model(model_id: str | None) -> Model | None:
    return _BY_ID.get(model_id or "")


def rank(model_id: str | None) -> int:
    found = model(model_id)
    return found.rank if found else 0


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


def lineup(provider_name: str | None, own: dict | None = None) -> dict[str, str | None]:
    """The model each tier runs on, for a project whose provider is
    `provider_name` and whose own choices are `own` (`Project.seat_models`).

    The project's choice for a tier, else the provider's, else the project's
    standard — so a provider that ships no lineup (a custom server, OpenRouter)
    needs one model named, and every tier runs on it. None where nothing
    answers: a seat on that tier cannot run, and the doors say so."""
    own = {t: m for t, m in (own or {}).items() if m}
    ships = provider(provider_name).lineup
    shipped = dict(zip(TIERS, ships, strict=True)) if ships else {}
    return {t: own.get(t) or shipped.get(t) or own.get(DEFAULT_TIER) for t in TIERS}


def tier_of(model_id: str | None) -> str:
    """The tier a model holds in its own provider's lineup — how a role written
    when roles named models (an old seed folder, an exported bundle) is read
    now. A model off the lineup reads as the rung nearest its rank, ties going
    up as efforts do (last generation's Opus is strong, not standard); one
    nobody ranks is standard."""
    shipped = provider(provider_of(model_id)).lineup
    if not shipped:
        return DEFAULT_TIER
    held = [t for t, m in zip(TIERS, shipped, strict=True) if m == model_id]
    if held:
        # A model a provider ships on two rungs (DeepSeek's chat model) reads
        # as the middle one, where a role that named it most likely meant it.
        return DEFAULT_TIER if DEFAULT_TIER in held else held[0]
    found = rank(model_id)
    if not found:
        return DEFAULT_TIER
    return min(zip(TIERS, shipped, strict=True),
               key=lambda rung: (abs(rank(rung[1]) - found), -TIERS.index(rung[0])))[0]


def efforts_taken(harness_efforts: tuple[str, ...] | None,
                  model_id: str | None) -> tuple[str, ...] | None:
    """The efforts a seat on this harness and model can actually run at, or
    None when nothing limits them (the Claude harnesses take yaaf's names).
    Empty when there is no dial at all."""
    found = model(model_id)
    taken = harness_efforts
    if found is not None and found.efforts is not None:
        taken = found.efforts if taken is None else tuple(
            e for e in taken if e in found.efforts)
    return taken


def fit_effort(effort: str, harness_efforts: tuple[str, ...] | None,
               model_id: str | None) -> str:
    """The effort a seat asked for, or the nearest one its harness and model
    take — ties go up, because the role asked for at least that much care.
    A role's effort is a level on one ladder, not a vendor's word; a Gemini
    seat whose role asks `xhigh` runs at `high`, and says so wherever the seat
    is shown (`Seat.effort_asked`). An effort somebody *typed* for one seat is
    held to R6 instead, at the door that took it."""
    taken = efforts_taken(harness_efforts, model_id)
    if not taken or effort in taken or effort not in EFFORTS:
        return effort
    at = EFFORTS.index(effort)
    return min((e for e in taken if e in EFFORTS),
               key=lambda e: (abs(EFFORTS.index(e) - at), -EFFORTS.index(e)),
               default=effort)
