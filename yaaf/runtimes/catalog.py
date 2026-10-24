"""The models a seat can be hired on: how they rank, what they cost, what the
API accepts for each — and the classes a role names instead of any of them.

A role says how much model it needs, not whose (docs/PROVIDERS.md §1): a model
reference is a concrete model (`gpt-6.1-sol`), a model of a provider the
catalog does not list (`openrouter/qwen/qwen3-coder`), a class (`@balanced`,
resolved on the project's primary provider) or a class of a named provider
(`openai/@balanced`). Each provider's lineup (yaaf.runtimes.providers) says
what each class is on it; an install overrides any of it (yaaf.core.
model_policy). Resolution is `resolve`, and nothing hands a reference to a
provider without going through it.

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

from collections.abc import Mapping
from dataclasses import dataclass

from yaaf.runtimes.providers import DEFAULT_PROVIDER, PROVIDERS

#: How hard a model thinks, in yaaf's words — weakest to strongest. A role and
#: a seat say effort this way whatever the vendor; each model's `efforts` are
#: the rungs of this ladder it takes (R6).
EFFORTS = ("low", "medium", "high", "xhigh", "max")
_EFFORT_RANK = {name: i for i, name in enumerate(EFFORTS)}

#: The classes a role names instead of a model, weakest to strongest.
CLASSES = ("fast", "balanced", "frontier")
CLASS_LABELS = {"frontier": "Frontier", "balanced": "Balanced", "fast": "Fast"}
CLASS_BLURBS = {
    "frontier": "the most capable model the provider sells",
    "balanced": "the one you would run all day",
    "fast": "cheap and quick: triage, formatting, lookups",
}
CLASS_SIGIL = "@"


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
    #: The rungs of yaaf's effort ladder the model takes, where its maker
    #: publishes the list; None when it does not, and then only the harness's
    #: own list is held (R6). A pinned seat must name one of these; a class
    #: seat runs at the lowest one at or above what it asked for (`rung`).
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
    # The names Mistral keeps pointed at its current model of each size, as
    # DeepSeek does. Unpriced, for the reason above; no effort dial on the
    # chat API.
    Model("mistral-small-latest", "mistral", 1, effort=False),
    Model("mistral-medium-latest", "mistral", 2, effort=False),
    Model("mistral-large-latest", "mistral", 3, effort=False),
)

_BY_ID = {m.id: m for m in MODELS}

#: What a seat with no role version, and a role nobody gave a model, runs on:
#: the project's primary provider's balanced model.
DEFAULT_MODEL = "@balanced"
DEFAULT_EFFORT = "high"


def model(model_id: str | None) -> Model | None:
    return _BY_ID.get(model_id or "")


# ── references: a model, or a class of one ─────────────────────────────────────


class Unresolved(ValueError):
    """A class reference read as a model without a provider to resolve it on.
    Raised rather than guessed: a reader that forgot to resolve must fail in a
    test, not bill the default provider in production."""


def parse(ref: str | None) -> tuple[str | None, str | None]:
    """(provider, class) of a class reference — provider None when the project's
    primary provider is meant — or (None, None) for a concrete model."""
    ref = ref or ""
    prefix, slash, rest = ref.partition("/")
    if slash and rest.startswith(CLASS_SIGIL) and prefix in PROVIDERS:
        return prefix, rest[1:]
    if ref.startswith(CLASS_SIGIL):
        return None, ref[1:]
    return None, None


def is_class(ref: str | None) -> bool:
    return parse(ref)[1] is not None


def class_ref(cls: str, provider: str | None = None) -> str:
    return f"{provider}/{CLASS_SIGIL}{cls}" if provider else f"{CLASS_SIGIL}{cls}"


def valid_ref(ref: str | None) -> str | None:
    """Why `ref` cannot be a model reference, or None. A class must be one of
    the three, and a provider named before it must exist; anything else is a
    model name and is the provider's to know."""
    if not (ref or "").strip():
        return "a model is required"
    _, cls = parse(ref)
    if cls is not None and cls not in CLASSES:
        return (f"'{ref}' names no class — a class is "
                f"{', '.join(class_ref(c) for c in reversed(CLASSES))}")
    prefix, slash, rest = (ref or "").partition("/")
    if slash and rest.startswith(CLASS_SIGIL) and prefix not in PROVIDERS:
        return f"'{ref}' names a class of '{prefix}', which is not a provider here"
    return None


def lineup_of(provider: str, overrides: Mapping[str, Mapping[str, str]] | None = None
              ) -> dict[str, str]:
    """A provider's lineup: what it ships, under what the install says."""
    shipped = dict(PROVIDERS[provider].lineup or {}) if provider in PROVIDERS else {}
    shipped.update({k: v for k, v in ((overrides or {}).get(provider) or {}).items() if v})
    return shipped


def resolve(ref: str | None, provider: str,
            overrides: Mapping[str, Mapping[str, str]] | None = None) -> str | None:
    """The concrete model `ref` names when the project's primary provider is
    `provider`, or None when the class has no model on that provider (R8 —
    refused at every door, never filled in with some other model)."""
    named, cls = parse(ref)
    if cls is None:
        return ref
    return lineup_of(named or provider, overrides).get(cls)


def class_label(ref: str | None) -> str:
    """A class reference as a person says it: "@balanced" is "Balanced",
    "openai/@fast" is "Fast (OpenAI)"."""
    provider, cls = parse(ref)
    name = CLASS_LABELS.get(cls or "", cls or "")
    return f"{name} ({PROVIDERS[provider].label})" if provider else name


def class_rank(ref: str | None) -> int:
    _, cls = parse(ref)
    return CLASSES.index(cls) if cls in CLASSES else -1


# ── effort: yaaf's ladder, and each model's rungs on it (R6) ────────────────────


def rungs(model_id: str | None, harness_efforts: tuple[str, ...] | None) -> tuple[str, ...] | None:
    """The rungs a seat on this model and harness can run at: the model's own
    list where its maker publishes one, else the harness's. None: yaaf's names
    pass as they are (the Claude harnesses). Empty: there is no dial."""
    row = model(model_id)
    if row is not None and not row.effort and row.efforts is None:
        return ()
    if harness_efforts is not None and row is not None and row.efforts is not None:
        return tuple(e for e in row.efforts if e in harness_efforts) or harness_efforts
    if row is not None and row.efforts is not None:
        return row.efforts
    return harness_efforts


def rung(effort: str, available: tuple[str, ...] | None) -> str | None:
    """The rung a class seat asking for `effort` runs at: the lowest one at or
    above it, or the top one when it asked for more than the model has. Never
    lower while a higher rung exists — "max sent as high is a seat run cheaper
    than it was hired at" (R6) — and None where there is no dial at all."""
    if available is None:
        return effort
    if not available:
        return None
    ordered = sorted(available, key=lambda e: _EFFORT_RANK.get(e, 99))
    want = _EFFORT_RANK.get(effort, 0)
    for candidate in ordered:
        if _EFFORT_RANK.get(candidate, 99) >= want:
            return candidate
    return ordered[-1]


_SPELLED = {"gpt": "GPT", "deepseek": "DeepSeek"}


def label(model_id: str | None) -> str:
    """A model as a person says it: "claude-sonnet-5-5" is "Claude Sonnet 5.5",
    "gpt-5.6-luna" is "GPT 5.6 Luna". Runs of version digits join with dots."""
    if not model_id:
        return "the default model"
    if is_class(model_id):
        return class_label(model_id)
    words: list[str] = []
    for part in model_id.split("/")[-1].split("-"):
        if part.isdigit() and words and words[-1].replace(".", "").isdigit():
            words[-1] = f"{words[-1]}.{part}"
        else:
            words.append(_SPELLED.get(part, part if part[:1].isdigit() else part.capitalize()))
    return " ".join(words)


def tier(model_id: str | None) -> int:
    found = model(model_id)
    return found.tier if found else 0


def provider_of(model_id: str | None) -> str:
    """The provider a concrete model — or a class of a named provider — is
    sold by. A bare class has no provider until it is resolved."""
    named, cls = parse(model_id)
    if cls is not None:
        if named is None:
            raise Unresolved(f"'{model_id}' is a class; resolve it on a provider first")
        return named
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
