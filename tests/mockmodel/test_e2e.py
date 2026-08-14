"""MockModel end-to-end: the whole org loop at $0 in model tokens.

CEO plans (hypothesis + team task) -> lead claims and decomposes -> employee
works the assignment -> the platform executes the command success test ->
lead completes the team task -> everything lands `done`, with messages flowing
through the hierarchy and every wake decided by the dispatcher's SQL checks.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Run, Task, utcnow
from yaaf.runner.backends import mock as mock_backend
from yaaf.runner.daemon import execute_job, lease_and_prepare
from yaaf.server.dispatcher import dispatch_once
from yaaf.server.janitor import janitor_once


def tick(client) -> int:
    """One platform beat: dispatch wakes, execute leased jobs, run the janitor."""
    with db_session() as session:
        dispatch_once(session)
    jobs = lease_and_prepare(limit=10)
    for job in jobs:
        backend = mock_backend.MockBackend(client=client) if job.kind == "agent_run" else None
        execute_job(job, backend=backend)
    with db_session() as session:
        janitor_once(session)
    return len(jobs)


def _first(api, path: str, **params) -> dict | None:
    rows = api.get(path, params=params or None).json()
    return rows[0] if rows else None


def ceo_behavior(api: mock_backend.AgentApi, job) -> str:
    pack = job.context_pack
    board = pack.get("board", {})
    team_id = pack["roster"][0]["team_id"]
    if not pack.get("hypotheses"):
        api.post("/agent/hypotheses", json={
            "statement": "A tiny CLI utility will earn stars from strangers",
            "kill_condition": "No unaffiliated star or issue within the window",
            "kill_date": (utcnow() + timedelta(days=14)).isoformat(),
        })
    if not board.get("open"):
        api.post("/agent/tasks", json={
            "team_id": team_id,
            "title": "Ship the utility and verify it runs",
            "goal": "Build the smallest version that works and prove it with the check.",
            "success_test": {"type": "command", "command": "cat DELIVERABLE.md 2>/dev/null "
                                                          "| grep -q shipped && echo verified"},
            "produces_external_evidence": False,
        })
        return "ceo: planned hypothesis + team task"
    return "ceo: nothing to plan"


def lead_behavior(api: mock_backend.AgentApi, job) -> str:
    pack = job.context_pack
    reasons = {r["kind"]: r for r in pack["trigger"].get("reasons", [])}
    if "team_task_ready" in reasons:
        task_id = reasons["team_task_ready"]["task_id"]
        api.post(f"/agent/tasks/{task_id}/claim")
        roster = pack["roster"][0]
        employee = next(m for m in roster["members"] if m["name"] == "Emp2")
        api.post("/agent/tasks", json={
            "team_id": roster["team_id"],
            "title": "Write the deliverable file",
            "goal": "Create DELIVERABLE.md containing the word shipped.",
            "success_test": {"type": "command",
                             "command": "grep -q shipped DELIVERABLE.md && echo assignment-ok"},
            "parent_task_id": task_id,
            "assignee_agent_id": employee["agent_id"],
        })
        team_room = next(c["id"] for c in api.get("/agent/channels").json()
                         if c["kind"] == "team")
        api.post("/agent/messages", json={
            "channel_id": team_room,
            "body": "Emp2: DELIVERABLE.md is yours; the check greps for 'shipped'.",
        })
        return "lead: claimed team task, decomposed, briefed the team"
    if "claimed_task" in reasons and pack.get("task"):
        task = pack["task"]
        if task["status"] == "claimed":
            api.post(f"/agent/tasks/{task['id']}/complete", json={
                "evidence": "Subtask done; ran `grep shipped DELIVERABLE.md` and saw "
                            "'shipped it'.",
            })
            return "lead: completed the team task"
    return "lead: nothing actionable"


def employee_behavior(api: mock_backend.AgentApi, job) -> str:
    pack = job.context_pack
    reasons = {r["kind"]: r for r in pack["trigger"].get("reasons", [])}
    if "assignment_ready" in reasons:
        task_id = reasons["assignment_ready"]["task_id"]
        api.post(f"/agent/tasks/{task_id}/claim")
        from pathlib import Path

        workspace = Path(job.workspace_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "DELIVERABLE.md").write_text("shipped it\n")
        api.post(f"/agent/tasks/{task_id}/complete", json={
            "evidence": "Wrote DELIVERABLE.md; `cat DELIVERABLE.md` -> 'shipped it'.",
        })
        return "employee: wrote the deliverable and completed the assignment"
    return "employee: nothing actionable"


def test_full_org_loop(client):
    mock_backend.clear_registry()
    mock_backend.register("CEO", ceo_behavior)
    mock_backend.register("Lead", lead_behavior)
    mock_backend.register("Emp2", employee_behavior)

    project = make_project(
        client,
        slug="toy",
        config={"runtime": "mock", "ceo_heartbeat_hours": 0.0},
        constitution={"wakes_per_agent_per_hour": 100, "external_ratio_min": 0.0},
    )

    for _ in range(12):
        tick(client)
        board = client.get(f"/observe/projects/{project['id']}/board", headers=ADMIN).json()
        top = [t for t in board if t["parent_task_id"] is None]
        if top and all(t["status"] == "done" for t in top):
            break

    board = client.get(f"/observe/projects/{project['id']}/board", headers=ADMIN).json()
    assert board, "the CEO should have planned work"
    team_tasks = [t for t in board if t["parent_task_id"] is None]
    subtasks = [t for t in board if t["parent_task_id"] is not None]
    assert team_tasks and all(t["status"] == "done" for t in team_tasks), board
    assert subtasks and all(t["status"] == "done" for t in subtasks), board

    # The platform ran both command checks and recorded their output.
    assert any("assignment-ok" in (t["check_output"] or "") for t in subtasks)
    assert any("verified" in (t["check_output"] or "") for t in team_tasks)

    # Hypothesis exists; messages flowed through the team room.
    hypotheses = client.get(f"/observe/projects/{project['id']}/hypotheses",
                            headers=ADMIN).json()
    assert hypotheses and hypotheses[0]["status"] == "active"

    runs = client.get(f"/observe/projects/{project['id']}/runs",
                      headers=ADMIN, params={"limit": 100}).json()
    assert all(r["status"] in ("done", "failed") for r in runs)
    assert any(r["kind"] == "success_check" for r in runs)

    # Every wake had a reason; no agent is woken for nothing once settled.
    # (Restore a sane planning cadence first — the test ran with an always-due
    # heartbeat to drive the loop quickly.)
    from yaaf.core.models import Project

    with db_session() as session:
        row = session.get(Project, project["id"])
        row.config = {**row.config, "ceo_heartbeat_hours": 6.0}
    with db_session() as session:
        idle = dispatch_once(session)
    assert idle == 0, "a settled project must cost nothing"

    # And every run left an auditable record.
    with db_session() as session:
        for run in session.query(Run).all():
            assert run.trigger
            assert run.status in ("done", "failed")
        for task in session.query(Task).all():
            assert task.success_test["type"] == "command"
