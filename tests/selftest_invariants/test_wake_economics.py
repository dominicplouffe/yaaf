"""Invariant selftests: a wake is a full-price run, so idle cadences and the
janitor's notice drip must not buy them — while the chairman's own actions and
real planning material still wake immediately."""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Heartbeat,
    Hypothesis,
    HypothesisStatus,
    Notice,
    NoticeKind,
    RunQueue,
    utcnow,
)
from yaaf.server import dispatcher


def _drain(max_rounds: int = 5) -> None:
    for _ in range(max_rounds):
        with db_session() as session:
            if dispatcher.dispatch_once(session) == 0:
                return
            session.query(RunQueue).delete()
    raise AssertionError("dispatcher would not settle")


def _force_heartbeat_due(agent_id: str) -> None:
    with db_session() as session:
        beat = session.query(Heartbeat).filter_by(agent_id=agent_id, name="planning").one()
        beat.last_fired_at = utcnow() - timedelta(hours=999)


def _add_notice(project: dict, kind: NoticeKind = NoticeKind.AGENT_RUN_REAPED,
                age_minutes: float = 0.0) -> None:
    with db_session() as session:
        session.add(Notice(id=ids.new_id(ids.NOTICE), project_id=project["id"],
                           agent_id=project["ceo_agent_id"], kind=kind, body="x",
                           created_at=utcnow() - timedelta(minutes=age_minutes)))


def test_first_heartbeat_bootstraps_then_idle_cadence_sleeps(client):
    project = make_project(client)
    # The first-ever heartbeat must fire even on an empty project — it is what
    # bootstraps planning. _drain consumes it (and asserts the queue settles).
    _drain()
    # After that: empty board, no bets, nothing unread → a due cadence is
    # skipped instead of buying a run to conclude "nothing to do".
    _force_heartbeat_due(project["ceo_agent_id"])
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0


def test_heartbeat_fires_when_there_is_material(client):
    project = make_project(client)
    _drain()
    with db_session() as session:
        session.add(Hypothesis(id=ids.new_id(ids.HYPOTHESIS), project_id=project["id"],
                               statement="bet", kill_condition="no signups by kill date",
                               kill_date=utcnow() + timedelta(days=7),
                               status=HypothesisStatus.ACTIVE,
                               created_by_agent_id=project["ceo_agent_id"]))
    _force_heartbeat_due(project["ceo_agent_id"])
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def test_heartbeat_always_config_restores_idle_wakes(client):
    project = make_project(client)
    _drain()
    with db_session() as session:
        from yaaf.core.models import Project
        row = session.get(Project, project["id"])
        row.config = {**row.config, "ceo_heartbeat_always": True}
    _force_heartbeat_due(project["ceo_agent_id"])
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def test_notice_drip_coalesces_into_one_wake(client):
    project = make_project(client)
    _drain()
    # One fresh machine-made notice is not yet worth a run.
    _add_notice(project)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0
    # Enough pile up → exactly one wake for the whole batch.
    _add_notice(project)
    _add_notice(project)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def test_stale_notice_wakes_alone(client):
    project = make_project(client)
    _drain()
    _add_notice(project, age_minutes=dispatcher.NOTICE_COALESCE_MINUTES + 1)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def test_urgent_notice_wakes_immediately(client):
    project = make_project(client)
    _drain()
    _add_notice(project, kind=NoticeKind.BUDGET_EXHAUSTED)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def test_full_memory_wakes_now_but_pressure_rides_an_existing_wake(client):
    """The whole token argument for warning about memory: a nearly-full store
    is not worth buying a run for, but a full one has stopped every member of
    the scope from writing and only this agent can restart it."""
    project = make_project(client)
    _drain()
    _add_notice(project, kind=NoticeKind.MEMORY_PRESSURE)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0, "pressure must coalesce, not wake"

    _add_notice(project, kind=NoticeKind.MEMORY_FULL)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def _fill_team(client, project, count):
    from tests.conftest import agent_by_name, bearer, live_token
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    for i in range(count):
        assert client.post(
            "/agent/memory",
            json={"scope": "team", "kind": "lesson", "subject": f"lesson {i}",
                  "body": "something durable we learned the hard way"},
            headers=bearer(lead_token),
        ).status_code == 200


def test_memory_pressure_warns_the_curator_once(client):
    """Warned early, warned once, and warned to the agent that can actually act
    — not to whichever employee happened to hit the wall."""
    from yaaf.core.constitution import effective
    from yaaf.core.models import Agent, Project
    from yaaf.server import janitor

    project = make_project(client, constitution={"memory_cap_team": 4})
    _fill_team(client, project, 3)  # 3/4 = 75%… under the 0.85 ratio
    with db_session() as session:
        row = session.get(Project, project["id"])
        janitor.warn_memory_pressure(session, row, effective(session, row.id))
        assert session.query(Notice).filter_by(kind=NoticeKind.MEMORY_PRESSURE).count() == 0

    _fill_team(client, project, 1)  # 4/4 — full
    for _ in range(3):  # the janitor ticks every 15s; it must not notice thrice
        with db_session() as session:
            row = session.get(Project, project["id"])
            janitor.warn_memory_pressure(session, row, effective(session, row.id))

    with db_session() as session:
        notices = session.query(Notice).filter_by(kind=NoticeKind.MEMORY_FULL).all()
        assert len(notices) == 1
        lead = session.get(Agent, notices[0].agent_id)
        assert lead.name == "Lead", "the curator is told, not the employee that was refused"
        assert "memory_compact" in notices[0].body


def test_leaderless_team_escalates_to_the_ceo(client):
    """A team with no lead has no curator at all: nobody on the platform may
    free a slot. Silence there would be a company quietly unable to remember."""
    from yaaf.core.constitution import effective
    from yaaf.core.models import Project, Team
    from yaaf.server import janitor

    project = make_project(client, constitution={"memory_cap_team": 2})
    _fill_team(client, project, 2)
    with db_session() as session:
        session.get(Team, project["teams"][0]["id"]).lead_agent_id = None

    with db_session() as session:
        row = session.get(Project, project["id"])
        janitor.warn_memory_pressure(session, row, effective(session, row.id))

    with db_session() as session:
        notices = session.query(Notice).filter_by(kind=NoticeKind.MEMORY_FULL).all()
        assert len(notices) == 1
        assert notices[0].agent_id == project["ceo_agent_id"]
        assert "no lead" in notices[0].body
