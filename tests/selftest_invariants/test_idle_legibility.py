"""Invariant selftests: a quiet project must always be able to say why.

The bug these pin down is not that projects go idle — most idle is correct and
costs nothing. It is that every *kind* of idle rendered as the same reassuring
sentence. A board whose only task was gated until 03:00 read "nothing blocking —
a run should start shortly — 1 task(s) ready" for two hours, and the chairman
paused the project with the reason "been idle and I have no idea why".

Two properties are enforced here:

1. Every gate the dispatcher's ladder can name has a sentence written for it.
   `pacing` shipped in the backend and never got one in the browser, so a brake
   that stops work every hour said nothing for months. A new gate now cannot
   ship without prose.
2. Work that exists but cannot be reached names its own wall, rather than
   counting as "ready" beside a promise that a run is about to start.
"""

from __future__ import annotations

import inspect
import re
from datetime import timedelta

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from yaaf.core import ids
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    AgentStatus,
    Notice,
    NoticeKind,
    Project,
    Run,
    RunQueue,
    RunStatus,
    Task,
    TaskStatus,
    Team,
    utcnow,
)
from yaaf.server import dispatcher

# Every value the ladder in `activity_state` and `_board_reachability` can put
# in `blocked_by`. None is the fifteenth: "nothing is stopping this".
GATES = {
    "daily_runs", "pacing", "governance_reserve", "concurrency", "agents_busy",
    "wake_ceiling", "coalescing", "scheduled", "no_lead", "paused_agents",
    "stalled_requirement", "rework_owed", "awaiting_chairman", "awaiting_ceo",
    "blocked_tasks", "checks_lost", "checking",
}
# Gates that mean the project is fine — working, or the platform verifying its
# finished work. These never raise the banner.
FINE = {"concurrency", "agents_busy", "checking", "awaiting_ceo"}
# The gates that are waiting out a clock. Only these may be dismissed in the
# UI, and each has to name the moment it ends — a wait with no falsifiable time
# in it reads as a permanent fault.
WAITS = {"daily_runs", "pacing", "governance_reserve", "wake_ceiling",
         "coalescing", "scheduled"}


def _drain(max_rounds: int = 6) -> None:
    for _ in range(max_rounds):
        with db_session() as session:
            if dispatcher.dispatch_once(session) == 0:
                return
            session.query(RunQueue).delete()
    raise AssertionError("dispatcher would not settle")


def _activity(project: dict) -> dict:
    with db_session() as session:
        row = session.get(Project, project["id"])
        return dispatcher.activity_state(session, row, effective(session, row.id))


def _synthetic(gate: str | None) -> dict:
    """A state shaped like `activity_state`'s output, pinned to one gate.

    Building thirteen live scenarios would test the ladder, not the prose; this
    tests that every branch of the prose exists and says something.
    """
    at = "2026-08-20T03:00:00+00:00"
    return {
        "blocked_by": gate,
        "daily_runs": {"used": 150, "cap": 150, "resets_at": at},
        "pacing": {"used": 15, "cap": 15, "frees_at": at},
        "governance_reserve": {"reserved": 3, "ordinary_cap": 147, "active": True},
        "wake_ceiling": {"cap": 6, "frees_at": at, "agents_held": 1},
        "coalescing": {"ripe_at": at, "agents_held": 1},
        "concurrency": {"in_flight": 3, "cap": 3},
        "waiting": {"ready_tasks": 1, "claimable_tasks": 0, "gated_tasks": 1,
                    "claimed_tasks": 0, "blocked_tasks": 1, "checking_tasks": 1,
                    "notices": 2, "agents_with_work": 1},
        "board": {"claimable": 0, "gated": 1, "next_gate_at": at, "blocked": 1,
                  "checking": 1, "checks_lost": ["Shortlist the models"],
                  "leaderless_teams": ["Alpha"], "paused_agents": ["Mike"],
                  "stalled_requirements": ["R-02"],
                  "rework_owed": ["R-06"],
                  "proposed_requirements": ["R-03"],
                  "observable_checking": ["R-01"], "plan_open": True,
                  "ceo_decides": ["scope", "verdicts"],
                  "unreachable": gate},
        "agents": [],
    }


def test_every_gate_the_ladder_can_name_has_a_sentence():
    """Read the ladder out of the source rather than trusting a hand-kept list.
    A gate added to the dispatcher without prose fails here, which is the whole
    point: `pacing` was added to the backend, never got a sentence, and a brake
    that fires every hour reported "nothing blocking" instead."""
    source = inspect.getsource(dispatcher)
    named = set(re.findall(r'(?:blocked_by|unreachable) = "([a-z_]+)"', source))
    assert named == GATES, (
        f"the ladder names {sorted(named ^ GATES)} that this test does not know "
        "about — add it to GATES and give it a sentence in _activity_brief")

    for gate in [*sorted(GATES), None]:
        brief = dispatcher._activity_brief(_synthetic(gate))
        assert brief["short"], f"{gate}: no tile sentence"
        assert brief["headline"], f"{gate}: no headline"
        assert len(brief["detail"]) > 40, f"{gate}: detail a chairman can act on"
        assert brief["tone"] in ("ok", "warn", "serious", "critical")
        # A dismissible brief must name the moment it stops being true, or the
        # reader is hiding a banner with no idea when it comes back.
        if brief["sig"]:
            assert brief["sig"] == gate
            assert brief["clears_at"], f"{gate}: dismissible with no clearing time"
            assert gate in WAITS
        else:
            assert gate not in WAITS or not brief["banner"]


def test_only_stopped_gates_raise_the_banner():
    """Concurrency, busy agents and checks in flight mean the project is
    *working*. A banner that fires when things are fine is how a reader learns
    to stop reading it."""
    for gate in (*FINE, None):
        assert dispatcher._activity_brief(_synthetic(gate))["banner"] is False
    for gate in sorted(GATES - FINE):
        assert dispatcher._activity_brief(_synthetic(gate))["banner"] is True


def test_a_task_scheduled_for_later_is_not_counted_as_ready(client):
    """The exact shape of the live failure. A gated task woke nobody — correctly
    — while `ready_tasks` counted it, so the screen promised a run that could
    not start for two hours."""
    project = make_project(client)
    _drain()
    team_id = project["teams"][0]["id"]
    with db_session() as session:
        session.add(Task(
            id=ids.new_id(ids.TASK), project_id=project["id"], team_id=team_id,
            created_by_agent_id=project["ceo_agent_id"],
            title="Compute the distributions", goal="Only once the pull lands.",
            success_test={"type": "command", "command": "test -f /workspace/x"},
            produces_external_evidence=False, status=TaskStatus.READY,
            not_before=utcnow() + timedelta(hours=2),
            created_at=utcnow(), updated_at=utcnow(),
        ))

    state = _activity(project)
    assert state["blocked_by"] == "scheduled"
    assert state["waiting"]["gated_tasks"] == 1
    assert state["waiting"]["claimable_tasks"] == 0
    assert state["board"]["next_gate_at"]
    assert "scheduled for" in state["brief"]["headline"]
    # And the dispatcher agrees: the reading and the refusal are one fact.
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0


def test_a_team_with_no_lead_says_its_queue_is_unclaimable(client):
    """Two or more members and nobody named: the team's tasks can never be
    claimed and nobody is ever woken for them. It does not clear on its own, so
    it is the one gate that is a hard failure rather than a wait."""
    project = make_project(client)
    _drain()
    team_id = project["teams"][0]["id"]
    with db_session() as session:
        session.get(Team, team_id).lead_agent_id = None
        session.add(Task(
            id=ids.new_id(ids.TASK), project_id=project["id"], team_id=team_id,
            created_by_agent_id=project["ceo_agent_id"],
            title="Pull every ad from the server", goal="Paginate to exhaustion.",
            success_test={"type": "command", "command": "test -f /workspace/x"},
            produces_external_evidence=True, status=TaskStatus.READY,
            created_at=utcnow(), updated_at=utcnow(),
        ))

    state = _activity(project)
    assert state["blocked_by"] == "no_lead"
    assert state["board"]["leaderless_teams"] == ["Alpha"]
    assert state["brief"]["tone"] == "critical"
    assert state["brief"]["clears_at"] is None, "this one never clears itself"
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0


def test_a_requirement_the_chairman_sent_back_is_not_a_quiet_board(client):
    """The state the loop-fix creates, and the reason it needs a name.

    Once a rejection sticks, the requirement sits open with an empty board and
    will not go back to the chairman until new work closes under it. Every
    counter reads zero and nothing is wrong with any of them — which is exactly
    the shape of quiet this ladder exists to refuse. It is the CEO's move, and
    the screen has to say so.
    """
    from tests.selftest_invariants.test_requirements import (
        _park_an_observable,
        _verdict,
        deliver_project,
    )

    project = deliver_project(client, slug="sent-back")
    rid = _park_an_observable(client, project)
    assert _verdict(client, rid, False, "The margins are wrong. Rebuild it.").status_code == 200

    # The CEO has been woken and has read it; what is left is that it has not
    # acted. An unread notice is a different sentence, and so is a run still
    # going — the tokens that built the deliverable are still RUNNING here.
    with db_session() as session:
        for notice in session.query(Notice).filter(Notice.consumed_at.is_(None)):
            notice.consumed_at = utcnow()
        for run in session.query(Run).filter(Run.status == RunStatus.RUNNING):
            run.status = RunStatus.DONE
    _drain()

    state = _activity(project)
    assert state["blocked_by"] == "rework_owed", state["brief"]
    assert state["board"]["rework_owed"] == ["R-01"]
    assert state["brief"]["tone"] == "serious"
    assert state["brief"]["clears_at"] is None, "no clock ends this one"
    assert "rework" in state["brief"]["short"]


def test_work_owned_by_a_paused_agent_is_not_silent(client):
    """A paused agent is skipped by the dispatcher entirely — and a claim only
    ever wakes its own claimant — so its work wakes nobody and left no trace in
    any counter on any screen."""
    project = make_project(client)
    _drain()
    team_id = project["teams"][0]["id"]
    worker = agent_by_name(project, "Lead")
    with db_session() as session:
        session.get(Agent, worker).status = AgentStatus.PAUSED
        session.add(Task(
            id=ids.new_id(ids.TASK), project_id=project["id"], team_id=team_id,
            created_by_agent_id=project["ceo_agent_id"],
            title="Pull every ad from the server", goal="Paginate to exhaustion.",
            success_test={"type": "command", "command": "test -f /workspace/x"},
            produces_external_evidence=True, status=TaskStatus.CLAIMED,
            claimed_by_agent_id=worker, claimed_at=utcnow(),
            created_at=utcnow(), updated_at=utcnow(),
        ))

    state = _activity(project)
    assert state["blocked_by"] == "paused_agents"
    assert state["board"]["paused_agents"] == ["Lead"]
    assert "Lead" in state["brief"]["headline"]
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0


def test_a_batched_wake_names_the_minute_it_lands(client):
    """Two ordinary notices are deliberately not worth a full-price run, so the
    dispatcher holds them. That wait is minutes long, entirely correct, and was
    named nowhere — the most common reason a healthy project looks stopped."""
    project = make_project(client)
    _drain()
    with db_session() as session:
        for _ in range(2):
            session.add(Notice(
                id=ids.new_id(ids.NOTICE), project_id=project["id"],
                agent_id=project["ceo_agent_id"], kind=NoticeKind.GOAL_CHANGED,
                body="R-01 is met.", created_at=utcnow() - timedelta(minutes=1)))

    state = _activity(project)
    assert state["blocked_by"] == "coalescing"
    assert state["coalescing"]["ripe_at"], "a batching window must name its end"
    assert state["brief"]["clears_at"] == state["coalescing"]["ripe_at"]
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0

    # Past the window it stops being a gate, and the run the chairman was
    # waiting for is enqueued.
    with db_session() as session:
        for notice in session.query(Notice).all():
            notice.created_at = utcnow() - timedelta(
                minutes=dispatcher.NOTICE_COALESCE_MINUTES + 1)
    assert _activity(project)["blocked_by"] != "coalescing"
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def test_an_urgent_notice_never_waits_out_the_window(client):
    """A budget trip or the chairman's own words skip the batch. If those were
    reported as 'batching' the brief would be telling the chairman to wait for
    something that is already on its way."""
    project = make_project(client)
    _drain()
    with db_session() as session:
        session.add(Notice(
            id=ids.new_id(ids.NOTICE), project_id=project["id"],
            agent_id=project["ceo_agent_id"], kind=NoticeKind.BUDGET_WARNING,
            body="80% spent.", created_at=utcnow()))

    assert _activity(project)["blocked_by"] is None
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def test_an_empty_board_is_still_quiet_not_stuck(client):
    """The counterweight to everything above. Idle with nothing waiting must
    stay unremarkable, or the new gates turn into noise and get ignored."""
    project = make_project(client)
    _drain()
    state = _activity(project)
    assert state["blocked_by"] is None
    assert state["brief"]["banner"] is False
    assert state["brief"]["tone"] == "ok"


def test_work_cannot_be_gated_further_out_than_the_rule_allows(client):
    """not_before is a real verb — it is how work is sequenced — but a gate the
    CEO guessed at is how a project goes silent with nobody able to say why.
    Past the limit the refusal tells it to sequence on the predecessor landing."""
    project = make_project(client)
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    team_id = project["teams"][0]["id"]

    def create(hours: float):
        return client.post(
            "/agent/tasks",
            json={"team_id": team_id, "title": "Compute the distributions",
                  "goal": "Bin the ACOS and spend columns and write the findings.",
                  "success_test": {"type": "command", "command": "test -f /w/x.json"},
                  "produces_external_evidence": True,
                  "not_before": (utcnow() + timedelta(hours=hours)).isoformat()},
            headers=bearer(ceo))

    within = create(1)
    assert within.status_code == 200, within.text

    beyond = create(48)
    assert beyond.status_code == 422
    assert "not_before" in beyond.json()["error"]
    assert "wakes nobody" in beyond.json()["error"]


def test_a_leaderless_team_is_given_a_lead_rather_than_left_unclaimable(client):
    """The one stall with an unambiguous correct action in code. Disclosed, not
    done quietly: a silent promotion is its own kind of invisible."""
    from yaaf.core.models import AuditLog
    from yaaf.server import janitor

    project = make_project(client)
    _drain()
    team_id = project["teams"][0]["id"]
    with db_session() as session:
        session.get(Team, team_id).lead_agent_id = None

    with db_session() as session:
        janitor.janitor_once(session)

    with db_session() as session:
        team = session.get(Team, team_id)
        assert team.lead_agent_id is not None
        elected = session.get(Agent, team.lead_agent_id)
        assert elected.name == "Lead", "the earliest member, same as a sole member"
        assert session.query(Notice).filter_by(
            kind=NoticeKind.LEAD_ELECTED).count() == 1
        assert session.query(AuditLog).filter_by(
            action="team.lead_elected").count() == 1

    # Idempotent: the second sweep must not re-elect or re-announce.
    with db_session() as session:
        janitor.janitor_once(session)
    with db_session() as session:
        assert session.query(Notice).filter_by(
            kind=NoticeKind.LEAD_ELECTED).count() == 1


def test_the_watchdog_speaks_when_a_project_goes_quiet_with_work_open(client):
    """Every gate under this is individually correct and individually invisible.
    What nobody had was the fact that they add up to a project doing nothing."""
    from yaaf.server import janitor

    project = make_project(client, constitution={"stall_hours": 2})
    _drain()
    team_id = project["teams"][0]["id"]
    with db_session() as session:
        # Work on the board, owned by an agent nobody can wake, and every run
        # this project ever had is hours old.
        session.get(Agent, agent_by_name(project, "Lead")).status = AgentStatus.PAUSED
        session.add(Task(
            id=ids.new_id(ids.TASK), project_id=project["id"], team_id=team_id,
            created_by_agent_id=project["ceo_agent_id"],
            title="Pull every ad from the server", goal="Paginate to exhaustion.",
            success_test={"type": "command", "command": "test -f /workspace/x"},
            produces_external_evidence=True, status=TaskStatus.CLAIMED,
            claimed_by_agent_id=agent_by_name(project, "Lead"), claimed_at=utcnow(),
            created_at=utcnow(), updated_at=utcnow(),
        ))
        for run in session.query(RunQueue).all():
            session.delete(run)

    with db_session() as session:
        janitor.janitor_once(session)

    with db_session() as session:
        stalls = session.query(Notice).filter_by(kind=NoticeKind.STALLED).all()
        assert len(stalls) == 1
        # The CEO reads the dispatcher's own sentence, not a paraphrase of it —
        # a second opinion on why the project is quiet is how it invents one.
        assert "is paused and holds the work" in stalls[0].body

    # Told once per window, not once per fifteen seconds.
    with db_session() as session:
        janitor.janitor_once(session)
    with db_session() as session:
        assert session.query(Notice).filter_by(kind=NoticeKind.STALLED).count() == 1


def test_the_watchdog_stays_quiet_while_a_gate_names_its_own_clock(client):
    """A wait the product already explains, with a moment attached, is not a
    stall. Crying stalled at it is how the notice stops being believed."""
    from yaaf.server import janitor

    project = make_project(client, constitution={"stall_hours": 2,
                                                 "runs_per_project_per_day": 1})
    with db_session() as session:
        # Spends the day's single run. The queue row is deliberately left in
        # place: the ceiling counts queued and historical runs alike, which is
        # what stops a burst from overshooting it.
        assert dispatcher.dispatch_once(session) == 1
    with db_session() as session:
        session.add(Notice(
            id=ids.new_id(ids.NOTICE), project_id=project["id"],
            agent_id=project["ceo_agent_id"], kind=NoticeKind.BUDGET_WARNING,
            body="80% spent.", created_at=utcnow() - timedelta(hours=4)))

    assert _activity(project)["blocked_by"] == "daily_runs"
    with db_session() as session:
        janitor.janitor_once(session)
    with db_session() as session:
        assert session.query(Notice).filter_by(kind=NoticeKind.STALLED).count() == 0


def test_one_broken_project_does_not_stop_every_other_one(client):
    """The whole tick used to share a transaction, so a single bad row rolled
    back and abandoned every project on the box — reported by one stderr line."""
    import yaaf.server.dispatcher as disp

    good = make_project(client, slug="good")
    bad = make_project(client, slug="bad")
    real = disp._dispatch_project

    def explode(session, project, cons):
        if project.id == bad["id"]:
            raise RuntimeError("this project has a broken row")
        return real(session, project, cons)

    disp._dispatch_project = explode
    try:
        assert disp.dispatch_all() >= 1, "the healthy project still got its tick"
    finally:
        disp._dispatch_project = real

    with db_session() as session:
        queued = {q.project_id for q in session.query(RunQueue).all()}
    assert good["id"] in queued
    assert bad["id"] not in queued


def test_the_health_page_carries_the_same_sentence(client):
    """One reader asking one question deserves one answer. The health check
    used to re-write three of the gates by hand and knew nothing of the rest."""
    project = make_project(client)
    _drain()
    team_id = project["teams"][0]["id"]
    with db_session() as session:
        session.add(Task(
            id=ids.new_id(ids.TASK), project_id=project["id"], team_id=team_id,
            created_by_agent_id=project["ceo_agent_id"],
            title="Compute the distributions", goal="Only once the pull lands.",
            success_test={"type": "command", "command": "test -f /workspace/x"},
            produces_external_evidence=False, status=TaskStatus.READY,
            not_before=utcnow() + timedelta(hours=2),
            created_at=utcnow(), updated_at=utcnow(),
        ))

    health = client.get(f"/observe/projects/{project['id']}/health",
                        headers=ADMIN).json()
    activity = [c for c in health["checks"] if c["key"] == "activity"]
    assert len(activity) == 1
    assert "scheduled for" in activity[0]["detail"]
