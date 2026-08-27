"""Invariant selftests: the CEO has the arithmetic, and an obligation to use it.

The chairman's complaint that started this was that his CEO "doesn't seem to
have all the information" and was "confident, yet there is nothing said that
gives comfort to match the confidence". Both halves were true, and they had
different causes.

The information half: four separate limits decide whether a run starts and the
CEO was shown one of them, so it forecast by dividing the daily cap and was
wrong sixty seconds after the allowance reset.

The confidence half was not an information problem at all. The same CEO had
written "run ration is the binding constraint, not the analysis" in its own run
summary three hours before telling him "solid progress", and the platform had
handed it the exact sentence to quote. Every mechanism between a CEO and its
chairman was a brake; nothing had ever required it to speak.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core import ids
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import (
    Cycle,
    Flag,
    FlagKind,
    FlagStatus,
    Message,
    Project,
    Recurrence,
    Requirement,
    RequirementStatus,
    Run,
    RunKind,
    RunQueue,
    RunStatus,
    Task,
    TaskStatus,
    utcnow,
)
from yaaf.server import dispatcher, flags
from yaaf.server.capacity import capacity_forecast


def _channel_id(project_id: str, kind: str) -> str:
    from yaaf.core.models import Channel

    with db_session() as session:
        return session.execute(
            select(Channel).where(Channel.project_id == project_id,
                                  Channel.kind == kind)
        ).scalars().first().id


def _drain(max_rounds: int = 6) -> None:
    for _ in range(max_rounds):
        with db_session() as session:
            if dispatcher.dispatch_once(session) == 0:
                return
            session.query(RunQueue).delete()
    raise AssertionError("dispatcher would not settle")


# ── C-1: reading is not work ────────────────────────────────────────────────────


def test_reading_the_mail_does_not_spend_the_work_allowance(client):
    """The failure this was written for: at the daily reset, three of the first
    four runs went to agents waking to read the message that told them not to
    work. That consumed the hourly ceiling, so the two agents actually holding
    ready tasks got nothing for an hour."""
    project = make_project(client)
    _drain()
    with db_session() as session:
        proj = session.get(Project, project["id"])
        cons = effective(session, proj.id)
        before = dispatcher.daily_runs_used(session, proj, dispatcher.day_start(utcnow()))

        # An inbox run: woke only to read ordinary agent-to-agent traffic.
        session.add(Run(
            id=ids.new_id(ids.RUN), project_id=proj.id,
            agent_id=project["ceo_agent_id"], kind=RunKind.INBOX_RUN,
            trigger={"type": "wake", "reasons": [{"kind": "messages", "urgent": False}]},
            status=RunStatus.DONE, created_at=utcnow(),
        ))
        session.flush()
        after = dispatcher.daily_runs_used(session, proj, dispatcher.day_start(utcnow()))
        assert after == before, "an inbox run must not spend the work allowance"

        # It does spend its own ceiling, so "cheap" never means "free".
        assert dispatcher.inbox_runs_used(
            session, proj, dispatcher.day_start(utcnow())) == 1
        assert cons.inbox_runs_per_project_per_hour >= 1


def test_the_chairman_speaking_is_work_not_correspondence(client):
    """An unanswered owner is not mail. A boardroom message, or anything he
    sends, must never be rate-limited by a ceiling meant for agent chatter."""
    ordinary = [{"kind": "messages", "channel_id": "c1", "count": 2, "urgent": False}]
    assert dispatcher.run_kind_for_reasons(ordinary) == RunKind.INBOX_RUN

    from_him = [{"kind": "messages", "channel_id": "c1", "count": 1, "urgent": True}]
    assert dispatcher.run_kind_for_reasons(from_him) == RunKind.AGENT_RUN

    # Anything else on the wake is work, even alongside mail.
    mixed = [{"kind": "messages", "count": 1, "urgent": False},
             {"kind": "team_task_ready", "task_id": "t1"}]
    assert dispatcher.run_kind_for_reasons(mixed) == RunKind.AGENT_RUN
    assert dispatcher.run_kind_for_reasons([]) == RunKind.AGENT_RUN


def test_a_run_that_changed_nothing_is_refunded(client):
    """Outcome metering. A wake cannot be known to be empty until it has
    happened, so the refund is retroactive — but a run that woke, thought and
    left the company exactly as it found it did not slow anything down, and the
    allowance should not pretend otherwise."""
    project = make_project(client)
    _drain()
    with db_session() as session:
        proj = session.get(Project, project["id"])
        start = dispatcher.day_start(utcnow())
        baseline = dispatcher.daily_runs_used(session, proj, start)

        empty = Run(id=ids.new_id(ids.RUN), project_id=proj.id,
                    agent_id=project["ceo_agent_id"], kind=RunKind.AGENT_RUN,
                    trigger={}, status=RunStatus.DONE, produced=0, metered=False,
                    created_at=utcnow())
        busy = Run(id=ids.new_id(ids.RUN), project_id=proj.id,
                   agent_id=project["ceo_agent_id"], kind=RunKind.AGENT_RUN,
                   trigger={}, status=RunStatus.DONE, produced=3, metered=True,
                   created_at=utcnow())
        session.add_all([empty, busy])
        session.flush()
        assert dispatcher.daily_runs_used(session, proj, start) == baseline + 1


def test_writing_something_counts_as_production(client):
    """The counter that decides the refund. Counted on the request's own
    session — a second connection per write serialises against the first under
    SQLite and turns every agent write into lock contention."""
    project = make_project(client)
    _drain()
    run_id, token = live_token(project["id"], project["ceo_agent_id"])

    # A read changes nothing.
    client.get("/agent/board", headers=bearer(token))
    with db_session() as session:
        assert session.get(Run, run_id).produced == 0

    # A write does.
    posted = client.post(
        "/agent/messages",
        json={"channel_id": _channel_id(project["id"], "leadership"),
              "body": "Snapshot is on disk."},
        headers=bearer(token),
    )
    assert posted.status_code == 200, posted.text
    with db_session() as session:
        assert session.get(Run, run_id).produced >= 1


# ── C-3: the arithmetic ─────────────────────────────────────────────────────────


def test_capacity_counts_every_limit_not_just_the_daily_cap(client):
    """"Ten ordinary runs, four needed, six spare" ignored the hourly ceiling,
    the per-agent wake cap and the concurrency cap. This is the same arithmetic
    the dispatcher gates on, so it cannot disagree with it."""
    project = make_project(client, constitution={
        "runs_per_project_per_day": 12,
        "runs_per_project_per_hour": 4,
        "wakes_per_agent_per_hour": 1,
        "reserved_governance_runs": 2,
    })
    _drain()
    with db_session() as session:
        proj = session.get(Project, project["id"])
        cons = effective(session, proj.id)
        forecast = capacity_forecast(session, proj, cons, utcnow() + timedelta(hours=3))

        # Never a point estimate: four numbers, and the sentence behind them.
        for key in ("available", "needed", "retry_reserve", "slack", "why", "basis"):
            assert key in forecast
        # Three hours cannot yield a whole day's allowance, whatever the daily
        # cap says — that was exactly the arithmetic error.
        assert forecast["available"] <= 12
        assert "an hour" in forecast["basis"] or "/hour" in forecast["basis"]
        # A reserve is held back even with no failures on record yet: a company
        # that has not failed is not a company that cannot.
        assert forecast["retry_basis"]


def test_one_desk_holding_the_work_is_a_serial_chain(client):
    """The constraint the old prose arithmetic could not express. Three tasks
    on one desk at one wake an hour take three hours, however much daily
    allowance is left."""
    project = make_project(client, constitution={"wakes_per_agent_per_hour": 1})
    _drain()
    team_id = project["teams"][0]["id"]
    with db_session() as session:
        proj = session.get(Project, project["id"])
        for n in range(3):
            session.add(Task(
                id=ids.new_id(ids.TASK), project_id=proj.id, team_id=team_id,
                created_by_agent_id=project["ceo_agent_id"],
                title=f"Piece {n} of the analysis",
                goal="Part of one chain that one desk owns.",
                success_test={"type": "command", "command": "true"},
                produces_external_evidence=False, status=TaskStatus.READY,
                created_at=utcnow(), updated_at=utcnow(),
            ))
        session.flush()
        cons = effective(session, proj.id)

        # Two hours is not enough for a three-deep chain at one wake an hour,
        # no matter what the other ceilings allow.
        tight = capacity_forecast(session, proj, cons, utcnow() + timedelta(hours=2))
        assert tight["chain_hours"] >= 3
        assert tight["feasible"] is False
        assert "wake" in tight["why"] or "chain" in tight["why"]

        # Given the hours, it fits.
        roomy = capacity_forecast(session, proj, cons, utcnow() + timedelta(hours=12))
        assert roomy["chain_hours"] >= 3
        assert roomy["available"] >= roomy["needed"]


# ── C-5: cycles carry a goal, a due-by, and recurrence ──────────────────────────


def test_a_deadline_is_a_fact_and_needs_its_source(client):
    """The CEO may record a deadline because a deadline is discovered, not
    written — the gameweek moves, the carrier's cutoff moves. But a fact cites
    where it came from, and "we will make it" stays the platform's arithmetic."""
    project = make_project(client, mode="operate")
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    due = (utcnow() + timedelta(hours=30)).isoformat()

    # A date with no provenance is refused.
    bare = client.post("/agent/cycle_goal", json={
        "goal": "Ship the gameweek memo before the deadline.",
        "due_at": due,
    }, headers=bearer(ceo))
    assert bare.status_code == 422
    assert "provenance" in bare.json()["error"]

    # 'data' means you read it somewhere; say where.
    unsourced = client.post("/agent/cycle_goal", json={
        "goal": "Ship the gameweek memo before the deadline.",
        "due_at": due, "due_source_kind": "data",
    }, headers=bearer(ceo))
    assert unsourced.status_code == 422

    ok = client.post("/agent/cycle_goal", json={
        "goal": "Ship the gameweek memo before the deadline.",
        "due_at": due,
        "due_source_kind": "data",
        "due_source_url": "https://fantasy.premierleague.com/api/bootstrap-static/",
        "due_source_note": "events[1].deadline_time",
    }, headers=bearer(ceo))
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["rolls_on_due_at"] is True
    # And the arithmetic rides back with it, unasked.
    assert body["capacity"] is not None
    assert "feasible" in body["capacity"]

    with db_session() as session:
        cycle = session.execute(
            Cycle.__table__.select().where(Cycle.project_id == project["id"])
        ).first()
        assert cycle is not None


def test_the_due_by_drives_the_roll_not_the_calendar(client):
    """A rhythm that cannot follow the world is a rhythm the work ignores. The
    platform believed one company rolled on Tuesdays while its real deadline
    landed on a different day every week."""
    from yaaf.server.janitor import next_cycle_roll

    with db_session() as session:
        cons = effective(session, make_project(client, slug="rhythm",
                                               mode="operate")["id"])
    opened = utcnow()
    calendar = next_cycle_roll(opened, cons, None)

    class _Stub:
        due_at = opened + timedelta(hours=7)

    assert next_cycle_roll(opened, cons, _Stub()) == _Stub.due_at
    assert calendar != _Stub.due_at


def test_hourly_is_a_rhythm(client):
    """An inbox swept every morning, an order queue checked on the hour. Every
    boundary used to snap to midnight, so the shortest rhythm was a day."""
    from yaaf.core.constitution import ConstitutionDoc
    from yaaf.server.janitor import next_cycle_roll

    cons = ConstitutionDoc(cycle_frequency="hourly")
    opened = utcnow()
    boundary = next_cycle_roll(opened, cons, None)
    assert timedelta(0) < boundary - opened <= timedelta(hours=1)
    assert boundary.minute == 0


def test_standing_requirements_stay_out_of_the_delivery_pace(client):
    """The trap in making requirements recur. `project_velocity` derives pace
    from met requirements and `commitment_projection` divides by it — so an
    obligation met every cycle by design would post met-events forever, and the
    platform would promise the chairman a date its own rhythm manufactured."""
    from yaaf.server.velocity import project_velocity

    project = make_project(client, mode="operate")
    with db_session() as session:
        proj = session.get(Project, project["id"])
        standing = Requirement(
            id=ids.new_id(ids.REQUIREMENT), project_id=proj.id, key="R-05",
            statement="The gameweek memo lands 24h before the deadline.",
            acceptance_test={"type": "command", "command": "true"},
            status=RequirementStatus.MET, recurrence=Recurrence.STANDING,
            created_at=utcnow(), met_at=utcnow(),
        )
        session.add(standing)
        session.flush()
        from yaaf.server import policy
        policy.record_requirement_event(
            session, standing, RequirementStatus.MET, "platform")
        session.flush()

        pace = project_velocity(session, proj.id)
        assert pace["met_3w"] == 0, (
            "a standing obligation met on its rhythm is not delivery progress"
        )


# ── C-6: the obligation to speak ────────────────────────────────────────────────


def test_a_flag_overrides_the_reporting_cadence(client):
    """Every mechanism here was a brake. A CEO sitting on a red cycle with a
    closed cadence slot could not raise it at all."""
    project = make_project(client, constitution={"chairman_report_interval_hours": 24})
    _drain()
    with db_session() as session:
        proj = session.get(Project, project["id"])
        session.add(Message(
            id=ids.new_id(ids.MESSAGE), project_id=proj.id,
            channel_id=_channel_id(proj.id, "boardroom"),
            sender_agent_id=project["ceo_agent_id"],
            body="Status: all well.", created_at=utcnow(),
        ))
        session.flush()

    _, ceo = live_token(project["id"], project["ceo_agent_id"], kind="chat_session")
    before = client.get("/agent/context", headers=bearer(ceo)).json()["chairman"]
    assert before["may_post_status_now"] is False, "the cadence brake should hold"

    with db_session() as session:
        proj = session.get(Project, project["id"])
        flags.open_flag(session, proj, FlagKind.WORK_STARVED,
                        "2 tasks ready and the allowance is spent.",
                        arithmetic={"claimable_tasks": 2})

    after = client.get("/agent/context", headers=bearer(ceo)).json()["chairman"]
    assert after["unraised_flags"] == 1
    assert after["may_post_status_now"] is True, "an open flag must require a post"
    assert "has NOT been told" in after["note"]


def test_a_flag_cannot_be_closed_by_going_quiet(client):
    """It closes by being said out loud, with a proposal. Closing one
    dismissively is a CEO doing its job; leaving it open while reporting that
    all is well is what is no longer available."""
    project = make_project(client)
    _drain()
    with db_session() as session:
        proj = session.get(Project, project["id"])
        flag = flags.open_flag(session, proj, FlagKind.DEADLINE_AT_RISK,
                               "The arithmetic does not reach Thursday.",
                               arithmetic={"slack": -2.0}, subject_id="cycl_x")
        session.flush()
        flag_id = flag.id

    _, ceo = live_token(project["id"], project["ceo_agent_id"])

    # A proposal alone is not enough — the chairman has to have been told.
    forged = client.post("/agent/flags/raise", json={
        "flag_id": flag_id, "message_id": "msg_nope",
        "proposal": "Cut the chip calendar to GW3 and keep the memo.",
    }, headers=bearer(ceo))
    assert forged.status_code == 422
    assert "boardroom message you posted" in forged.json()["error"]

    with db_session() as session:
        assert session.get(Flag, flag_id).status == FlagStatus.OPEN

    # Said in the room, with what to do about it.
    said = client.post("/agent/messages", json={
        "channel_id": _channel_id(project["id"], "boardroom"),
        "body": ("Thursday's memo cannot include the chip calendar. Six ordinary "
                 "runs left, three needed. I would cut the chip calendar to GW3 — "
                 "first chip expiry is GW19."),
    }, headers=bearer(ceo))
    assert said.status_code == 200, said.text
    message_id = said.json()["id"]

    raised = client.post("/agent/flags/raise", json={
        "flag_id": flag_id, "message_id": message_id,
        "proposal": "Cut the chip calendar to GW3; first expiry is GW19.",
    }, headers=bearer(ceo))
    assert raised.status_code == 200, raised.text

    with db_session() as session:
        closed = session.get(Flag, flag_id)
        assert closed.status == FlagStatus.RAISED
        assert closed.raised_in_message_id == message_id
        assert closed.proposal


def test_the_chairman_can_see_what_he_has_not_been_told(client):
    """The property that makes the mechanism trustworthy without being trusted:
    when it stops working, the silence is visible rather than inferred."""
    project = make_project(client)
    _drain()
    with db_session() as session:
        proj = session.get(Project, project["id"])
        flags.open_flag(session, proj, FlagKind.WORK_STARVED,
                        "3 tasks ready and the allowance is spent.",
                        arithmetic={"claimable_tasks": 3})

    seen = client.get(f"/observe/projects/{project['id']}/flags", headers=ADMIN).json()
    assert seen["unraised"] == 1
    assert seen["flags"][0]["arithmetic"]["claimable_tasks"] == 3

    health = client.get(f"/observe/projects/{project['id']}/health",
                        headers=ADMIN).json()
    flag_check = [c for c in health["checks"] if c["key"] == "flags"]
    assert flag_check and "not yet raised" in flag_check[0]["detail"]


def test_the_same_condition_does_not_reopen_every_tick(client):
    """The trip conditions are recomputed on every janitor tick. A forecast
    that stays negative for six hours is one piece of news, not seventy-two."""
    project = make_project(client)
    _drain()
    with db_session() as session:
        proj = session.get(Project, project["id"])
        first = flags.open_flag(session, proj, FlagKind.WORK_STARVED, "ready work waits")
        session.flush()
        assert first is not None
        again = flags.open_flag(session, proj, FlagKind.WORK_STARVED, "ready work waits")
        assert again is None, "one live flag per condition"
        assert flags.unraised_count(session, proj.id) == 1


def test_the_trip_conditions_actually_run(client):
    """The janitor swallows exceptions from flag evaluation so a bug there can
    never stop the sweep — which also means a bug there is SILENT. This calls
    it directly, because the first version of this module queried a requirement
    status that does not exist and the whole feature was quietly dead."""
    project = make_project(client, mode="operate")
    with db_session() as session:
        proj = session.get(Project, project["id"])
        cons = effective(session, proj.id)
        assert flags.evaluate(session, proj, cons) >= 0  # must not raise


def test_a_missed_standing_obligation_is_flagged(client):
    """A memo owed 38 times and shipped once used to be one row reading MET.
    The per-cycle record is what makes "you owed this last cycle and it did not
    happen" a fact the platform can state."""
    from yaaf.core.models import CycleStatus, RequirementCycleResult

    project = make_project(client, mode="operate")
    with db_session() as session:
        proj = session.get(Project, project["id"])
        cons = effective(session, proj.id)
        cycle = session.execute(
            select(Cycle).where(Cycle.project_id == proj.id)).scalars().first()
        assert cycle is not None
        cycle.status = CycleStatus.MISSED
        cycle.closed_at = utcnow()

        requirement = Requirement(
            id=ids.new_id(ids.REQUIREMENT), project_id=proj.id, key="R-05",
            statement="The gameweek memo lands 24h before the deadline.",
            acceptance_test={"type": "command", "command": "false"},
            status=RequirementStatus.PENDING, recurrence=Recurrence.STANDING,
            cycle_id=cycle.id, created_at=utcnow(),
        )
        session.add(requirement)
        session.add(RequirementCycleResult(
            id=ids.new_id(ids.REQUIREMENT_RESULT), project_id=proj.id,
            requirement_id=requirement.id, cycle_id=cycle.id,
            status=RequirementStatus.PENDING, created_at=utcnow(),
        ))
        session.flush()

        flags.evaluate(session, proj, cons)
        session.flush()
        kinds = {f.kind for f in flags.open_flags(session, proj.id)}

    assert FlagKind.STANDING_MISSED in kinds, "an unmet standing obligation must speak"
    # The cycle rolled anyway — that is deliberate. Stopping the rhythm because
    # last turn went badly makes the outage worse; the flag is what stops it
    # rolling quietly.
    assert FlagKind.CYCLE_MISSED in kinds
