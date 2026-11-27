"""Invariant selftests: a merge that fails is never left where nothing moves it.

Found by a review of the merge pipeline after the Split app's first night:
a GitHub 502 during the merge left a fully green change OPEN for good; a
timeout after GitHub had in fact merged closed the change as "merged outside
the platform" and froze merging over the platform's own merge; a process that
died mid-merge left the change in MERGING, which nothing reads; a merge GitHub
could not make (a conflict) was tried again on every tick and told nobody; and
a return's note was still shown as the verdict on the push that answered it.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta

import httpx
import pytest

from tests.selftest_invariants.test_merge_gate import (
    FakeGitHub,
    _change,
    _claimed_task,
    _factory,
    _green,
    _hook,
    _merge,
    _open,
    _sign,
)
from yaaf.core.db import db_session
from yaaf.core.models import Change, ChangeStatus, Flag, FlagKind, Notice, Project, utcnow
from yaaf.server import changes, github


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(github, "_request", fake)
    monkeypatch.setattr(github, "platform_token", lambda session: "platform-token")
    return fake


def _poll(project):
    with db_session() as session:
        row = session.get(Project, project["id"])
        changes.poll_github(session, row, force=True)
        changes.reconcile_main(session, row)


def _ready(client):
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    _green(client, project, change["head_sha"])
    assert _sign(client, project, change["id"]).status_code == 200
    assert _change(change["id"]).status == ChangeStatus.READY
    return project, change


def _notes(project) -> list[str]:
    with db_session() as session:
        return [n.body for n in session.query(Notice).filter_by(project_id=project["id"])]


def test_a_timeout_after_github_merged_is_the_platforms_own_merge(client, gh, monkeypatch):
    project, change = _ready(client)
    _poll(project)
    real = github._request

    def slow(method, path, token, json=None):
        if method == "PUT" and path.endswith("/merge"):
            gh.prs[change["number"]].update(state="closed", merged=True,
                                             merge_commit_sha=gh.merge_sha)
            gh.main = gh.merge_sha
            raise httpx.ReadTimeout("slow")
        return real(method, path, token, json)

    monkeypatch.setattr(github, "_request", slow)
    _merge(project)
    monkeypatch.setattr(github, "_request", gh)
    _poll(project)
    row = _change(change["id"])
    assert row.status == ChangeStatus.MERGED and row.merged_sha == gh.merge_sha
    with db_session() as session:
        assert session.query(Flag).filter_by(project_id=project["id"],
                                             kind=FlagKind.MAIN_BYPASSED).count() == 0


def test_github_failing_once_is_tried_again_not_stranded(client, gh):
    project, change = _ready(client)
    gh.merge_status = 502
    _merge(project)
    assert _change(change["id"]).status == ChangeStatus.READY
    gh.merge_status = 200
    with db_session() as session:
        row = session.get(Change, change["id"])
        row.findings = {**row.findings,
                        "merge_retry_at": (utcnow() - timedelta(seconds=1)).isoformat()}
    assert _merge(project) == 1
    assert _change(change["id"]).status == ChangeStatus.MERGED


def test_a_merge_github_cannot_make_tells_the_author_and_is_not_retried(client, gh):
    project, change = _ready(client)
    gh.merge_status = 405
    _merge(project)
    row = _change(change["id"])
    assert row.status == ChangeStatus.OPEN and not row.gates["mergeable"]["ok"]
    assert any("Rebase the branch onto main" in n for n in _notes(project))
    _merge(project)
    _merge(project)
    assert len(gh.merges()) == 1, "refused once, not every tick"


def test_a_change_left_merging_by_a_dead_process_is_settled(client, gh, monkeypatch):
    project, change = _ready(client)

    def die(*a, **k):
        raise SystemExit("worker killed mid-merge")

    monkeypatch.setattr(github, "merge_pull_request", die)
    with contextlib.suppress(SystemExit):
        _merge(project)
    assert _change(change["id"]).status == ChangeStatus.MERGING
    monkeypatch.undo()
    monkeypatch.setattr(github, "_request", gh)
    monkeypatch.setattr(github, "platform_token", lambda session: "platform-token")
    gh.prs[change["number"]].update(state="closed", merged=True, merge_commit_sha=gh.merge_sha)
    with db_session() as session:
        session.get(Change, change["id"]).updated_at = utcnow() - timedelta(minutes=10)
    _merge(project)
    row = _change(change["id"])
    assert row.status == ChangeStatus.MERGED and row.merged_sha == gh.merge_sha


def test_a_return_is_not_the_verdict_on_the_push_that_answers_it(client, gh):
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    _green(client, project, change["head_sha"])
    _sign(client, project, change["id"], verdict="return")
    pr = dict(gh.prs[change["number"]])
    pr["head"] = {"sha": "new" + "2" * 37}
    _hook(client, project, "pull_request", {"action": "synchronize", "pull_request": pr,
                                            "sender": {"login": "mika"}}, "s-1")
    assert _change(change["id"]).gates["review"]["detail"] == \
        "waiting for a challenge seat to read it"


def test_a_workflow_or_test_renamed_out_of_its_path_is_caught():
    from yaaf.server import diffscan

    moved = [{"filename": "ci-old/test.yml", "previous_filename": ".github/workflows/test.yml",
              "status": "renamed", "patch": None},
             {"filename": "notes/hold_spec.txt", "previous_filename": "tests/test_hold.py",
              "status": "renamed", "patch": None}]
    assert diffscan.protected(moved, changes.CI_PATHS) == [".github/workflows/test.yml"]
    assert any("moved out of the test paths" in f for f in diffscan.weakened_tests(moved))


def test_a_pr_closed_on_github_tells_its_author(client, gh):
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    pr = {**gh.prs[change["number"]], "state": "closed", "merged": False}
    _hook(client, project, "pull_request", {"action": "closed", "pull_request": pr,
                                            "sender": {"login": "dplouffe"}}, "c-1")
    assert _change(change["id"]).status == ChangeStatus.CLOSED
    assert any("was closed on GitHub" in n for n in _notes(project))
    _hook(client, project, "pull_request", {"action": "reopened", "pull_request": pr,
                                            "sender": {"login": "dplouffe"}}, "c-2")
    assert _change(change["id"]).status in (ChangeStatus.OPEN, ChangeStatus.READY)


def test_a_push_to_main_is_judged_before_the_platform_merges_on_top(client, gh):
    project, change = _ready(client)
    _poll(project)
    gh.main = "human" + "1" * 35          # straight to main, and no webhook came
    assert _merge(project) == 0
    assert gh.merges() == []
    with db_session() as session:
        assert session.query(Flag).filter_by(project_id=project["id"],
                                             kind=FlagKind.MAIN_BYPASSED).count() == 1


def test_a_scenario_run_that_died_is_run_again(client, gh):
    from yaaf.core.models import Run, RunKind, RunQueue, RunStatus

    project = _factory(client, scenarios=True)
    change = _open(client, project, _claimed_task(client, project)).json()
    with db_session() as session:
        from yaaf.core import ids
        from yaaf.core.models import Scenario
        row = session.get(Change, change["id"])
        session.add(Scenario(id=ids.new_id(ids.SCENARIO), project_id=project["id"],
                             requirement_id=row.requirement_id, name="holds",
                             command="true", author_agent_id=project["ceo_agent_id"]))
        session.flush()
        changes.queue_scenarios(session, session.get(Project, project["id"]), row)
        queued = session.query(RunQueue).filter_by(project_id=project["id"],
                                                    kind=RunKind.SCENARIO_CHECK).one()
        # the runner leased it, and the run died without a result
        session.add(Run(id=queued.id, project_id=project["id"], kind=RunKind.SCENARIO_CHECK,
                        trigger=queued.trigger, status=RunStatus.FAILED,
                        started_at=utcnow() - timedelta(minutes=10),
                        finished_at=utcnow() - timedelta(minutes=9)))
        session.delete(queued)
    with db_session() as session:
        assert changes.requeue_lost_jobs(session, session.get(Project, project["id"])) == 1
        assert session.query(RunQueue).filter_by(project_id=project["id"],
                                                 kind=RunKind.SCENARIO_CHECK).count() == 1


def test_a_signature_does_not_erase_another_seats_return(client, gh):
    from tests.conftest import ADMIN, bearer, live_token

    project = _factory(client)
    added = client.post(f"/admin/projects/{project['id']}/teams", headers=ADMIN, json={
        "role_slug": "reviewer", "name": "Security", "employees": ["Sasha"]})
    assert added.status_code == 200, added.text
    sasha = next(a["id"] for a in added.json()["agents"])
    change = _open(client, project, _claimed_task(client, project)).json()
    _green(client, project, change["head_sha"])
    _, token = live_token(project["id"], sasha)
    returned = client.post(f"/agent/changes/{change['id']}/review", headers=bearer(token),
                           json={"verdict": "return", "note": "The hold leaks across tabs."})
    assert returned.status_code == 200, returned.text
    signed = _sign(client, project, change["id"])
    assert signed.status_code == 409 and "Sasha returned this commit" in signed.json()["error"]
    assert _change(change["id"]).status == ChangeStatus.OPEN


def test_a_change_is_not_merged_while_its_ci_is_still_running(client, gh, monkeypatch):
    project, change = _ready(client)
    real = github._request

    def suites(method, path, token, json=None):
        if "/check-suites" in path:
            return httpx.Response(200, json={"check_suites": [
                {"status": "in_progress", "latest_check_runs_count": 1}]})
        return real(method, path, token, json)

    monkeypatch.setattr(github, "_request", suites)
    assert _merge(project) == 0 and gh.merges() == []
    assert "still running" in (_change(change["id"]).status_reason or "")
    monkeypatch.setattr(github, "_request", gh)
    assert _merge(project) == 1


def test_a_requirement_is_judged_after_its_change_lands_not_before(client, gh):
    """The Durations factory: the parser task closed on its builder's checkout,
    R-02 was tested on a main still holding the stub and failed; and once that
    had happened, the merge moved no task, so it would never be tested again."""
    from yaaf.core.constitution import effective
    from yaaf.core.models import Requirement, RequirementStatus, Task, TaskStatus
    from yaaf.server import janitor, policy

    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    with db_session() as session:
        row = session.get(Change, change["id"])
        session.get(Task, row.task_id).status = TaskStatus.DONE
        rid = row.requirement_id
        session.get(Requirement, rid).status = RequirementStatus.IN_PROGRESS

    def sweep():
        with db_session() as session:
            p = session.get(Project, project["id"])
            janitor.check_finished_requirements(session, p, effective(session, p.id))
            return session.get(Requirement, rid).status

    assert sweep() == RequirementStatus.IN_PROGRESS, "its change has not landed"
    with db_session() as session:   # judged once, failed, and then the change merged
        req = session.get(Requirement, rid)
        policy.record_requirement_event(session, req, RequirementStatus.IN_PROGRESS,
                                        "checker", note="FAILED",
                                        from_status=RequirementStatus.CHECKING)
        session.flush()
        row = session.get(Change, change["id"])
        row.status, row.merged_at, row.merged_sha = (ChangeStatus.MERGED,
                                                     utcnow() + timedelta(seconds=5), "m" * 40)
    assert sweep() == RequirementStatus.CHECKING
