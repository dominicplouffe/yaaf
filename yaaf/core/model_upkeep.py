"""Keeping an install's models and prices current.

A lineup and a price list are written once and then rot: a provider retires
the model a class resolves to, ships a better one beside it, moves a price.
Each of those otherwise surfaces as a symptom — runs refused at the lease, a
budget off by a third — weeks after the fact. Two facts keep it honest:

- **What each key can actually call**, re-read from the provider's own model
  list (a free call) and remembered per credential. A seat whose model a
  provider no longer lists is named on its project's health checklist before
  its next run fails; models the key can call that no lineup uses are shown
  beside the lineup, to be picked rather than typed.
- **Prices from the feed**, re-read daily. Only prices that came from the
  feed are moved, and models a lineup, a seat or a key's own list offers are
  added; a price an administrator typed, and the catalog's checked ones, are
  never touched (yaaf.core.price_feed). Every change is audited with what it
  was before.

A key's list is also priced the moment it is read — when a lineup is being
chosen from it — so the models on offer carry their price, window and kind
(chat, or image and video ones a seat cannot run on) before one is picked.
That only fills gaps: moving a price is the daily pass's, so a feed that could
not be reached, and a snapshot read in its place, never undoes a fresher one.

On by default, because the alternative is a lineup picked from names with no
price beside them and refused the moment it is saved. An install that must not
reach the feed every day switches it off, and then yaaf moves no price on its
own: lists read then are described from the shipped snapshot, and priced by
hand or by Import. "Run now" does the daily pass once either way.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import platform, price_feed
from yaaf.core.models import ProviderCheck, Secret, utcnow
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
    """On unless an administrator switched it off: an install that never
    touched the setting gets the daily pass."""
    return platform.get_setting(session, ENABLED) != "0"


def set_enabled(session: Session, on: bool) -> None:
    platform.set_setting(session, ENABLED, "1" if on else "0")


def last(session: Session) -> dict:
    return _load(session, LAST)


# ── what each key can call ─────────────────────────────────────────────────────


def list_served(session: Session, secret: Secret) -> tuple[list[str], dict[str, int]]:
    """The provider's own model list for this credential, by its own names,
    and when it says each was created (wire.model_dates). Raises
    `wire.WireError`, `httpx.HTTPError` or `model_tokens.MintError`."""
    return asyncio.run(_list(_endpoint(secret)))


def record(session: Session, secret: Secret, names: list[str],
           feed: dict | None = None, created: dict[str, int] | None = None) -> None:
    """Remember what the key listed, and what `feed` says each listed model is
    (price_feed.details). Where `feed` is silent — it was down, or the
    snapshot read in its place lists only the catalog's models — what an
    earlier feed said stands for the names still listed: a feed that could not
    be read today did not change what a model is."""
    models = sorted(set(names))
    before = (discovered(session, secret.id) or {}).get("details") or {}
    described = {n: before[n] for n in models if n in before}
    for name in models if feed is not None else ():
        found = price_feed.details(feed, catalog.by_api_name(secret.provider, name))
        if found is not None:
            described[name] = found
    platform.set_setting(session, DISCOVERY + secret.id, json.dumps(
        {"at": utcnow().isoformat(), "provider": secret.provider, "models": models,
         "details": described,
         "created": {n: t for n, t in (created or {}).items() if n in set(models)}}))


def listed_runnable(session: Session) -> set[str]:
    """Every model a key's own list offers that the feed says can hold a seat,
    by the name a seat would use. Only those the feed described: one it does
    not list has no price to fetch, and the gap is named where a seat needs it."""
    out = set()
    for secret in session.scalars(_listable()):
        found = discovered(session, secret.id)
        for name, said in ((found or {}).get("details") or {}).items():
            if price_feed.runnable(said.get("kind")):
                out.add(catalog.by_api_name(secret.provider, name))
    return out


#: How long a model picker waits on the feed before it describes the key's
#: list from the snapshot instead. The daily pass waits the fetch's full minute.
PICKER_FEED_TIMEOUT = 15


def feed_for_picker(session: Session) -> tuple[dict, str, bool]:
    """(feed, its source label, live) for describing and pricing a key's list
    as it is read: the live feed when upkeep is on and it answers, else the
    shipped snapshot. Never raises: a picker with no feed still lists models."""
    if enabled(session):
        url = price_feed.feed_url(session)
        try:
            return price_feed.fetch(url, timeout=PICKER_FEED_TIMEOUT), \
                price_feed.source_label(), True
        except Exception as exc:  # noqa: BLE001 — any failure falls back, by design
            log.info("price feed unreachable for a model list (%s): %s", url, exc)
    feed, label = price_feed.snapshot_feed()
    return feed, label, False


def refresh_prices(session: Session, feed: dict, models: list[str], *, source: str,
                   add_only: bool = False, trigger: str = "daily") -> tuple[list[dict], list[dict]]:
    """Price `models` from `feed` by upkeep's rule: never an administrator's
    own price, never the catalog's checked one (the preview already holds both
    back); a feed row moves only a price that came from a feed, and with
    `add_only` none at all. Each change audited with what it was. Returns
    (the preview's rows, the changes written)."""
    from yaaf.server.api.admin import _audit

    rows = price_feed.preview(session, feed, models, source=source)
    movable = [r for r in rows if r["action"] == "add" or (
        not add_only and r["action"] == "update" and (r["current"] or {}).get("source"))]
    written = price_feed.apply(session, movable)
    changes = [{"model": r["model"], "from": r["current"], "to": r["proposed"]}
               for r in movable if r["model"] in written]
    if changes:
        _audit(session, None, "model_price.refresh", "model", "feed",
               {"source": source, "trigger": trigger, "changes": changes})
    return rows, changes


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


def not_served(session: Session, models: list[str],
               keys: dict[str, str] | None = None) -> dict[str, dict]:
    """Of `models`, the ones whose provider's fresh model list leaves them
    out: {model: {"at": the list's date, "provider", "answered": when a check
    on the same key last had it answer since that list, or None}}. `keys` names
    the credential that pays each provider ({provider: secret id}); its own
    list is read where there is one — two keys of one provider can be given different models — else the
    provider's newest. A provider with no fresh list says nothing — no list is
    not evidence of absence."""
    lists = served(session)
    for provider_name, secret_id in (keys or {}).items():
        own = discovered(session, secret_id)
        if own and _fresh(own):
            lists[provider_name] = {**own, "credential_id": secret_id}
    out = {}
    for model in models:
        found = lists.get(catalog.provider_of(model))
        if found is not None and catalog.api_name(model) not in set(found["models"]):
            out[model] = {"at": found["at"], "provider": catalog.provider_of(model),
                          "answered": answered_since(session, found.get("credential_id"),
                                                     model, found["at"])}
    return out


def answered_since(session: Session, secret_id: str | None, model: str,
                   since: str) -> str | None:
    """When a provider check on this credential last had `model` answer, if
    that was at or after `since` (a model list's date); else None.

    A list leaving a model out is a forecast — the provider has stopped
    advertising the name — and a call that comes back after it is the fact
    the forecast is about. Neither cancels the other: a name dropped from the
    list but still answered is, almost always, a retired alias the provider
    has not switched off *yet* (DeepSeek's `deepseek-reasoner` still answered
    months past its announced 2026-07-24 cutoff). So this does not clear the
    warning; it corrects what the warning claims — "still answers, may stop without
    notice" rather than "would be refused". Only an answer from after the
    list counts: one from before it says nothing about a name since dropped."""
    if not secret_id:
        return None
    try:
        after = datetime.fromisoformat(since)
    except (TypeError, ValueError):
        return None
    rows = session.scalars(
        select(ProviderCheck).where(ProviderCheck.secret_id == secret_id,
                                    ProviderCheck.model == model,
                                    ProviderCheck.finished_at.is_not(None),
                                    ProviderCheck.finished_at >= after)
        .order_by(ProviderCheck.finished_at.desc()).limit(20))
    for row in rows:
        # A step graded a pass is a call the model answered. A check whose
        # every call was refused — the model not found — is no answer at all.
        if any(o.get("ok") is True for o in row.outcomes or []):
            return row.finished_at.isoformat()
    return None


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


def run() -> dict:
    """Read the feed, re-read every key's model list (described by the feed),
    then refresh prices — for what seats run on and for every chat model the
    keys list. Returns what it did, which is also kept as `upkeep.last`.

    Its own short transactions, none held across a network call: a slow
    provider must not hold a write lock every other writer waits on. Every
    failure is recorded against what it failed on and the pass goes on — one
    credential's odd answer (an HTML login page, a provider this install no
    longer knows) must not stop the rest, nor leave the pass due again."""
    from yaaf.core.db import db_session
    from yaaf.runtimes.providers import PROVIDERS

    with db_session() as session:
        targets = [(s.id, s.name) for s in session.scalars(_listable())
                   if s.provider in PROVIDERS and PROVIDERS[s.provider].protocol]
        url = price_feed.feed_url(session)
    # The feed first: each key's list is described by it as it is recorded.
    feed: dict | None = None
    prices: dict = {}
    try:
        feed = price_feed.fetch(url)
    except Exception as exc:  # noqa: BLE001
        prices = {"error": f"the price feed could not be read: {exc}"[:300]}
    lists: dict[str, dict] = {}
    for secret_id, name in targets:
        try:
            with db_session() as session:
                secret = session.get(Secret, secret_id)
                if secret is None:
                    continue
                held = _Held(secret.provider, secret.llm_mode, secret.base_url,
                             secret.ciphertext)
            # Minting (Vertex) is a network call too: outside the session.
            names, created = asyncio.run(_list(_endpoint(held)))
            with db_session() as session:
                secret = session.get(Secret, secret_id)
                if secret is not None:
                    record(session, secret, names, feed, created)
            lists[secret_id] = {"name": name, "models": len(names)}
        except Exception as exc:  # noqa: BLE001 — recorded per credential, by design
            lists[secret_id] = {"name": name, "error": f"{type(exc).__name__}: {exc}"[:200]}
    if feed is not None:
        try:
            with db_session() as session:
                rows, changes = refresh_prices(session, feed, price_feed.wanted(session),
                                               source=price_feed.source_label())
            prices = {"changed": [c["model"] for c in changes], "checked": len(rows)}
        except Exception as exc:  # noqa: BLE001
            prices = {"error": f"the prices could not be refreshed: {exc}"[:300]}
    # Priced now: an empty class on a provider that ships no lineup is filled
    # from its key's list (yaaf.core.lineup_pick). Never one somebody named.
    lineups: dict = {}
    try:
        from yaaf.core import lineup_pick

        with db_session() as session:
            for provider_name in sorted(served(session)):
                filled = lineup_pick.fill(session, provider_name)
                if filled:
                    lineups[provider_name] = filled
    except Exception as exc:  # noqa: BLE001
        lineups = {"error": f"the lineups could not be filled: {exc}"[:300]}
    summary = {"at": utcnow().isoformat(), "credentials": lists, "prices": prices,
               "lineups": lineups}
    with db_session() as session:
        platform.set_setting(session, LAST, json.dumps(summary))
    return summary


@dataclass(frozen=True)
class _Held:
    """What reaching a provider needs of a credential row, read inside a
    session and used outside it."""
    provider: str
    llm_mode: str | None
    base_url: str | None
    ciphertext: str


def _endpoint(secret: Secret | _Held):
    from yaaf.core import model_tokens
    from yaaf.core.crypto import decrypt
    from yaaf.runtimes import wire
    from yaaf.runtimes.providers import provider, spec

    seller = provider(secret.provider)
    key = decrypt(secret.ciphertext)
    if seller.mints_tokens:
        key, _ = model_tokens.mint(seller.name, key)
    return wire.Endpoint.from_spec(spec(seller, secret.llm_mode, secret.base_url), key)


async def _list(endpoint) -> tuple[list[str], dict[str, int]]:
    from yaaf.runtimes import wire

    created: dict[str, int] = {}
    names = await wire.list_models(endpoint, dates=created)
    return names, created


_RUNNING = threading.Lock()


def tick() -> None:
    """The janitor's call. When the pass is due it is marked started — so a
    pass that dies is not due again for a day, rather than every tick — and
    run on a thread of its own: the janitor reaps runs and enforces budgets,
    and must not wait on every provider's model list."""
    from yaaf.core.db import db_session
    from yaaf.server import acting

    try:
        with db_session() as session:
            if not due(session) or _RUNNING.locked():
                return
            platform.set_setting(session, LAST, json.dumps(
                {**last(session), "at": utcnow().isoformat(), "running": True}))
    except Exception:  # noqa: BLE001 — a background pass reports, never raises
        log.exception("model upkeep could not start")
        return

    def work() -> None:
        if not _RUNNING.acquire(blocking=False):
            return
        try:
            # Its price changes are audited, and an audit row names who did it.
            with acting.acting(acting.Actor("system", "model-upkeep")):
                run()
        except Exception:  # noqa: BLE001
            log.exception("model upkeep failed")
        finally:
            _RUNNING.release()

    threading.Thread(target=work, name="model-upkeep", daemon=True).start()
