"""Invariant selftests: the Runs page's day.

One project showed 40 of 40 on its Inbox, 29 on its Runs page and 42 on a
chart, for the same day. `/observe/projects/{id}/runs/day` exists so there is
one answer. What it has to hold:

  * the day is the limit's day (00:00 UTC), not the reader's midnight
  * every run started in it lands in exactly one lane, the lanes add up to
    every run started, and "counted" is the number the dispatcher stops work at
  * why a run woke is said in words, from the trigger the dispatcher stored
  * "usual" is the middle of the fortnight before, so a spike reads as one
  * what went wrong is named, including the shared plan's own refusals
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, agent_by_name, make_project, person
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    AuditLog,
    Run,
    RunKind,
    RunQueue,
    RunStatus,
    Task,
    utcnow,
)
from yaaf.server.dispatcher import day_start


def run_day(client, project: dict, headers: dict = ADMIN) -> dict:
    response = client.get(f"/observe/projects/{project['id']}/runs/day", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def add_run(project: dict, *, at=None, kind=RunKind.AGENT_RUN, status=RunStatus.DONE,
            metered=True, summary=None, agent_id=None, reasons=None, seconds=60,
            attempt=1, prior=None) -> str:
    at = at or utcnow()
    run_id = ids.new_id(ids.RUN)
    with db_session() as session:
        session.add(Run(id=run_id, project_id=project["id"],
                        agent_id=agent_id or project["ceo_agent_id"], kind=kind,
                        trigger={"type": "wake", "reasons": reasons or [{"kind": "heartbeat",
                                                                         "name": "planning"}]},
                        metered=metered, status=status, summary=summary, attempt=attempt,
                        prior_run_id=prior, created_at=at, started_at=at,
                        finished_at=at + timedelta(seconds=seconds)))
    return run_id


def clear(project: dict) -> None:
    with db_session() as session:
        session.query(Run).filter(Run.project_id == project["id"]).delete()
        session.query(RunQueue).filter(RunQueue.project_id == project["id"]).delete()


def test_every_run_lands_in_one_lane_and_counted_is_the_gate_s_number(client):
    project = make_project(client)
    clear(project)
    for _ in range(3):
        add_run(project)
    add_run(project, metered=False)                      # given back
    add_run(project, kind=RunKind.INBOX_RUN)             # its own allowance
    add_run(project, kind=RunKind.SUCCESS_CHECK)         # the platform's
    add_run(project, kind=RunKind.CHAT_SESSION, status=RunStatus.RUNNING)
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=project["ceo_agent_id"], kind=RunKind.AGENT_RUN,
                             trigger={"type": "test"}))

    body = run_day(client, project)
    started = body["started"]
    assert started == {"total": 8, "counted": 4, "given_back": 1, "reads": 1,
                       "checks": 1, "chat": 1}
    assert sum(v for k, v in started.items() if k != "total") == started["total"]
    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert body["limit"]["counted"] == dash["activity"]["daily_runs"]["used"]
    assert [r["lane"] for r in body["runs"]].count("given_back") == 1


def test_the_day_starts_at_midnight_utc_not_the_reader_s(client):
    project = make_project(client)
    clear(project)
    start = day_start(utcnow())
    add_run(project, at=start - timedelta(minutes=30))   # yesterday's
    add_run(project, at=start + timedelta(minutes=10))   # today's
    body = run_day(client, project)
    assert body["day"]["starts_at"].startswith(start.date().isoformat())
    assert body["limit"]["counted"] == 1
    assert body["days"][-2]["counted"] == 1 and body["days"][-1]["counted"] == 1


def test_why_a_run_woke_is_said_in_words(client):
    project = make_project(client)
    clear(project)
    lead = agent_by_name(project, "Lead")
    task_id = ids.new_id(ids.TASK)
    with db_session() as session:
        team_id = session.get(Agent, lead).team_id
        session.add(Task(id=task_id, project_id=project["id"], team_id=team_id,
                         created_by_agent_id=project["ceo_agent_id"],
                         title="Nov 26 post draft", goal="Write it.", success_test={}))
    add_run(project, agent_id=lead, reasons=[{"kind": "messages", "count": 1},
                                             {"kind": "claimed_task", "task_id": task_id}])
    add_run(project)  # a planning check-in

    body = run_day(client, project)
    whys = {r["agent"]: r["why"] for r in body["runs"]}
    assert whys["Lead"] == "Task: Nov 26 post draft"   # the work, not the chatter with it
    assert whys["CEO"] == "Planning check-in"
    assert body["by_reason"] == {"task": 1, "plan": 1}
    lead_row = next(w for w in body["who"] if w["name"] == "Lead")
    assert lead_row["reasons"] == {"task": 1}
    # Seats with no runs are still listed, so a quiet one is visible.
    assert any(w["name"] == "Emp2" and w["counted"] == 0 for w in body["who"])


def test_the_limit_names_when_it_was_reached(client):
    project = make_project(client, constitution={"runs_per_project_per_day": 3,
                                                 "runs_hard_stop_multiple": 1.0})
    clear(project)
    start = day_start(utcnow())
    for minutes in (5, 10, 15, 20):
        add_run(project, at=start + timedelta(minutes=minutes))
    body = run_day(client, project)
    assert body["limit"]["wall"] == 3
    assert body["limit"]["reached_at"].startswith((start + timedelta(minutes=15)).isoformat()[:16])
    assert any("limit was reached" in part["text"] for part in body["story"])


def test_usual_is_the_middle_of_the_fortnight_so_a_spike_reads_as_one(client):
    project = make_project(client)
    clear(project)
    start = day_start(utcnow())
    for back in range(1, 15):
        for _ in range(2):
            add_run(project, at=start - timedelta(days=back) + timedelta(hours=1))
    for _ in range(6):
        add_run(project, at=start + timedelta(minutes=5))
    body = run_day(client, project)
    assert body["usual"]["counted"] == 2
    assert body["verdict"] == "3× a normal day"
    assert len(body["days"]) == 14 and body["days"][-1]["counted"] == 6


def test_what_went_wrong_is_named(client):
    project = make_project(client)
    clear(project)
    start = day_start(utcnow())
    at = start + timedelta(minutes=30)
    add_run(project, metered=False, reasons=[{"kind": "messages", "count": 1}])
    add_run(project, status=RunStatus.KILLED, at=at, seconds=600)
    with db_session() as session:
        session.add(AuditLog(id=ids.new_id(ids.AUDIT),
                             project_id=project["id"], actor="user:chair", action="project.pause",
                             entity="project", entity_id=project["id"],
                             at=at + timedelta(minutes=2)))
    failed = add_run(project, status=RunStatus.FAILED, at=at + timedelta(minutes=20),
                     summary="sandbox exited 137 without a result file")
    add_run(project, at=at + timedelta(minutes=25), attempt=2, prior=failed)
    add_run(project, status=RunStatus.FAILED, at=at + timedelta(minutes=40),
            summary="You've hit your 5-hour limit · resets 6pm")

    problems = run_day(client, project)["problems"]
    assert problems["given_back"]["count"] == 1
    assert problems["given_back"]["by_agent"] == {"CEO": 1}
    labels = {c["label"]: c for c in problems["ended_early"]["causes"]}
    assert labels["Stopped: you paused"]["count"] == 1
    assert labels["Failed: ran out of memory"]["retried_ok"] is True
    assert "Turned away by Claude" in labels
    assert problems["plan_refused"]["count"] == 1
    assert problems["timeouts"]["longest_s"] == 600


def test_plan_readings_keep_their_past_and_forget_after_a_month(client):
    from yaaf.core import plan_usage
    from yaaf.core.models import PlanReadingHistory

    token = "sk-ant-oat01-a-plan-with-a-past"
    with db_session() as session:
        for share in (0.2, 0.64, 0.4):
            plan_usage.record(session, token, {"status": "allowed", "windows": [
                {"key": "five_hour", "utilization": share},
                {"key": "seven_day", "utilization": 0.3}]})
        digest = plan_usage.fingerprint(token)
        session.add(PlanReadingHistory(token_fingerprint=digest,
                                       at=utcnow() - timedelta(days=plan_usage.HISTORY_DAYS + 2),
                                       five_hour=0.9))
    with db_session() as session:
        plan_usage.record(session, token, {"status": "allowed",
                                           "windows": [{"key": "five_hour", "utilization": 0.1}]})
    with db_session() as session:
        past = plan_usage.history(session, digest, utcnow() - timedelta(days=60))
    assert [round(h["five_hour"], 2) for h in past] == [0.2, 0.64, 0.4, 0.1]


def test_a_stranger_reads_nothing(client):
    project = make_project(client)
    response = client.get(f"/observe/projects/{project['id']}/runs/day",
                          headers=person("stranger@example.com"))
    assert response.status_code == 404
