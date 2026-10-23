"""Invariant selftests: the team, visible where it lives.

YAAF is a team hired for one job, so the Team page is where an owner learns
who is doing it, on what, at what cost, and on whose word. Each test pins one
thing that chart must say without a trip to another page.
"""

from __future__ import annotations

from tests.conftest import ADMIN, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Run, utcnow
from yaaf.runtimes import catalog


def _org(client, project: dict) -> dict:
    response = client.get(f"/observe/projects/{project['id']}/org", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def test_every_person_says_what_they_run_on_and_what_they_have_cost(client):
    project = make_project(client)
    org = _org(client, project)
    ceo = org["ceo"]
    member = org["teams"][0]["agents"][0]
    with db_session() as session:
        for agent_id, usd in ((ceo["id"], 3.0), (member["id"], 1.0)):
            session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"], agent_id=agent_id,
                            kind="agent_run", trigger={"type": "test"}, status="done",
                            cost_usd=usd, created_at=utcnow(), started_at=utcnow()))
    org = _org(client, project)
    ceo, member = org["ceo"], org["teams"][0]["agents"][0]
    assert ceo["model"] == catalog.label(ceo["model_id"])
    assert ceo["month_usd"] == 3.0 and ceo["month_share"] == 0.75
    assert member["month_usd"] == 1.0 and member["month_share"] == 0.25


def test_a_model_reads_the_way_a_person_says_it():
    assert catalog.label("claude-sonnet-5-5") == "Claude Sonnet 5.5"
    assert catalog.label("gpt-5.6-luna") == "GPT 5.6 Luna"
    assert catalog.label("deepseek-chat") == "DeepSeek Chat"


def test_a_team_the_ceo_hired_is_drawn_as_new_with_its_reason(client):
    from datetime import timedelta

    from tests.selftest_invariants.test_staffing import _ceo, _hire
    from yaaf.core.models import AuditLog

    project = make_project(client)
    hired = _hire(client, _ceo(project))
    assert hired.status_code == 200, hired.text
    team = next(t for t in _org(client, project)["teams"] if t["id"] == hired.json()["id"])
    change = team["last_change"]
    assert change["action"] == "hire" and change["by_agent"] is True
    assert change["reason"] == "The build half needs a second pair of hands for two weeks."
    assert change["recent"] is True
    untouched = [t for t in _org(client, project)["teams"] if t["id"] != team["id"]]
    assert all(t["last_change"] is None for t in untouched)

    with db_session() as session:
        row = session.query(AuditLog).filter_by(entity_id=team["id"], action="team.hire").one()
        row.at = utcnow() - timedelta(days=8)
    team = next(t for t in _org(client, project)["teams"] if t["id"] == hired.json()["id"])
    assert team["last_change"]["recent"] is False, "a week on, it is just part of the team"
