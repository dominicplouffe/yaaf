"""Invariant selftests: a Code Factory's home is the platform's account. Every
change in flight says what it waits on, read from its own gates; every seat
says whether it is working; every number that summarises changes says how many
it rests on and is marked early below five; a change waiting only on the
chairman reaches his decisions, not just the Changes tab; and a project that
is not a factory gets none of it."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import ADMIN, bearer, live_token
from tests.selftest_invariants.test_merge_gate import (
    FakeGitHub,
    _agent,
    _claimed_task,
    _factory,
    _green,
    _open,
    _sign,
)
from tests.selftest_invariants.test_quality_gates import CI_FILE, _ready_but_for, _sign_with
from tests.selftest_invariants.test_releases import (
    _deployed,
    _propose,
    _shipped_change,
    _tick,
    _with_prod,
)
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Run, RunKind, utcnow
from yaaf.server import github, releases


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(github, "_request", fake)
    monkeypatch.setattr(github, "platform_token", lambda session: "platform-token")
    return fake


def _view(client, project):
    response = client.get(f"/observe/projects/{project['id']}/factory", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def _row(view, change_id):
    return next(r for r in view["in_flight"] if r["id"] == change_id)


def test_each_change_in_flight_says_what_it_waits_on(client, gh):
    """Read from its gates, in the order a person would chase it: the author
    for a fix, the machines while they run, a reader, then the chairman."""
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    assert _row(_view(client, project), change["id"])["waiting_on"] == "checks", \
        "no check has reported yet"

    _green(client, project, change["head_sha"])
    view = _view(client, project)
    row = _row(view, change["id"])
    assert row["waiting_on"] == "review" and row["author"] == "Mika"
    assert view["waiting_counts"]["review"] == 1
    assert row["gates_green"] < row["gates_total"]

    _sign(client, project, change["id"], verdict="return")
    assert _row(_view(client, project), change["id"])["waiting_on"] == "fix"


def test_a_change_waiting_only_on_the_chairman_is_one_of_his_decisions(client, gh):
    """Found on a real first project: the CI setup waited on a button only
    the Changes tab showed — in no decisions list, no count, no Inbox."""
    project = _factory(client)
    change = _ready_but_for(client, project, gh, CI_FILE)
    _sign_with(client, project, change["id"], allow_ci_change=(
        "Adds the first CI workflow; it runs the test suite and blocks on failure."))
    view = _view(client, project)
    assert _row(view, change["id"])["waiting_on"] == "you"
    assert view["in_flight"][0]["id"] == change["id"], "a person first"

    desk = client.get("/observe/desk", headers=ADMIN).json()
    row = next(p for p in desk["projects"] if p["id"] == project["id"])
    waiting, = row["pending_changes"]
    assert waiting["id"] == change["id"] and waiting["why"], "with the gate's own words"
    assert row["needs_you"]["changes"] == 1
    from yaaf.cli import desk as cli_desk

    assert [i.type for i in cli_desk.project_items(row)].count("change") == 1, \
        "the terminal inbox lists it too"

    approved = client.post(f"/admin/changes/{change['id']}/approve", headers=ADMIN)
    assert approved.status_code == 200, approved.text
    desk = client.get("/observe/desk", headers=ADMIN).json()
    row = next(p for p in desk["projects"] if p["id"] == project["id"])
    assert row["pending_changes"] == [] and row["needs_you"]["changes"] == 0


def test_the_crew_says_who_is_working_and_on_what(client, gh):
    project = _factory(client)
    task = _claimed_task(client, project)
    mika = _agent(project, "Mika")
    with db_session() as session:
        session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"], agent_id=mika,
                        kind=RunKind.AGENT_RUN, trigger={}, task_id=task, status="running",
                        started_at=utcnow() - timedelta(minutes=12), tokens_in=900,
                        tokens_out=100))
    crew = {s["name"]: s for s in _view(client, project)["crew"]}
    assert crew["Mika"]["state"] == "working" and crew["Mika"]["on"]
    # The test's own login token is a run too, so at least the one added here.
    assert crew["Mika"]["runs_today"] >= 1 and crew["Mika"]["tokens_today"] == 1000
    assert crew["Rhea"]["state"] == "idle"
    assert next(iter(crew.values()))["lead"], "the lead first"


def test_the_numbers_are_the_platforms_and_say_how_much_they_rest_on(client, gh, monkeypatch):
    """Merges, a review, a deploy the guard rolled back: each counted from the
    platform's rows, each with its sample, each marked early below five."""
    monkeypatch.setattr(releases, "probe", lambda url: (False, "HTTP 503"))
    project = _factory(client, release_authority="autonomous", canary_max_failures=1)
    _with_prod(client, project)
    _shipped_change(client, project, gh)
    view = _view(client, project)
    merge = view["numbers"]["time_to_merge_hours"]
    assert merge["n"] == 1 and merge["early"] is True and merge["value"] is not None
    assert view["numbers"]["first_review_minutes"]["n"] == 1
    assert view["progress"]["merged_week"] == 1
    assert view["cost"]["merged"] == 1 and view["cost"]["unit"] in ("usd", "tokens")

    body = _propose(client, project, "0.1.0").json()
    _deployed(body["id"])
    _tick(project)  # the guard breaks and rolls back
    numbers = _view(client, project)["numbers"]
    assert numbers["deploys"]["count"] == 1
    assert numbers["rolled_back"] == {"count": 1, "n": 1, "early": True}
    assert _view(client, project)["stage"] == "running"


def test_a_project_that_is_not_a_factory_has_no_factory_home(client, gh):
    project = _factory(client, mode="deliver", slug="plain")
    assert _view(client, project) == {"phase": None}
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    assert bearer(ceo)  # the project is real; it simply is not a factory


def test_the_cost_says_what_was_billed_and_what_a_subscription_stands_for(client, gh):
    """A factory may code on a subscription and talk on an API key. The cap
    counts both, as it did; the card says which dollars a provider charged."""
    from yaaf.core.models import LedgerEntry, LedgerKind

    project = _factory(client)
    mika = _agent(project, "Mika")
    before = _view(client, project)["cost"]
    with db_session() as session:
        for paid, usd in (("usd", 0.40), ("tokens", 1.10), (None, 0.25)):
            run = Run(id=ids.new_id(ids.RUN), project_id=project["id"], agent_id=mika,
                      kind=RunKind.AGENT_RUN, trigger={}, status="done", paid_in=paid,
                      cost_usd=usd, priced_usd=usd, started_at=utcnow())
            session.add(run)
            session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project["id"],
                                    kind=LedgerKind.MODEL_USAGE, usd=usd, run_id=run.id))
    cost = _view(client, project)["cost"]
    assert cost["unit"] == "usd" and before["subscription_usd"] == 0
    # The total, which is what the cap is held against, is what it always was.
    assert cost["spent_usd"] == round(before["spent_usd"] + 1.75, 2)
    # A run from before the payer was pinned reads as the billing mode's.
    assert cost["subscription_usd"] == 1.10
    assert cost["billed_usd"] == round(before["billed_usd"] + 0.65, 2)
    assert round(cost["billed_usd"] + cost["subscription_usd"], 2) == cost["spent_usd"]
    # No merge yet, so no per-merge figure in either kind of dollar; the seat
    # that spent most says how much of it was a bill.
    assert cost["per_merged_change_billed_usd"] is None
    assert cost["top_seat"]["name"] == "Mika" and cost["top_seat"]["billed_usd"] == 0.65
