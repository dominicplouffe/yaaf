"""Running a provider check off the request that asked for it, and reading it
back (yaaf.core.provider_check is what a check *is*; this is where it lives).

A check is tens of real calls per model — a minute or several — so it runs the
way a founding turn does (yaaf.founder.runner): the request writes one
`ProviderCheck` row per model and returns, a daemon thread does the work, and
each probe or task is written to its row the moment it finishes, so a screen
polling the row watches it fill. A failure is recorded on the row, never only
raised: nobody is on the other end of the thread to catch it.

The credential is decrypted inside the thread, per model, and never written
anywhere — the same discipline as the smoke call.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids, model_policy, model_tokens
from yaaf.core import prices as install_prices
from yaaf.core.crypto import decrypt
from yaaf.core.db import db_session
from yaaf.core.models import ProviderCheck, Secret, utcnow
from yaaf.core.provider_check import ESTIMATE_TOKENS, Target, plan, run_step, verdict
from yaaf.runtimes import catalog, wire
from yaaf.runtimes.providers import provider, spec

log = logging.getLogger("yaaf.provider_check")

#: A row still `running` this long after its last write was orphaned by a
#: restart: no one step takes this long, and a screen should not spin on it.
STALE_AFTER = timedelta(minutes=30)


class CheckRefused(ValueError):
    """Why this credential cannot be checked from the control plane."""


def checkable(secret: Secret) -> None:
    seller = provider(secret.provider)
    if seller.protocol is None or (secret.llm_mode or "api_key") != "api_key":
        raise CheckRefused(f"a {seller.kind(secret.llm_mode).label} pays only its own CLI, "
                           "which runs only inside a sandbox — its first run is the check")


def default_models(session: Session, secret: Secret) -> list[str]:
    """What a check runs on when nobody names a model: what each class
    resolves to on this credential's provider — the models its seats would
    actually be hired on — once each."""
    out: list[str] = []
    for cls in catalog.CLASSES:
        model = model_policy.resolve(session, catalog.class_ref(cls), provider=secret.provider)
        if model and model not in out:
            out.append(model)
    return out


def estimate_usd(session: Session, models: list[str], tasks: bool) -> float | None:
    """Roughly what a check will spend, for the screen to say before it runs.
    None when a model has no price to estimate by."""
    total = 0.0
    share = 1.0 if tasks else 0.25
    for model in models:
        price = install_prices.price(session, model)
        if price is None:
            return None
        total += share * (ESTIMATE_TOKENS["input"] * price.input
                          + ESTIMATE_TOKENS["output"] * price.output) / 1_000_000
    return round(total, 4)


def _effort(model: str) -> str | None:
    """The lowest rung of the model's dial — the check proves the dial is
    taken, not that thinking harder costs more — or None where there is none."""
    available = catalog.rungs(model, None)
    if not available or len(available) < 2:
        return None
    return sorted(available, key=catalog.EFFORTS.index)[0]


def _target(session: Session, secret: Secret, model: str) -> Target:
    seller = provider(secret.provider)
    key = decrypt(secret.ciphertext)
    if seller.mints_tokens:
        key, _ = model_tokens.mint(seller.name, key)
    endpoint = wire.Endpoint.from_spec(spec(seller, secret.llm_mode, secret.base_url), key)
    return Target(endpoint=endpoint, model=catalog.api_name(model), effort=_effort(model),
                  search=seller.search and seller.protocol in (
                      "openai_responses", "gemini", "anthropic"),
                  transport=wire.TRANSPORT)


def start(session: Session, secret: Secret, models: list[str], *, tasks: bool,
          started_by: str | None) -> list[ProviderCheck]:
    checkable(secret)
    running = session.scalar(select(ProviderCheck).where(
        ProviderCheck.secret_id == secret.id, ProviderCheck.status == "running",
        ProviderCheck.updated_at > utcnow() - STALE_AFTER))
    if running is not None:
        raise CheckRefused(f"a check of this credential is already running ({running.model})")
    rows = []
    for model in models:
        row = ProviderCheck(id=ids.new_id(ids.PROVIDER_CHECK), secret_id=secret.id,
                            provider=secret.provider, model=model, status="running",
                            summary="queued", outcomes=[], tasks=tasks, started_by=started_by,
                            started_at=utcnow())
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def submit(check_ids: list[str]) -> None:
    """Run the checks, one model after another. Returns immediately."""
    threading.Thread(target=_work, args=(list(check_ids),),
                     name=f"provider-check-{check_ids[0][:12]}", daemon=True).start()


def run_now(check_ids: list[str]) -> None:
    """The same work on the caller's thread — for the CLI, which waits."""
    _work(check_ids)


def _work(check_ids: list[str]) -> None:
    for check_id in check_ids:
        try:
            _check(check_id)
        except Exception as exc:  # noqa: BLE001 — nobody is catching this for us
            log.exception("provider check %s failed", check_id)
            _finish(check_id, "error", f"{type(exc).__name__}: {exc}"[:300])


def _check(check_id: str) -> None:
    with db_session() as session:
        row = session.get(ProviderCheck, check_id)
        secret = session.get(Secret, row.secret_id) if row is not None else None
        if row is None:
            return
        if secret is None:
            _record_error(row, "the credential was deleted before the check ran")
            return
        try:
            target = _target(session, secret, row.model)
        except model_tokens.MintError as exc:
            _record_error(row, str(exc))
            return
        steps, model = plan(row.tasks), row.model
        # Its own start, not the batch's: models run one after another, and a
        # sixth model queued behind five is not stale.
        row.summary, row.started_at = "running", utcnow()
    for name, kind, step in steps:
        outcome = run_step(target, name, kind, step)
        with db_session() as session:
            row = session.get(ProviderCheck, check_id)
            if row is None:
                return
            found = outcome.as_dict()
            if found["cost_usd"] is None and found["calls"]:
                price = install_prices.price(session, model)
                if price is not None:
                    seller = provider(row.provider)
                    found["cost_usd"] = (found["input_tokens"] * price.input
                                         + found["output_tokens"] * price.output
                                         + found["cache_read"] * price.cache_read
                                         ) / 1_000_000 + found["web_searches"] * (
                        seller.search_usd or catalog.WEB_SEARCH_USD)
            row.outcomes = [*row.outcomes, found]
            row.summary = f"running — {name} done"
        # A dead key fails every step the same way; one is the finding.
        if outcome.ok is False and "refused a call (auth)" in outcome.detail:
            _finish(check_id, "error", "the provider refused the key: " + outcome.detail[:240])
            return
    with db_session() as session:
        row = session.get(ProviderCheck, check_id)
        if row is None:
            return
        status, line = verdict(row.outcomes)
        row.status, row.summary = status, line
        costs = [o["cost_usd"] for o in row.outcomes if o.get("cost_usd") is not None]
        row.cost_usd = sum(costs) if costs else None
        row.finished_at = utcnow()


def _record_error(row: ProviderCheck, detail: str) -> None:
    row.status, row.summary, row.finished_at = "error", detail[:300], utcnow()


def _finish(check_id: str, status: str, summary: str) -> None:
    try:
        with db_session() as session:
            row = session.get(ProviderCheck, check_id)
            if row is not None:
                costs = [o["cost_usd"] for o in row.outcomes if o.get("cost_usd") is not None]
                row.cost_usd = sum(costs) if costs else None
                row.status, row.summary, row.finished_at = status, summary[:300], utcnow()
    except Exception:  # pragma: no cover - the log is the last resort
        log.exception("could not record the end of provider check %s", check_id)


# ── reading it back ────────────────────────────────────────────────────────────


def render(row: ProviderCheck) -> dict:
    status = row.status
    # Stale by its last write, not its start: a long check writes every step.
    if status == "running" and row.updated_at is not None and \
            utcnow() - row.updated_at > STALE_AFTER:
        status = "interrupted"
    graded = [o for o in row.outcomes if o.get("ok") is not None]
    return {"id": row.id, "credential_id": row.secret_id, "provider": row.provider,
            "model": row.model, "status": status, "summary": row.summary,
            "tasks": row.tasks, "outcomes": row.outcomes,
            "passed": sum(1 for o in graded if o["ok"]), "graded": len(graded),
            "steps": len(plan(row.tasks)), "cost_usd": row.cost_usd,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None}


def latest(session: Session, secret_id: str) -> list[dict]:
    """The newest check of each model on this credential, newest first."""
    seen: dict[str, ProviderCheck] = {}
    for row in session.scalars(select(ProviderCheck).where(ProviderCheck.secret_id == secret_id)
                               .order_by(ProviderCheck.started_at.desc()).limit(200)):
        seen.setdefault(row.model, row)
    return [render(row) for row in seen.values()]


def scoreboard(session: Session) -> list[dict]:
    """The newest finished check of every model this install has checked, on
    any credential — the evidence a lineup is chosen from."""
    seen: dict[str, ProviderCheck] = {}
    for row in session.scalars(select(ProviderCheck)
                               .where(ProviderCheck.status != "running")
                               .order_by(ProviderCheck.started_at.desc()).limit(500)):
        seen.setdefault(row.model, row)
    out = []
    for row in seen.values():
        found = render(row)
        tasks = [o for o in row.outcomes if o.get("kind") == "task" and o.get("ok") is not None]
        found["task_calls"] = (round(sum(o["calls"] for o in tasks) / len(tasks), 1)
                               if tasks else None)
        found["task_cost_usd"] = (round(sum(o.get("cost_usd") or 0 for o in tasks) / len(tasks),
                                        6) if tasks else None)
        found["task_seconds"] = (round(sum(o["latency_ms"] for o in tasks) / len(tasks) / 1000,
                                       1) if tasks else None)
        out.append(found)
    return sorted(out, key=lambda r: (r["provider"], r["model"]))
