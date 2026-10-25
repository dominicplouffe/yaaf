"""Prices an install imports, from a feed or a dated snapshot (docs/PROVIDERS.md §5).

The rule this serves stands unchanged: yaaf ships only prices it could check
against the vendor's own published list, because a budget trusts the number it
is given — and from where this was built, only Anthropic's list could be
checked, while the third-party lists that could be read disagreed with each
other. So every other model is priced by the install (yaaf.core.prices), and
this module is how an install does that without typing two dozen numbers in by
hand: it reads a price feed — the LiteLLM `model_prices_and_context_window.json`
format, at an address the install chooses — maps it onto yaaf's model names,
and shows what it *would* write. Nothing is written until an administrator
applies it, and then each row carries where it came from (`ModelPrice.source`).

Three kinds of row are never touched unless asked:

- a price the administrator typed (source NULL) — theirs wins over any feed;
- a model the catalog ships a checked price for — the vendor's own list wins
  over a feed's copy of it;
- a model the feed does not list — reported, so the gap is visible.

The gap check (yaaf.server.api.observe.price_gaps) still compares every bill a
self-reporting harness sends against the price it was metered at, imported
prices included: a feed that is wrong shows up as drift on the health list.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import model_policy, platform
from yaaf.core.models import Agent, ModelPrice, RoleVersion, utcnow
from yaaf.runtimes import catalog
from yaaf.runtimes.providers import PROVIDERS

#: The community-maintained feed whose shape this reads. An install may point
#: the setting at its own mirror; air-gapped installs import the snapshot.
DEFAULT_FEED_URL = ("https://raw.githubusercontent.com/BerriAI/litellm/main/"
                    "model_prices_and_context_window.json")
FEED_URL = "prices.feed_url"
FEED_LABEL = "LiteLLM price feed"
SNAPSHOT = Path(__file__).resolve().parents[1] / "runtimes" / "price_snapshot.json"
#: The transport the feed is fetched over — a test's stand-in, else httpx's.
TRANSPORT: httpx.BaseTransport | None = None

#: Where each yaaf provider's models sit in the feed: (the feed's provider
#: name, the prefix its keys carry). First match wins, so the provider's own
#: API is read before a cloud's copy of the same model. A provider absent here
#: has nothing a feed can say: OpenRouter states each call's cost, an Azure
#: model is a deployment's name, a local server is the chairman's own machine.
FEED_PROVIDERS: dict[str, tuple[tuple[str, str], ...]] = {
    "anthropic": (("anthropic", ""),),
    "openai": (("openai", ""),),
    "google": (("gemini", "gemini/"), ("vertex_ai-language-models", "")),
    "vertex": (("vertex_ai-language-models", ""),),
    "deepseek": (("deepseek", "deepseek/"), ("deepseek", "")),
    "mistral": (("mistral", "mistral/"),),
    "xai": (("xai", "xai/"),),
    "groq": (("groq", "groq/"),),
    "together": (("together_ai", "together_ai/"),),
    "fireworks": (("fireworks_ai", "fireworks_ai/"),),
    "cerebras": (("cerebras", "cerebras/"),),
    "cohere": (("cohere_chat", ""), ("cohere", "cohere/")),
    "nvidia": (("nvidia_nim", "nvidia_nim/"),),
    "bedrock": (("bedrock_converse", ""), ("bedrock", "bedrock/"), ("bedrock", "")),
    "moonshot": (("moonshot", "moonshot/"),),
    "qwen": (("dashscope", "dashscope/"),),
    "zai": (("zai", "zai/"),),
}
#: The feed's kinds of entry a seat can run on.
_CHAT_MODES = ("chat", "responses", None)


@dataclass(frozen=True)
class FeedPrice:
    """USD per million tokens, as yaaf prices."""
    input: float
    output: float
    cache_read: float
    cache_write: float
    #: The input window the feed states, which the loop compacts against.
    context_window: int | None = None

    def as_dict(self) -> dict:
        return {"input": self.input, "output": self.output, "cache_read": self.cache_read,
                "cache_write": self.cache_write, "context_window": self.context_window}


def feed_url(session: Session) -> str:
    return platform.get_setting(session, FEED_URL) or DEFAULT_FEED_URL


def fetch(url: str) -> dict:
    """The feed, as JSON. A failure is the caller's to report — nothing here
    falls back to a guess."""
    with httpx.Client(transport=TRANSPORT, timeout=60, follow_redirects=True) as http:
        response = http.get(url)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("the feed is not a JSON object of models")
    return body


def _per_million(value) -> float:
    return round(float(value or 0.0) * 1_000_000, 6)


def entry_price(entry: dict) -> FeedPrice | None:
    if not isinstance(entry, dict) or entry.get("input_cost_per_token") is None \
            or entry.get("output_cost_per_token") is None:
        return None
    return FeedPrice(input=_per_million(entry["input_cost_per_token"]),
                     output=_per_million(entry["output_cost_per_token"]),
                     cache_read=_per_million(entry.get("cache_read_input_token_cost")),
                     cache_write=_per_million(entry.get("cache_creation_input_token_cost")),
                     context_window=_tokens(entry.get("max_input_tokens")))


def _tokens(value) -> int | None:
    try:
        tokens = int(value)
    except (TypeError, ValueError):
        return None
    return tokens if tokens > 0 else None


def lookup(feed: dict, model: str) -> FeedPrice | None:
    """The feed's price for a yaaf model name, or None."""
    provider = catalog.provider_of(model)
    name = catalog.api_name(model)
    for feed_provider, prefix in FEED_PROVIDERS.get(provider, ()):
        entry = feed.get(f"{prefix}{name}")
        if (isinstance(entry, dict) and entry.get("litellm_provider") == feed_provider
                and entry.get("mode") in _CHAT_MODES):
            found = entry_price(entry)
            if found is not None:
                return found
    return None


def provider_models(feed: dict, provider: str) -> list[str]:
    """Every chat model the feed lists for a provider, by yaaf's names."""
    listed = {m.id for m in catalog.MODELS if m.provider == provider}
    out = set()
    for key, entry in feed.items():
        if not isinstance(entry, dict) or entry.get("mode") not in _CHAT_MODES:
            continue
        for feed_provider, prefix in FEED_PROVIDERS.get(provider, ()):
            if entry.get("litellm_provider") == feed_provider and key.startswith(prefix) \
                    and entry_price(entry) is not None:
                name = key[len(prefix):]
                out.add(name if name in listed else f"{provider}/{name}")
                break
    return sorted(out)


def wanted(session: Session) -> list[str]:
    """The models this install could run a seat on: every catalog model, every
    model a lineup resolves a class to, every model a role version or a seat
    pins, and the Founder's — each a concrete name, never a class."""
    names = {m.id for m in catalog.MODELS}
    own = model_policy.lineups(session)
    for provider in PROVIDERS:
        names |= set(catalog.lineup_of(provider, own).values())
    pinned = set(session.scalars(select(RoleVersion.model)))
    pinned |= set(session.scalars(select(Agent.model_override)
                                  .where(Agent.model_override.is_not(None))))
    names |= {ref for ref in pinned if ref and not catalog.is_class(ref)}
    names.add(platform.founder_model(session))
    return sorted(n for n in names if n and not catalog.is_class(n))


def preview(session: Session, feed: dict, models: list[str], *, source: str,
            overwrite_own: bool = False, include_shipped: bool = False) -> list[dict]:
    """What applying `feed` to `models` would do, model by model: `add`,
    `update`, `same`, or why not — `own` (a hand-set price), `shipped` (the
    catalog's checked price), `missing` (the feed does not list it)."""
    rows = []
    for model in sorted(set(models)):
        if catalog.is_class(model) or catalog.provider_of(model) not in PROVIDERS:
            continue
        found = lookup(feed, model)
        current = session.get(ModelPrice, model)
        shipped = catalog.price(model)
        row = {"model": model, "provider": catalog.provider_of(model),
               "proposed": found.as_dict() if found else None,
               "current": ({"input": current.input, "output": current.output,
                            "cache_read": current.cache_read or 0.0,
                            "cache_write": current.cache_write or 0.0,
                            "context_window": current.context_window,
                            "source": current.source} if current is not None else None),
               "shipped": shipped is not None}
        if found is None:
            row["action"] = "missing"
        elif current is not None and current.source is None and not overwrite_own:
            row["action"] = "own"
        elif current is None and shipped is not None and not include_shipped:
            row["action"] = "shipped"
        elif current is not None and all(
                abs((getattr(current, k) or 0.0) - (v or 0.0)) < 1e-9
                # A window the feed does not state changes nothing: the
                # one the row has stays (apply keeps it).
                for k, v in found.as_dict().items() if v is not None or k != "context_window"):
            row["action"] = "same"
        else:
            row["action"] = "update" if current is not None else "add"
        row["source"] = source
        rows.append(row)
    return rows


def apply(session: Session, rows: list[dict]) -> list[str]:
    """Write every `add` and `update` row of a preview. Returns the models."""
    written = []
    for row in rows:
        if row["action"] not in ("add", "update"):
            continue
        price = session.get(ModelPrice, row["model"]) or ModelPrice(model=row["model"])
        proposed = row["proposed"]
        price.input, price.output = proposed["input"], proposed["output"]
        price.cache_read, price.cache_write = proposed["cache_read"], proposed["cache_write"]
        # A feed that does not state a window leaves one somebody typed.
        price.context_window = proposed.get("context_window") or price.context_window
        price.source = row["source"][:200]
        price.updated_at = utcnow()
        session.add(price)
        written.append(row["model"])
    session.flush()
    return written


@functools.cache
def _snapshot_text() -> str:
    return SNAPSHOT.read_text()


def snapshot() -> dict:
    """The dated snapshot shipped in the repo: the feed's rows for the models
    yaaf names, for an install that cannot reach the feed."""
    return json.loads(_snapshot_text())


def shipped_window(model: str) -> int | None:
    """The input window the shipped snapshot states for a model, or None."""
    found = lookup(snapshot()["models"], model)
    return found.context_window if found is not None else None


def snapshot_feed() -> tuple[dict, str]:
    """The snapshot as a feed, and the source label its rows carry."""
    found = snapshot()
    return found["models"], f"{FEED_LABEL} snapshot {found['as_of']}"


def build_snapshot(feed: dict, models: list[str], *, url: str = DEFAULT_FEED_URL) -> dict:
    """The snapshot file's contents, from a live feed: the feed's own entries
    for `models`, kept in its shape so the snapshot is read exactly as the
    feed is. `yaaf prices snapshot` writes it; a release refreshes it."""
    kept = {}
    for model in models:
        provider = catalog.provider_of(model)
        name = catalog.api_name(model)
        for feed_provider, prefix in FEED_PROVIDERS.get(provider, ()):
            entry = feed.get(f"{prefix}{name}")
            if isinstance(entry, dict) and entry.get("litellm_provider") == feed_provider \
                    and entry_price(entry) is not None:
                kept[f"{prefix}{name}"] = {k: entry[k] for k in (
                    "litellm_provider", "mode", "input_cost_per_token", "output_cost_per_token",
                    "cache_read_input_token_cost", "cache_creation_input_token_cost",
                    "max_input_tokens", "max_output_tokens") if k in entry}
                break
    return {"source": FEED_LABEL, "url": url,
            "as_of": datetime.now(UTC).date().isoformat(), "models": kept}


def source_label(when: datetime | None = None) -> str:
    return f"{FEED_LABEL} {(when or datetime.now(UTC)).date().isoformat()}"
