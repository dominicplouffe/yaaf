"""Invariant selftests: a seat may be given more thinking, not only a better model.

`agents.model_override` has existed since the baseline and effort never had a
twin, so a project could buy the seat whose output the chairman actually reads
a stronger model and could not buy it more reasoning — effort was readable only
from the role version, which is global and shared by every project on the box.

These assert the whole path rather than the column: a spec sets it, the hire
stores it, and the runner puts it on the job that becomes `YAAF_EFFORT` in the
sandbox. A field that stops at the database is the failure worth catching —
it reads as configured on every screen and changes nothing about the run.
"""

from __future__ import annotations

from tests.conftest import ADMIN, agent_by_name, make_project, seed_role
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Agent, AgentKind, RunKind, RunQueue
from yaaf.runner import daemon


def _job_for(agent_id: str, project_id: str):
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project_id,
                             agent_id=agent_id, kind=RunKind.AGENT_RUN,
                             trigger={"reasons": []}, attempt=1))
    jobs = daemon.lease_and_prepare(limit=1)
    assert len(jobs) == 1
    return jobs[0]


def test_the_override_reaches_the_job_and_the_role_decides_when_it_is_absent(client):
    """Both halves in one test, because the default is the load-bearing one:
    almost every seat has no override and must keep running at the effort its
    role names."""
    project = make_project(client, start=True)
    plain = project["teams"][0]["agents"][0]["id"]
    assert _job_for(plain, project["id"]).effort == "high", "the role's own effort"

    lifted = agent_by_name(project, "Emp2")
    with db_session() as session:
        session.get(Agent, lifted).effort_override = "xhigh"
    job = _job_for(lifted, project["id"])
    assert job.effort == "xhigh"
    # The env var is what the sandbox actually reads; the column is only how it
    # got here.
    assert job.model, "the model pick must be unaffected by an effort override"


def test_a_project_lifts_one_seat_without_touching_the_shared_library(client):
    """The reason the column exists rather than an edit to `roles/`. Two seats
    on the same role version: one lifted, one not, and the role itself
    unchanged — because raising effort in the library raises it for every
    project on the install."""
    project = make_project(client, start=True)
    writer = agent_by_name(project, "Emp2")
    with db_session() as session:
        session.get(Agent, writer).effort_override = "max"

    assert _job_for(writer, project["id"]).effort == "max"
    other = project["teams"][0]["agents"][0]["id"]
    assert _job_for(other, project["id"]).effort == "high"

    role = client.get("/admin/roles/worker", headers=ADMIN).json()
    assert role["effort"] == "high", "the library must not have moved"


def test_a_spec_can_hire_a_seat_at_a_named_effort(client):
    """The path a setup file and the Founder both take: it arrives in the
    spec, not by patching a row afterwards."""
    client.post("/admin/users", json={"email": "chair@example.com",
                                      "display_name": "Chair"}, headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")
    created = client.post("/admin/projects", json={
        "slug": "lifted", "name": "Lifted", "goal": "Publish something good.",
        "chairman_email": "chair@example.com",
        "ceo_effort_override": "max",
        "teams": [{"role_slug": "worker", "name": "Desk", "employees": ["Ada", "Kit"],
                   "effort_overrides": {"Ada": "xhigh"}}],
    }, headers=ADMIN)
    assert created.status_code == 200, created.text

    with db_session() as session:
        project_id = created.json()["id"]
        ceo = session.query(Agent).filter_by(project_id=project_id,
                                             kind=AgentKind.CEO).one()
        assert ceo.effort_override == "max"
        assert session.query(Agent).filter_by(project_id=project_id,
                                              name="Ada").one().effort_override == "xhigh"
        # The seat nobody named keeps the role's answer, which is null here.
        assert session.query(Agent).filter_by(project_id=project_id,
                                              name="Kit").one().effort_override is None


def test_an_effort_the_sdk_does_not_know_is_refused_at_the_door(client):
    """A dict's values get no pattern from the model, so this is checked by
    hand — and it has to be, because an unknown effort is not rejected until
    the sandbox is already running, where the chairman sees a dead container
    rather than his own typo."""
    client.post("/admin/users", json={"email": "chair@example.com",
                                      "display_name": "Chair"}, headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")
    body = {
        "slug": "typo", "name": "Typo", "goal": "Publish something good.",
        "chairman_email": "chair@example.com",
        "teams": [{"role_slug": "worker", "name": "Desk", "employees": ["Ada"],
                   "effort_overrides": {"Ada": "hgih"}}],
    }
    refused = client.post("/admin/projects", json=body, headers=ADMIN)
    assert refused.status_code == 422
    assert "hgih" in refused.text and "xhigh" in refused.text, "name the typo and the choices"

    # The CEO's own field, and a name that is on no team, are refused too.
    ceo_typo = client.post("/admin/projects", json={
        **body, "slug": "typo2", "ceo_effort_override": "very-high",
        "teams": [{"role_slug": "worker", "name": "Desk", "employees": ["Ada"]}],
    }, headers=ADMIN)
    assert ceo_typo.status_code == 422

    stranger = client.post("/admin/projects", json={
        **body, "slug": "typo3",
        "teams": [{"role_slug": "worker", "name": "Desk", "employees": ["Ada"],
                   "effort_overrides": {"Nobody": "xhigh"}}],
    }, headers=ADMIN)
    assert stranger.status_code == 422 and "Nobody" in stranger.text


def test_a_role_seeded_with_an_unknown_effort_is_refused(client):
    """The library is the other door into the same env var."""
    refused = client.post("/admin/roles", json={
        "slug": "sloppy", "name": "Sloppy", "description": "d",
        "system_prompt": "x", "model": "claude-sonnet-5-5", "effort": "maximum",
    }, headers=ADMIN)
    assert refused.status_code == 422
