"""Invariant selftests: Code Factory, the fourth shape.

A factory is a Deliver while it is built and an Operate once it is kept, and
it moves from one to the other without being rebuilt. Each half must answer
the three questions every mode branch asks (`yaaf.core.shape`) the way its
older sibling does, and the move between them must be one way. Attacked here:

  * the start is the factory's word only, and adopting a repo needs the repo
  * a factory being built has no cycles, may hold a dated target, and refuses
    standing obligations — there is no cycle yet to owe them in
  * completing the build writes Deliver's receipt and does NOT complete the
    project: it keeps running into upkeep cycle 1 with the plan gate reopened,
    and a second completion is refused
  * in upkeep it runs on cycles: standing obligations and cycle goals are
    accepted, a project date is refused
  * a factory that adopts a repo starts in upkeep, and a clone starts where
    its source started, not where it got to
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import ADMIN, bearer, live_token, make_project
from tests.selftest_invariants.test_daily_obligations import DAILY
from tests.selftest_invariants.test_modes import fabricate_met, run_mode_tick
from tests.selftest_invariants.test_requirements import accept, ceo_token, propose
from yaaf.core import shape
from yaaf.core.db import db_session
from yaaf.core.models import (
    Cycle,
    CycleStatus,
    Decision,
    FactoryPhase,
    Project,
    ProjectMode,
    ProjectStatus,
    Requirement,
    RequirementStatus,
    utcnow,
)
from yaaf.server import policy


def factory(client, slug="slotwise", **kwargs) -> dict:
    return make_project(client, slug=slug, mode="code_factory", **kwargs)


def adopted(client, slug="invoice-api", **kwargs) -> dict:
    return make_project(client, slug=slug, mode="code_factory", factory_start="repo",
                        github_repo="acme/invoice-api", **kwargs)


def _body(slug, **extra):
    return {"slug": slug, "name": "X", "goal": "Build a booking app for clinics",
            "chairman_email": "chair@example.com", "ceo_role_slug": "ceo",
            "teams": [{"role_slug": "worker", "name": "A", "employees": ["E1"]}], **extra}


def _row(project) -> Project:
    with db_session() as session:
        row = session.get(Project, project["id"])
        session.expunge(row)
        return row


def _built(client, project) -> str:
    """Scope accepted and made true, so the build is ready to hand over."""
    token = ceo_token(project)
    rid = propose(client, token, statement="Patients can book a free slot")["requirement_id"]
    accept(client, rid, project)
    fabricate_met(project["id"], rid)
    return rid


# ── the shape, as pure answers ──────────────────────────────────────────────────


@pytest.mark.parametrize("mode,phase,scope,once,cycles,reads_as", [
    ("explore", None, False, False, False, "explore"),
    ("deliver", None, True, True, False, "deliver"),
    ("operate", None, True, False, True, "operate"),
    ("code_factory", "build", True, True, False, "deliver"),
    ("code_factory", "upkeep", True, False, True, "operate"),
])
def test_each_shape_answers_the_three_questions(mode, phase, scope, once, cycles, reads_as):
    assert shape.has_scope(mode) is scope
    assert shape.ships_once(mode, phase) is once
    assert shape.runs_on_cycles(mode, phase) is cycles
    assert shape.behaves_as(mode, phase) == reads_as


def test_only_a_factory_has_a_phase():
    assert shape.initial_phase("operate", None) is None
    assert shape.initial_phase("deliver", "repo") is None
    assert shape.initial_phase("code_factory", None) == FactoryPhase.BUILD
    assert shape.initial_phase("code_factory", "new") == FactoryPhase.BUILD
    assert shape.initial_phase("code_factory", "repo") == FactoryPhase.UPKEEP


# ── creation ────────────────────────────────────────────────────────────────────


def test_the_start_is_a_factorys_word_and_adopting_needs_the_repo(client):
    make_project(client, slug="seed", start=False)  # users and roles
    stray = client.post("/admin/projects", json=_body(
        "stray", mode="deliver", factory_start="repo"), headers=ADMIN)
    assert stray.status_code == 422
    assert "Code Factory" in stray.json()["detail"]

    nothing_to_keep = client.post("/admin/projects", json=_body(
        "no-repo", mode="code_factory", factory_start="repo"), headers=ADMIN)
    assert nothing_to_keep.status_code == 422
    assert "github_repo" in nothing_to_keep.json()["detail"]

    invented = client.post("/admin/projects", json=_body(
        "bad-start", mode="code_factory", factory_start="fork"), headers=ADMIN)
    assert invented.status_code == 422

    invented_mode = client.post("/admin/projects", json=_body(
        "bad-mode", mode="factory"), headers=ADMIN)
    assert invented_mode.status_code == 422


def test_a_factory_built_from_nothing_starts_owing_a_plan_with_no_cycle(client):
    project = factory(client)
    row = _row(project)
    assert row.mode == ProjectMode.CODE_FACTORY
    assert row.factory_phase == FactoryPhase.BUILD
    assert row.integrations.get("factory_start") == "new"
    with db_session() as session:
        assert policy.plan_requirement(session, project["id"]) is not None
        assert session.query(Cycle).filter_by(project_id=project["id"]).count() == 0


def test_a_factory_being_built_may_hold_a_sourced_target_date(client):
    make_project(client, slug="seed", start=False)
    deadline = (utcnow() + timedelta(days=60)).isoformat()
    anonymous = client.post("/admin/projects", json=_body(
        "dated", mode="code_factory", deadline=deadline), headers=ADMIN)
    assert anonymous.status_code == 422
    assert "provenance" in anonymous.json()["detail"]
    sourced = client.post("/admin/projects", json=_body(
        "dated-ok", mode="code_factory", deadline=deadline,
        deadline_source={"kind": "chairman", "note": "the pilot starts Feb 2"}),
        headers=ADMIN)
    assert sourced.status_code == 200, sourced.text


def test_a_factory_being_built_refuses_standing_obligations(client):
    project = factory(client)
    response = client.post("/agent/requirements", json={
        "statement": "Main stays green every day", "acceptance_test": DAILY,
        "est_runs": 2, "recurrence": "standing"}, headers=bearer(ceo_token(project)))
    assert response.status_code == 422
    assert "after the hand-over" in response.json()["error"]


def test_a_factory_being_built_has_no_cycle_goal(client):
    project = factory(client)
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    response = client.post("/agent/cycle_goal", json={"goal": "Ship the booking flow."},
                           headers=bearer(ceo))
    assert response.status_code == 422
    assert "still being built" in response.json()["error"]


# ── the hand-over ───────────────────────────────────────────────────────────────


def test_completing_the_build_hands_over_to_upkeep_and_keeps_running(client):
    project = factory(client, slug="handover")
    rid = _built(client, project)
    run_mode_tick(project)  # readiness detected

    done = client.post(f"/admin/projects/{project['id']}/complete", headers=ADMIN)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["receipt"]["met"] == 1
    assert body["status"] == ProjectStatus.RUNNING
    assert body["revoked_runs"] == 0 and body["voided_approvals"] == 0

    with db_session() as session:
        row = session.get(Project, project["id"])
        assert row.status == ProjectStatus.RUNNING
        assert row.factory_phase == FactoryPhase.UPKEEP
        cycle = session.query(Cycle).filter_by(project_id=project["id"]).one()
        assert cycle.ordinal == 1 and cycle.status == CycleStatus.OPEN
        # Upkeep plans fresh, and the build's scope is history, not this cycle's.
        plan = policy.plan_requirement(session, project["id"])
        assert plan.status == RequirementStatus.PENDING
        assert session.get(Requirement, rid).cycle_id is None
        assert policy.scope_requirements(session, project["id"]) == []
        # The same receipt Deliver writes.
        assert session.query(Decision).filter(
            Decision.project_id == project["id"],
            Decision.subject.like("delivery receipt%")).count() == 1

    # One way: there is no second build to complete.
    again = client.post(f"/admin/projects/{project['id']}/complete", headers=ADMIN)
    assert again.status_code == 422
    assert "upkeep" in again.json()["detail"]


def test_the_build_is_refused_completion_while_anything_is_open(client):
    project = factory(client, slug="unfinished")
    rid = propose(client, ceo_token(project))["requirement_id"]
    accept(client, rid, project)
    refused = client.post(f"/admin/projects/{project['id']}/complete", headers=ADMIN)
    assert refused.status_code == 409
    assert _row(project).factory_phase == FactoryPhase.BUILD


def test_in_upkeep_it_runs_on_cycles_and_refuses_a_project_date(client):
    project = factory(client, slug="kept")
    _built(client, project)
    assert client.post(f"/admin/projects/{project['id']}/complete",
                       headers=ADMIN).status_code == 200

    token = ceo_token(project)
    standing = propose(client, token, statement="Every day, main is green by 08:00",
                       test=DAILY, recurrence="standing",
                       meta={"title": "Main is green", "cadence": "daily"})
    assert standing["status"] == RequirementStatus.PROPOSED

    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    goal = client.post("/agent/cycle_goal", json={"goal": "Patch every high advisory."},
                       headers=bearer(ceo))
    assert goal.status_code == 200, goal.text

    dated = client.patch(f"/admin/projects/{project['id']}", json={
        "deadline": (utcnow() + timedelta(days=30)).isoformat(),
        "deadline_source": {"kind": "chairman", "note": "because"}}, headers=ADMIN)
    assert dated.status_code == 422
    assert "upkeep" in dated.json()["detail"]


# ── adopting a repo ─────────────────────────────────────────────────────────────


def test_a_factory_that_adopts_a_repo_starts_in_upkeep_with_cycle_one(client):
    project = adopted(client)
    row = _row(project)
    assert row.factory_phase == FactoryPhase.UPKEEP
    assert row.github_repo == "acme/invoice-api"
    with db_session() as session:
        cycle = session.query(Cycle).filter_by(project_id=project["id"]).one()
        assert cycle.status == CycleStatus.OPEN
    refused = client.post(f"/admin/projects/{project['id']}/complete", headers=ADMIN)
    assert refused.status_code == 422


def test_an_adopted_repo_refuses_a_project_date_at_creation(client):
    make_project(client, slug="seed", start=False)
    response = client.post("/admin/projects", json=_body(
        "adopt-dated", mode="code_factory", factory_start="repo",
        github_repo="acme/x", deadline=(utcnow() + timedelta(days=9)).isoformat(),
        deadline_source={"kind": "chairman", "note": "because"}), headers=ADMIN)
    assert response.status_code == 422
    assert "upkeep" in response.json()["detail"]


def test_a_clone_starts_where_its_source_started(client):
    project = factory(client, slug="built-once")
    _built(client, project)
    assert client.post(f"/admin/projects/{project['id']}/complete",
                       headers=ADMIN).status_code == 200
    twin = client.post(f"/admin/projects/{project['id']}/clone",
                       json={"slug": "built-twice"}, headers=ADMIN)
    assert twin.status_code == 200, twin.text
    with db_session() as session:
        row = session.get(Project, twin.json()["id"])
        assert row.mode == ProjectMode.CODE_FACTORY
        assert row.factory_phase == FactoryPhase.BUILD

    kept = adopted(client, slug="kept-repo")
    twin = client.post(f"/admin/projects/{kept['id']}/clone",
                       json={"slug": "kept-repo-twin"}, headers=ADMIN)
    assert twin.status_code == 200, twin.text
    with db_session() as session:
        assert session.get(Project, twin.json()["id"]).factory_phase == FactoryPhase.UPKEEP


def test_a_setup_file_carries_where_the_factory_started(client):
    project = adopted(client, slug="exported")
    bundle = client.get(f"/admin/projects/{project['id']}/export", headers=ADMIN)
    assert bundle.status_code == 200, bundle.text
    spec = bundle.json()["project"]
    assert spec["mode"] == "code_factory"
    assert spec["integrations"].get("factory_start") == "repo"
