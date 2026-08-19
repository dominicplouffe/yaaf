"""Invariant selftests: the topology experiment — one variable, four numbers.

"Something is wrong with the topology" was the chairman's suspicion; this is
it made answerable instead of lived with. Attacked here:

  * arms are identical but for topology: same goal text, same constitution
    body, same chairman-authored requirement set, all accepted at birth —
    the CEOs plan HOW, never WHAT
  * projects.topology is a recorded fact matching the actual org shape
  * the solo arm is genuinely reachable — its CEO can claim and work its own
    tasks — and the report CARRIES the self-grading asymmetry instead of
    hiding it
  * the stop rule: an arm that breached its budget with nothing met is
    halted and reported; an arm that met something is left for the chairman
  * the metrics come from one producer and honestly refuse impossible
    denominators
"""

from __future__ import annotations

from tests.conftest import ADMIN, bearer, live_token, seed_role
from tests.selftest_invariants.test_requirements import tick
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    AgentKind,
    ExperimentArm,
    Project,
    ProjectStatus,
    Requirement,
    RequirementStatus,
    utcnow,
)

CMD = {"type": "command", "command": "test -s out/report.csv"}


def make_experiment(client, topologies=None, **overrides) -> dict:
    client.post("/admin/users",
                json={"email": "chair@example.com", "display_name": "Chair"},
                headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")
    body = {
        "name": "Is the employee layer worth it?",
        "slug_base": "topo",
        "goal": "Produce the weekly audit report, correctly and cheaply.",
        "chairman_email": "chair@example.com",
        "worker_role_slug": "worker",
        "requirements": [
            {"statement": "The audit report exists with all six sections",
             "acceptance_test": CMD, "est_runs": 4},
            {"statement": "The summary email is drafted and saved for review",
             "acceptance_test": {"type": "command", "command": "test -s out/email.txt"},
             "est_runs": 2},
        ],
        **({"topologies": topologies} if topologies else {}),
        **overrides,
    }
    response = client.post("/admin/experiments", json=body, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def arm_project(experiment: dict, topology: str) -> str:
    return next(a["project_id"] for a in experiment["arms"] if a["topology"] == topology)


# ── arms are identical but for the one variable ─────────────────────────────────


def test_arms_share_goal_constitution_and_scope_exactly(client):
    experiment = make_experiment(client)
    assert [a["topology"] for a in experiment["arms"]] == \
        ["solo", "ceo_team", "ceo_leads_workers"]

    with db_session() as session:
        projects = [session.get(Project, a["project_id"]) for a in experiment["arms"]]
        goals = {p.goal for p in projects}
        assert len(goals) == 1, "same goal text, verbatim"
        constitutions = [effective(session, p.id).model_dump() for p in projects]
        assert constitutions[0] == constitutions[1] == constitutions[2], \
            "identical constitutions, field for field"
        for p in projects:
            scope = session.query(Requirement).filter(
                Requirement.project_id == p.id, Requirement.key != "R-00").all()
            assert [(r.key, r.statement, r.est_runs) for r in scope] == [
                ("R-01", "The audit report exists with all six sections", 4),
                ("R-02", "The summary email is drafted and saved for review", 2),
            ]
            assert all(r.status == RequirementStatus.PENDING for r in scope), \
                "chairman-authored, accepted at birth — the CEO plans HOW, never WHAT"
            assert p.status == ProjectStatus.RUNNING
            assert p.mode == "deliver"


def test_topology_is_a_recorded_fact_matching_the_org(client):
    experiment = make_experiment(client)
    with db_session() as session:
        for arm in experiment["arms"]:
            project = session.get(Project, arm["project_id"])
            assert project.topology == arm["topology"]
            agents = session.query(Agent).filter_by(project_id=project.id).all()
            ceo = [a for a in agents if a.kind == AgentKind.CEO]
            workers = [a for a in agents if a.kind == AgentKind.EMPLOYEE]
            if arm["topology"] == "solo":
                assert len(agents) == 1 and ceo, "one brain, and it is the CEO"
                assert ceo[0].team_id is not None, "the CEO is the team"
            elif arm["topology"] == "ceo_team":
                assert len(workers) == 2
                assert len({w.team_id for w in workers}) == 2, "flat: one-person teams"
            else:
                assert len(workers) == 2
                assert len({w.team_id for w in workers}) == 1, "one team, one lead"


def test_the_plan_holds_by_itself_in_every_arm(client):
    """Chairman-authored scope is accepted and estimated from birth, so R-00
    closes on the first janitor look — no CEO run was needed to make the
    denominators identical."""
    experiment = make_experiment(client)
    for arm in experiment["arms"]:
        tick({"id": arm["project_id"]})
    with db_session() as session:
        for arm in experiment["arms"]:
            plan = session.query(Requirement).filter_by(
                project_id=arm["project_id"], key="R-00").one()
            assert plan.status == RequirementStatus.MET, arm["topology"]


# ── solo is reachable, and its asymmetry is reported ────────────────────────────


def test_the_solo_arm_works_and_the_asymmetry_is_carried(client):
    experiment = make_experiment(client)
    solo_id = arm_project(experiment, "solo")
    tick({"id": solo_id})

    with db_session() as session:
        ceo = session.query(Agent).filter_by(project_id=solo_id).one()
        rid = session.query(Requirement).filter_by(
            project_id=solo_id, key="R-01").one().id
        team_id = ceo.team_id
    _, token = live_token(solo_id, ceo.id)
    created = client.post("/agent/tasks", json={
        "team_id": team_id, "title": "build the report",
        "goal": "make R-01 true", "requirement_id": rid,
        "success_test": {"type": "observable",
                         "target": "out/report.csv exists with six sections"},
    }, headers=bearer(token))
    assert created.status_code == 200, created.text
    claimed = client.post(f"/agent/tasks/{created.json()['id']}/claim",
                          headers=bearer(token))
    assert claimed.status_code == 200, \
        "solo must not degrade to a CEO with nobody to hand work to"

    report = client.get(f"/observe/experiments/{experiment['id']}", headers=ADMIN)
    assert report.status_code == 200
    asymmetries = report.json()["asymmetries"]
    assert any("self-grade" in a for a in asymmetries), \
        "the known asymmetry is REPORTED, not hidden"


# ── the metrics: one producer, honest denominators ──────────────────────────────


def test_the_report_carries_four_numbers_per_arm(client):
    experiment = make_experiment(client, topologies=["solo", "ceo_team"])
    report = client.get(f"/observe/experiments/{experiment['id']}", headers=ADMIN).json()
    assert len(report["arms"]) == 2
    for arm in report["arms"]:
        metrics = arm["metrics"]
        assert metrics["requirements_met"] == 0
        assert metrics["cost_per_met_usd"] is None, "0 met → no invented number"
        assert metrics["hours_to_first_attested_evidence"] is None
        assert metrics["share_of_spend_producing_nothing"] is None, "no spend yet"
        assert metrics["words_per_delivered_byte"] is None, "nothing delivered yet"


# ── the stop rule ───────────────────────────────────────────────────────────────


def test_a_breached_arm_with_nothing_met_is_halted_and_one_with_wins_is_not(client):
    from yaaf.server.experiments import check_experiment_arms

    experiment = make_experiment(client, topologies=["solo", "ceo_team"])
    loser = arm_project(experiment, "solo")
    survivor = arm_project(experiment, "ceo_team")

    with db_session() as session:
        for project_id in (loser, survivor):
            row = session.get(Project, project_id)
            row.status = ProjectStatus.PAUSED
            row.status_reason = "budget exhausted: 1,000/1,000 USD this month"
        met = session.query(Requirement).filter_by(
            project_id=survivor, key="R-01").one()
        met.status = RequirementStatus.MET
        met.met_at = utcnow()

    with db_session() as session:
        for project_id in (loser, survivor):
            check_experiment_arms(session, session.get(Project, project_id))
    with db_session() as session:
        stopped = session.get(Project, loser)
        assert stopped.status == ProjectStatus.STOPPED
        assert "stop rule" in stopped.status_reason
        arm = session.query(ExperimentArm).filter_by(project_id=loser).one()
        assert arm.stopped_reason == "budget breached with zero requirements met"

        kept = session.get(Project, survivor)
        assert kept.status == ProjectStatus.PAUSED, \
            "an arm that met something earned its pause; the chairman decides"


def test_closing_the_experiment_stops_the_burn_but_keeps_the_record(client):
    experiment = make_experiment(client, topologies=["solo", "ceo_team"])
    closed = client.post(f"/admin/experiments/{experiment['id']}/close",
                         json={"reason": "the reading is in"}, headers=ADMIN)
    assert closed.status_code == 200
    assert closed.json()["arms_stopped"] == 2
    report = client.get(f"/observe/experiments/{experiment['id']}", headers=ADMIN).json()
    assert report["status"] == "closed"
    assert len(report["arms"]) == 2, "closing ends the spend, not the record"
