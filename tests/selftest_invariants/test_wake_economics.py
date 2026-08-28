"""Invariant selftests: a wake is a full-price run, so idle cadences, the
janitor's notice drip and agent-to-agent chatter must not buy them — while the
chairman's own actions and real planning material still wake immediately."""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import agent_by_name, amend, channel_of, make_project
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
    amend(client, project["id"], ceo_heartbeat_always=True)
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
    free a slot. Silence there would be a project quietly unable to remember."""
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
# project that had spent its whole daily allowance and one with an empty board
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


# ── when the throttle lifts ─────────────────────────────────────────────────────
# The hourly ceiling is a trailing window per agent, not a clock hour, so the
# banner that reported it ("work resumes as the hour rolls forward") named no
# moment anyone could check. A warning with nothing falsifiable in it is
# indistinguishable from a stuck one — which is how a self-clearing throttle got
# reported as an error that would not go away. activity_state now names the
# instant, and these tests hold that promise to the dispatcher that enforces it.


def _burn_wakes(project: dict, agent_id: str, ages_minutes: list[float]) -> None:
    """Finished runs at the given ages, all inside the trailing hour."""
    from yaaf.core.models import Run, RunKind, RunStatus

    with db_session() as session:
        for age in ages_minutes:
            at = utcnow() - timedelta(minutes=age)
            session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"],
                            agent_id=agent_id, kind=RunKind.AGENT_RUN,
                            status=RunStatus.DONE, trigger={"type": "wake"},
                            created_at=at, started_at=at, finished_at=at))


def test_the_wake_ceiling_names_the_minute_it_lifts(client):
    """A throttle that clears itself has to say when. The moment is the oldest
    wake in the window plus an hour — the instant `_wakes_last_hour` drops back
    under the cap — and it is reported to the minute, not as 'soon'."""
    # Cap 3, three wakes in the window, the oldest 50 minutes old.
    project = make_project(client, constitution={"wakes_per_agent_per_hour": 3})
    _drain()
    ceo = project["ceo_agent_id"]
    _burn_wakes(project, ceo, [50.0, 20.0, 5.0])
    _force_heartbeat_due(ceo)
    _add_notice(project, kind=NoticeKind.BUDGET_WARNING)  # real work waiting

    state = _activity(project)
    assert state["blocked_by"] == "wake_ceiling"
    assert state["wake_ceiling"]["cap"] == 3
    assert state["wake_ceiling"]["agents_held"] >= 1

    frees_at = state["wake_ceiling"]["frees_at"]
    assert frees_at is not None, "a ceiling that cannot say when it lifts reads as permanent"
    from datetime import datetime
    minutes_out = (datetime.fromisoformat(frees_at) - utcnow()).total_seconds() / 60
    assert 9 < minutes_out < 11, f"oldest wake was 50m ago under a 1h window: {minutes_out}"


def test_the_moment_it_names_is_the_moment_the_dispatcher_agrees(client):
    """The banner's promise and the enforcer's behaviour are the same fact. If
    the stated moment arrives and nothing dispatches, the chairman has been
    told to wait for something that was never going to happen."""
    project = make_project(client, constitution={"wakes_per_agent_per_hour": 2})
    _drain()
    ceo = project["ceo_agent_id"]
    _burn_wakes(project, ceo, [55.0, 30.0])
    _force_heartbeat_due(ceo)
    _add_notice(project, kind=NoticeKind.BUDGET_WARNING)

    assert _activity(project)["blocked_by"] == "wake_ceiling"
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0, "the ceiling must actually be shut"

    # Travel to the stated moment by aging the wakes past it: the oldest falls
    # out of the trailing hour and the slot the reading promised opens.
    from yaaf.core.models import Run

    with db_session() as session:
        for run in session.query(Run).filter(Run.agent_id == ceo).all():
            run.created_at = run.created_at - timedelta(minutes=6)

    state = _activity(project)
    assert state["blocked_by"] is None, "the reading must clear on its own"
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def test_an_idle_agent_parked_at_its_cap_is_not_reported_as_a_throttle(client):
    """The ceiling only counts as the blocker when it is holding work back. An
    agent that burned its wakes and now has nothing to do is not being
    throttled, and a banner that said so would fire on a healthy project."""
    project = make_project(client, constitution={"wakes_per_agent_per_hour": 2})
    _drain()
    ceo = project["ceo_agent_id"]
    _burn_wakes(project, ceo, [40.0, 10.0])  # at the cap, nothing pending

    state = _activity(project)
    assert state["blocked_by"] is None
    assert state["wake_ceiling"]["frees_at"] is None
    assert state["wake_ceiling"]["agents_held"] == 0


def test_the_resident_chat_session_never_counts_against_the_ceiling(client):
    """A chat session is presence, not a wake — it is exempt from the timeout
    and from the hourly brake alike. Counting it would throttle the project for
    holding a conversation."""
    from yaaf.core.models import Run, RunKind, RunStatus

    project = make_project(client, constitution={"wakes_per_agent_per_hour": 1})
    _drain()
    ceo = project["ceo_agent_id"]
    with db_session() as session:
        at = utcnow() - timedelta(minutes=5)
        session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"], agent_id=ceo,
                        kind=RunKind.CHAT_SESSION, status=RunStatus.RUNNING,
                        trigger={"type": "chat_session"}, created_at=at, started_at=at,
                        heartbeat_at=utcnow()))
    _force_heartbeat_due(ceo)
    _add_notice(project, kind=NoticeKind.BUDGET_WARNING)

    state = _activity(project)
    assert state["blocked_by"] is None, "a live conversation is not a throttle"
    assert state["wake_ceiling"]["frees_at"] is None
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def _release_claims(project) -> int:
    """One janitor pass of release_dead_claims, at the project's own rules."""
    from yaaf.core.constitution import effective
    from yaaf.server.janitor import release_dead_claims

    with db_session() as session:
        row = session.get(Project, project["id"])
        return release_dead_claims(session, row, effective(session, project["id"]))


def _claimed_task(project, agent_id: str, claimed_ago, updated_ago):
    """A task claimed `claimed_ago` back, last written to `updated_ago` back."""
    from yaaf.core.models import Task, TaskStatus

    with db_session() as session:
        task = Task(
            id=ids.new_id(ids.TASK), project_id=project["id"],
            team_id=project["teams"][0]["id"], created_by_agent_id=project["ceo_agent_id"],
            title="a task being actively worked",
            goal="prove the janitor can tell work from a crash",
            success_test={"type": "command", "command": "echo checking something real"},
            status=TaskStatus.CLAIMED, claimed_by_agent_id=agent_id,
            claimed_at=utcnow() - claimed_ago, updated_at=utcnow() - updated_ago,
        )
        session.add(task)
        session.flush()
        return task.id


def test_janitor_does_not_release_a_claim_that_is_being_worked(client):
    """The regression that cost 22 hours.

    `release_dead_claims` exists to free a claim whose run died. It used to age
    off `claimed_at`, which is stamped once at claim time and never refreshed —
    so it measured nothing about liveness, and the wake throttle *guarantees*
    long stretches with no run in flight. A claimant failing its success test
    every twenty minutes had its task released to READY on the one-hour mark,
    eight times, each release re-opening the task as fresh team work."""
    from yaaf.core.models import Task

    project = make_project(client, constitution={"run_timeout_s": 60})
    lead = agent_by_name(project, "Lead")
    # Claimed three hours ago — far past 2x the timeout — but written to
    # seconds ago, and with no run RUNNING, exactly as the throttle leaves it.
    task_id = _claimed_task(project, lead, claimed_ago=timedelta(hours=3),
                            updated_ago=timedelta(seconds=5))

    assert _release_claims(project) == 0, "an actively-worked claim is not a dead claim"
    with db_session() as session:
        assert session.get(Task, task_id).status == "claimed"


def test_janitor_still_releases_a_genuinely_abandoned_claim(client):
    """The other half: the original purpose must survive the fix."""
    from yaaf.core.models import Task

    project = make_project(client, constitution={"run_timeout_s": 60})
    lead = agent_by_name(project, "Lead")
    task_id = _claimed_task(project, lead, claimed_ago=timedelta(hours=3),
                            updated_ago=timedelta(hours=3))

    assert _release_claims(project) == 1
    with db_session() as session:
        row = session.get(Task, task_id)
        assert row.status == "ready"
        assert row.claimed_by_agent_id is None
# ── the pacing brake ────────────────────────────────────────────────────────
#
# The daily ceiling is a fixed window with no rate limit, which is how a live
# project spent 55 of 55 runs in 82 minutes and then sat dead for 22 hours.
# Max burn is wakes_per_agent_per_hour x agent count, so org size silently
# multiplies one brake against the other. These pin the brake that relates them.


def test_the_hourly_brake_stops_a_burst_the_daily_ceiling_would_allow(client):
    """The whole point: runs left in the day, and still nothing starts."""
    project = make_project(client, constitution={"runs_per_project_per_day": 100,
                                                 "runs_per_project_per_hour": 1})
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1
    _force_heartbeat_due(project["ceo_agent_id"])
    _add_notice(project, kind=NoticeKind.BUDGET_WARNING)

    state = _activity(project)
    assert state["blocked_by"] == "pacing", "the day is nowhere near spent"
    assert state["daily_runs"]["used"] < state["daily_runs"]["cap"]
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0


def test_the_daily_ceiling_still_outranks_pacing(client):
    """Both gates shut at once: the reading names the one that needs the
    chairman, not the one that clears itself in an hour."""
    project = make_project(client, constitution={"runs_per_project_per_day": 1,
                                                 "runs_per_project_per_hour": 1})
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1
    _force_heartbeat_due(project["ceo_agent_id"])
    _add_notice(project, kind=NoticeKind.BUDGET_WARNING)
    assert _activity(project)["blocked_by"] == "daily_runs"


def test_pacing_names_the_minute_it_lifts(client):
    """A trailing window names no clock moment on its own, and a throttle that
    cannot say when it clears gets read as a stuck error — the same reason
    _wake_slot_frees_at exists."""
    project = make_project(client, constitution={"runs_per_project_per_day": 100,
                                                 "runs_per_project_per_hour": 1})
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1
    _force_heartbeat_due(project["ceo_agent_id"])
    _add_notice(project, kind=NoticeKind.BUDGET_WARNING)

    pacing = _activity(project)["pacing"]
    assert pacing["used"] >= pacing["cap"]
    assert pacing["frees_at"], "must name the moment, not 'as the hour advances'"


def test_pacing_is_a_trailing_window_not_a_clock_hour(client):
    """Backdate the run past the hour and the brake lifts without anything
    resetting — that is the difference from the daily ceiling."""
    project = make_project(client, constitution={"runs_per_project_per_day": 100,
                                                 "runs_per_project_per_hour": 1})
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1
    assert _activity(project)["blocked_by"] == "pacing"

    with db_session() as session:
        for row in session.query(RunQueue).filter(RunQueue.project_id == project["id"]):
            row.created_at = utcnow() - timedelta(hours=2)
    _force_heartbeat_due(project["ceo_agent_id"])
    _add_notice(project, kind=NoticeKind.BUDGET_WARNING)

    state = _activity(project)
    assert state["pacing"]["used"] == 0
    assert state["blocked_by"] != "pacing"


def test_the_ceo_is_told_the_hourly_ration(client):
    """It cannot explain a stop it was never told about — the same gap that
    had a CEO apologising for a cap instead of naming it."""
    from yaaf.core.constitution import effective
    from yaaf.server.contextpack import constitution_prose

    project = make_project(client, constitution={"runs_per_project_per_hour": 9})
    with db_session() as session:
        row = session.get(Project, project["id"])
        prose = constitution_prose(effective(session, row.id), row)
    assert "9 in any trailing hour" in prose


# ── the per-run spend ceiling ───────────────────────────────────────────────


def test_a_run_carries_the_projects_per_run_spend_ceiling(client):
    """Nothing bounded a single run's cost. One task spent $8.05 across 53 of
    them and every individual run looked ordinary — the ceiling has to reach
    the sandbox, or it is a number in a table."""
    from yaaf.core.constitution import effective
    from yaaf.runner import daemon

    project = make_project(client, constitution={"max_usd_per_run": 1.25})
    with db_session() as session:
        row = session.get(Project, project["id"])
        assert effective(session, row.id).max_usd_per_run == 1.25

    with db_session() as session:
        dispatcher.dispatch_once(session)
    jobs = daemon.lease_and_prepare(limit=1)
    assert jobs, "the bootstrap heartbeat should have queued a run"
    assert jobs[0].max_usd == 1.25


def test_zero_means_unbounded_and_is_not_passed_as_a_ceiling(client):
    """0 has to mean off rather than 'may spend nothing', or setting it to zero
    would stop every run instead of removing the limit."""
    from yaaf.runner import daemon

    make_project(client, constitution={"max_usd_per_run": 0})
    with db_session() as session:
        dispatcher.dispatch_once(session)
    jobs = daemon.lease_and_prepare(limit=1)
    assert jobs
    assert jobs[0].max_usd == 0
