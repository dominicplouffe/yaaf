"""Invariant selftest: tokens always, dollars only when dollars move."""

from __future__ import annotations

from sqlalchemy import select

from tests.conftest import live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import LedgerEntry, Run
from yaaf.runner.daemon import finalize
from yaaf.runner.types import RunJob, RunResult


def _finalize_with_cost(project: dict, agent_id: str, run_id: str) -> None:
    job = RunJob(
        run_id=run_id,
        project_id=project["id"],
        agent_id=agent_id,
        kind="agent_run",
        trigger={"type": "test"},
        task_id=None,
        attempt=1,
    )
    finalize(job, RunResult(ok=True, summary="test run", tokens_in=1000, tokens_out=2000,
                            cache_creation_tokens=50_000, cache_read_tokens=250_000,
                            cost_usd=1.23))


def test_subscription_mode_books_tokens_not_dollars(client):
    project = make_project(client, billing_mode="subscription")
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    _finalize_with_cost(project, project["ceo_agent_id"], run_id)

    with db_session() as session:
        run = session.get(Run, run_id)
        assert run.tokens_out == 2000
        assert (run.cache_creation_tokens, run.cache_read_tokens) == (50_000, 250_000)
        assert run.cost_usd is None, "a subscription run must not carry a fictional dollar"
        entry = session.execute(
            select(LedgerEntry).where(LedgerEntry.run_id == run_id)
        ).scalar_one()
        assert entry.tokens_in == 1000 and entry.tokens_out == 2000
        assert (entry.cache_creation_tokens, entry.cache_read_tokens) == (50_000, 250_000)
        assert entry.usd is None
        assert "subscription" in (entry.note or "")


def test_api_key_mode_books_real_dollars(client):
    project = make_project(client, slug="acme2", billing_mode="api_key")
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    _finalize_with_cost(project, project["ceo_agent_id"], run_id)

    with db_session() as session:
        run = session.get(Run, run_id)
        assert run.cost_usd == 1.23
        entry = session.execute(
            select(LedgerEntry).where(LedgerEntry.run_id == run_id)
        ).scalar_one()
        assert entry.usd == 1.23
        assert (entry.cache_creation_tokens, entry.cache_read_tokens) == (50_000, 250_000)
