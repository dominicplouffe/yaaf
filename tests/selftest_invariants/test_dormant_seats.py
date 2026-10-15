"""A seat that has stopped running is named, on the health card and the roster.

An idle seat costs nothing, so no ceiling ever fires for it, and its status
stays "active" the whole time. Measured on five live projects, that was where
the long unread waits came from — a seat hired and never run, another holding
eighteen of its project's twenty unread messages — and nothing on any screen
said so. These tests pin the one definition both screens read.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, agent_by_name, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Agent, AgentStatus, Run, RunStatus, utcnow


def _age_seats(project: dict, days: float) -> None:
    """Every seat on the project hired `days` ago."""
    with db_session() as session:
        for agent in session.query(Agent).filter(Agent.project_id == project["id"]):
            agent.created_at = utcnow() - timedelta(days=days)


def _ran(project: dict, agent_id: str, days_ago: float) -> None:
    at = utcnow() - timedelta(days=days_ago)
    with db_session() as session:
        session.add(Run(
            id=ids.new_id(ids.RUN), project_id=project["id"], agent_id=agent_id,
            kind="agent_run", trigger={}, status=RunStatus.DONE, cost_usd=0.1,
            created_at=at, started_at=at, finished_at=at))


def _finding(client, project: dict) -> dict | None:
    checks = client.get(f"/observe/projects/{project['id']}/health",
                        headers=ADMIN).json()["checks"]
    return next((c for c in checks if c["key"] == "dormant-seats"), None)


def _org_seat(client, project: dict, agent_id: str) -> dict:
    org = client.get(f"/observe/projects/{project['id']}/org", headers=ADMIN).json()
    seats = [org["ceo"], *[a for t in org["teams"] for a in t["agents"]]]
    return next(s for s in seats if s["id"] == agent_id)


def test_a_seat_that_never_ran_and_one_gone_quiet_are_both_named(client):
    project = make_project(client)
    _age_seats(project, 20)
    lead = agent_by_name(project, "Lead")
    ceo = project["ceo_agent_id"]
    _ran(project, ceo, 1)
    _ran(project, lead, 9)
    # The third seat never ran at all.

    finding = _finding(client, project)
    assert finding is not None and finding["status"] == "warn"
    assert finding["where"] == "team"
    assert "Lead (9 days)" in finding["detail"]
    assert "(never)" in finding["detail"]
    assert "CEO (" not in finding["detail"], "the CEO ran yesterday"

    # The roster says the same thing about the same seats.
    seat = _org_seat(client, project, lead)
    assert (seat["dormant"], seat["dormant_days"]) == (True, 9)
    assert _org_seat(client, project, ceo)["dormant"] is False


def test_a_seat_hired_inside_the_week_has_not_had_its_chance(client):
    """A new project has run nothing yet, and that is not news."""
    project = make_project(client)
    _age_seats(project, 3)
    assert _finding(client, project) is None
    assert _org_seat(client, project, project["ceo_agent_id"])["dormant"] is False


def test_a_paused_seat_and_a_paused_project_are_not_dormant(client):
    """Paused is a decision somebody made, with its own label on the roster.
    And a paused project runs nobody, so every seat would be "dormant" — the
    pause is the explanation, already on the page."""
    project = make_project(client)
    _age_seats(project, 20)
    for agent_id in (project["ceo_agent_id"], agent_by_name(project, "Lead")):
        _ran(project, agent_id, 1)
    quiet = next(a["id"] for t in project["teams"] for a in t["agents"]
                 if a["name"] != "Lead")
    with db_session() as session:
        session.get(Agent, quiet).status = AgentStatus.PAUSED
    assert _finding(client, project) is None

    with db_session() as session:
        session.get(Agent, quiet).status = AgentStatus.ACTIVE
    assert _finding(client, project) is not None
    assert client.post(f"/admin/projects/{project['id']}/pause", json={"reason": "holiday"},
                       headers=ADMIN).status_code == 200
    assert _finding(client, project) is None
