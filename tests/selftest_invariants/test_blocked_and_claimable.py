"""Invariant selftests: a blocked task has a way back, and no task is filed
that nobody could ever claim, or judged by a test that can never pass.

28 November 2026, Snip: of 25 killed tasks, six were killed only to be filed
again because their blocker had cleared and a blocked task could not be
claimed or unblocked; four were assignments a lead alone on its team gave
itself, which the claim rule (the author of a test never claims the work it
judges) then refused; one was a test that ran `git fetch` in a check with no
GitHub key.

Attacked here:

  * a task blocked on another task or a change is back on the board when that
    finishes, and its planner hears when it ended the other way
  * the planner can unblock a task directly; nobody else can
  * an assignment to yourself, or a team task only you could claim, is refused
    when it is filed rather than when it is claimed
  * a success test that reaches a git remote is refused
"""

from __future__ import annotations

from tests.conftest import agent_by_name, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Notice, NoticeKind, Task, TaskStatus
from yaaf.server import janitor

CMD = {"type": "command", "command": "test -s out/report.pdf"}


def _tokens(project: dict) -> tuple[str, str, str]:
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    _, lead = live_token(project["id"], agent_by_name(project, "Lead"))
    _, emp = live_token(project["id"], agent_by_name(project, "Emp2"))
    return ceo, lead, emp


def _task(client, project: dict, ceo: str, title: str, test: dict = CMD):
    return client.post("/agent/tasks", json={
        "team_id": project["teams"][0]["id"], "title": title, "goal": "and prove it",
        "success_test": test, "produces_external_evidence": True}, headers=bearer(ceo))


def _status(task_id: str) -> str:
    with db_session() as session:
        return session.get(Task, task_id).status


def _sweep(project: dict) -> int:
    with db_session() as session:
        from yaaf.core.models import Project
        return janitor.release_finished_waits(session, session.get(Project, project["id"]))


def test_a_task_blocked_on_another_is_back_on_the_board_when_that_is_done(client):
    project = make_project(client, slug="waitdone")
    ceo, lead, _emp = _tokens(project)
    first = _task(client, project, ceo, "Build the API").json()["id"]
    later = _task(client, project, ceo, "Build the page that calls it").json()["id"]
    assert client.post(f"/agent/tasks/{later}/claim", headers=bearer(lead)).status_code == 200
    blocked = client.post(f"/agent/tasks/{later}/block", headers=bearer(lead), json={
        "reason": "The page needs the API, which is the other task.",
        "waits_on_task": first})
    assert blocked.status_code == 200, blocked.text
    assert _sweep(project) == 0 and _status(later) == TaskStatus.BLOCKED

    with db_session() as session:
        session.get(Task, first).status = TaskStatus.DONE
    assert _sweep(project) == 1
    assert _status(later) == TaskStatus.READY
    assert client.post(f"/agent/tasks/{later}/claim", headers=bearer(lead)).status_code == 200


def test_a_wait_that_ends_the_other_way_tells_the_planner_once(client):
    project = make_project(client, slug="waitgone")
    ceo, lead, _emp = _tokens(project)
    first = _task(client, project, ceo, "Build the API").json()["id"]
    later = _task(client, project, ceo, "Build the page").json()["id"]
    client.post(f"/agent/tasks/{later}/claim", headers=bearer(lead))
    client.post(f"/agent/tasks/{later}/block", headers=bearer(lead), json={
        "reason": "The page needs the API task first.", "waits_on_task": first})
    with db_session() as session:
        session.get(Task, first).status = TaskStatus.KILLED
    _sweep(project)
    _sweep(project)
    assert _status(later) == TaskStatus.BLOCKED
    with db_session() as session:
        told = session.query(Notice).filter(
            Notice.agent_id == project["ceo_agent_id"], Notice.kind == NoticeKind.TASK_BLOCKED,
            Notice.body.contains("will not unblock by itself")).count()
    assert told == 1


def test_the_planner_unblocks_a_task_and_nobody_else_does(client):
    project = make_project(client, slug="unblock")
    ceo, lead, _emp = _tokens(project)
    task = _task(client, project, ceo, "Ship the thing").json()["id"]
    client.post(f"/agent/tasks/{task}/claim", headers=bearer(lead))
    client.post(f"/agent/tasks/{task}/block", headers=bearer(lead),
                json={"reason": "Waiting on the chairman's key."})
    reason = {"reason": "The chairman added the key this morning."}
    assert client.post(f"/agent/tasks/{task}/unblock", headers=bearer(lead),
                       json=reason).status_code == 403
    done = client.post(f"/agent/tasks/{task}/unblock", headers=bearer(ceo), json=reason)
    assert done.status_code == 200, done.text
    assert _status(task) == TaskStatus.READY
    assert client.post(f"/agent/tasks/{task}/unblock", headers=bearer(ceo),
                       json=reason).status_code == 409, "only a blocked task"


def test_an_assignment_to_yourself_is_refused_when_it_is_filed(client):
    project = make_project(client, slug="selfassign")
    ceo, lead, _emp = _tokens(project)
    parent = _task(client, project, ceo, "The whole page").json()["id"]
    client.post(f"/agent/tasks/{parent}/claim", headers=bearer(lead))
    body = {"team_id": project["teams"][0]["id"], "parent_task_id": parent,
            "title": "The header", "goal": "and prove it", "success_test": CMD,
            "produces_external_evidence": True}
    mine = client.post("/agent/tasks", headers=bearer(lead),
                       json={**body, "assignee_agent_id": agent_by_name(project, "Lead")})
    assert mine.status_code == 422 and "test you wrote" in mine.text
    theirs = client.post("/agent/tasks", headers=bearer(lead),
                         json={**body, "assignee_agent_id": agent_by_name(project, "Emp2")})
    assert theirs.status_code == 200, theirs.text


def test_a_test_that_reaches_a_git_remote_is_refused(client):
    project = make_project(client, slug="gitfetch")
    ceo, _lead, _emp = _tokens(project)
    for command in ("git fetch origin && npm test", "git -C /workspace pull --ff-only",
                    "bash -c 'git clone https://github.com/a/b x && test -f x/y'"):
        refused = _task(client, project, ceo, "Re-run it",
                        {"type": "command", "command": command})
        assert refused.status_code == 422 and "no GitHub" in refused.text, command
    fine = _task(client, project, ceo, "Read the log",
                 {"type": "command", "command": "git log -1 --format=%H | grep -q ."})
    assert fine.status_code == 200, fine.text
