"""Which models a class names on this install, and which provider a project's
classes resolve on (docs/PROVIDERS.md §1).

The rules are `yaaf.runtimes.seats`, which knows no database. This is the half
that does: the install's lineups over the shipped ones, its default provider,
its per-provider concurrency ceilings, and a project's primary provider — the
one its billing credential pays. Every door, the lease, the chat lane and the
reports build their `ModelContext` here, so "what does @balanced mean on this
project" has one answer.
"""

from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from yaaf.core import platform
from yaaf.core.models import Project, Secret
from yaaf.runtimes import catalog
from yaaf.runtimes.providers import DEFAULT_PROVIDER, PROVIDERS
from yaaf.runtimes.seats import ModelContext

#: Setting keys (yaaf.core.platform.get_setting).
LINEUPS = "models.lineups"
DEFAULT_PROVIDER_SETTING = "models.default_provider"
CEILINGS = "models.provider_ceilings"


def _json(session: Session, key: str) -> dict:
    raw = platform.get_setting(session, key)
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def lineups(session: Session) -> dict[str, dict[str, str]]:
    """The install's own lineup entries, {provider: {class: model}} — only
    what it changed; the shipped lineups sit beneath (catalog.lineup_of)."""
    stored = _json(session, LINEUPS)
    return {p: {c: m for c, m in (row or {}).items() if c in catalog.CLASSES and m}
            for p, row in stored.items() if p in PROVIDERS and isinstance(row, dict)}


def lineup_table(session: Session) -> list[dict]:
    """Every provider's lineup as a screen shows it: per class, the model, and
    whether it is the shipped one or the install's."""
    own = lineups(session)
    out = []
    for seller in PROVIDERS.values():
        shipped = dict(seller.lineup or {})
        mine = own.get(seller.name, {})
        out.append({"provider": seller.name, "label": seller.label, "classes": {
            cls: {"model": mine.get(cls) or shipped.get(cls),
                  "shipped": shipped.get(cls),
                  "source": "install" if cls in mine else ("shipped" if cls in shipped
                                                           else None)}
            for cls in reversed(catalog.CLASSES)}})
    return out


def set_lineup(session: Session, provider: str, cls: str, model: str | None) -> None:
    """Name the model a class is on a provider — or, with None, go back to the
    shipped one. Refused for a class that does not exist, and for a model that
    is not that provider's: a lineup that names another vendor's model would
    move a seat across vendors behind a label that says it stayed."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=422, detail=f"unknown provider '{provider}'")
    if cls not in catalog.CLASSES:
        raise HTTPException(status_code=422, detail=(
            f"unknown class '{cls}' — one of {', '.join(reversed(catalog.CLASSES))}"))
    stored = _json(session, LINEUPS)
    row = dict(stored.get(provider) or {})
    if model:
        model = model.strip()
        if catalog.is_class(model):
            raise HTTPException(status_code=422, detail="a lineup names a model, not a class")
        if catalog.provider_of(model) != provider:
            raise HTTPException(status_code=422, detail=(
                f"{model} is not {PROVIDERS[provider].label}'s — a lineup names one of its "
                f"own provider's models ({provider}/<model> for one the catalog does not "
                f"list)"))
        row[cls] = model
    else:
        row.pop(cls, None)
    stored[provider] = row
    platform.set_setting(session, LINEUPS, json.dumps(stored, sort_keys=True))


def default_provider(session: Session) -> str:
    """Where a class resolves on a project that pays nobody yet, and what the
    Founder's own class resolves on before it has a credential."""
    name = platform.get_setting(session, DEFAULT_PROVIDER_SETTING)
    return name if name in PROVIDERS else DEFAULT_PROVIDER


def set_default_provider(session: Session, name: str) -> None:
    if name not in PROVIDERS:
        raise HTTPException(status_code=422, detail=(
            f"unknown provider '{name}' — one of: {', '.join(PROVIDERS)}"))
    platform.set_setting(session, DEFAULT_PROVIDER_SETTING, name)


def ceilings(session: Session) -> dict[str, int]:
    """At most how many runs on each provider run at once, where the install
    set a number — beside `max_concurrent_runs`, which is everybody's."""
    return {p: int(n) for p, n in _json(session, CEILINGS).items()
            if p in PROVIDERS and isinstance(n, int) and n > 0}


def set_ceiling(session: Session, provider: str, limit: int | None) -> None:
    if provider not in PROVIDERS:
        raise HTTPException(status_code=422, detail=f"unknown provider '{provider}'")
    stored = _json(session, CEILINGS)
    if limit:
        stored[provider] = int(limit)
    else:
        stored.pop(provider, None)
    platform.set_setting(session, CEILINGS, json.dumps(stored, sort_keys=True))


def provider_of_secret(secret: Secret | None) -> str | None:
    return (secret.provider or DEFAULT_PROVIDER) if secret is not None else None


def primary_provider(session: Session, project: Project | None,
                     billing_secret_id: str | None = None) -> str:
    """The provider a project's bare classes resolve on: the one its billing
    credential pays, else the install's default. `billing_secret_id` reads a
    draft that is not a row yet."""
    secret_id = billing_secret_id or (project.billing_secret_id if project is not None
                                      else None)
    if secret_id:
        secret = session.get(Secret, secret_id)
        if secret is not None:
            return secret.provider or DEFAULT_PROVIDER
    return default_provider(session)


def failover(session: Session, project: Project | None) -> Secret | None:
    """The fallback credential a project's class seats run on right now: its
    billing credential's breaker is open and the fallback's is not
    (yaaf.core.breakers). None while the billing credential is healthy."""
    from yaaf.core import breakers

    if project is None or not project.fallback_secret_id or not project.billing_secret_id:
        return None
    if not breakers.is_open(session, project.billing_secret_id):
        return None
    fallback = session.get(Secret, project.fallback_secret_id)
    if fallback is None or fallback.hidden_at is not None \
            or breakers.is_open(session, fallback.id):
        return None
    return fallback


def context(session: Session, project: Project | None = None, *,
            provider: str | None = None, modes: dict[str, str] | None = None) -> ModelContext:
    """A project's ModelContext — or, with no project, the install's (a role
    door, the Founder) on `provider` when one is named."""
    from yaaf.core import web_search

    if modes is None and project is not None:
        from yaaf.core import project_credentials

        modes = {}
        for name, secret in project_credentials.held(session, project).items():
            modes[name] = (project.billing_mode if secret.id == project.billing_secret_id
                           else secret.llm_mode or "api_key")
    if provider is None:
        moved = failover(session, project)
        provider = (moved.provider or DEFAULT_PROVIDER) if moved is not None \
            else primary_provider(session, project)
    return ModelContext(provider=provider,
                        lineups=lineups(session), modes=modes or {},
                        platform_search=web_search.configured(session))


def resolve(session: Session, ref: str | None, project: Project | None = None, *,
            provider: str | None = None) -> str | None:
    """The concrete model `ref` names on this project (or on `provider`)."""
    return context(session, project, provider=provider).resolve(ref)
