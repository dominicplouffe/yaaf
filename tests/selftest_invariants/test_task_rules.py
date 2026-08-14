"""Invariant selftests: every rule gets a test that tries to BREAK it, over
HTTP, with a real scoped principal — lana's selftest.sh spirit."""

from __future__ import annotations

from tests.conftest import agent_by_name, bearer, live_token, make_project

CMD_TEST = {"type": "command", "command": "echo everything is fine"}
OBS_TEST = {"type": "observable", "target": "https://example.org/report.pdf contains a table"}


def _team(project):
    return project["teams"][0]["id"]


def test_forged_token_is_rejected(client):
    make_project(client)
    response = client.get("/agent/board", headers=bearer("yrt_totally-forged"))
    assert response.status_code == 401


def test_only_ceo_creates_top_level_tasks(client):
    project = make_project(client)
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    response = client.post(
        "/agent/tasks",
        json={"team_id": _team(project), "title": "sneaky work", "goal": "invent my own work",
              "success_test": CMD_TEST},
        headers=bearer(lead_token),
    )
    assert response.status_code == 403
    assert "Only the CEO" in response.json()["error"]


def test_success_test_is_structural(client):
    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    for bad in [
        {},  # missing entirely
        {"type": "vibes", "target": "it feels done"},
        {"type": "command", "command": "ls"},  # too short to be a real check
        {"type": "observable", "target": "looks good"},
    ]:
        response = client.post(
            "/agent/tasks",
            json={"team_id": _team(project), "title": "task with a bad test",
                  "goal": "prove the validator bites", "success_test": bad},
            headers=bearer(ceo_token),
        )
        assert response.status_code == 422, (bad, response.text)


def test_queue_ceiling_refuses(client):
    project = make_project(client, constitution={"queue_ceiling": 2,
                                                 "external_ratio_min": 0.0})
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    for i in range(2):
        ok = client.post(
            "/agent/tasks",
            json={"team_id": _team(project), "title": f"task {i}", "goal": "fill the queue up",
                  "success_test": CMD_TEST},
            headers=bearer(ceo_token),
        )
        assert ok.status_code == 200, ok.text
    over = client.post(
        "/agent/tasks",
        json={"team_id": _team(project), "title": "one too many", "goal": "break the ceiling",
              "success_test": CMD_TEST},
        headers=bearer(ceo_token),
    )
    assert over.status_code == 409
    assert "Queue ceiling" in over.json()["error"]


def test_external_evidence_floor(client):
    project = make_project(
        client,
        constitution={"external_ratio_min": 0.5, "external_ratio_applies_from": 2},
    )
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    first = client.post(
        "/agent/tasks",
        json={"team_id": _team(project), "title": "internal plumbing", "goal": "internal work",
              "success_test": CMD_TEST, "produces_external_evidence": False},
        headers=bearer(ceo_token),
    )
    assert first.status_code == 200
    second_internal = client.post(
        "/agent/tasks",
        json={"team_id": _team(project), "title": "more plumbing", "goal": "internal again",
              "success_test": CMD_TEST, "produces_external_evidence": False},
        headers=bearer(ceo_token),
    )
    assert second_internal.status_code == 409
    assert "floor" in second_internal.json()["error"]
    second_external = client.post(
        "/agent/tasks",
        json={"team_id": _team(project), "title": "reach a human", "goal": "external work",
              "success_test": OBS_TEST, "produces_external_evidence": True},
        headers=bearer(ceo_token),
    )
    assert second_external.status_code == 200


def test_claim_rules(client):
    project = make_project(client)
    team_id = _team(project)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    task = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "a team task", "goal": "test claim rules",
              "success_test": CMD_TEST},
        headers=bearer(ceo_token),
    ).json()

    # An employee who is not the lead cannot claim a team task.
    _, emp_token = live_token(project["id"], agent_by_name(project, "Emp2"))
    denied = client.post(f"/agent/tasks/{task['id']}/claim", headers=bearer(emp_token))
    assert denied.status_code == 403

    # The lead can.
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    claimed = client.post(f"/agent/tasks/{task['id']}/claim", headers=bearer(lead_token))
    assert claimed.status_code == 200

    # One at a time: a second claim by the same agent is refused.
    task2 = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "another team task", "goal": "test one-at-a-time",
              "success_test": OBS_TEST, "produces_external_evidence": True},
        headers=bearer(ceo_token),
    ).json()
    hoarding = client.post(f"/agent/tasks/{task2['id']}/claim", headers=bearer(lead_token))
    assert hoarding.status_code == 409
    assert "One item at a time" in hoarding.json()["error"]


def test_completion_requires_evidence_and_claimant(client):
    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    task = client.post(
        "/agent/tasks",
        json={"team_id": _team(project), "title": "completable", "goal": "test completion",
              "success_test": OBS_TEST},
        headers=bearer(ceo_token),
    ).json()
    client.post(f"/agent/tasks/{task['id']}/claim", headers=bearer(lead_token))

    no_evidence = client.post(
        f"/agent/tasks/{task['id']}/complete", json={"evidence": "done"},
        headers=bearer(lead_token),
    )
    assert no_evidence.status_code == 422

    _, emp_token = live_token(project["id"], agent_by_name(project, "Emp2"))
    wrong_agent = client.post(
        f"/agent/tasks/{task['id']}/complete",
        json={"evidence": "I watched someone else's task finish"},
        headers=bearer(emp_token),
    )
    assert wrong_agent.status_code == 403

    real = client.post(
        f"/agent/tasks/{task['id']}/complete",
        json={"evidence": "Opened https://example.org/report.pdf and saw the table on p2"},
        headers=bearer(lead_token),
    )
    assert real.status_code == 200
    assert real.json()["status"] == "done"  # observable test: no platform check run


def test_command_tests_are_platform_executed(client):
    """Completing a command-test task goes to `checking`, and the platform's
    check — not the agent — decides done."""
    from yaaf.runner.daemon import execute_job, lease_and_prepare

    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    task = client.post(
        "/agent/tasks",
        json={"team_id": _team(project), "title": "checked task", "goal": "platform checks me",
              "success_test": {"type": "command", "command": "echo ok && exit 0"}},
        headers=bearer(ceo_token),
    ).json()
    client.post(f"/agent/tasks/{task['id']}/claim", headers=bearer(lead_token))
    completed = client.post(
        f"/agent/tasks/{task['id']}/complete",
        json={"evidence": "ran the build locally, output attached"},
        headers=bearer(lead_token),
    )
    assert completed.json()["status"] == "checking"

    jobs = lease_and_prepare(limit=5)
    check_jobs = [j for j in jobs if j.kind == "success_check"]
    assert len(check_jobs) == 1
    execute_job(check_jobs[0])

    from tests.conftest import ADMIN

    board = client.get(f"/observe/projects/{project['id']}/board", headers=ADMIN).json()
    row = next(t for t in board if t["id"] == task["id"])
    assert row["status"] == "done"
    assert "ok" in (row["check_output"] or "")


def test_failed_command_check_bounces_back_to_claimant(client):
    from yaaf.runner.daemon import execute_job, lease_and_prepare

    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    task = client.post(
        "/agent/tasks",
        json={"team_id": _team(project), "title": "failing check", "goal": "check must fail",
              "success_test": {"type": "command", "command": "echo nope && exit 3"}},
        headers=bearer(ceo_token),
    ).json()
    client.post(f"/agent/tasks/{task['id']}/claim", headers=bearer(lead_token))
    client.post(f"/agent/tasks/{task['id']}/complete",
                json={"evidence": "claiming success optimistically"},
                headers=bearer(lead_token))
    for job in lease_and_prepare(limit=5):
        execute_job(job)

    from tests.conftest import ADMIN

    board = client.get(f"/observe/projects/{project['id']}/board", headers=ADMIN).json()
    row = next(t for t in board if t["id"] == task["id"])
    assert row["status"] == "claimed"  # back with the claimant, with the output
    assert "nope" in (row["check_output"] or "")


def test_subtask_rules(client):
    project = make_project(client)
    team_id = _team(project)
    lead_id = agent_by_name(project, "Lead")
    emp_id = agent_by_name(project, "Emp2")
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    _, lead_token = live_token(project["id"], lead_id)
    _, emp_token = live_token(project["id"], emp_id)

    task = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "decomposable", "goal": "test decomposition",
              "success_test": OBS_TEST},
        headers=bearer(ceo_token),
    ).json()

    # Decomposing before claiming is refused.
    early = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "subtask", "goal": "premature decomposition",
              "success_test": CMD_TEST, "parent_task_id": task["id"],
              "assignee_agent_id": emp_id},
        headers=bearer(lead_token),
    )
    assert early.status_code == 422
    client.post(f"/agent/tasks/{task['id']}/claim", headers=bearer(lead_token))

    # A non-lead employee may not decompose.
    not_lead = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "subtask", "goal": "employee decomposing",
              "success_test": CMD_TEST, "parent_task_id": task["id"],
              "assignee_agent_id": emp_id},
        headers=bearer(emp_token),
    )
    assert not_lead.status_code == 403

    subtask = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "the assignment", "goal": "employee does this",
              "success_test": CMD_TEST, "parent_task_id": task["id"],
              "assignee_agent_id": emp_id},
        headers=bearer(lead_token),
    )
    assert subtask.status_code == 200, subtask.text

    # Completing the team task with an open subtask is refused.
    too_soon = client.post(
        f"/agent/tasks/{task['id']}/complete",
        json={"evidence": "the team is done because I say so"},
        headers=bearer(lead_token),
    )
    assert too_soon.status_code == 409
    assert "subtask" in too_soon.json()["error"]

    # The lead cannot claim the employee's assignment.
    steal = client.post(f"/agent/tasks/{subtask.json()['id']}/claim",
                        headers=bearer(lead_token))
    assert steal.status_code == 403


def test_refusal_cap_revokes_token(client):
    project = make_project(client, constitution={"refusal_cap_per_run": 3})
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    for i in range(3):
        response = client.post(
            "/agent/tasks",
            json={"team_id": _team(project), "title": f"forbidden {i}",
                  "goal": "refuse me repeatedly", "success_test": CMD_TEST},
            headers=bearer(lead_token),
        )
        assert response.status_code == 403
    # Cap reached: the token is dead, whatever the endpoint.
    after = client.get("/agent/board", headers=bearer(lead_token))
    assert after.status_code == 401
    assert "revoked" in after.json()["detail"]


def test_paused_project_refuses_state_changes(client):
    from tests.conftest import ADMIN

    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    client.post(f"/admin/projects/{project['id']}/pause", json={"reason": "test"},
                headers=ADMIN)
    response = client.post(
        "/agent/tasks",
        json={"team_id": _team(project), "title": "task while paused",
              "goal": "should be refused", "success_test": CMD_TEST},
        headers=bearer(ceo_token),
    )
    # The pause revoked the run's token (401) — or, for a token minted after,
    # the policy layer refuses (409). Either way: no state change.
    assert response.status_code in (401, 409)
