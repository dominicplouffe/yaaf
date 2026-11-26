"""Invariant selftests: forward pressure. Work a Code Factory needs starts
without anyone asking for it.

  * a release the guard rolled back puts a fix on the board — an accepted
    requirement and a ready task for the build team carrying the timeline
  * the same gate failing on three different changes in two weeks opens work
    to remove the cause, once; two failures, or three on one change, do not
"""

from __future__ import annotations

import pytest

from tests.selftest_invariants.test_merge_gate import FakeGitHub, _factory
from tests.selftest_invariants.test_releases import (
    _deployed,
    _propose,
    _shipped_change,
    _tick,
    _with_prod,
)
from yaaf.core.db import db_session
from yaaf.core.models import Change, Project, Requirement, Task, TaskStatus
from yaaf.server import github, pressure, releases


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(github, "_request", fake)
    monkeypatch.setattr(github, "platform_token", lambda session: "platform-token")
    return fake


def _platform_tasks(project_id) -> list[Task]:
    with db_session() as session:
        rows = [t for t in session.query(Task).filter_by(project_id=project_id)
                if session.get(Requirement, t.requirement_id) is not None
                and (session.get(Requirement, t.requirement_id).meta or {}).get("opened_by")
                == "platform"]
        for t in rows:
            session.expunge(t)
        return rows


def test_a_rollback_puts_the_fix_on_the_board(client, gh, monkeypatch):
    monkeypatch.setattr(releases, "probe", lambda url: (False, "HTTP 503"))
    project = _factory(client, release_authority="autonomous", canary_max_failures=1)
    _with_prod(client, project)
    _shipped_change(client, project, gh)
    body = _propose(client, project, "0.1.0").json()
    _deployed(body["id"])
    _tick(project)
    (task,) = _platform_tasks(project["id"])
    assert task.status == TaskStatus.READY and task.title.startswith("Fix incident 1")
    assert "Timeline" in task.goal and "guard broken" in task.goal
    with db_session() as session:
        team = next(t for t in project["teams"] if t["id"] == task.team_id)
        assert team["name"] == "Build"
        assert session.get(Requirement, task.requirement_id).accepted_at is not None


def _fail(project, n_changes, gate="tests", per_change=1):
    with db_session() as session:
        row = session.get(Project, project["id"])
        for i in range(n_changes):
            change = Change(id=f"chg_fake{gate}{i}", number=100 + i)
            for _ in range(per_change):
                pressure.gate_failed(session, row, change, gate, f"assertion removed in #{100+i}")


def _check(project) -> int:
    with db_session() as session:
        return pressure.check_recurring(session, session.get(Project, project["id"]))


def test_three_changes_failing_one_gate_open_work_on_the_cause_once(client, gh):
    project = _factory(client)
    _fail(project, 2)
    assert _check(project) == 0, "two is a coincidence"
    _fail(project, 3)
    assert _check(project) == 1
    (task,) = _platform_tasks(project["id"])
    assert "at the source" in task.title and "#100" in task.goal
    assert _check(project) == 0, "one lesson per gate per window"


def test_one_change_failing_many_times_is_not_a_pattern(client, gh):
    project = _factory(client)
    _fail(project, 1, per_change=5)
    assert _check(project) == 0


def test_a_previews_screenshot_is_served_for_its_commit_only(client, gh, monkeypatch):
    from tests.conftest import ADMIN
    from tests.selftest_invariants.test_merge_gate import _claimed_task, _open
    from yaaf.server import changes

    monkeypatch.setattr(releases, "probe", lambda url: (True, "HTTP 200"))
    project = _factory(client)
    envs = {"production": {"deploy": "fly deploy", "health_url": "https://app.example/healthz"},
            "preview": {"deploy": "fly deploy -a pr-$YAAF_CHANGE_NUMBER",
                        "health_url": "https://pr-{number}.app.example/healthz"}}
    assert client.put(f"/admin/projects/{project['id']}/environments", json=envs,
                      headers=ADMIN).status_code == 200
    change = _open(client, project, _claimed_task(client, project)).json()
    png = b"\x89PNG\r\n\x1a\nfake"
    with db_session() as session:
        changes.record_preview(session, session.get(Change, change["id"]), change["head_sha"],
                               True, "deployed", screenshot=png)
    shot = client.get(f"/observe/changes/{change['id']}/preview.png", headers=ADMIN)
    assert shot.status_code == 200 and shot.content == png
    with db_session() as session:
        session.get(Change, change["id"]).head_sha = "moved" * 8
    assert client.get(f"/observe/changes/{change['id']}/preview.png",
                      headers=ADMIN).status_code == 404, "a picture of older code is not shown"


def test_a_screenshot_that_cannot_be_taken_does_not_fail_the_preview(tmp_path):
    from yaaf.runner import daemon
    from yaaf.runner.types import RunJob

    job = RunJob(run_id="r", project_id="p", agent_id=None, kind="deploy", trigger={},
                 task_id=None, page_url="http://127.0.0.1:9/")
    assert daemon._screenshot(job, tmp_path) is None
