"""Invariant selftest: a seat's end date is written into the plan and fired by
the platform.

The one moment a CEO knows which roles are for the front half of a project —
the requirements, the build, the validation — is while it is writing the plan.
It is also the moment nobody has ever asked it. Afterwards nothing raises the
question and an idle seat costs quietly, so every roster on this platform was
hired for the first half and kept for the second.

So the answer is recorded against the plan's own objects and the platform acts
on it. A prompt to the CEO would not do: a reminder loses to whatever else is
in the run, which is the failure mode this whole file exists inside.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    AgentStatus,
    Flag,
    FlagKind,
    FlagStatus,
    Notice,
    NoticeKind,
    Requirement,
    RequirementStatus,
    Run,
    RunStatus,
    Task,
    TaskStatus,
    Team,
    utcnow,
)


def _ceo(project: dict) -> str:
    _, token = live_token(project["id"], project["ceo_agent_id"])
    return token


def _hire(client, token: str, name: str = "Build Desk", **extra) -> dict:
    response = client.post("/agent/teams", headers=bearer(token), json={
        "role_slug": "worker", "name": name, "employees": ["Kit"],
        "reason": "The build half needs a pair of hands until the spec is met.",
        **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _requirement(project_id: str, key: str, *, stage: int = 1,
                 status: str = RequirementStatus.PENDING) -> None:
    with db_session() as session:
        session.add(Requirement(
            id=ids.new_id(ids.REQUIREMENT), project_id=project_id, key=key,
            statement=f"Requirement {key} exists and can be settled.",
            acceptance_test={"kind": "observable", "text": "somebody can see it"},
            status=status, stage=stage))


def _settle(project_id: str, key: str) -> None:
    with db_session() as session:
        row = session.query(Requirement).filter(
            Requirement.project_id == project_id, Requirement.key == key).one()
        row.status = RequirementStatus.MET


def _tick(project_id: str) -> None:
    from yaaf.core.constitution import effective
    from yaaf.server.janitor import stand_down_due_teams

    with db_session() as session:
        from yaaf.core.models import Project

        project = session.get(Project, project_id)
        effective(session, project_id)
        stand_down_due_teams(session, project)


def _status(project_id: str, name: str) -> str:
    with db_session() as session:
        return session.query(Agent).filter(
            Agent.project_id == project_id, Agent.name == name).one().status


# ── declaring the condition ─────────────────────────────────────────────────────


def test_a_condition_the_platform_cannot_check_is_refused(client):
    """A date or a sentence would leave the seat running forever while looking
    like it had an end. The refusal has to hand back the two shapes that work,
    or the CEO writes prose again."""
    project = make_project(client)
    token = _ceo(project)
    team = _hire(client, token)

    refused = client.post(f"/agent/teams/{team['id']}/stand_down_when",
                          headers=bearer(token),
                          json={"when": {"kind": "after", "date": "2026-10-01"},
                                "reason": "Done by October."})
    assert refused.status_code == 422
    assert "requirement_met" in refused.text and "stage_cleared" in refused.text


def test_a_condition_can_be_set_at_hire_and_cleared_later(client):
    project = make_project(client)
    token = _ceo(project)
    team = _hire(client, token,
                 stand_down_when={"kind": "requirement_met", "keys": ["r-03"]})
    with db_session() as session:
        # Normalised on the way in, so a lowercase key does not silently never fire.
        assert session.get(Team, team["id"]).stand_down_when == {
            "kind": "requirement_met", "keys": ["R-03"]}

    cleared = client.post(f"/agent/teams/{team['id']}/stand_down_when",
                          headers=bearer(token),
                          json={"when": None, "reason": "Keeping this seat after all."})
    assert cleared.status_code == 200
    assert "no stand-down condition" in cleared.json()["note"]


# ── the platform firing it ──────────────────────────────────────────────────────


def test_the_platform_stands_a_team_down_when_its_condition_comes_true(client):
    project = make_project(client)
    token = _ceo(project)
    _requirement(project["id"], "R-03")
    team = _hire(client, token,
                 stand_down_when={"kind": "requirement_met", "keys": ["R-03"]})

    _tick(project["id"])
    assert _status(project["id"], "Kit") == "active", "not met yet"

    _settle(project["id"], "R-03")
    _tick(project["id"])
    assert _status(project["id"], "Kit") == AgentStatus.STOOD_DOWN
    with db_session() as session:
        assert session.get(Team, team["id"]).stood_down_at is not None


def test_the_ceo_is_told_its_roster_changed_under_it(client):
    """The decision was made when the condition was written; this is the
    receipt, so the next run plans against the roster it actually has rather
    than the one it remembers."""
    project = make_project(client)
    token = _ceo(project)
    _requirement(project["id"], "R-03")
    _hire(client, token, stand_down_when={"kind": "requirement_met", "keys": ["R-03"]})
    _settle(project["id"], "R-03")
    _tick(project["id"])

    with db_session() as session:
        notice = session.query(Notice).filter(
            Notice.project_id == project["id"],
            Notice.kind == NoticeKind.SEAT_STOOD_DOWN).one()
        assert notice.agent_id == project["ceo_agent_id"]
        assert "R-03 are met" in notice.body


def test_a_condition_naming_a_requirement_nobody_wrote_never_fires(client):
    """Otherwise a typo stands a seat down by vacuum — the condition would be
    vacuously satisfied by an empty set on the very first tick."""
    project = make_project(client)
    token = _ceo(project)
    _hire(client, token, stand_down_when={"kind": "requirement_met", "keys": ["R-99"]})
    _tick(project["id"])
    assert _status(project["id"], "Kit") == "active"


def test_a_stage_condition_waits_for_every_requirement_in_it(client):
    project = make_project(client)
    token = _ceo(project)
    _requirement(project["id"], "R-01", stage=1)
    _requirement(project["id"], "R-02", stage=1)
    _hire(client, token, stand_down_when={"kind": "stage_cleared", "stage": 1})

    _settle(project["id"], "R-01")
    _tick(project["id"])
    assert _status(project["id"], "Kit") == "active", "one of two is not the stage"

    _settle(project["id"], "R-02")
    _tick(project["id"])
    assert _status(project["id"], "Kit") == AgentStatus.STOOD_DOWN


def test_scope_the_chairman_cut_settles_the_condition_too(client):
    """A seat waiting on a requirement nobody will ever work would otherwise
    never stand down. Cut scope is as settled as met scope."""
    project = make_project(client)
    token = _ceo(project)
    _requirement(project["id"], "R-04")
    _hire(client, token, stand_down_when={"kind": "requirement_met", "keys": ["R-04"]})
    with db_session() as session:
        session.query(Requirement).filter(
            Requirement.key == "R-04").one().status = RequirementStatus.CUT

    _tick(project["id"])
    assert _status(project["id"], "Kit") == AgentStatus.STOOD_DOWN


def test_a_condition_never_orphans_work_and_is_never_lost_either(client):
    """A team holding work is skipped rather than refused: the condition stays
    true and fires on the tick after the board clears. So a declared stand-down
    can neither strand a claimed task nor quietly fail to happen."""
    project = make_project(client)
    token = _ceo(project)
    _requirement(project["id"], "R-05")
    team = _hire(client, token,
                 stand_down_when={"kind": "requirement_met", "keys": ["R-05"]})
    with db_session() as session:
        session.add(Task(
            id="task_stillopen", project_id=project["id"], team_id=team["id"],
            title="Still open", goal="Work that has not landed yet.",
            success_test={"kind": "observable", "text": "somebody can see it"},
            status=TaskStatus.CLAIMED, created_by_agent_id=project["ceo_agent_id"]))
    _settle(project["id"], "R-05")

    _tick(project["id"])
    assert _status(project["id"], "Kit") == "active", "work would have been orphaned"

    with db_session() as session:
        session.query(Task).filter(Task.id == "task_stillopen").one().status = (
            TaskStatus.DONE)
    _tick(project["id"])
    assert _status(project["id"], "Kit") == AgentStatus.STOOD_DOWN


# ── the flag for seats nobody declared one for ──────────────────────────────────


def _idle_flags(project_id: str) -> list[Flag]:
    with db_session() as session:
        return list(session.query(Flag).filter(
            Flag.project_id == project_id, Flag.kind == FlagKind.SEAT_IDLE,
            Flag.status != FlagStatus.RESOLVED))


def _spend_runs(project_id: str, agent_name: str, n: int) -> None:
    with db_session() as session:
        agent = session.query(Agent).filter(
            Agent.project_id == project_id, Agent.name == agent_name).one()
        for _ in range(n):
            session.add(Run(
                id=ids.new_id(ids.RUN), project_id=project_id, agent_id=agent.id,
                kind="agent_run", trigger={"type": "wake"}, status=RunStatus.DONE,
                created_at=utcnow() - timedelta(hours=1)))


def _evaluate(project_id: str) -> None:
    from yaaf.core.constitution import effective
    from yaaf.core.models import Project
    from yaaf.server.flags import evaluate

    with db_session() as session:
        project = session.get(Project, project_id)
        evaluate(session, project, effective(session, project_id))


def test_a_seat_that_spends_and_closes_nothing_is_flagged(client):
    project = make_project(client)
    token = _ceo(project)
    _hire(client, token)
    _spend_runs(project["id"], "Kit", 6)

    _evaluate(project["id"])
    flags = _idle_flags(project["id"])
    assert len(flags) == 1, [f.summary for f in flags]
    assert "Build Desk (Kit)" in flags[0].summary
    assert "6 runs" in flags[0].summary
    # And it proposes both answers, not just the firing.
    assert "stand it down" in flags[0].summary and "stand_down_when" in flags[0].summary


def test_a_seat_with_work_waiting_is_not_an_idle_seat(client):
    """A team sitting on unclaimed work is a stalled claimant, and the board
    already owns that failure. Telling the CEO to stand down a seat with work
    waiting for it would be the flag proposing the wrong fix confidently."""
    project = make_project(client)
    token = _ceo(project)
    team = _hire(client, token)
    _spend_runs(project["id"], "Kit", 6)
    with db_session() as session:
        session.add(Task(
            id="task_waiting", project_id=project["id"], team_id=team["id"],
            title="Waiting to be claimed", goal="Work sitting on this team's board.",
            success_test={"kind": "observable", "text": "somebody can see it"},
            status=TaskStatus.READY, created_by_agent_id=project["ceo_agent_id"]))

    _evaluate(project["id"])
    assert not _idle_flags(project["id"])


def test_a_seat_that_already_has_an_end_date_is_not_nagged_about_one(client):
    """The CEO answered this question when it planned. Asking again is the
    platform not reading its own record."""
    project = make_project(client)
    token = _ceo(project)
    _requirement(project["id"], "R-06")
    _hire(client, token, stand_down_when={"kind": "requirement_met", "keys": ["R-06"]})
    _spend_runs(project["id"], "Kit", 6)

    _evaluate(project["id"])
    assert not _idle_flags(project["id"])


def test_a_quiet_seat_that_spent_nothing_is_not_flagged(client):
    """Not "has not run recently": a seat waiting behind a start time has not
    run and is not idle, and a flag that cried wolf on that would be muted
    inside a week."""
    project = make_project(client)
    _hire(client, _ceo(project))
    _evaluate(project["id"])
    assert not _idle_flags(project["id"])


def test_the_flag_clears_when_the_seat_closes_something(client):
    project = make_project(client)
    token = _ceo(project)
    team = _hire(client, token)
    _spend_runs(project["id"], "Kit", 6)
    _evaluate(project["id"])
    assert _idle_flags(project["id"])

    with db_session() as session:
        kit = session.query(Agent).filter(Agent.name == "Kit").one()
        session.add(Task(
            id="task_landed", project_id=project["id"], team_id=team["id"],
            title="Landed", goal="Something this seat actually finished.",
            success_test={"kind": "observable", "text": "somebody can see it"},
            status=TaskStatus.DONE, claimed_by_agent_id=kit.id,
            created_by_agent_id=project["ceo_agent_id"]))

    _evaluate(project["id"])
    assert not _idle_flags(project["id"])


def test_the_chairman_sees_the_idle_seat_on_his_own_screen(client):
    project = make_project(client)
    _hire(client, _ceo(project))
    _spend_runs(project["id"], "Kit", 6)
    _evaluate(project["id"])

    view = client.get(f"/observe/projects/{project['id']}/flags", headers=ADMIN).json()
    assert any(f["kind"] == "seat_idle" for f in view["flags"]), view


def test_a_reviewer_that_reviews_is_not_an_idle_seat(client):
    """A reviewer never claims a task — it reads somebody else's and gives a
    verdict. Counting only completions marked every reviewer on the platform
    permanently idle, and then proposed standing down the one seat the platform
    refuses to let a CEO stand down: its last critic. That is an obligation the
    CEO cannot discharge, which is worse than no flag at all.

    Found by running the arithmetic against live projects before shipping it:
    five teams flagged across three of them, and every one was called Coverage
    Review, Independent Read or Red Team."""
    from yaaf.core.models import TaskEvent
    from yaaf.server import policy

    project = make_project(client)
    token = _ceo(project)
    team = _hire(client, token, name="Red Team")
    _spend_runs(project["id"], "Kit", 6)
    _evaluate(project["id"])
    assert _idle_flags(project["id"]), "a seat that has done nothing still flags"

    # Now it gives a verdict on work somebody else claimed: the task leaves
    # REVIEW with this seat as the actor, which is the whole record of a read.
    with db_session() as session:
        kit = session.query(Agent).filter(Agent.name == "Kit").one()
        session.add(Task(
            id="task_reviewed", project_id=project["id"], team_id=team["id"],
            title="Somebody else's work", goal="Written by another seat entirely.",
            success_test={"kind": "observable", "text": "somebody can see it"},
            status=TaskStatus.DONE, created_by_agent_id=project["ceo_agent_id"]))
        session.flush()
        session.add(TaskEvent(
            id=ids.new_id(ids.TASK_EVENT), project_id=project["id"],
            task_id="task_reviewed", from_status=TaskStatus.REVIEW,
            to_status=TaskStatus.DONE, actor=f"agent:{kit.id}",
            note="read it, it holds", at=utcnow()))
        assert policy is not None

    _evaluate(project["id"])
    assert not _idle_flags(project["id"])
