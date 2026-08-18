"""A reaped run must book what it actually spent.

The sandbox checkpoints usage to result-<run>.json after every message so that
a hard kill is metered rather than free. Only `finalize` ever read it, and a
reaped run never reaches finalize — the lease expiring means the RUNNER died,
not that the model did no work.

Fleet-wide that hid 244 runs and ~86 wall-hours behind $0.00 and zero tokens,
which made "non-DONE runs are only 7% of spend" a metering artefact rather than
a fact. Every budget decision taken on that number was wrong by an unknown
margin, so these pin the recovery.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from tests.conftest import live_token, make_project
from yaaf.core.config import settings
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import LedgerEntry, Project, Run, RunStatus, utcnow
from yaaf.server import janitor

CHECKPOINT = {
    "ok": False,
    "partial": True,
    "summary": "killed mid-run",
    "tokens_in": 1200,
    "tokens_out": 340,
    "cache_creation_tokens": 8000,
    "cache_read_tokens": 512000,
    "cost_usd": 0.42,
}


def _checkpoint(slug: str, run_id: str, data: dict | None = None) -> Path:
    staging = Path(settings().workspaces_root) / slug / ".yaaf"
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / f"result-{run_id}.json"
    path.write_text(json.dumps(CHECKPOINT if data is None else data))
    return path


def _go_stale(run_id: str) -> None:
    """Age the lease past ttl so the next janitor pass reaps it."""
    with db_session() as session:
        run = session.get(Run, run_id)
        run.heartbeat_at = utcnow() - timedelta(seconds=settings().lease_ttl_s * 3)


def _reap(project: dict) -> int:
    with db_session() as session:
        row = session.get(Project, project["id"])
        return janitor.reap_stale_runs(session, row, effective(session, row.id))


def _ledger(project: dict) -> list[LedgerEntry]:
    with db_session() as session:
        return list(
            session.query(LedgerEntry).filter(LedgerEntry.project_id == project["id"]).all()
        )


def test_a_reaped_run_books_the_usage_it_checkpointed(client):
    slug = "reaped"
    project = make_project(client, slug=slug)
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    _checkpoint(slug, run_id)
    _go_stale(run_id)

    assert _reap(project) == 1

    with db_session() as session:
        run = session.get(Run, run_id)
        assert run.status == RunStatus.REAPED
        assert run.tokens_in == 1200
        assert run.cache_read_tokens == 512000, "cache traffic is most of the bill"
        assert run.cost_usd == 0.42

    entries = _ledger(project)
    assert len(entries) == 1
    assert entries[0].run_id == run_id
    assert entries[0].usd == 0.42
    assert "reaped" in entries[0].note


def test_a_reaped_run_with_no_checkpoint_books_nothing(client):
    """It died before its first message. Inventing a number would be worse than
    the zero — the point is to stop guessing, in both directions."""
    slug = "reaped-early"
    project = make_project(client, slug=slug)
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    _go_stale(run_id)

    assert _reap(project) == 1
    assert _ledger(project) == []


def test_an_empty_checkpoint_books_nothing(client):
    """A checkpoint written before any usage accrued is not a $0.00 run worth a
    ledger row — it is no row."""
    slug = "reaped-zero"
    project = make_project(client, slug=slug)
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    _checkpoint(slug, run_id, {"ok": False, "partial": True, "tokens_in": 0,
                                          "tokens_out": 0, "cache_creation_tokens": 0,
                                          "cache_read_tokens": 0, "cost_usd": None})
    _go_stale(run_id)

    assert _reap(project) == 1
    assert _ledger(project) == []


def test_subscription_projects_recover_tokens_but_not_dollars(client):
    """Same rule the runner applies at finalize: tokens always, dollars only
    when dollars move. A token-budgeted project must not have a USD figure
    invented for it by the recovery path."""
    slug = "reaped-sub"
    project = make_project(client, slug=slug)
    with db_session() as session:
        session.get(Project, project["id"]).billing_mode = "subscription"
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    _checkpoint(slug, run_id)
    _go_stale(run_id)

    assert _reap(project) == 1
    with db_session() as session:
        assert session.get(Run, run_id).cost_usd is None
    assert _ledger(project)[0].usd is None
    assert _ledger(project)[0].cache_read_tokens == 512000


def test_a_corrupt_checkpoint_does_not_stop_the_reap(client):
    """The janitor is the only thing that recovers a dead lease. It may not
    become dependent on a file the sandbox may have half-written."""
    slug = "reaped-corrupt"
    project = make_project(client, slug=slug)
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    staging = Path(settings().workspaces_root) / slug / ".yaaf"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / f"result-{run_id}.json").write_text("{not json at all")
    _go_stale(run_id)

    assert _reap(project) == 1, "the claim must still be released"
    with db_session() as session:
        assert session.get(Run, run_id).status == RunStatus.REAPED
    assert _ledger(project) == []
