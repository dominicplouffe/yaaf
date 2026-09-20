"""Invariant selftests: velocity is measured on requirements, waste has a name.

The forecast stage 5 builds is remaining ÷ velocity, so the pace has to be
measured on the thing being forecast. Tasks closing is activity; a requirement
going MET is progress — the audited install had a complete task-throughput
query and still could not say whether the project was faster than last week.

Pinned here:

  * the pace counts MET transitions from requirement_events, in real windows —
    something met four weeks ago is in the 12-week rate and not the 3-week
  * R-00 opening is not pace — the gate lifting is not scope landing
  * a run that dies charges the requirement it served, from both death paths
    (finalize and the reaper), so a falling pace carries the name of its cause
  * the CEO's pack and the chairman's screen read one producer
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, backdate_project, make_project
from tests.selftest_invariants.test_requirements import (
    accept,
    ceo_token,
    deliver_project,
    propose,
)
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    Project,
    Requirement,
    RequirementEvent,
    RequirementStatus,
    Run,
    RunStatus,
    utcnow,
)
from yaaf.server import policy
from yaaf.server.velocity import project_velocity


def _mark_met(project: dict, requirement_id: str, days_ago: float) -> None:
    """Settle a requirement the way the platform does, then backdate the event
    — the pace reads the log, so the log is what the test controls."""
    with db_session() as session:
        row = session.get(Requirement, requirement_id)
        row.status = RequirementStatus.MET
        row.met_at = utcnow() - timedelta(days=days_ago)
        policy.record_requirement_event(session, row, RequirementStatus.MET, "checker",
                                        from_status=RequirementStatus.IN_PROGRESS)
        event = session.query(RequirementEvent).filter_by(
            requirement_id=requirement_id, to_status=RequirementStatus.MET).one()
        event.at = utcnow() - timedelta(days=days_ago)


def _dead_run(project: dict, requirement_id: str | None, status=RunStatus.FAILED) -> str:
    with db_session() as session:
        run = Run(id=ids.new_id(ids.RUN), project_id=project["id"],
                  agent_id=project["ceo_agent_id"], kind="agent_run", trigger={},
                  status=RunStatus.RUNNING, requirement_id=requirement_id,
                  started_at=utcnow())
        session.add(run)
        return run.id


def test_the_pace_lives_in_real_windows(client):
    project = deliver_project(client)
    # Old enough to have lived through the windows its own events sit in. The
    # pace divides by how long the project has actually existed, not by the
    # nominal 21 days, so a fresh project holding a 40-day-old event is a state
    # production cannot reach and the arithmetic should not have to honour.
    backdate_project(project["id"], hours=60 * 24)
    token = ceo_token(project)
    keys = []
    for n in range(4):
        r = propose(client, token, statement=f"Thing {n} is verifiably true")
        accept(client, r["requirement_id"], project)
        keys.append(r["requirement_id"])

    _mark_met(project, keys[0], days_ago=2)    # inside 3w
    _mark_met(project, keys[1], days_ago=10)   # inside 3w
    _mark_met(project, keys[2], days_ago=40)   # 12w only

    with db_session() as session:
        v = project_velocity(session, project["id"])
    assert v["met_3w"] == 2
    assert v["met_12w"] == 3
    assert v["per_week_3w"] == round(2 / 3, 2)
    assert v["remaining"] == 1
    assert v["weeks_left_at_pace"] == round(1 / v["per_week_3w"], 1)


def test_the_plan_gate_opening_is_not_pace(client):
    """R-00 goes MET the moment the plan holds. Counting that would gift every
    project one free unit of velocity in its first week — a forecast built on
    it would start optimistic by exactly one requirement."""
    project = deliver_project(client)
    token = ceo_token(project)
    accept(client, propose(client, token)["requirement_id"], project)
    with db_session() as session:
        plan = policy.plan_requirement(session, project["id"])
        assert plan.status == RequirementStatus.MET   # the gate lifted
        v = project_velocity(session, project["id"])
    assert v["met_3w"] == 0
    assert v["met_12w"] == 0


def test_a_dying_run_charges_the_requirement_it_served(client):
    """Both death paths: the reaper for a stale lease, charge_failure directly
    for the finalize statuses. The charge lands once, at the terminal
    transition, on the open requirement the run was stamped against."""
    from yaaf.core.constitution import effective
    from yaaf.server.janitor import reap_stale_runs

    project = deliver_project(client)
    token = ceo_token(project)
    r = propose(client, token, statement="Something worth dying for is true")
    accept(client, r["requirement_id"], project)
    rid = r["requirement_id"]

    # Path one: the reaper. A run with a stale heartbeat dies REAPED.
    run_id = _dead_run(project, rid)
    with db_session() as session:
        run = session.get(Run, run_id)
        run.heartbeat_at = utcnow() - timedelta(hours=3)
        run.created_at = utcnow() - timedelta(hours=3)
    with db_session() as session:
        row = session.get(Project, project["id"])
        reaped = reap_stale_runs(session, row, effective(session, row.id))
    assert reaped == 1
    with db_session() as session:
        assert session.get(Requirement, rid).failure_load == 1

    # Path two: finalize's terminal statuses, via the same charge.
    run_id = _dead_run(project, rid)
    with db_session() as session:
        run = session.get(Run, run_id)
        run.status = RunStatus.FAILED
        policy.charge_failure(session, run)
    with db_session() as session:
        assert session.get(Requirement, rid).failure_load == 2

    # A run serving nothing charges nothing — overhead is visible in the
    # attribution split instead, never smeared onto a requirement.
    run_id = _dead_run(project, None)
    with db_session() as session:
        run = session.get(Run, run_id)
        run.status = RunStatus.FAILED
        policy.charge_failure(session, run)
    with db_session() as session:
        assert session.get(Requirement, rid).failure_load == 2

    with db_session() as session:
        v = project_velocity(session, project["id"])
    assert v["worst_failure"]["key"] == r["key"]
    assert v["worst_failure"]["failure_load"] == 2


def test_waste_and_attribution_are_measured_on_the_same_window(client):
    project = deliver_project(client)
    token = ceo_token(project)
    r = propose(client, token, statement="The window has honest denominators")
    accept(client, r["requirement_id"], project)
    rid = r["requirement_id"]

    # ceo_token() minted a real run to authenticate with, and that run is in
    # the window — the window is honest about everything in it, so the test
    # asserts deltas rather than pretending the fixture spent nothing.
    with db_session() as session:
        before = project_velocity(session, project["id"])["runs_3w"]

    with db_session() as session:
        for status, req in [(RunStatus.DONE, rid), (RunStatus.DONE, rid),
                            (RunStatus.FAILED, rid), (RunStatus.DONE, None)]:
            session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"],
                            agent_id=project["ceo_agent_id"], kind="agent_run",
                            trigger={}, status=status, requirement_id=req,
                            cost_usd=1.0, started_at=utcnow(), finished_at=utcnow()))

    with db_session() as session:
        runs = project_velocity(session, project["id"])["runs_3w"]
    assert runs["total"] == before["total"] + 4
    assert runs["dead"] == before["dead"] + 1
    assert runs["attributed"] == before["attributed"] + 3
    assert runs["usd"] == before["usd"] + 4.0
    assert runs["attributed_usd"] == before["attributed_usd"] + 3.0
    assert runs["failure_share"] == round(runs["dead"] / runs["total"], 3)
    assert runs["attributed_share"] == round(runs["attributed"] / runs["total"], 3)


def test_the_pack_and_the_screen_read_one_producer(client):
    """Two computations of "how fast are we" is how the CEO and the chairman
    end up arguing about the weather. The pack block and the /plan block are
    the same dict from the same function."""
    from tests.conftest import live_token
    from yaaf.server.contextpack import build_context_pack

    project = deliver_project(client)
    token = ceo_token(project)
    accept(client, propose(client, token)["requirement_id"], project)

    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        run = session.get(Run, run_id)
        run.status = RunStatus.RUNNING
        pack = build_context_pack(
            session, run, session.get(Agent, project["ceo_agent_id"]),
            session.get(Project, project["id"]))

    view = client.get(f"/observe/projects/{project['id']}/plan", headers=ADMIN).json()
    # The run created for the pack lands in the window between the two reads,
    # so drop the run-window block and compare the pace itself.
    pack_v = {k: v for k, v in pack["velocity"].items() if k != "runs_3w"}
    view_v = {k: v for k, v in view["velocity"].items() if k != "runs_3w"}
    assert pack_v == view_v


def test_an_explore_project_has_no_velocity_block(client):
    from tests.conftest import live_token
    from yaaf.server.contextpack import build_context_pack

    project = make_project(client, slug="explorer")
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        run = session.get(Run, run_id)
        run.status = RunStatus.RUNNING
        pack = build_context_pack(
            session, run, session.get(Agent, project["ceo_agent_id"]),
            session.get(Project, project["id"]))
    assert "velocity" not in pack, "a pace over requirements that cannot exist is noise"
