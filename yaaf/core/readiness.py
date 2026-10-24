"""Is this install ready to run projects on each provider it pays — and if not,
what exactly is missing and what fixes it (docs/PROVIDERS.md §8).

Every way a provider can be half set up was already refused somewhere: a class
with no model (R8), a model with no price (R3), a web role on a provider that
cannot search (R2), a model the key no longer lists, a model that failed its
check. Each refusal is right, and each used to be met one at a time — by
creating a project and reading why it was refused. This reads them all at
once, per provider, with the fix beside each, so the Settings screen, the
connect flow and Home can show a chairman the whole list *before* the first
refusal, and offer the one-click fix where there is one.

It refuses nothing itself: the doors still do. It reports what they would say.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import (
    llm_credentials,
    model_policy,
    model_upkeep,
    platform,
    price_feed,
    prices,
    provider_check_runs,
    web_search,
)
from yaaf.core.models import Role, RoleVersion, Secret
from yaaf.runtimes import catalog
from yaaf.runtimes.providers import DEFAULT_PROVIDER, PROVIDERS, provider


def _web_roles(session: Session) -> tuple[int, int]:
    """(roles granting web, all roles) in the library — how much a provider
    that cannot search would refuse."""
    newest = (select(RoleVersion.role_id, func.max(RoleVersion.version).label("version"))
              .group_by(RoleVersion.role_id).subquery())
    versions = session.scalars(select(RoleVersion).join(
        newest, (RoleVersion.role_id == newest.c.role_id)
        & (RoleVersion.version == newest.c.version))).all()
    web = sum(1 for v in versions if (v.tool_permissions or {}).get("web"))
    return web, len(versions)


def _issue(code: str, severity: str, text: str, fix: dict | None = None) -> dict:
    return {"code": code, "severity": severity, "text": text, "fix": fix}


def provider_readiness(session: Session, name: str, *, credentials: list[Secret],
                       served: dict, checks: dict, web_roles: tuple[int, int]) -> dict:
    seller = provider(name)
    lineup = catalog.lineup_of(name, model_policy.lineups(session))
    own = model_policy.lineups(session).get(name, {})
    listed = served.get(name)
    names = set(listed["models"]) if listed else None
    snapshot = price_feed.snapshot()["models"]
    tiers, issues = [], []
    unpriced, importable, unset, failed = [], [], [], []
    for cls in reversed(catalog.CLASSES):
        model = lineup.get(cls)
        price = prices.price(session, model) if model else None
        covered = bool(model) and prices.covered(session, model)
        check = checks.get(model) if model else None
        tiers.append({
            "class": cls, "label": catalog.CLASS_LABELS[cls], "model": model,
            "source": "install" if cls in own else ("shipped" if model else None),
            "priced": covered,
            "price": {"input": price.input, "output": price.output} if price else None,
            "states_cost": seller.reports_cost,
            "served": (catalog.api_name(model) in names) if model and names is not None
            else None,
            "context_window": prices.context_window(session, model) if model else None,
            "check": ({"status": check["status"], "summary": check["summary"],
                       "at": check["finished_at"]} if check else None)})
        if not model:
            unset.append(cls)
            continue
        if not covered and model not in unpriced:
            unpriced.append(model)
            if price_feed.lookup(snapshot, model) is not None:
                importable.append(model)
        if names is not None and catalog.api_name(model) not in names:
            issues.append(_issue(
                "not_served", "warn",
                f"{model} is not among the models {seller.label} lists for this key — the "
                "key may lack access, or the model was retired.",
                {"action": "set_lineup", "class": cls}))
        if check and check["status"] in ("failed", "error") and model not in failed:
            failed.append(model)
            issues.append(_issue(
                "check_failed", "warn",
                f"{model} failed its last check ({check['summary']}) — seats on it may get "
                "little done.", {"action": "check", "models": [model]}))
    if unset:
        labels = [catalog.CLASS_LABELS[c] for c in unset]
        named = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " or " + labels[-1]
        issues.insert(0, _issue(
            "no_model", "block",
            f"no {named} model is set — seats on "
            f"{'that class' if len(unset) == 1 else 'those classes'} are refused here. Pick "
            f"{'one' if len(unset) == 1 else 'them'} from the models the key can call.",
            {"action": "set_lineup", "class": unset[0], "classes": unset}))
    if unpriced:
        issues.insert(0, _issue(
            "unpriced", "block",
            f"{', '.join(unpriced)} {'has' if len(unpriced) == 1 else 'have'} no price on this "
            "install — a budget cannot stop what it cannot price, so seats on "
            f"{'it' if len(unpriced) == 1 else 'them'} are refused.",
            {"action": "import_prices", "models": importable} if importable
            else {"action": "set_price", "models": unpriced}))
    searches = "own" if seller.search else (
        "platform" if web_search.configured(session) else "none")
    web, total = web_roles
    if searches == "none" and (web or not total):
        # An empty library still gets the warning: the roles it will be seeded
        # with browse (35 of the 38 shipped ones do).
        issues.append(_issue(
            "no_search", "warn",
            f"{seller.label}'s API cannot search the web and this install has no search "
            "backend, so "
            + (f"{web} of the {total} roles in the library — every one that browses — are "
               "refused here." if total else "every role that browses will be refused here."),
            {"action": "web_search"}))
    blocked = any(i["severity"] == "block" for i in issues)
    return {
        "provider": name, "label": seller.label, "local": seller.local,
        "is_default": model_policy.default_provider(session) == name,
        "search": searches, "states_cost": seller.reports_cost,
        "credentials": [{"id": s.id, "name": s.name, "mode": s.llm_mode or "api_key",
                         "kind": seller.kind(s.llm_mode).label} for s in credentials],
        "tiers": tiers,
        # The key's own list as last read: the tier editor offers it without
        # asking the provider again.
        "served": {"at": listed["at"], "count": len(listed["models"]),
                   "models": listed["models"]} if listed else None,
        "ceiling": model_policy.ceilings(session).get(name),
        "issues": issues,
        "ready": not blocked,
    }


def readiness(session: Session) -> dict:
    """Every provider the install holds a credential for, ready or not and why;
    and the install's own first steps, for Home's checklist."""
    held: dict[str, list[Secret]] = {}
    for secret in session.scalars(llm_credentials.offerable().order_by(Secret.name)):
        held.setdefault(secret.provider or DEFAULT_PROVIDER, []).append(secret)
    served = model_upkeep.served(session)
    checks = {row["model"]: row for row in provider_check_runs.scoreboard(session)}
    web_roles = _web_roles(session)
    out = [provider_readiness(session, name, credentials=held[name], served=served,
                              checks=checks, web_roles=web_roles)
           for name in PROVIDERS if name in held]
    default = model_policy.default_provider(session)
    roles = session.scalar(select(func.count()).select_from(Role)) or 0
    return {
        "default_provider": default,
        "providers": out,
        "install": {
            "connected": bool(out),
            "ready": any(p["ready"] for p in out),
            "default_connected": default in held,
            "founder": platform.has_credential(session),
            "web_search": web_search.configured(session),
            "upkeep": model_upkeep.enabled(session),
            "roles": roles,
        },
    }
