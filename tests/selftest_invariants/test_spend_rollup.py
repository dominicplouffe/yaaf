"""The spend rollup sums the ledger, groups it by model/team/agent, and adds
up to the budget meter printed above it."""

from __future__ import annotations

from tests.conftest import ADMIN, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    LedgerEntry,
    LedgerKind,
    Run,
    RunStatus,
    utcnow,
)


def _add_run(project_id: str, agent_id: str | None, model: str | None = None,
             cost: float | None = None, tokens_out: int = 0, cache_read: int = 0,
             entries: int = 1) -> str:
    """A run and the ledger rows it booked — the shape the runner writes at
    finalize. `entries` splits the same spend over several rows the way a chat
    session books one per turn."""
    run_id = ids.new_id(ids.RUN)
    with db_session() as session:
        session.add(Run(id=run_id, project_id=project_id, agent_id=agent_id,
                        trigger={"type": "test"}, status=RunStatus.DONE, model=model,
                        cost_usd=cost, tokens_out=tokens_out, cache_read_tokens=cache_read,
                        started_at=utcnow(), finished_at=utcnow()))
        for i in range(entries):
            session.add(LedgerEntry(
                id=ids.new_id(ids.LEDGER), project_id=project_id,
                kind=LedgerKind.MODEL_USAGE,
                tokens_out=tokens_out // entries, cache_read_tokens=cache_read // entries,
                usd=None if cost is None else cost / entries,
                run_id=run_id, note=f"test turn {i}",
            ))
    return run_id


def test_spend_rollup_by_model_and_team(client):
    project = make_project(client)
    ceo = project["ceo_agent_id"]
    _add_run(project["id"], ceo, model="claude-opus-5", cost=2.0, tokens_out=10)
    _add_run(project["id"], ceo, model="claude-opus-5", cost=1.0, cache_read=5_000)
    _add_run(project["id"], ceo, model=None, cost=0.5)  # pre-migration history

    by_model = client.get(f"/observe/projects/{project['id']}/spend?group_by=model",
                          headers=ADMIN).json()
    rows = {r["key"]: r for r in by_model["rows"]}
    assert rows["claude-opus-5"]["runs"] == 2
    assert rows["claude-opus-5"]["usd"] == 3.0
    assert rows["claude-opus-5"]["cache_read"] == 5_000
    assert rows["unknown"]["runs"] == 1
    assert by_model["rows"][0]["key"] == "claude-opus-5", "sorted by usd desc"

    by_team = client.get(f"/observe/projects/{project['id']}/spend?group_by=team",
                         headers=ADMIN).json()
    # The CEO has no team; it rolls up under its own name, not "unknown".
    assert "CEO" in [r["label"] for r in by_team["rows"]]

    bad = client.get(f"/observe/projects/{project['id']}/spend?group_by=nope", headers=ADMIN)
    assert bad.status_code == 400
    bad_window = client.get(f"/observe/projects/{project['id']}/spend?window=fortnight",
                            headers=ADMIN)
    assert bad_window.status_code == 400


def test_a_run_that_books_many_ledger_rows_is_still_one_run(client):
    """A chat session books a row per turn. The column says runs, not rows."""
    project = make_project(client)
    _add_run(project["id"], project["ceo_agent_id"], model="claude-opus-5",
             tokens_out=300, entries=3)

    rows = client.get(f"/observe/projects/{project['id']}/spend?group_by=model",
                      headers=ADMIN).json()["rows"]
    assert rows[0]["runs"] == 1
    assert rows[0]["tokens_out"] == 300


def test_spend_by_team_adds_up_to_the_budget_meter(client):
    """The invariant the Overview card rests on. The meter and the breakdown
    sit in one card, so they read the same ledger over the same month with the
    same arithmetic — cache tokens included, revenue excluded. A card that
    contradicts itself teaches the reader to distrust both numbers."""
    project = make_project(client)
    ceo = project["ceo_agent_id"]
    _add_run(project["id"], ceo, model="claude-opus-5", tokens_out=10, cache_read=900_000)
    _add_run(project["id"], ceo, model="claude-opus-5", tokens_out=25, cache_read=120_000)
    # Spend with no agent behind it (a success check) is still someone's money.
    _add_run(project["id"], None, model="claude-haiku-4-5", tokens_out=7, cache_read=4_000)
    with db_session() as session:
        # Revenue is money coming IN. It is on the ledger and in neither number.
        session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project["id"],
                                kind=LedgerKind.REVENUE, usd=500.0, note="a sale"))

    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    mtd = dash["month_to_date"]
    meter = (mtd["tokens_in"] + mtd["tokens_out"]
             + mtd["cache_creation"] + mtd["cache_read"])

    report = client.get(
        f"/observe/projects/{project['id']}/spend?group_by=team&window=month",
        headers=ADMIN,
    ).json()
    assert report["window"] == "month"
    breakdown = sum(
        r["tokens_in"] + r["tokens_out"] + r["cache_creation"] + r["cache_read"]
        for r in report["rows"]
    )
    assert breakdown == meter > 0


def test_spend_without_a_surviving_run_is_named_not_dropped(client):
    """Attribution comes from the run; the money does not. A ledger row whose
    run is gone still spent it, so it gets a row instead of quietly widening
    the gap between the breakdown and the meter."""
    project = make_project(client)
    with db_session() as session:
        session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project["id"],
                                kind=LedgerKind.MODEL_USAGE, tokens_out=42,
                                run_id=None, note="booked outside a run"))

    rows = client.get(f"/observe/projects/{project['id']}/spend?group_by=team&window=month",
                      headers=ADMIN).json()["rows"]
    assert [r["key"] for r in rows] == ["unattributed"]
    assert rows[0]["tokens_out"] == 42
    assert rows[0]["runs"] == 0
