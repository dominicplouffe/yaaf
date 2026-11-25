"""Invariant selftests: the project home's vital signs.

The home page draws its gauges and charts from `/observe/projects/{id}/vitals`.
A chart is a claim, so the properties it has to hold are the same as a
sentence's:

  * "runs today" is the number the dispatcher stops work with — inbox reads,
    refunded runs and checks do not fill the bar, a queued run does
  * a limit's history counts the hours it actually held the project, and a
    plan refusal is told apart from an ordinary failure
  * the shared plan names only projects the reader can already open: who else
    an install runs is not something a seat on this project buys
  * reading it changes nothing
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, make_project, person
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Approval,
    ApprovalStatus,
    LedgerEntry,
    Run,
    RunKind,
    RunQueue,
    RunStatus,
    utcnow,
)
from yaaf.server.dispatcher import day_start


def vitals(client, project: dict, headers: dict = ADMIN) -> dict:
    response = client.get(f"/observe/projects/{project['id']}/vitals", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def add_run(project: dict, *, at=None, kind=RunKind.AGENT_RUN, status=RunStatus.DONE,
            metered=True, summary=None, agent_id=None, seconds=60) -> None:
    at = at or utcnow()
    with db_session() as session:
        session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"],
                        agent_id=agent_id or project["ceo_agent_id"], kind=kind,
                        trigger={"type": "test"}, metered=metered, status=status,
                        summary=summary, created_at=at, started_at=at,
                        finished_at=at + timedelta(seconds=seconds)))


def clear_runs(project: dict) -> None:
    """Starting a project wakes its CEO; the tests below count from nothing."""
    with db_session() as session:
        session.query(Run).filter(Run.project_id == project["id"]).delete()
        session.query(RunQueue).filter(RunQueue.project_id == project["id"]).delete()


def test_runs_today_is_the_number_the_dispatcher_stops_work_with(client):
    project = make_project(client)
    clear_runs(project)
    for _ in range(3):
        add_run(project)
    add_run(project, kind=RunKind.INBOX_RUN)          # its own lane
    add_run(project, metered=False)                   # refunded: changed nothing
    add_run(project, kind=RunKind.SUCCESS_CHECK)      # the platform's, not work

    today = vitals(client, project)["today"]
    assert today["used"] == 3
    assert today["hours"][today["current_hour"]] == 3
    # And it agrees with the gate that would stop the work.
    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert dash["activity"]["daily_runs"]["used"] == today["used"]


def test_a_queued_run_fills_the_bar_before_it_runs(client):
    project = make_project(client)
    clear_runs(project)
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=project["ceo_agent_id"], kind=RunKind.AGENT_RUN,
                             trigger={"type": "test"}))
    assert vitals(client, project)["today"]["used"] == 1


def test_the_daily_wall_counts_the_hours_the_project_was_stopped(client):
    project = make_project(client, constitution={"runs_per_project_per_day": 2,
                                                 "runs_hard_stop_multiple": 1.0})
    clear_runs(project)
    yesterday = day_start(utcnow()) - timedelta(days=1)
    add_run(project, at=yesterday + timedelta(hours=1))
    add_run(project, at=yesterday + timedelta(hours=6))   # the wall: stopped from 06:00

    limits = vitals(client, project)["limits"]
    daily = next(r for r in limits["rows"] if r["key"] == "daily_runs")
    assert len(daily["hours"]) == len(limits["days"]) == 14
    assert daily["hours"][-2] == 18.0
    assert daily["hours"][-1] == 0


def test_one_agent_at_its_hourly_ceiling_is_its_own_row(client):
    project = make_project(client, constitution={"wakes_per_agent_per_hour": 2,
                                                 "runs_per_project_per_hour": 10})
    clear_runs(project)
    then = day_start(utcnow()) - timedelta(days=3) + timedelta(hours=10)
    add_run(project, at=then)
    add_run(project, at=then + timedelta(minutes=20))

    rows = {r["key"]: r["hours"] for r in vitals(client, project)["limits"]["rows"]}
    assert rows["wake_ceiling"][-4] == 1
    # Two runs is not the project's hourly ceiling of ten.
    assert rows["pacing"][-4] == 0


def test_a_plan_refusal_is_not_counted_as_an_ordinary_failure_only(client):
    project = make_project(client)
    clear_runs(project)
    add_run(project, status=RunStatus.FAILED,
            summary="You've hit your 5-hour limit · resets 6pm")
    add_run(project, status=RunStatus.FAILED, summary="sandbox exited 137 without a result file")
    add_run(project)
    add_run(project, metered=False)

    body = vitals(client, project)
    plan_row = next(r for r in body["limits"]["rows"] if r["key"] == "plan")
    assert plan_row["hours"][-1] == 1
    today = body["runs"]["days"][-1]
    # Counted runs split by how they ended; the given-back one is apart, so
    # the bar drawn under the daily limit holds only what the limit counts.
    assert today == {"day": today["day"], "finished": 1, "failed": 2, "given_back": 1, "reads": 0}
    assert body["runs"]["failure_share"] == round(2 / 3, 3)


def test_spend_is_by_day_in_the_budget_s_unit_with_a_projection(client):
    project = make_project(client, billing_mode="subscription")
    with db_session() as session:
        session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project["id"],
                                kind="model_usage", tokens_in=1_000, tokens_out=500,
                                cache_creation_tokens=0, cache_read_tokens=8_500))
    spend = vitals(client, project)["spend"]
    assert spend["unit"] == "tokens"
    assert spend["days"][-1] >= 10_000
    assert spend["spent"] >= 10_000
    assert spend["projected"] >= spend["spent"]
    assert spend["warn_at"] < spend["cap"] <= spend["pauses_at"]


def test_an_approval_that_expired_waiting_counts_against_the_chairman(client):
    project = make_project(client)
    with db_session() as session:
        session.add(Approval(id=ids.new_id(ids.APPROVAL), project_id=project["id"],
                             kind="spend", requested_by_agent_id=project["ceo_agent_id"],
                             title="Buy a dataset", body="It is the only source.",
                             status=ApprovalStatus.EXPIRED,
                             expires_at=utcnow() - timedelta(days=1)))
    you = vitals(client, project)["you"]
    assert you["approvals_expired_30d"] == 1
    assert you["open_asks"] == 0 and you["ask_cap"] >= 1


def _bill_with(client, project: dict, secret_id: str) -> None:
    response = client.patch(f"/admin/projects/{project['id']}",
                            json={"billing_mode": "subscription", "billing_secret_id": secret_id},
                            headers=ADMIN)
    assert response.status_code == 200, response.text


def test_the_shared_plan_names_only_projects_the_reader_can_open(client):
    """Two projects on one subscription. A co-chair seated on one learns that
    the plan is shared and how much of it went elsewhere — the fact that
    explains a full window — and never what the other project is called."""
    from yaaf.core import plan_usage

    token = "sk-ant-oat01-one-plan-for-two-projects"
    secret = client.post("/admin/settings/llm-credentials", headers=ADMIN,
                         json={"name": "Shared plan", "value": token,
                               "mode": "subscription"}).json()
    ours = make_project(client, slug="ours")
    theirs = make_project(client, slug="theirs")
    _bill_with(client, ours, secret["id"])
    _bill_with(client, theirs, secret["id"])
    with db_session() as session:
        plan_usage.record(session, token, {"status": "allowed", "windows": [
            {"key": "five_hour", "utilization": 0.7,
             "resets_at": (utcnow() + timedelta(hours=2)).isoformat()}]})
        for project, n in ((ours, 100), (theirs, 300)):
            session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project["id"],
                                    kind="model_usage", tokens_in=n, tokens_out=0,
                                    cache_creation_tokens=0, cache_read_tokens=0))

    admin_view = vitals(client, ours)["plan"]
    assert {p["name"] for p in admin_view["projects"]} == {"Ours", "Theirs"}
    assert admin_view["windows"][0]["key"] == "five_hour"

    co_chair = person("cochair@example.com")
    added = client.post(f"/admin/projects/{ours['id']}/members",
                        json={"email": "cochair@example.com", "role": "co_chair"},
                        headers=ADMIN)
    assert added.status_code == 200, added.text
    seen = vitals(client, ours, co_chair)["plan"]
    assert [p["name"] for p in seen["projects"]] == ["Ours"]
    assert seen["others"]["count"] == 1
    assert seen["others"]["share"] == 0.75
    assert "Theirs" not in str(seen) and theirs["id"] not in str(seen)


def test_an_api_key_project_has_no_plan_to_draw(client):
    project = make_project(client)
    assert vitals(client, project)["plan"] is None


def test_a_stranger_reads_nothing(client):
    project = make_project(client)
    stranger = person("stranger@example.com")
    response = client.get(f"/observe/projects/{project['id']}/vitals", headers=stranger)
    assert response.status_code == 404


def test_a_published_result_lands_on_its_day(client):
    import base64

    from tests.conftest import agent_by_name, bearer, live_token

    project = make_project(client)
    before = vitals(client, project)["published"]
    assert len(before["days"]) == len(before["counts"]) == 14
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    published = client.post("/agent/artifacts", headers=bearer(token),
                            json={"filename": "digest.md",
                                  "content_base64": base64.b64encode(b"# What changed").decode()})
    assert published.status_code == 200, published.text
    after = vitals(client, project)["published"]
    assert after["counts"][-1] == before["counts"][-1] + 1
