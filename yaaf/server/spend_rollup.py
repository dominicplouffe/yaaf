"""Spend across the install: what each provider, model and project cost, over time.

The per-project rollup (`observe.spend`) answers "where did this project's
budget go". The person who runs the box asks a different question — what is
each provider costing me, which model, which project, and is it going up — and
until this module nothing could answer it. The one cross-project number was
`yaaf spend-report --all-projects`: it opens the database directly, so it only
works on the server, prints one project after another with no way to add them
up, and has no time axis at all.

It follows the per-project rollup's rules, because two screens that disagree
about the same money teach the reader to believe neither:

* **Amounts from the ledger, attribution from the runs.** The ledger is where
  spend is counted; a run that no longer exists still spent its money, so its
  rows land under `unattributed` rather than vanishing.
* **Revenue never counts.** It is money coming in.
* **Reach is `authority.reachable`.** Every cross-project rollup goes through
  it, so an observer on two projects sees two projects, and the instance admin
  sees the install.

And it adds one rule the per-project rollup does not need: **dollars only where
a provider billed dollars.** A ledger row's `usd` is what the call cost its
project's *budget*. On a dollar budget, a subscription seat is counted there at
its catalog price (`budget.counted`) — right for the wall, wrong for this
question, because no provider sent a bill for it. So a row is billed when the
credential that paid its run bills dollars (`Run.paid_in`, pinned at lease),
and a subscription's usage is shown as what it was: tokens on a flat plan.
Nothing here turns those tokens into a list price.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import LedgerEntry, LedgerKind, Project, Run
from yaaf.runtimes.providers import DEFAULT_PROVIDER, PROVIDERS

EVERY = ("day", "week", "month")
GROUP_BY = ("provider", "model", "project")
#: Which ledger rows count. `model_usage` is what keys and plans were used for,
#: the default because the question is about providers; `all` adds what runs
#: bought out in the world and what infrastructure cost — everything the budget
#: meter counts.
KINDS = ("model_usage", "all", LedgerKind.EXTERNAL_SPEND.value, LedgerKind.INFRA.value)
PAID_IN = ("usd", "tokens")
#: How many buckets a default window holds: a month of days, a quarter of
#: weeks, a year of months.
DEFAULT_SPAN = {"day": 30, "week": 13, "month": 12}
#: A ceiling on the series, not on the money: past it the chart is a smear and
#: the payload is a download. 400 days, 400 weeks or 400 months.
MAX_BUCKETS = 400
UNATTRIBUTED = "unattributed"

NOTE_DOLLARS = ("Dollars are what API keys billed. A subscription's usage is shown in "
                "tokens: its bill is the plan's flat fee, and yaaf never prices those "
                "tokens at a list price.")
NOTE_TOKENS = ("Tokens include cache reads and writes. Runs recorded before the metering "
               "fix are known short on input, because cache tokens were dropped.")


# ── attribution, shared with the per-project rollup ───────────────────────────


def provider_key(provider: str | None) -> tuple[str, str]:
    """(key, label) for the provider a run pinned. A run from before providers
    were recorded was Anthropic's — the receipt and `observe.spend` say so too."""
    key = provider or DEFAULT_PROVIDER
    seller = PROVIDERS.get(key)
    return key, seller.label if seller else key


def model_key(model: str | None) -> str:
    return model or "unknown"


def billed(kind: str, usd: float | None, paid_in: str | None) -> tuple[float, bool]:
    """(dollars a provider billed, was this on a flat plan?) for one ledger row.

    A purchase or an infrastructure line is money whatever paid the model, so
    only model usage asks the run. A run that pinned how its credential bills is
    believed — every leased run and every boardroom session pins it
    (`runner.daemon._pin_seat`). One from before that booked dollars exactly
    when its project billed in them: pinning and the catalog count arrived in
    the same change, so no unpinned row carries a catalog figure, and a dollar
    figure on it is a bill and its absence is a plan.
    """
    if kind != LedgerKind.MODEL_USAGE:
        return usd or 0.0, False
    if paid_in == "tokens":
        return 0.0, True
    if paid_in == "usd":
        return usd or 0.0, False
    return (usd, False) if usd is not None else (0.0, True)


# ── the window ─────────────────────────────────────────────────────────────────


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        raise HTTPException(status_code=422,
                            detail=f"unknown timezone {name!r}; use an IANA name such as "
                                   "America/Toronto, or UTC") from None


def _day(raw: str | None, what: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise HTTPException(status_code=422,
                            detail=f"{what} must be a date, YYYY-MM-DD; got {raw!r}") from None


def bucket_of(day: date, every: str) -> date:
    """The first day of the bucket `day` falls in. Weeks start on Monday, the
    ISO week, so a week means the same seven days to every reader."""
    if every == "week":
        return day - timedelta(days=day.weekday())
    if every == "month":
        return day.replace(day=1)
    return day


def _step(start: date, every: str, n: int = 1) -> date:
    if every == "day":
        return start + timedelta(days=n)
    if every == "week":
        return start + timedelta(weeks=n)
    months = start.year * 12 + start.month - 1 + n
    return date(months // 12, months % 12 + 1, 1)


def window(every: str, since: str | None, until: str | None, tz: ZoneInfo,
           now: datetime | None = None) -> tuple[date, date, list[date]]:
    """(first day, last day, bucket starts), all on the reader's calendar.

    The first day is snapped back to the start of its bucket. A weekly chart
    whose first bar is three days long reads as a quiet week, and nothing on
    the chart would say otherwise; a whole first bucket is the honest one.
    """
    today = (now or datetime.now(UTC)).astimezone(tz).date()
    last = _day(until, "until") or today
    first = _day(since, "since")
    if first is None:
        first = _step(bucket_of(last, every), every, -(DEFAULT_SPAN[every] - 1))
    first = bucket_of(first, every)
    if first > last:
        raise HTTPException(status_code=422, detail="since must be on or before until")
    buckets = [first]
    while (following := _step(buckets[-1], every)) <= last:
        buckets.append(following)
        if len(buckets) > MAX_BUCKETS:
            raise HTTPException(
                status_code=422,
                detail=f"that is more than {MAX_BUCKETS} {every}s; pick a shorter window "
                       "or a coarser step (week, month)")
    return first, last, buckets


def _utc_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(UTC)


# ── the rollup ─────────────────────────────────────────────────────────────────


def _measures() -> dict:
    return {"tokens_in": 0, "tokens_out": 0, "cache_creation": 0, "cache_read": 0,
            "tokens": 0, "plan_tokens": 0, "usd": 0.0}


def _add(into: dict, line: dict) -> None:
    for name in ("tokens_in", "tokens_out", "cache_creation", "cache_read", "tokens",
                 "plan_tokens", "usd"):
        into[name] += line[name]


def _finish(bucket: dict, runs: set[str] | None = None) -> dict:
    out = {**bucket, "usd": round(bucket["usd"], 6)}
    if runs is not None:
        out["runs"] = len(runs)
    return out


def _resolve_projects(reachable: list[Project], refs: list[str] | None) -> list[Project]:
    """The projects asked for, by id or slug — each one the reader may open. A
    project they may not open is refused in the words a missing one gets, so
    the filter is not an oracle for what other people run."""
    if not refs:
        return reachable
    by_ref = {p.id: p for p in reachable} | {p.slug: p for p in reachable}
    chosen: dict[str, Project] = {}
    for ref in refs:
        found = by_ref.get(ref)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown project")
        chosen[found.id] = found
    return [p for p in reachable if p.id in chosen]


def rollup(session: Session, reachable: list[Project], *, every: str = "day",
           group_by: str = "provider", since: str | None = None, until: str | None = None,
           tz: str | None = None, projects: list[str] | None = None,
           providers: list[str] | None = None, models: list[str] | None = None,
           paid_in: str | None = None, kind: str = "model_usage",
           now: datetime | None = None) -> dict:
    if every not in EVERY:
        raise HTTPException(status_code=400, detail=f"every must be {'|'.join(EVERY)}")
    if group_by not in GROUP_BY:
        raise HTTPException(status_code=400, detail=f"group_by must be {'|'.join(GROUP_BY)}")
    if kind not in KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be {'|'.join(KINDS)}")
    if paid_in is not None and paid_in not in PAID_IN:
        raise HTTPException(status_code=400, detail=f"paid_in must be {'|'.join(PAID_IN)}")
    zone = _zone(tz)
    first, last, buckets = window(every, since, until, zone, now)
    scope = _resolve_projects(reachable, projects)
    names = {p.id: p for p in scope}

    lines = []
    if scope:
        query = (
            select(LedgerEntry.project_id, LedgerEntry.at, LedgerEntry.kind,
                   LedgerEntry.tokens_in, LedgerEntry.tokens_out,
                   LedgerEntry.cache_creation_tokens, LedgerEntry.cache_read_tokens,
                   LedgerEntry.usd, LedgerEntry.run_id, Run.id.label("run_found"),
                   Run.provider, Run.model, Run.paid_in)
            .select_from(LedgerEntry)
            .outerjoin(Run, Run.id == LedgerEntry.run_id)
            .where(LedgerEntry.project_id.in_(list(names)),
                   LedgerEntry.at >= _utc_midnight(first, zone),
                   LedgerEntry.at < _utc_midnight(last + timedelta(days=1), zone),
                   LedgerEntry.kind != LedgerKind.REVENUE)
        )
        if kind != "all":
            query = query.where(LedgerEntry.kind == kind)
        lines = session.execute(query).all()

    seen_providers: dict[str, str] = {}
    seen_models: set[str] = set()
    spent_on: set[str] = set()
    totals, total_runs = _measures(), set()
    groups: dict[str, dict] = {}
    group_runs: dict[str, set[str]] = defaultdict(set)
    detail: dict[tuple, dict] = {}
    detail_runs: dict[tuple, set[str]] = defaultdict(set)
    series: dict[date, dict] = {start: {**_measures(), "by": {}} for start in buckets}

    for line in lines:
        if line.run_found is None:
            provider, provider_label, model = UNATTRIBUTED, UNATTRIBUTED, UNATTRIBUTED
        else:
            provider, provider_label = provider_key(line.provider)
            model = model_key(line.model)
        # The choices offered are what this window holds across the projects
        # chosen, before the provider, model and billing filters narrow it — a
        # filter whose own options vanish once it is set can never be widened.
        seen_providers[provider] = provider_label
        seen_models.add(model)
        if providers and provider not in providers:
            continue
        if models and model not in models:
            continue
        usd, on_plan = billed(line.kind, line.usd, line.paid_in)
        if paid_in == "usd" and on_plan or paid_in == "tokens" and not on_plan:
            continue
        tokens = ((line.tokens_in or 0) + (line.tokens_out or 0)
                  + (line.cache_creation_tokens or 0) + (line.cache_read_tokens or 0))
        measure = {"tokens_in": line.tokens_in or 0, "tokens_out": line.tokens_out or 0,
                   "cache_creation": line.cache_creation_tokens or 0,
                   "cache_read": line.cache_read_tokens or 0, "tokens": tokens,
                   "plan_tokens": tokens if on_plan else 0, "usd": usd}
        project = names[line.project_id]
        spent_on.add(project.id)
        key, label = {"provider": (provider, provider_label), "model": (model, model),
                      "project": (project.id, project.name)}[group_by]

        _add(totals, measure)
        group = groups.setdefault(key, {"key": key, "label": label, **_measures()})
        if group_by == "project":
            group["slug"] = project.slug
        _add(group, measure)
        row_key = (project.id, provider, model)
        row = detail.setdefault(row_key, {
            "project_id": project.id, "project": project.name, "slug": project.slug,
            "provider": provider, "provider_label": provider_label, "model": model,
            **_measures()})
        _add(row, measure)
        if line.run_id:
            total_runs.add(line.run_id)
            group_runs[key].add(line.run_id)
            detail_runs[row_key].add(line.run_id)

        cell = series[bucket_of(line.at.astimezone(zone).date(), every)]
        _add(cell, measure)
        by = cell["by"].setdefault(key, {"usd": 0.0, "tokens": 0, "plan_tokens": 0})
        by["usd"] += usd
        by["tokens"] += tokens
        by["plan_tokens"] += measure["plan_tokens"]

    def ranked(rows):
        return sorted(rows, key=lambda r: (-r["usd"], -r["tokens"], r.get("label", "")))

    return {
        "every": every, "group_by": group_by, "kind": kind, "paid_in": paid_in,
        "tz": zone.key, "since": first.isoformat(), "until": last.isoformat(),
        "filters": {"project": [p.id for p in scope] if projects else [],
                    "provider": providers or [], "model": models or []},
        "totals": _finish(totals, total_runs),
        "groups": ranked(_finish(g, group_runs[g["key"]]) for g in groups.values()),
        "rows": ranked(_finish(r, detail_runs[k]) for k, r in detail.items()),
        "series": [
            {"start": start.isoformat(),
             "end": min(_step(start, every) - timedelta(days=1), last).isoformat(),
             **_finish({k: v for k, v in cell.items() if k != "by"}),
             "by": {k: {**v, "usd": round(v["usd"], 6)} for k, v in cell["by"].items()}}
            for start, cell in series.items()
        ],
        "options": {
            "projects": [{"id": p.id, "slug": p.slug, "name": p.name,
                          "spent": p.id in spent_on} for p in reachable],
            "providers": sorted(({"key": k, "label": v} for k, v in seen_providers.items()),
                                key=lambda o: o["label"].lower()),
            "models": sorted(seen_models),
        },
        "notes": {"dollars": NOTE_DOLLARS, "tokens": NOTE_TOKENS},
    }
