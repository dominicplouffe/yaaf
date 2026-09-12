"""Invariant selftest: the CEO can read who spent the runs.

Asked "we've already done 15 today though, who did those 15", a CEO answered
"honestly, I can see the totals but not a name-by-name ledger for today",
estimated that five or six were its own, and had spent twelve of seventeen. It
was asking the chairman for fifty percent more runs the same afternoon.

Nothing was missing from the database. `runs.agent_id` had it, the ledger had
the money, and the chairman's own dashboard already drew the breakdown. There
was simply no route from any of that to the one agent being asked. These tests
hold that route open, and hold the two halves — the chairman's card and the
CEO's answer — to the same numbers.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import LedgerEntry, LedgerKind, Run, RunStatus, utcnow


def _activity(client, token: str, **params) -> dict:
    response = client.get("/agent/activity", headers=bearer(token), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _seat(payload: dict, name: str) -> dict:
    for row in payload["seats"]:
        if row["name"] == name:
            return row
    raise AssertionError(f"{name} not in {[r['name'] for r in payload['seats']]}")


def _spend(session, project_id: str, run_id: str, *, usd: float = 0.5,
           tokens_in: int = 1000, tokens_out: int = 100, at=None) -> None:
    session.add(LedgerEntry(
        id=ids.new_id(ids.LEDGER), project_id=project_id, kind=LedgerKind.MODEL_USAGE,
        tokens_in=tokens_in, tokens_out=tokens_out, cache_creation_tokens=0,
        cache_read_tokens=0, usd=usd, run_id=run_id, note="test",
        at=at or utcnow(),
    ))


def test_the_ceo_can_name_who_spent_the_runs(client):
    project = make_project(client)
    ceo_id = project["ceo_agent_id"]
    lead_id = agent_by_name(project, "Lead")
    _, ceo_token = live_token(project["id"], ceo_id)
    for _ in range(3):
        live_token(project["id"], ceo_id)
    live_token(project["id"], lead_id)

    payload = _activity(client, ceo_token)

    # Five runs: the four minted for the CEO (one of them this very call's) and
    # the lead's one. The point of the test is that they are ATTRIBUTED, not
    # summed: the failure being fixed was a correct total with a guessed split.
    assert _seat(payload, "CEO")["runs"] == 4
    assert _seat(payload, "Lead")["runs"] == 1
    assert payload["totals"]["runs"] == 5


def test_the_roster_answer_and_the_chairmans_card_agree(client):
    """Two numbers in one answer that cannot both be true is the failure this
    shape exists to avoid, so the money is joined the same way on both sides."""
    project = make_project(client)
    ceo_id = project["ceo_agent_id"]
    run_id, ceo_token = live_token(project["id"], ceo_id)
    with db_session() as session:
        _spend(session, project["id"], run_id, usd=1.25, tokens_in=4000, tokens_out=250)

    mine = _seat(_activity(client, ceo_token, since="7d"), "CEO")
    card = client.get(f"/observe/projects/{project['id']}/spend",
                      params={"group_by": "agent", "days": 7}, headers=ADMIN).json()
    row = next(r for r in card["rows"] if r["label"] == "CEO")

    assert mine["usd"] == row["usd"] == 1.25
    assert mine["tokens_in"] == row["tokens_in"] == 4000
    assert mine["tokens_out"] == row["tokens_out"] == 250


def test_money_whose_run_is_gone_is_reported_not_dropped(client):
    """A run that no longer exists still spent its money. Quietly losing it
    would make the seats add up to less than the month, which is the bug that
    makes a breakdown untrustworthy rather than merely incomplete."""
    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        _spend(session, project["id"], "run_this_never_existed", usd=3.0)

    payload = _activity(client, ceo_token)
    assert _seat(payload, "(unattributed)")["usd"] == 3.0
    assert payload["totals"]["usd"] == 3.0


def test_an_employee_reads_only_itself(client):
    """The roster's costs are the CEO's business. An employee gets its own row
    and a total it can account for — never a denominator with other people's
    runs inside it."""
    project = make_project(client)
    live_token(project["id"], project["ceo_agent_id"])
    _, emp_token = live_token(project["id"], agent_by_name(project, "Emp2"))

    payload = _activity(client, emp_token)
    assert [r["name"] for r in payload["seats"]] == ["Emp2"]
    assert payload["totals"]["runs"] == 1


def test_a_lead_reads_its_own_team(client):
    project = make_project(client)
    live_token(project["id"], project["ceo_agent_id"])
    live_token(project["id"], agent_by_name(project, "Emp2"))
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))

    payload = _activity(client, lead_token)
    assert sorted(r["name"] for r in payload["seats"]) == ["Emp2", "Lead"]
    assert payload["totals"]["runs"] == 2


def test_today_is_the_run_day_and_not_a_rolling_one(client):
    """`days=1` on the chairman's card reaches back into yesterday afternoon.
    "Today" has to be a fixed window or the answer moves under the reader."""
    project = make_project(client)
    ceo_id = project["ceo_agent_id"]
    _, ceo_token = live_token(project["id"], ceo_id)
    with db_session() as session:
        stale = Run(id=ids.new_id(ids.RUN), project_id=project["id"], agent_id=ceo_id,
                    kind="agent_run", trigger={"type": "test"}, status=RunStatus.DONE,
                    created_at=utcnow() - timedelta(days=2))
        session.add(stale)

    assert _seat(_activity(client, ceo_token, since="today"), "CEO")["runs"] == 1
    assert _seat(_activity(client, ceo_token, since="7d"), "CEO")["runs"] == 2


def test_a_window_nobody_can_resolve_is_refused_with_the_shapes_that_work(client):
    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    refused = client.get("/agent/activity", headers=bearer(ceo_token),
                         params={"since": "last tuesday"})
    assert refused.status_code == 422
    assert "today" in refused.text and "7d" in refused.text


def test_the_pack_carries_each_seats_day_so_the_ceo_cannot_guess_it(client):
    """The availability block was the only per-agent thing in the pack, and it
    could not answer the question that was actually asked."""
    project = make_project(client)
    ceo_id = project["ceo_agent_id"]
    live_token(project["id"], ceo_id)
    _, ceo_token = live_token(project["id"], ceo_id)

    pack = client.get("/agent/context", headers=bearer(ceo_token)).json()
    seat = next(a for a in pack["situation"]["agents"] if a["name"] == "CEO")
    assert seat["today"]["runs"] == 2
    assert set(seat["today"]) == {"runs", "runs_metered", "tokens", "usd",
                                  "tasks_completed"}


def test_the_pack_roster_says_whether_a_seat_is_running(client):
    """A CEO could not see from its own context that one of its seats was
    paused, while the chairman's org view — drawing the same roster — could."""
    project = make_project(client)
    lead_id = agent_by_name(project, "Lead")
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])

    assert client.post(f"/admin/agents/{lead_id}/pause", headers=ADMIN,
                       json={"paused": True}).status_code == 200
    pack = client.get("/agent/context", headers=bearer(ceo_token)).json()
    members = {m["name"]: m for m in pack["roster"][0]["members"]}
    assert members["Lead"]["status"] == "paused"
    assert members["Emp2"]["status"] == "active"
    assert members["Lead"]["role_slug"] == "worker"


def test_todays_breakdown_adds_up_to_the_ration_the_ceo_is_shown(client):
    """The one number the CEO is asked about — "we've done 15 today, who did
    those 15" — is a RATIONED count on a fixed UTC window. Resolving `today` on
    the project's local calendar instead put this breakdown and the ration
    counter beside it in the same context pack on two different clocks: on the
    project that prompted this (America/Toronto) it silently dropped every run
    between midnight and 04:00 UTC, turning seventeen into ten.

    So the window is the dispatcher's own day, and the seats carry the metered
    subset — the runs the cap actually counts — so the split ADDS UP to the
    total printed above it."""
    project = make_project(client, constitution={"timezone": "America/Toronto"})
    ceo_id = project["ceo_agent_id"]
    _, ceo_token = live_token(project["id"], ceo_id)
    live_token(project["id"], ceo_id)
    # A platform check spends no ration, so it must land in `runs` and not in
    # `runs_metered`.
    live_token(project["id"], ceo_id, kind="success_check")

    payload = _activity(client, ceo_token, since="today")
    pack = client.get("/agent/context", headers=bearer(ceo_token)).json()

    assert payload["totals"]["runs_metered"] == pack["situation"]["runs_today"]["used"]
    assert payload["totals"]["runs"] > payload["totals"]["runs_metered"], (
        "the unmetered check should still be visible as something that happened")
    assert "00:00 UTC" in payload["window"], payload["window"]


def test_a_bare_date_is_still_a_calendar_day_in_the_projects_timezone(client):
    """The ration day is a fact about the cap. A date is a question about the
    calendar, and those are allowed to be different — the answer just has to
    say which one it gave you."""
    project = make_project(client, constitution={"timezone": "America/Toronto"})
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    payload = _activity(client, ceo_token, since="2026-09-12", until="2026-09-13")
    assert payload["since"].endswith("04:00:00+00:00"), payload["since"]
