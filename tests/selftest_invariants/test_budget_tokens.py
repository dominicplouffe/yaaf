"""Invariant selftest: budget enforcement counts the full prompt, cache included.

The API's input_tokens is only the uncached remainder — a subscription cap that
ignored cache traffic would be ~100x too permissive and never trip.
"""

from __future__ import annotations

from sqlalchemy import select

from tests.conftest import live_token, make_project
from yaaf.core import ids
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import LedgerEntry, Project, ProjectStatus, Run, RunQueue
from yaaf.server.janitor import enforce_budget, month_to_date


def _book(project_id: str, *, tokens_in: int = 0, tokens_out: int = 0,
          cache_creation: int = 0, cache_read: int = 0, usd: float | None = None) -> None:
    with db_session() as session:
        session.add(LedgerEntry(
            id=ids.new_id(ids.LEDGER), project_id=project_id, kind="model_usage",
            tokens_in=tokens_in, tokens_out=tokens_out,
            cache_creation_tokens=cache_creation, cache_read_tokens=cache_read, usd=usd,
        ))


def test_month_to_date_counts_cache_tokens(client):
    project = make_project(client, billing_mode="subscription")
    _book(project["id"], tokens_in=100, tokens_out=200,
          cache_creation=30_000, cache_read=400_000)
    with db_session() as session:
        _, tokens = month_to_date(session, project["id"])
    assert tokens == 430_300


def test_subscription_cap_trips_on_cache_dominated_spend(client):
    project = make_project(client, billing_mode="subscription",
                           constitution={"monthly_token_cap": 100_000})
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=project["ceo_agent_id"], trigger={"type": "test"}))
    # Recorded tokens_in alone is nowhere near the cap — the cache traffic is
    # what actually blows it.
    _book(project["id"], tokens_in=500, tokens_out=2_000,
          cache_creation=20_000, cache_read=90_000)
    with db_session() as session:
        p = session.get(Project, project["id"])
        enforce_budget(session, p, effective(session, p.id))
    with db_session() as session:
        p = session.get(Project, project["id"])
        assert p.status == ProjectStatus.PAUSED
        assert "budget exhausted" in (p.status_reason or "")
        assert session.get(Run, run_id).token_revoked is True
        queued = session.execute(
            select(RunQueue).where(RunQueue.project_id == p.id)
        ).scalars().all()
        assert queued == []


def test_api_key_cap_is_dollars_not_tokens(client):
    project = make_project(client, slug="acme2", billing_mode="api_key",
                           constitution={"monthly_token_cap": 100_000})
    _book(project["id"], tokens_in=500, tokens_out=2_000,
          cache_creation=20_000, cache_read=90_000, usd=1.0)
    with db_session() as session:
        p = session.get(Project, project["id"])
        enforce_budget(session, p, effective(session, p.id))
        assert p.status != ProjectStatus.PAUSED
