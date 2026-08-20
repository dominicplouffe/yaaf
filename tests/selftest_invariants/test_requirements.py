"""Invariant selftests: requirements are the spine.

The audit's diagnosis in one line was that work got planned, claimed, done,
evidenced, memorised and decided — and almost none of it became a thing that
exists. 126 tasks completed; 20 side effects, all time. The cause was that
nothing said what work was FOR: the CEO's standing instruction was "keep the
queue fed but small", a pipeline with no terminus, so there was no denominator
to divide by velocity and nothing to cut when a date slipped.

A requirement is what must be true at the end. Four rules make it real, and
each is attacked here with a live run token, over HTTP, the way an agent would:

  * the CEO proposes, only the chairman accepts — scope is never self-served
  * no agent can write `met` by any route, because the verb does not exist
  * work is refused for a requirement whose dependencies are unmet
  * a dependency edge that closes a loop is refused, naming the loop
"""

from __future__ import annotations

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import (
    Project,
    ProjectMode,
    Requirement,
    RequirementEvent,
    RequirementStatus,
    RunKind,
)

CMD_TEST = {"type": "command", "command": "test -s out/report.pdf"}
OBS_TEST = {"type": "observable", "target": "https://example.org/report.pdf exists and loads"}


def deliver_project(client, slug: str = "acme", **kwargs) -> dict:
    """A Deliver project: a task must serve a requirement, and the project
    starts owing a plan, so R-00 is created at start."""
    return make_project(client, slug=slug, mode=ProjectMode.DELIVER, **kwargs)


def tick(project: dict) -> None:
    """One janitor pass. In production this runs every 15 seconds; the plan
    gate opens and closes there rather than on a route, because the plan can
    stop adding up without anyone touching a requirement."""
    from yaaf.core.constitution import effective
    from yaaf.server import janitor

    with db_session() as session:
        row = session.get(Project, project["id"])
        janitor.check_plan(session, row, effective(session, row.id))


def ceo_token(project: dict) -> str:
    _, token = live_token(project["id"], project["ceo_agent_id"])
    return token


def propose(client, token: str, statement="A report exists, sourced and dated",
            test=None, est_runs=5, **extra) -> dict:
    body = {"statement": statement, "acceptance_test": test or CMD_TEST, **extra}
    if est_runs is not None:
        body["est_runs"] = est_runs
    response = client.post("/agent/requirements", json=body, headers=bearer(token))
    assert response.status_code == 200, response.text
    return response.json()


def accept(client, requirement_id: str, project: dict | None = None, **body) -> dict:
    response = client.post(f"/admin/requirements/{requirement_id}/accept",
                           json=body, headers=ADMIN)
    assert response.status_code == 200, response.text
    if project is not None:
        tick(project)   # the plan may now hold, which opens the board
    return response.json()


def make_task(client, token: str, project: dict, requirement_id: str | None, **extra):
    body = {"team_id": project["teams"][0]["id"], "title": "do the thing",
            "goal": "and prove it", "success_test": OBS_TEST, **extra}
    if requirement_id is not None:
        body["requirement_id"] = requirement_id
    return client.post("/agent/tasks", json=body, headers=bearer(token))


# ── scope is the chairman's ─────────────────────────────────────────────────────


def test_a_proposal_is_not_scope_until_the_chairman_accepts(client):
    project = deliver_project(client)
    token = ceo_token(project)
    proposed = propose(client, token)
    assert proposed["status"] == RequirementStatus.PROPOSED
    assert proposed["key"] == "R-01"

    refused = make_task(client, token, project, proposed["requirement_id"])
    assert refused.status_code == 409
    assert "not accepted" in refused.json()["error"] or "proposed" in refused.json()["error"]

    accept(client, proposed["requirement_id"], project)
    allowed = make_task(client, token, project, proposed["requirement_id"])
    assert allowed.status_code == 200, allowed.text


def test_the_ceo_cannot_accept_its_own_proposal(client):
    """There is no agent route that accepts scope. The forecast is remaining ÷
    velocity — a CEO that owned both sides of that would own its scoreboard."""
    project = deliver_project(client)
    token = ceo_token(project)
    proposed = propose(client, token)

    for path in (f"/agent/requirements/{proposed['requirement_id']}/accept",
                 f"/agent/requirements/{proposed['requirement_id']}"):
        response = client.post(path, json={}, headers=bearer(token))
        assert response.status_code in (404, 405), f"{path} answered {response.status_code}"

    with db_session() as session:
        assert session.get(
            Requirement, proposed["requirement_id"]
        ).status == RequirementStatus.PROPOSED


def test_only_the_ceo_proposes_requirements(client):
    project = deliver_project(client)
    employee = agent_by_name(project, "Lead")
    _, token = live_token(project["id"], employee)
    response = client.post(
        "/agent/requirements",
        json={"statement": "I would like a pony, verifiably", "acceptance_test": CMD_TEST},
        headers=bearer(token),
    )
    assert response.status_code == 403
    assert "suggestion" in response.json()["error"]


def test_a_task_must_serve_a_requirement(client):
    project = deliver_project(client)
    token = ceo_token(project)
    accept(client, propose(client, token)["requirement_id"], project)   # opens the board
    refused = make_task(client, token, project, None)
    assert refused.status_code == 422
    assert "requirement_id" in refused.json()["error"]


def test_explore_has_no_requirements_and_says_so(client):
    """The open-ended arm is deliberately exempt: demanding a plan for work
    whose whole point is that the plan is not known yet would be theatre."""
    project = make_project(client, slug="explorer")   # defaults to explore
    token = ceo_token(project)
    allowed = make_task(client, token, project, None)
    assert allowed.status_code == 200, allowed.text


def test_an_explore_ceo_cannot_park_the_company_on_an_acceptance(client):
    """The kickoff deadlock: an Explore CEO once proposed staged requirements
    and then waited on an acceptance the mode gives the chairman no screen to
    perform. The proposal is refused at the door, and the refusal names both
    what to do instead and whose decision a reshape would be."""
    project = make_project(client, slug="no-scope-here")   # defaults to explore
    token = ceo_token(project)
    response = client.post(
        "/agent/requirements",
        json={"statement": "A report exists, sourced and dated",
              "acceptance_test": CMD_TEST, "est_runs": 5},
        headers=bearer(token),
    )
    assert response.status_code == 409
    assert "Explore" in response.json()["error"]
    assert "chairman" in response.json()["error"]


def test_the_dashboard_counts_scope_waiting_on_the_chairman(client):
    """The deadlock's UI half: the CEO's report can say "waiting on R-01
    acceptance" without saying where accepting happens. The dashboard carries
    the proposed count so every tab can point at the Plan tab."""
    project = deliver_project(client, slug="counted")
    token = ceo_token(project)

    def proposed_count() -> int:
        response = client.get(f"/observe/projects/{project['id']}", headers=ADMIN)
        assert response.status_code == 200, response.text
        return response.json()["requirements_proposed"]

    assert proposed_count() == 0   # R-00 is born accepted, never proposed
    proposal = propose(client, token)
    assert proposed_count() == 1
    accept(client, proposal["requirement_id"], project)
    assert proposed_count() == 0


# ── done is never self-graded ───────────────────────────────────────────────────


def test_no_route_lets_an_agent_mark_a_requirement_met(client):
    """The strongest form of the rule: not one an agent can argue with, but a
    verb it was never given."""
    project = deliver_project(client)
    token = ceo_token(project)
    requirement = propose(client, token)
    rid = requirement["requirement_id"]
    accept(client, rid, project)

    attempts = [
        ("POST", f"/agent/requirements/{rid}/complete", {"evidence": "it is done"}),
        ("POST", f"/agent/requirements/{rid}/met", {}),
        ("POST", f"/agent/requirements/{rid}/verdict", {"ok": True}),
        ("PATCH", f"/agent/requirements/{rid}", {"status": "met"}),
    ]
    for method, path, body in attempts:
        response = client.request(method, path, json=body, headers=bearer(token))
        assert response.status_code in (404, 405), \
            f"{method} {path} answered {response.status_code} — that is a way to self-grade"

    with db_session() as session:
        row = session.get(Requirement, rid)
        assert row.status == RequirementStatus.PENDING
        assert row.met_at is None


def test_closing_every_task_moves_a_requirement_to_checking_not_to_met(client):
    """Tasks being done is not the requirement being true. Five deliverables in
    the audited week were marked DONE while failing their own stated test."""
    from yaaf.server import janitor

    project = deliver_project(client)
    token = ceo_token(project)
    requirement = propose(client, token, test=OBS_TEST)
    rid = requirement["requirement_id"]
    accept(client, rid, project)

    created = make_task(client, token, project, rid)
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]

    with db_session() as session:
        assert session.get(Requirement, rid).status == RequirementStatus.IN_PROGRESS

    lead = agent_by_name(project, "Lead")
    _, lead_token = live_token(project["id"], lead)
    assert client.post(f"/agent/tasks/{task_id}/claim",
                       headers=bearer(lead_token)).status_code == 200
    assert client.post(f"/agent/tasks/{task_id}/complete",
                       json={"evidence": "https://example.org/report.pdf is up"},
                       headers=bearer(lead_token)).status_code == 200

    with db_session() as session:
        janitor.check_finished_requirements(session, session.get(Project, project["id"]))
    with db_session() as session:
        row = session.get(Requirement, rid)
        assert row.status == RequirementStatus.CHECKING, \
            "an observable requirement must wait for the chairman, never self-close"
        assert row.met_at is None


def test_an_acceptance_test_is_run_as_a_command_not_as_a_model(client):
    """A requirement check IS the command, and the platform runs it.

    It was dispatched to the model backend instead, which launches a sandbox
    with no token, no prompt and no billing credential — so every acceptance
    test died as "Not logged in · Please run /login", returned no exit code,
    and left its requirement failing a test that had never executed. A
    requirement that cannot be met is worse than one with no test at all: it
    burns a container per attempt and reports the failure as the work's.
    """
    from yaaf.runner import daemon
    from yaaf.runner.types import RunJob

    ran: list[str] = []

    def fake_command(command, workspace_dir, timeout_s):
        ran.append(command)
        return 0, "ok"

    job = RunJob(run_id="run_x", project_id="proj_x", agent_id=None,
                 kind=RunKind.REQUIREMENT_CHECK, trigger={}, task_id=None, attempt=1,
                 check_command="test -s out/report.pdf", workspace_dir="/tmp")

    import yaaf.runner.sandbox as sandbox_mod

    original = sandbox_mod.run_check_command
    sandbox_mod.run_check_command = fake_command
    try:
        result = daemon._run_check_command(job)
    finally:
        sandbox_mod.run_check_command = original

    assert ran == ["test -s out/report.pdf"], "the acceptance test must actually run"
    assert result.check_exit_code == 0
    assert "acceptance test" in result.summary

    # And a command test with no command never passes: an empty string handed
    # to a shell exits 0, and exit 0 is precisely what writes `met`.
    blank = RunJob(run_id="run_y", project_id="proj_x", agent_id=None,
                   kind=RunKind.REQUIREMENT_CHECK, trigger={}, task_id=None, attempt=1,
                   check_command="   ", workspace_dir="/tmp")
    empty = daemon._run_check_command(blank)
    assert empty.check_exit_code is None and empty.ok is False


def test_the_chairman_verdict_is_the_only_thing_that_meets_an_observable(client):
    from yaaf.server import janitor

    project = deliver_project(client)
    token = ceo_token(project)
    rid = propose(client, token, test=OBS_TEST)["requirement_id"]
    accept(client, rid, project)
    task_id = make_task(client, token, project, rid).json()["id"]
    lead = agent_by_name(project, "Lead")
    _, lead_token = live_token(project["id"], lead)
    client.post(f"/agent/tasks/{task_id}/claim", headers=bearer(lead_token))
    completed = client.post(
        f"/agent/tasks/{task_id}/complete",
        json={"evidence": "https://example.org/report.pdf returns 200; saved to workspace"},
        headers=bearer(lead_token))
    assert completed.status_code == 200, completed.text
    with db_session() as session:
        janitor.check_finished_requirements(session, session.get(Project, project["id"]))

    rejected = client.post(f"/admin/requirements/{rid}/verdict",
                           json={"ok": False, "note": "the dates are wrong"}, headers=ADMIN)
    assert rejected.status_code == 200
    with db_session() as session:
        row = session.get(Requirement, rid)
        assert row.status == RequirementStatus.IN_PROGRESS
        assert row.failure_load == 1, "a rejected requirement is an error, and errors count"

    with db_session() as session:
        janitor.check_finished_requirements(session, session.get(Project, project["id"]))
    passed = client.post(f"/admin/requirements/{rid}/verdict",
                         json={"ok": True, "note": "checked it myself"}, headers=ADMIN)
    assert passed.status_code == 200
    with db_session() as session:
        row = session.get(Requirement, rid)
        assert row.status == RequirementStatus.MET
        assert row.met_at is not None


# ── the graph is a gate, not a picture ──────────────────────────────────────────


def test_work_is_refused_while_a_dependency_is_unmet(client):
    project = deliver_project(client)
    token = ceo_token(project)
    first = propose(client, token, statement="The data exists and is sourced")
    accept(client, first["requirement_id"], project)
    second = propose(client, token, statement="The report is built from that data",
                     depends_on=[first["requirement_id"]])
    accept(client, second["requirement_id"], project)

    refused = make_task(client, token, project, second["requirement_id"])
    assert refused.status_code == 409
    assert "R-01" in refused.json()["error"], "the refusal must name what is blocking"

    # Cutting a blocker settles it: a cut requirement is decided, not pending.
    client.post(f"/admin/requirements/{first['requirement_id']}/cut",
                json={"reason": "we already have this data from the client"}, headers=ADMIN)
    allowed = make_task(client, token, project, second["requirement_id"])
    assert allowed.status_code == 200, allowed.text


def test_a_dependency_loop_is_refused_and_the_loop_is_named(client):
    project = deliver_project(client)
    token = ceo_token(project)
    a = propose(client, token, statement="A must be true before anything else")
    b = propose(client, token, statement="B follows from A", depends_on=[a["requirement_id"]])
    c = propose(client, token, statement="C follows from B", depends_on=[b["requirement_id"]])

    closing = client.post(
        "/agent/requirement_edges",
        json={"requirement_id": a["requirement_id"],
              "depends_on_requirement_id": c["requirement_id"]},
        headers=bearer(token),
    )
    assert closing.status_code == 409
    error = closing.json()["error"]
    assert "loop" in error
    for key in ("R-01", "R-02", "R-03"):
        assert key in error, f"the refusal should draw the cycle; missing {key}: {error}"


def test_a_requirement_cannot_depend_on_itself(client):
    project = deliver_project(client)
    token = ceo_token(project)
    only = propose(client, token)
    response = client.post(
        "/agent/requirement_edges",
        json={"requirement_id": only["requirement_id"],
              "depends_on_requirement_id": only["requirement_id"]},
        headers=bearer(token),
    )
    assert response.status_code == 422


# ── the requester owns the test ─────────────────────────────────────────────────


def test_a_lead_cannot_assign_itself_the_check_it_wrote(client):
    """The real self-grading path, and the one the audit found: a lead
    decomposes a task, writes the success test, assigns the subtask to itself
    and passes its own check. The convention one CEO invented after thirty
    hours of being lied to, promoted from something a company must discover to
    something it starts with."""
    project = deliver_project(client)
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    parent_id = make_task(client, token, project, rid).json()["id"]

    lead = agent_by_name(project, "Lead")
    _, lead_token = live_token(project["id"], lead)
    assert client.post(f"/agent/tasks/{parent_id}/claim",
                       headers=bearer(lead_token)).status_code == 200

    mine = make_task(client, lead_token, project, None,
                     parent_task_id=parent_id, assignee_agent_id=lead)
    assert mine.status_code == 200, mine.text
    refused = client.post(f"/agent/tasks/{mine.json()['id']}/claim", headers=bearer(lead_token))
    assert refused.status_code == 403
    assert "judges" in refused.json()["error"]

    other = [a["id"] for a in project["teams"][0]["agents"] if a["id"] != lead][0]
    theirs = make_task(client, lead_token, project, None,
                       parent_task_id=parent_id, assignee_agent_id=other)
    assert theirs.status_code == 200, theirs.text
    _, other_token = live_token(project["id"], other)
    assert client.post(f"/agent/tasks/{theirs.json()['id']}/claim",
                       headers=bearer(other_token)).status_code == 200


# ── the log the forecast will divide by ─────────────────────────────────────────


def test_every_transition_lands_in_requirement_events(client):
    """Velocity is measured on requirements because the forecast divides by
    requirements. A metric needs its own log, not an inference from another."""
    project = deliver_project(client)
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    make_task(client, token, project, rid)

    with db_session() as session:
        events = session.query(RequirementEvent).filter_by(requirement_id=rid) \
            .order_by(RequirementEvent.at).all()
        statuses = [e.to_status for e in events]
        assert statuses == [RequirementStatus.PROPOSED, RequirementStatus.PENDING,
                            RequirementStatus.IN_PROGRESS]
        assert events[0].from_status is None
        assert events[1].actor.startswith("user:"), "acceptance is the chairman's act"
        assert events[2].actor.startswith("agent:")


def test_keys_are_stable_and_human(client):
    """The chairman says "cut R-09". An opaque id would make him quote a hash
    back at his own CEO. R-00 is the plan and is reserved, so the CEO's own
    first requirement is always R-01."""
    project = deliver_project(client)
    token = ceo_token(project)
    keys = [propose(client, token, statement=f"Thing number {n} is true")["key"]
            for n in range(3)]
    assert keys == ["R-01", "R-02", "R-03"]

    listed = client.get(f"/admin/projects/{project['id']}/requirements", headers=ADMIN)
    assert listed.status_code == 200
    assert [r["key"] for r in listed.json()] == ["R-00", "R-01", "R-02", "R-03"]


def test_the_ceo_is_told_its_scope_and_what_is_blocked(client):
    """A gate an agent cannot see is a gate it discovers by being refused, and
    a refusal costs a run. The pack names the requirements, their stages, and
    what each is waiting on."""
    from yaaf.core.models import Agent, Run, RunStatus
    from yaaf.server.contextpack import build_context_pack

    project = deliver_project(client)
    token = ceo_token(project)
    first = propose(client, token, statement="The data exists and is sourced")
    accept(client, first["requirement_id"], project)
    second = propose(client, token, statement="The report is built from that data",
                     depends_on=[first["requirement_id"]])
    accept(client, second["requirement_id"])
    propose(client, token, statement="A nice-to-have nobody has accepted")

    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        run = session.get(Run, run_id)
        run.status = RunStatus.RUNNING
        pack = build_context_pack(
            session, run,
            session.get(Agent, project["ceo_agent_id"]),
            session.get(Project, project["id"]),
        )

    by_key = {r["key"]: r for r in pack["requirements"]}
    assert set(by_key) == {"R-01", "R-02", "R-03"}
    assert by_key["R-02"]["blocked_by"] == ["R-01"], "the gate must be visible before it bites"
    assert by_key["R-01"]["blocked_by"] == []
    assert by_key["R-03"]["status"] == RequirementStatus.PROPOSED

    assert pack["scope"]["open"] == 2
    assert pack["scope"]["awaiting_your_chairman"] == 1
    assert pack["scope"]["met"] == 0
    assert "can mark one met" in pack["scope"]["note"]


def test_an_explore_project_is_not_told_about_scope_it_cannot_have(client):
    from yaaf.core.models import Agent, Run, RunStatus
    from yaaf.server.contextpack import build_context_pack

    project = make_project(client, slug="explorer2")
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        run = session.get(Run, run_id)
        run.status = RunStatus.RUNNING
        pack = build_context_pack(
            session, run,
            session.get(Agent, project["ceo_agent_id"]),
            session.get(Project, project["id"]),
        )
    assert "requirements" not in pack
    assert "scope" not in pack
