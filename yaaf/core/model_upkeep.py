"""Keeping an install's models and prices current (docs/PROVIDERS.md §5, §8).

A lineup and a price list are written once and then rot: a provider retires
the model a class resolves to, ships a better one beside it, moves a price.
Each of those otherwise surfaces as a symptom — runs refused at the lease, a
budget off by a third — weeks after the fact. Two facts keep it honest:

- **What each key can actually call**, re-read from the provider's own model
  list (a free call) and remembered per credential. A seat whose model a
  provider no longer lists is named on its project's health checklist before
  its next run fails; models the key can call that no lineup uses are shown
  beside the lineup, to be picked rather than typed.
- **Prices from the feed**, re-read daily when the install opted in. Only
  prices that came from the feed are moved, and models a lineup or a seat
  needs are added; a price an administrator typed, and the catalog's checked
  ones, are never touched (yaaf.core.price_feed). Every change is audited
  with what it was before.

Off by default: it reaches out to every provider and to the feed once a day,
and an install decides whether it wants that. "Run now" does the same once.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import platform, price_feed
from yaaf.core.models import Secret, utcnow
from yaaf.runtimes import catalog

log = logging.getLogger("yaaf.model_upkeep")

ENABLED = "upkeep.enabled"
LAST = "upkeep.last"
DISCOVERY = "discovery."
#: How often the daily pass runs, and how old a model list may be before a
#: health check stops trusting it: a list from last month says little.
EVERY = timedelta(hours=23)
FRESH_FOR = timedelta(days=14)


def _load(session: Session, key: str) -> dict:
    raw = platform.get_setting(session, key)
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def enabled(session: Session) -> bool:
    return platform.get_setting(session, ENABLED) == "1"


def set_enabled(session: Session, on: bool) -> None:
    platform.set_setting(session, ENABLED, "1" if on else "0")


def last(session: Session) -> dict:
    return _load(session, LAST)


# ── what each key can call ─────────────────────────────────────────────────────


def list_served(session: Session, secret: Secret) -> list[str]:
    """The provider's own model list for this credential, by its own names.
    Raises `wire.WireError`, `httpx.HTTPError` or `model_tokens.MintError`."""
    from yaaf.core import model_tokens
    from yaaf.core.crypto import decrypt
    from yaaf.runtimes import wire
    from yaaf.runtimes.providers import provider, spec

    seller = provider(secret.provider)
    key = decrypt(secret.ciphertext)
    if seller.mints_tokens:
        key, _ = model_tokens.mint(seller.name, key)
    endpoint = wire.Endpoint.from_spec(spec(seller, secret.llm_mode, secret.base_url), key)
    return asyncio.run(wire.list_models(endpoint))


def record(session: Session, secret: Secret, names: list[str]) -> None:
    platform.set_setting(session, DISCOVERY + secret.id, json.dumps(
        {"at": utcnow().isoformat(), "provider": secret.provider, "models": sorted(set(names))}))


def discovered(session: Session, secret_id: str) -> dict | None:
    found = _load(session, DISCOVERY + secret_id)
    return found or None


def _fresh(found: dict) -> bool:
    try:
        at = datetime.fromisoformat(found["at"])
    except (KeyError, TypeError, ValueError):
        return False
    return utcnow() - at <= FRESH_FOR


def served(session: Session) -> dict[str, dict]:
    """Per provider: the newest fresh model list any of its credentials
    returned — {"at", "models", "credential_id"}."""
    out: dict[str, dict] = {}
    for secret in session.scalars(_listable()):
        found = discovered(session, secret.id)
        if not found or not _fresh(found):
            continue
        current = out.get(secret.provider)
        if current is None or found["at"] > current["at"]:
            out[secret.provider] = {**found, "credential_id": secret.id}
    return out


def not_served(session: Session, models: list[str]) -> dict[str, dict]:
    """Of `models`, the ones whose provider's fresh model list leaves them
    out: {model: the list's date}. A provider with no fresh list says nothing
    — no list is not evidence of absence."""
    lists = served(session)
    out = {}
    for model in models:
        found = lists.get(catalog.provider_of(model))
        if found is not None and catalog.api_name(model) not in set(found["models"]):
            out[model] = {"at": found["at"], "provider": catalog.provider_of(model)}
    return out


def _listable():
    """The credentials a model list can be read for: API keys of a provider
    with a wire, not hidden."""
    return select(Secret).where(Secret.llm_mode == "api_key", Secret.project_id.is_(None),
                                Secret.hidden_at.is_(None))


# ── the daily pass ─────────────────────────────────────────────────────────────


def due(session: Session) -> bool:
    if not enabled(session):
        return False
    try:
        at = datetime.fromisoformat(last(session).get("at", ""))
    except ValueError:
        return True
    return utcnow() - at >= EVERY


def run(session: Session) -> dict:
    """Re-read every key's model list, then refresh prices from the feed.
    Returns what it did, which is also kept as `upkeep.last`."""
    from yaaf.core import model_tokens
    from yaaf.runtimes import wire
    from yaaf.runtimes.providers import provider
    from yaaf.server.api.admin import _audit

    lists: dict[str, dict] = {}
    for secret in session.scalars(_listable()).all():
        if provider(secret.provider).protocol is None:
            continue
        try:
            names = list_served(session, secret)
        except (wire.WireError, httpx.HTTPError, model_tokens.MintError) as exc:
            lists[secret.id] = {"name": secret.name, "error": str(exc)[:200]}
            continue
        record(session, secret, names)
        lists[secret.id] = {"name": secret.name, "models": len(names)}
    prices: dict = {}
    try:
        feed = price_feed.fetch(price_feed.feed_url(session))
    except (httpx.HTTPError, ValueError) as exc:
        prices = {"error": f"the price feed could not be read: {exc}"[:300]}
    else:
        rows = price_feed.preview(session, feed, price_feed.wanted(session),
                                  source=price_feed.source_label())
        # Never an administrator's own price, never the catalog's checked one
        # (the preview already holds both back); and a feed row moves only a
        # price that came from a feed in the first place.
        movable = [r for r in rows if r["action"] == "add" or (
            r["action"] == "update" and (r["current"] or {}).get("source"))]
        written = price_feed.apply(session, movable)
        changes = [{"model": r["model"], "from": r["current"], "to": r["proposed"]}
                   for r in movable if r["model"] in written]
        if changes:
            _audit(session, None, "model_price.refresh", "model", "feed",
                   {"source": price_feed.source_label(), "changes": changes})
        prices = {"changed": [c["model"] for c in changes], "checked": len(rows)}
    summary = {"at": utcnow().isoformat(), "credentials": lists, "prices": prices}
    platform.set_setting(session, LAST, json.dumps(summary))
    return summary


def tick() -> None:
    """The janitor's call: the daily pass when it is due, in its own
    transaction, its failure logged and recorded, never raised."""
    from yaaf.core.db import db_session
    from yaaf.server import acting

    try:
        # Its price changes are audited, and an audit row names who did it.
        with acting.acting(acting.Actor("system", "model-upkeep")), db_session() as session:
            if due(session):
                run(session)
    except Exception:  # noqa: BLE001 — a background pass reports, never raises
        log.exception("model upkeep failed")
