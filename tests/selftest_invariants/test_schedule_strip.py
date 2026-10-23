"""Invariant selftests: the "who works when" strip draws the dispatcher's schedule.

Under the org chart, one row per person: the runs of the last half day and the
wakes of the next. Attacked here: that the future half is the same arithmetic
the dispatcher fires on (a CEO planning every six hours shows a wake every six
hours, from its last one), that the past half is the runs that happened, that
a paused project schedules nothing, and that a stood-down seat is not drawn.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from tests.conftest import ADMIN, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Agent, AgentStatus, Heartbeat, utcnow


def _strip(client, project: dict) -> dict:
    response = client.get(f"/observe/projects/{project['id']}/schedule", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def _row(strip: dict, agent_id: str) -> dict:
    return next(r for r in strip["rows"] if r["agent_id"] == agent_id)


def test_the_ceo_comes_first_with_its_planning_wakes(client):
    project = make_project(client)  # running
    with db_session() as session:
        session.query(Heartbeat).filter_by(agent_id=project["ceo_agent_id"],
                                           name="planning").delete()
        session.add(Heartbeat(agent_id=project["ceo_agent_id"], name="planning",
                              last_fired_at=utcnow() - timedelta(hours=1)))
    strip = _strip(client, project)
    ceo = strip["rows"][0]
    assert ceo["agent_id"] == project["ceo_agent_id"] and ceo["team"] == "CEO"
    wakes = [datetime.fromisoformat(n["at"]) for n in ceo["next"]]
    assert wakes, "a running project with a heartbeat schedules the CEO"
    gaps = {round((b - a).total_seconds() / 3600, 3) for a, b in zip(wakes, wakes[1:], strict=False)}
    assert len(gaps) <= 1, "one rhythm, evenly spaced"
    assert "plans every" in ceo["wakes"]


def test_the_past_half_is_the_runs_that_happened(client):
    project = make_project(client)
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    assert [r["run_id"] for r in _row(_strip(client, project), project["ceo_agent_id"])["ran"]] == [run_id]


def test_a_paused_project_schedules_nothing(client):
    project = make_project(client)
    paused = client.post(f"/admin/projects/{project['id']}/pause", headers=ADMIN,
                         json={"reason": "Holding while the brief is rewritten."})
    assert paused.status_code == 200, paused.text
    strip = _strip(client, project)
    assert strip["live"] is False
    assert all(r["next"] == [] for r in strip["rows"])


def test_a_stood_down_seat_is_not_drawn(client):
    project = make_project(client)
    member = project["teams"][0]["agents"][0]["id"]
    with db_session() as session:
        session.get(Agent, member).status = AgentStatus.STOOD_DOWN
    assert all(r["agent_id"] != member for r in _strip(client, project)["rows"])
