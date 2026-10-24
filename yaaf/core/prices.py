"""What a model costs on this install: its own price over the shipped catalog.

The catalog (yaaf.runtimes.catalog) ships only prices yaaf could check against
the vendor's own list — Anthropic's. Everything else is priced here, by the
administrator, in Settings: a DeepSeek model at DeepSeek's current rates, a
local Ollama model at zero. An install's price wins for any model, the
catalog's included, so a price that moved is a settings change and not a
release.

One lookup, read by every place a price decides something — the seat doors
and the lease (R3), the sandbox's meter (the lease hands it the row), and the
price-gap check — so no two of them can disagree about whether a model is
priced.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import ModelPrice
from yaaf.runtimes import catalog
from yaaf.runtimes.providers import provider


def _row_price(row: ModelPrice) -> catalog.Price:
    # One cache-write rate: the TTL split is Anthropic's, and the catalog
    # prices Anthropic. Elsewhere a cache write is a cache write.
    write = row.cache_write or 0.0
    return catalog.Price(input=row.input, output=row.output, cache_read=row.cache_read or 0.0,
                         cache_write_5m=write, cache_write_1h=write,
                         as_of=row.updated_at.date().isoformat() if row.updated_at else "")


def price(session: Session, model: str) -> catalog.Price | None:
    row = session.get(ModelPrice, model)
    return _row_price(row) if row is not None else catalog.price(model)


#: An administrator's own windows, {model: tokens}, apart from any price: a
#: shipped price stays the shipped one when only its window is corrected.
WINDOWS = "models.context_windows"


def windows(session: Session) -> dict[str, int]:
    from yaaf.core import platform

    try:
        stored = json.loads(platform.get_setting(session, WINDOWS) or "{}")
    except ValueError:
        stored = {}
    return {m: int(n) for m, n in stored.items() if isinstance(n, int) and n > 0} \
        if isinstance(stored, dict) else {}


def set_window(session: Session, model: str, tokens: int | None) -> None:
    from yaaf.core import platform

    stored = windows(session)
    if tokens:
        stored[model] = int(tokens)
    else:
        stored.pop(model, None)
    platform.set_setting(session, WINDOWS, json.dumps(stored, sort_keys=True))


def context_window(session: Session, model: str) -> int | None:
    """The model's input window in tokens: the one an administrator set, else
    the one imported or typed with its price, else the shipped snapshot's,
    else None — and then yaaf's own loop compacts only when the provider
    refuses a call for length."""
    from yaaf.core import price_feed

    own = windows(session).get(model)
    if own:
        return own
    row = session.get(ModelPrice, model)
    if row is not None and row.context_window:
        return row.context_window
    return price_feed.shipped_window(model)


def covered(session: Session, model: str) -> bool:
    """Whether a run on `model` can be metered in dollars: it has a price, or
    its provider states each call's cost (OpenRouter)."""
    return price(session, model) is not None or provider(catalog.provider_of(model)).reports_cost


def table(session: Session) -> list[dict]:
    """Every price this install meters by: the catalog's, then its own — an
    own price beside the catalog model it replaces."""
    own = {row.model: row for row in session.scalars(select(ModelPrice))}
    out = []
    for model in catalog.MODELS:
        shipped = model.price.as_dict() if model.price else None
        row = own.pop(model.id, None)
        out.append({"model": model.id, "provider": model.provider, "listed": True,
                    "shipped": shipped,
                    "own": _row_price(row).as_dict() if row is not None else None,
                    "source": row.source if row is not None else None,
                    "context_window": context_window(session, model.id)})
    for model_id, row in sorted(own.items()):
        out.append({"model": model_id, "provider": catalog.provider_of(model_id),
                    "listed": False, "shipped": None, "own": _row_price(row).as_dict(),
                    "source": row.source, "context_window": context_window(session, model_id)})
    return out
