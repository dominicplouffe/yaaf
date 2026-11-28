"""Invariant selftests: a Code Factory's requirement is judged on main, in a
checkout made for that one check, and says which commit it judged.

27 November 2026, Snip: requirement checks ran in the project's shared tree,
brought to main first unless it "held uncommitted edits". R-12's browser test
rewrites sixteen screenshots the repo tracks, so after its first check the tree
always held edits and was never moved again. For fourteen hours every
acceptance test judged a commit from the night before: five requirements red
with "No tests found" while main passed them, two marked met on code main no
longer had, and the crew spent 41 runs proving main was fine, because nothing
they could read said which commit the platform had judged.

Attacked here:

  * the check runs on main's latest commit, whatever the shared tree holds
  * what a test writes never reaches the next check, and the checkout is gone
  * every verdict names the commit it judged
  * a commit a test already failed at is not run again, and not counted again
  * a check that cannot fetch main is no verdict, and the chairman is told
  * any merge re-opens a red command requirement for judging
  * a seat that cannot have its own checkout does not run in the shared tree
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from tests.conftest import make_project
from tests.selftest_invariants.test_github import (
    _git,
    _install_platform_secret,
    _push_to_main,
    _seed_bare_repo,
    _set_github_repo,
)
from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.db import db_session
from yaaf.core.models import (
    AuditLog,
    Change,
    ChangeStatus,
    Project,
    Requirement,
    RequirementEvent,
    RequirementStatus,
    Run,
    RunKind,
    RunQueue,
    RunStatus,
    Task,
    TaskStatus,
    utcnow,
)
from yaaf.runner import daemon
from yaaf.server import github, janitor


def _factory(client, monkeypatch, tmp_path, slug: str) -> tuple[dict, Path]:
    project = make_project(client, slug=slug, mode="code_factory")
    _install_platform_secret(client)
    bare = _seed_bare_repo(tmp_path)
    monkeypatch.setattr(github, "clone_url", lambda name, token: f"file://{bare}")
    _set_github_repo(project["id"], f"acme/{slug}")
    with db_session() as session:
        daemon._prepare_workspace(session, session.get(Project, project["id"]))
    return project, bare


def _requirement(project: dict, command: str) -> str:
    with db_session() as session:
        row = Requirement(id=ids.new_id(ids.REQUIREMENT), project_id=project["id"],
                          key="R-01", statement="The thing works.",
                          acceptance_test={"type": "command", "command": command},
                          status=RequirementStatus.CHECKING, stage=1, created_at=utcnow())
        session.add(row)
        return row.id


def _judge(project: dict, requirement_id: str):
    """Queue, materialize, run and finalize one requirement check."""
    with db_session() as session:
        row = session.get(Project, project["id"])
        session.get(Requirement, requirement_id).status = RequirementStatus.CHECKING
        queued = RunQueue(id=ids.new_id(ids.RUN), project_id=row.id,
                          kind=RunKind.REQUIREMENT_CHECK,
                          trigger={"type": "requirement_check"},
                          requirement_id=requirement_id)
        session.add(queued)
        session.flush()
        job = daemon._materialize(session, queued, row)
    assert job is not None
    result = daemon._run_check_command(job)
    daemon.finalize(job, result)
    with db_session() as session:
        req = session.get(Requirement, requirement_id)
        last = session.execute(
            select(RequirementEvent).where(RequirementEvent.requirement_id == requirement_id)
            .order_by(RequirementEvent.at.desc())).scalars().first()
        return job, result, req.status, req.failure_load, req.check_output or "", \
            (last.note if last else "")


def _shared(project: dict) -> Path:
    with db_session() as session:
        return Path(settings().workspaces_root) / session.get(Project, project["id"]).slug


def test_a_requirement_is_judged_on_main_whatever_the_shared_tree_holds(
        client, monkeypatch, tmp_path):
    project, bare = _factory(client, monkeypatch, tmp_path, "freshmain")
    shared = _shared(project)
    (shared / "README.md").write_text("a stray edit, as a test that rewrites tracked files "
                                      "leaves behind\n")
    _push_to_main(tmp_path, bare, "ci.yml")
    requirement = _requirement(project, "test -f ci.yml")
    job, _result, status, _load, output, note = _judge(project, requirement)
    sha = _git(bare, "rev-parse", "HEAD")
    assert status == RequirementStatus.MET, output
    assert output.startswith(f"judged main at {sha[:7]}"), output
    assert sha[:7] in note, "the requirement's history names the commit it judged"
    assert not Path(job.workspace_dir).exists(), "the check's checkout is removed after"
    assert job.workspace_dir.startswith(str(Path(settings().workspaces_root) / ".checks"))
    assert (shared / "ci.yml").exists() and "stray" not in (shared / "README.md").read_text(), \
        "the shared tree is a mirror of main: what was left in it is gone"


def test_what_one_check_writes_never_reaches_the_next(client, monkeypatch, tmp_path):
    project, bare = _factory(client, monkeypatch, tmp_path, "freshwrite")
    requirement = _requirement(
        project, "bash -c 'echo rewritten > README.md; test -f later.txt'")
    _job, _r, status, load, output, note = _judge(project, requirement)
    assert status == RequirementStatus.IN_PROGRESS and load == 1
    assert "FAILED" in note and _git(bare, "rev-parse", "HEAD")[:7] in note

    _push_to_main(tmp_path, bare, "later.txt")
    _job, _r, status, _load, output, _note = _judge(project, requirement)
    assert status == RequirementStatus.MET, \
        f"the first check's README edit stopped nothing moving: {output}"


def test_a_commit_already_failed_is_not_run_or_counted_again(client, monkeypatch, tmp_path):
    project, bare = _factory(client, monkeypatch, tmp_path, "freshsame")
    marker = tmp_path / "ran"
    requirement = _requirement(project, f"bash -c 'echo x >> {marker}; false'")
    _judge(project, requirement)
    with db_session() as session:
        assert session.get(Requirement, requirement).failed_at_sha == \
            _git(bare, "rev-parse", "HEAD")

    _job, result, status, load, _out, note = _judge(project, requirement)
    assert result.extra.get("unchanged")
    assert status == RequirementStatus.IN_PROGRESS and load == 1, "not a second failure"
    assert "not run again" in note
    assert marker.read_text().count("x") == 1, "the test itself did not run"

    _push_to_main(tmp_path, bare, "fix.txt")
    _judge(project, requirement)
    assert marker.read_text().count("x") == 2, "a new commit is judged"


def test_a_check_that_cannot_fetch_main_is_no_verdict_and_the_chairman_hears(
        client, monkeypatch, tmp_path):
    project, _bare = _factory(client, monkeypatch, tmp_path, "freshnofetch")
    monkeypatch.setattr(github, "clone_url",
                        lambda name, token: f"file://{tmp_path}/no-such-repo.git")
    requirement = _requirement(project, "true")
    sent = []
    from yaaf.server import notify
    monkeypatch.setattr(notify, "chairman_notify", lambda *a, **k: sent.append(a[2]))
    for _ in range(2):
        _job, _r, status, load, _out, _note = _judge(project, requirement)
        assert status == RequirementStatus.CHECKING and load == 0, \
            "the platform failing is not the work failing"
    assert len(sent) == 1, "told once, not once per retry"
    with db_session() as session:
        assert session.execute(select(AuditLog).where(
            AuditLog.action == "requirement.unjudgeable")).scalars().all()


def test_any_merge_reopens_a_red_command_requirement_for_judging(client, monkeypatch,
                                                                 tmp_path):
    project, _bare = _factory(client, monkeypatch, tmp_path, "freshmerge")
    requirement = _requirement(project, "false")
    _judge(project, requirement)
    with db_session() as session:
        req = session.get(Requirement, requirement)
        task = Task(id=ids.new_id(ids.TASK), project_id=project["id"],
                    team_id=project["teams"][0]["id"], title="Built it", goal="It works.",
                    success_test={"type": "command", "command": "true"},
                    status=TaskStatus.DONE, requirement_id=requirement,
                    created_by_agent_id=project["ceo_agent_id"],
                    updated_at=utcnow().replace(year=2020))
        session.add(task)
        session.flush()
        assert not janitor._work_closed_since_last_check(session, req, [task])
        session.add(Change(id=ids.new_id(ids.CHANGE), project_id=project["id"],
                           task_id=task.id, title="Somebody else's work", branch="other",
                           status=ChangeStatus.MERGED, merged_at=utcnow(),
                           author_agent_id=project["ceo_agent_id"]))
        session.flush()
        assert janitor._work_closed_since_last_check(session, req, [task]), \
            "main moved, so the test has something new to judge"


def test_a_seat_without_its_own_checkout_does_not_run_in_the_shared_tree(
        client, monkeypatch, tmp_path):
    project, _bare = _factory(client, monkeypatch, tmp_path, "freshnoseat")
    monkeypatch.setattr(daemon, "_seat_workspace", lambda *a, **k: None)
    sent = []
    from yaaf.server import notify
    monkeypatch.setattr(notify, "chairman_notify", lambda *a, **k: sent.append(a[2]))
    builder = project["teams"][0]["agents"][0]["id"]
    with db_session() as session:
        row = session.get(Project, project["id"])
        queued = RunQueue(id=ids.new_id(ids.RUN), project_id=row.id, agent_id=builder,
                          kind=RunKind.AGENT_RUN, trigger={"type": "heartbeat"})
        session.add(queued)
        session.flush()
        assert daemon._materialize(session, queued, row) is None
        run = session.get(Run, queued.id)
        assert run.status == RunStatus.FAILED and "own checkout" in run.summary
    assert sent, "the chairman hears that no run can start"


def test_the_ceo_asks_for_a_recheck_only_where_the_answer_can_change(
        client, monkeypatch, tmp_path):
    from tests.conftest import bearer, live_token

    project, _bare = _factory(client, monkeypatch, tmp_path, "freshrecheck")
    requirement = _requirement(project, "false")
    _judge(project, requirement)
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    seat = project["teams"][0]["agents"][0]["id"]
    _, builder = live_token(project["id"], seat)
    ask = {"reason": "Main has had the spec since change #34 merged."}
    url = "/agent/requirements/R-01/recheck"

    assert client.post(url, json=ask, headers=bearer(builder)).status_code == 403
    same = client.post(url, json=ask, headers=bearer(ceo))
    assert same.status_code == 409 and "already failed at" in same.text, same.text

    with db_session() as session:
        task = Task(id=ids.new_id(ids.TASK), project_id=project["id"],
                    team_id=project["teams"][0]["id"], title="Spec", goal="It works.",
                    success_test={"type": "command", "command": "true"},
                    status=TaskStatus.DONE, requirement_id=requirement,
                    created_by_agent_id=project["ceo_agent_id"])
        session.add(task)
        session.flush()
        session.add(Change(id=ids.new_id(ids.CHANGE), project_id=project["id"],
                           task_id=task.id, title="The spec", branch="spec",
                           status=ChangeStatus.MERGED, merged_at=utcnow(),
                           author_agent_id=seat))
        before = session.query(RunQueue).filter_by(requirement_id=requirement).count()
    queued = client.post(url, json=ask, headers=bearer(ceo))
    assert queued.status_code == 200, queued.text
    with db_session() as session:
        assert session.get(Requirement, requirement).status == RequirementStatus.CHECKING
        assert session.query(RunQueue).filter_by(
            requirement_id=requirement, kind=RunKind.REQUIREMENT_CHECK).count() == before + 1
    again = client.post(url, json=ask, headers=bearer(ceo))
    assert again.status_code == 409, "it is being judged now"


def test_a_rejudged_requirement_that_still_passes_keeps_its_day_and_is_quiet(
        client, monkeypatch, tmp_path):
    from yaaf.core.models import Notice

    project, _bare = _factory(client, monkeypatch, tmp_path, "freshrejudge")
    requirement = _requirement(project, "true")
    _judge(project, requirement)
    with db_session() as session:
        first = session.get(Requirement, requirement).met_at
        notices = session.query(Notice).filter_by(project_id=project["id"]).count()
        row = session.get(Project, project["id"])
        session.get(Requirement, requirement).status = RequirementStatus.CHECKING
        queued = RunQueue(id=ids.new_id(ids.RUN), project_id=row.id,
                          kind=RunKind.REQUIREMENT_CHECK,
                          trigger={"type": "requirement_check", "rejudge": True},
                          requirement_id=requirement)
        session.add(queued)
        session.flush()
        job = daemon._materialize(session, queued, row)
    daemon.finalize(job, daemon._run_check_command(job))
    with db_session() as session:
        again = session.get(Requirement, requirement)
        assert again.status == RequirementStatus.MET and again.met_at == first
        assert session.query(Notice).filter_by(project_id=project["id"]).count() == notices
