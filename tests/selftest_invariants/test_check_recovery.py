"""Invariant selftests: a lost check can never wedge the company silently.

CHECKING is a promise that the platform will move the row itself, so nothing
else watches it: it wakes nobody, is claimed by nobody, and used to count as
nothing at all. When the check run behind it was lost — reaped mid-flight, or
its queue row purged by a pause — the promise broke silently and permanently.
One project sat exactly this way for 80 minutes: a task in `checking` with
zero failed checks, its requirement still OPEN and gating every later stage,
the screen reading "Nothing waiting. The board is empty" — until the chairman
asked why nothing was happening and the CEO had to dig the wedge out by hand.

Three properties are enforced here:

1. The janitor requeues a lost check itself — for tasks and for requirements —
   idempotently, on the audit record.
2. A check that keeps dying escalates to the CEO once, so the one agent that
   can re-plan around a broken checker knows before the chairman asks.
3. A board holding only CHECKING work reads as work in hand ("verifying"),
   never as an empty board.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import (
    AuditLog,
    Notice,
    NoticeKind,
    Project,
    Requirement,
    RequirementEvent,
    RequirementStatus,
    RunKind,
    RunQueue,
    Task,
    TaskEvent,
    TaskStatus,
    utcnow,
)
from yaaf.server import dispatcher, janitor, policy

CMD_TEST = {"type": "command", "command": "test -s out/report.pdf"}
OBS_TEST = {"type": "observable", "target": "https://example.org/report.pdf loads"}


def _drain(max_rounds: int = 6) -> None:
    for _ in range(max_rounds):
        with db_session() as session:
            if dispatcher.dispatch_once(session) == 0:
                return
            session.query(RunQueue).delete()
    raise AssertionError("dispatcher would not settle")


def _task_in_checking(client, project: dict) -> str:
    """A command-test task completed by its claimant: CHECKING, with the
    platform's check queued."""
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    _, lead = live_token(project["id"], agent_by_name(project, "Lead"))
    task = client.post(
        "/agent/tasks",
        json={"team_id": project["teams"][0]["id"], "title": "Shortlist the models",
              "goal": "and prove it", "success_test": CMD_TEST,
              "produces_external_evidence": True},
        headers=bearer(ceo),
    ).json()
    client.post(f"/agent/tasks/{task['id']}/claim", headers=bearer(lead))
    completed = client.post(
        f"/agent/tasks/{task['id']}/complete",
        json={"evidence": "wrote the shortlist, sources cited"},
        headers=bearer(lead),
    )
    assert completed.json()["status"] == "checking"
    return task["id"]


def _lose_the_check(project_id: str, task_id: str) -> None:
    """The wedge itself: the queue row is gone (a pause purged it, a reaped run
    never wrote a result) and the stay in CHECKING is older than the grace."""
    past = utcnow() - policy.CHECK_LOST_GRACE - timedelta(minutes=1)
    with db_session() as session:
        session.query(RunQueue).filter_by(task_id=task_id).delete()
        for event in session.query(TaskEvent).filter_by(
                task_id=task_id, to_status=TaskStatus.CHECKING):
            event.at = past


def _sweep() -> None:
    with db_session() as session:
        janitor.janitor_once(session)


def _queued_checks(task_id: str) -> int:
    with db_session() as session:
        return session.query(RunQueue).filter_by(
            task_id=task_id, kind=RunKind.SUCCESS_CHECK).count()


def test_a_lost_success_check_is_requeued_and_audited(client):
    project = make_project(client)
    _drain()
    task_id = _task_in_checking(client, project)
    _lose_the_check(project["id"], task_id)

    _sweep()
    assert _queued_checks(task_id) == 1, "the janitor owes the row a new check"
    with db_session() as session:
        assert session.get(Task, task_id).status == TaskStatus.CHECKING
        assert session.query(AuditLog).filter_by(
            action="task.check_requeued", entity_id=task_id).count() == 1

    # Idempotent: a queued check means the row is no longer lost.
    _sweep()
    assert _queued_checks(task_id) == 1
    with db_session() as session:
        assert session.query(AuditLog).filter_by(
            action="task.check_requeued", entity_id=task_id).count() == 1


def test_a_fresh_check_waits_out_its_grace_before_being_called_lost(client):
    """A queue backlog or a busy runner is not a lost check. Within the grace
    the janitor keeps its hands off, so it can never race a healthy check."""
    project = make_project(client)
    _drain()
    task_id = _task_in_checking(client, project)
    with db_session() as session:
        session.query(RunQueue).filter_by(task_id=task_id).delete()

    _sweep()
    assert _queued_checks(task_id) == 0


def test_checks_that_keep_dying_wake_the_ceo_exactly_once(client):
    project = make_project(client)
    _drain()
    task_id = _task_in_checking(client, project)

    def lost_notices(session) -> list[Notice]:
        return [n for n in session.query(Notice).filter_by(
                    kind=NoticeKind.TASK_CHECK_FAILED)
                if "lost" in n.body]

    for attempt in range(1, janitor.LOST_CHECKS_BEFORE_ESCALATION + 2):
        _lose_the_check(project["id"], task_id)
        _sweep()
        with db_session() as session:
            expected = 1 if attempt >= janitor.LOST_CHECKS_BEFORE_ESCALATION else 0
            assert len(lost_notices(session)) == expected, f"attempt {attempt}"

    with db_session() as session:
        notice = lost_notices(session)[0]
        assert "checker" in notice.body
        assert "Do not plan around this task" in notice.body


def test_a_lost_requirement_check_is_requeued(client):
    from tests.selftest_invariants.test_requirements import (
        accept,
        ceo_token,
        deliver_project,
        make_task,
        propose,
    )

    project = deliver_project(client, slug="lost-check")
    token = ceo_token(project)
    proposed = propose(client, token, test=CMD_TEST)
    accept(client, proposed["requirement_id"], project)
    task = make_task(client, token, project, proposed["requirement_id"],
                     success_test=OBS_TEST)
    assert task.status_code == 200, task.text
    _, lead = live_token(project["id"], agent_by_name(project, "Lead"))
    task_id = task.json()["id"]
    client.post(f"/agent/tasks/{task_id}/claim", headers=bearer(lead))
    done = client.post(f"/agent/tasks/{task_id}/complete",
                       json={"evidence": "report written and uploaded"},
                       headers=bearer(lead))
    assert done.json()["status"] == "done"

    # All work closed: the sweep sends the requirement to the platform's checker.
    _sweep()
    rid = proposed["requirement_id"]
    with db_session() as session:
        assert session.get(Requirement, rid).status == RequirementStatus.CHECKING

    # The wedge: the queued acceptance test vanishes and the stay goes stale.
    past = utcnow() - policy.CHECK_LOST_GRACE - timedelta(minutes=1)
    with db_session() as session:
        session.query(RunQueue).filter_by(requirement_id=rid).delete()
        for event in session.query(RequirementEvent).filter_by(
                requirement_id=rid, to_status=RequirementStatus.CHECKING):
            event.at = past

    _sweep()
    with db_session() as session:
        assert session.query(RunQueue).filter_by(
            requirement_id=rid, kind=RunKind.REQUIREMENT_CHECK).count() == 1
        assert session.get(Requirement, rid).status == RequirementStatus.CHECKING
        assert session.query(AuditLog).filter_by(
            action="requirement.check_requeued", entity_id=rid).count() == 1


def test_a_board_of_only_checking_work_reads_as_verifying_not_empty(client):
    """The sentence on the chairman's screen — and the one the CEO's own
    context quotes. It must never read "the board is empty" while finished
    work is with the checker and a stage gate holds everything behind it."""
    project = make_project(client)
    _drain()
    task_id = _task_in_checking(client, project)
    with db_session() as session:
        # The claimant's and CEO's own runs are over; only the check remains.
        from yaaf.core.models import Run, RunStatus

        for run in session.query(Run).filter_by(status=RunStatus.RUNNING):
            run.status = RunStatus.DONE
            run.finished_at = utcnow()

    with db_session() as session:
        row = session.get(Project, project["id"])
        state = dispatcher.activity_state(session, row, effective(session, row.id))
    assert state["blocked_by"] == "checking"
    assert state["waiting"]["checking_tasks"] == 1
    assert state["board"]["checks_lost"] == []
    assert state["brief"]["chip"] == "verifying"
    assert state["brief"]["banner"] is False

    # Once the check is lost, the tile names the wedge and the recovery.
    _lose_the_check(project["id"], task_id)
    with db_session() as session:
        row = session.get(Project, project["id"])
        state = dispatcher.activity_state(session, row, effective(session, row.id))
    assert state["blocked_by"] == "checks_lost"
    assert state["board"]["checks_lost"] == ["Shortlist the models"]
    assert "re-run" in state["brief"]["short"]

    # And the health page carries it too — one reader, one answer.
    health = client.get(f"/observe/projects/{project['id']}/health",
                        headers=ADMIN).json()
    activity = next(c for c in health["checks"] if c["key"] == "activity")
    assert "re-running" in activity["detail"] or "re-run" in activity["detail"]
