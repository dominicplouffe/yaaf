"""Invariant selftests: a wake is a full-price run, so idle cadences, the
janitor's notice drip and agent-to-agent chatter must not buy them — while the
chairman's own actions and real planning material still wake immediately."""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import agent_by_name, channel_of, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Heartbeat,
    Hypothesis,
    HypothesisStatus,
    Message,
    Notice,
    NoticeKind,
    Project,
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


# ── why nothing is happening ────────────────────────────────────────────────────
# The dispatcher decides this ~30x/minute and used to throw it away, so a
# company that had spent its whole daily allowance and one with an empty board
# were indistinguishable on every screen. activity_state is that decision made
# readable; these tests pin it to the enforcer it reports on.


def _activity(project: dict) -> dict:
    from yaaf.core.constitution import effective
    from yaaf.core.models import Project

    with db_session() as session:
        row = session.get(Project, project["id"])
        return dispatcher.activity_state(session, row, effective(session, row.id))


def test_a_capped_out_project_says_so_and_dispatches_nothing(client):
    """The reading and the refusal are the same fact. A blocked_by that
    disagreed with the dispatcher would be worse than showing nothing, because
    the chairman would believe it."""
    project = make_project(client, constitution={"runs_per_project_per_day": 1})
    # One tick spends the day: the bootstrap heartbeat enqueues the single
    # allowed run. Deliberately left queued — the ceiling counts queued and
    # historical runs alike, which is what stops a burst from overshooting it.
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1
    _force_heartbeat_due(project["ceo_agent_id"])
    _add_notice(project, kind=NoticeKind.BUDGET_WARNING)  # urgent: real work waiting

    state = _activity(project)
    assert state["blocked_by"] == "daily_runs"
    assert state["daily_runs"]["used"] >= state["daily_runs"]["cap"]
    assert state["waiting"]["notices"] == 1
    assert state["daily_runs"]["resets_at"].endswith("00:00:00+00:00")
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0


def test_an_empty_board_is_not_reported_as_blocked(client):
    """Idle with nothing waiting is the system working, not a fault. Crying
    'blocked' here is how a needs-you count stops being trusted."""
    project = make_project(client)
    _drain()
    state = _activity(project)
    assert state["blocked_by"] is None
    assert state["waiting"]["agents_with_work"] == 0


def test_work_waiting_with_capacity_is_not_blocked(client):
    project = make_project(client)
    _drain()
    _add_notice(project, kind=NoticeKind.BUDGET_WARNING)
    state = _activity(project)
    assert state["blocked_by"] is None, "capacity is free — a run starts next tick"
    assert state["waiting"]["notices"] == 1
    assert any(a["pending"] for a in state["agents"])


def test_reading_the_state_never_moves_it(client):
    """activity_state runs on every dashboard poll. If it advanced the planning
    heartbeat or froze a thread, opening a page would spend governance."""
    from yaaf.core.models import Heartbeat, Message

    project = make_project(client)
    _drain()
    _force_heartbeat_due(project["ceo_agent_id"])

    def snapshot() -> tuple:
        with db_session() as session:
            beats = [(b.agent_id, b.name, b.last_fired_at)
                     for b in session.query(Heartbeat).order_by(Heartbeat.id).all()]
            frozen = [(m.id, m.frozen_at) for m in session.query(Message)
                      .order_by(Message.id).all()]
            queued = session.query(RunQueue).count()
            return beats, frozen, queued

    before = snapshot()
    _activity(project)
    _activity(project)
    assert snapshot() == before


# ── message wakes ───────────────────────────────────────────────────────────────
# Half of all runs used to start within five minutes of the same agent's prior
# run: every agent-to-agent reply bought a full-price wake. Ordinary chatter now
# batches like notices; the chairman (and the boardroom) still wake instantly.


def _post_message(project: dict, channel_id: str, sender_agent_id: str | None = None,
                  sender_user_id: str | None = None, age_minutes: float = 0.0,
                  body: str = "hi") -> str:
    message_id = ids.new_id(ids.MESSAGE)
    with db_session() as session:
        session.add(Message(id=message_id, project_id=project["id"], channel_id=channel_id,
                            sender_agent_id=sender_agent_id, sender_user_id=sender_user_id,
                            body=body, created_at=utcnow() - timedelta(minutes=age_minutes)))
    return message_id


def test_agent_chatter_coalesces_instead_of_waking(client):
    """One fresh reply from a colleague is not worth a full-price run. Nobody
    in the team room wakes: the recipient batches it, and the sender is never
    woken by its own message."""
    project = make_project(client)
    _drain()
    team_room = channel_of(client, project["id"], "team")
    _post_message(project, team_room, sender_agent_id=agent_by_name(project, "Lead"))
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0


def test_stale_agent_message_wakes_the_recipient_alone(client):
    """The same message, once it has waited out the window, buys exactly one
    wake — for the recipient, never the sender."""
    project = make_project(client)
    _drain()
    team_room = channel_of(client, project["id"], "team")
    message_id = _post_message(project, team_room,
                               sender_agent_id=agent_by_name(project, "Lead"))
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0
    with db_session() as session:
        session.get(Message, message_id).created_at = utcnow() - timedelta(
            minutes=dispatcher.MESSAGE_COALESCE_MINUTES + 1)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def test_message_pileup_coalesces_into_one_wake(client):
    """Enough fresh chatter piles up → one wake for the whole batch."""
    project = make_project(client)
    _drain()
    team_room = channel_of(client, project["id"], "team")
    lead = agent_by_name(project, "Lead")
    for i in range(dispatcher.MESSAGE_COALESCE_COUNT):
        _post_message(project, team_room, sender_agent_id=lead, body=f"note {i}")
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def test_chairman_message_wakes_the_ceo_instantly(client):
    """The chairman is not chatter: one fresh word from him wakes the CEO on
    the next tick, exactly as before coalescing existed."""
    project = make_project(client)
    _drain()
    boardroom = channel_of(client, project["id"], "boardroom")
    with db_session() as session:
        chairman = session.get(Project, project["id"]).chairman_user_id
    _post_message(project, boardroom, sender_user_id=chairman)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def test_leadership_chatter_coalesces_for_the_ceo_too(client):
    """The CEO gets no special treatment as a recipient — only the channel and
    the sender confer urgency. (An agent-sent boardroom message that another
    agent could read is unconstructible: the boardroom's only agent member is
    the CEO, and a sender never wakes for its own message — so the CEO-side
    path is pinned here in leadership instead.) Fresh chatter from a lead
    batches; aged, it wakes the CEO alone."""
    project = make_project(client)
    _drain()
    leadership = channel_of(client, project["id"], "leadership")
    message_id = _post_message(project, leadership,
                               sender_agent_id=agent_by_name(project, "Lead"))
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0
    with db_session() as session:
        session.get(Message, message_id).created_at = utcnow() - timedelta(
            minutes=dispatcher.MESSAGE_COALESCE_MINUTES + 1)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1
        queued = session.query(RunQueue).one()
        assert queued.agent_id == project["ceo_agent_id"]
