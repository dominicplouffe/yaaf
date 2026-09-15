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


# ── why the dead ones died ──────────────────────────────────────────────────────
#
# The second half of the same failure. `runs_by_outcome` told a CEO eleven of a
# seat's twenty-one runs had "failed" and nothing more, so it reported eleven
# runs that "died without finishing anything" and moved to stand the seat down.
# Seven of the eleven had filed work before they were cut at the per-run dollar
# ceiling; all five tasks that seat touched that night completed on a retry.
# The cause was on `runs.summary` the whole time, where the chairman could read
# it and the seat paying for it could not.

#: Summaries copied from real dead runs on this deployment. A recognizer tested
#: only against strings its author invented recognizes its author.
LIVE_SUMMARIES = [
    ("[sandbox_entry error: ResultError('Claude Code returned an error result: "
     "Reached maximum budget ($2) (exit code: 1)')]", "per-run spend ceiling ($2)"),
    ("I'll start by claiming the task and reading the code. [sandbox_entry error: "
     "ResultError('Claude Code returned an error result: Reached maximum budget "
     "($1.25) (exit code: 1)')]", "per-run spend ceiling ($1.25)"),
    ("You've hit your org's monthly spend limit · ask your admin to raise it",
     "upstream of this project"),
    ("chat session container lost or mismatched; supervisor restarts it exit=137 "
     "oom=False error='' started=2026-09-14T00:00:00Z", "boardroom chat session"),
    ("sandbox exited 137 without a result file; tail:\nexit=137 oom=true error=''",
     "out of memory"),
    ("[killed mid-run, exit 124; usage recovered from checkpoint]", "run_timeout_s"),
    ("API Error: 529 Overloaded. This is a server-side issue", "overloaded"),
    ("Not logged in · Please run /login", "credential was rejected"),
]


def _dead(session, project_id: str, agent_id: str, summary: str | None, *,
          status: str = RunStatus.FAILED, produced: int = 0) -> str:
    run = Run(id=ids.new_id(ids.RUN), project_id=project_id, agent_id=agent_id,
              kind="agent_run", trigger={"type": "test"}, status=status,
              summary=summary, produced=produced,
              created_at=utcnow(), started_at=utcnow())
    session.add(run)
    return run.id


def _cause(seat: dict, contains: str) -> dict:
    for cause, bucket in seat["died_of"].items():
        if contains in cause:
            return bucket
    raise AssertionError(f"no cause containing {contains!r} in {list(seat['died_of'])}")


def test_every_cause_this_deployment_has_actually_produced_is_recognised():
    """Pure arithmetic over strings the platform wrote itself, so it is cheap
    enough to hold every shape rather than one."""
    from yaaf.server import deaths

    for summary, expected in LIVE_SUMMARIES:
        cause = deaths.cause_of(RunStatus.FAILED, summary)
        assert expected in cause, f"{summary[:60]!r} -> {cause!r}"
        assert cause != deaths.UNRECORDED


def test_a_cause_nobody_wrote_down_says_so_rather_than_guessing():
    """A hedged cause is worse than none: it gets acted on. The janitor reaps a
    stale lease and records nothing about why, which is several hundred of the
    deaths on this deployment, and the honest report of that is that the
    platform cannot answer it yet."""
    from yaaf.server import deaths

    assert "cannot answer" in deaths.cause_of(RunStatus.REAPED, None)
    assert deaths.cause_of(RunStatus.FAILED, "I'll start by reading the board.") == (
        deaths.UNRECORDED)


def test_the_ceo_is_told_what_killed_a_run_and_which_lever_moves_it(client):
    """"Failed" is not a thing a CEO can act on. The cause has to name the
    setting, or the next decision is about the seat instead of the ceiling."""
    project = make_project(client)
    ceo_id = project["ceo_agent_id"]
    _, ceo_token = live_token(project["id"], ceo_id)
    with db_session() as session:
        _dead(session, project["id"], ceo_id,
              "[sandbox_entry error: ResultError('Claude Code returned an error "
              "result: Reached maximum budget ($2) (exit code: 1)')]")

    bucket = _cause(_seat(_activity(client, ceo_token), "CEO"),
                    "per-run spend ceiling ($2)")
    assert bucket["runs"] == 1
    cause = next(iter(_seat(_activity(client, ceo_token), "CEO")["died_of"]))
    assert "max_usd_per_run" in cause


def test_a_dead_run_that_filed_work_first_is_not_reported_as_an_empty_one(client):
    """The fact that flips the conclusion. Cut at the ceiling after claiming a
    task and filing work is a seat being interrupted; the CEO that could only
    see "failed" read it as a seat doing nothing and proposed removing it."""
    project = make_project(client)
    ceo_id = project["ceo_agent_id"]
    lead_id = agent_by_name(project, "Lead")
    _, ceo_token = live_token(project["id"], ceo_id)
    budget = ("[sandbox_entry error: ResultError('Claude Code returned an error "
              "result: Reached maximum budget ($2) (exit code: 1)')]")
    with db_session() as session:
        _dead(session, project["id"], lead_id, budget, produced=3)
        _dead(session, project["id"], lead_id, budget, produced=0)

    bucket = _cause(_seat(_activity(client, ceo_token), "Lead"), "per-run spend ceiling")
    assert bucket["runs"] == 2
    assert bucket["produced_work_first"] == 1


def test_what_the_deaths_cost_is_the_ledgers_number_not_the_run_rows(client):
    """Same discipline as every other token in this payload. A chat session
    books a ledger row per turn, so summing `runs.tokens_*` for the dead ones
    would put two token counts in one answer that could not both be true."""
    project = make_project(client)
    ceo_id = project["ceo_agent_id"]
    _, ceo_token = live_token(project["id"], ceo_id)
    with db_session() as session:
        run_id = _dead(session, project["id"], ceo_id,
                       "sandbox exited 137 without a result file; tail:\nexit=137 "
                       "oom=true error=''")
        _spend(session, project["id"], run_id, usd=0.4, tokens_in=9000, tokens_out=500)

    seat = _seat(_activity(client, ceo_token), "CEO")
    assert _cause(seat, "out of memory")["tokens"] == 9500
    assert seat["tokens_in"] + seat["tokens_out"] == 9500


def test_the_causes_fold_into_the_totals_so_the_desk_adds_up(client):
    """A CEO asking "where did the night go" is asking about the desk, not one
    seat. The per-seat split has to sum to something it can put in a sentence."""
    project = make_project(client)
    ceo_id = project["ceo_agent_id"]
    _, ceo_token = live_token(project["id"], ceo_id)
    budget = ("[sandbox_entry error: ResultError('Claude Code returned an error "
              "result: Reached maximum budget ($2) (exit code: 1)')]")
    with db_session() as session:
        _dead(session, project["id"], ceo_id, budget, produced=1)
        _dead(session, project["id"], agent_by_name(project, "Lead"), budget, produced=2)
        _dead(session, project["id"], agent_by_name(project, "Emp2"), None,
              status=RunStatus.REAPED)

    totals = _activity(client, ceo_token)["totals"]
    assert _cause(totals, "per-run spend ceiling")["runs"] == 2
    assert _cause(totals, "per-run spend ceiling")["produced_work_first"] == 2
    assert _cause(totals, "cannot answer")["runs"] == 1


def test_a_seat_that_lost_nothing_carries_no_causes(client):
    """The block is empty rather than absent, and absent rather than noisy: a
    healthy seat should cost nothing to read past."""
    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])

    assert _seat(_activity(client, ceo_token), "CEO")["died_of"] == {}


def test_the_refusal_cap_beats_the_mechanism_its_death_was_dressed_in():
    """A run cut off for refusals dies the way any revoked run dies — killed
    mid-run, exit 137 — and one CEO narrated its own end as "this run's token
    was revoked". Both describe what happened to it. Neither is why, and the
    status is the only thing on the row that knows."""
    from yaaf.server import deaths

    for summary in ("[killed mid-run, exit 137; usage recovered from checkpoint]",
                    "This run's token was revoked after hitting the refusal cap "
                    "(5 refused task_create calls)"):
        cause = deaths.cause_of(RunStatus.TERMINATED_REFUSALS, summary)
        assert "refusal_cap_per_run" in cause
