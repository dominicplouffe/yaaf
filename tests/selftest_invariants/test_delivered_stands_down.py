"""Invariant selftests: a Deliver project that has delivered stops spending.

Completion is the chairman's gate — one of the three moments he chose to be
asked at — so the platform may not close a project for him. What it must not do
is keep *paying* while it waits, and for a long time it did.

The incident these attack: a project met 13 of 13 requirements and its CEO told
the chairman it was finished. For the next 33 hours five agents reviewed each
other's citation indexes in the leadership channel — 79 runs and 35.8M tokens,
against 125 runs for the entire real project. Nothing stopped it because
nothing was wrong: every wake was a legitimate unread message. The only brake
was the daily inbox ceiling, which re-arms at midnight, so the chairman got a
fresh burst of messages every morning about a project he had been told was done.

Asked why, the CEO answered "nothing is running" three times. It was not lying
and it was not blind: its own state block said "the project is working flat
out". It read `pending: 1` beside two agents, decided that meant a notice
rather than a task, and contradicted the number it had been told to quote.

So four rules, each a refusal or an automatic state change, none of them a
prompt:

  * a delivered project does not wake for agent-to-agent messages — but the
    chairman still gets through, because he is the one who can end it
  * a delivered project may not grow itself back open
  * the CEO can SEE that it delivered, which it could not before: the pack
    dropped `scope` at exactly the moment every requirement went true
  * the chairman is told, and keeps being told, in the boardroom rather than
    only in an email he was not reading
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import (
    agent_by_name,
    amend,
    bearer,
    channel_of,
    live_token,
)
from tests.selftest_invariants.test_modes import fabricate_met, run_mode_tick
from tests.selftest_invariants.test_requirements import (
    CMD_TEST,
    accept,
    ceo_token,
    deliver_project,
    propose,
)
from tests.selftest_invariants.test_wake_economics import _drain, _post_message
from yaaf.core.db import db_session
from yaaf.core.models import (
    Heartbeat,
    Message,
    Notice,
    NoticeKind,
    Project,
    Requirement,
    RequirementStatus,
    Run,
    RunQueue,
    RunStatus,
    utcnow,
)
from yaaf.server import dispatcher, policy


def settle_runs(project: dict) -> None:
    """Finish the runs `live_token` left RUNNING. An agent with a live run is
    `busy` forever, and a busy agent is never dispatched — which would make
    every brake in this file look like it worked."""
    with db_session() as session:
        for run in session.query(Run).filter_by(project_id=project["id"],
                                                status=RunStatus.RUNNING):
            run.status = RunStatus.DONE
            run.finished_at = utcnow()


def delivered(client, slug: str) -> dict:
    """A Deliver project with everything true and the chairman's button unpressed.

    Exactly the state the incident sat in for 33 hours: `delivery_outstanding`
    empty, `status` still RUNNING, and five agents who have not been told.
    """
    project = deliver_project(client, slug=slug)
    token = ceo_token(project)
    proposal = propose(client, token, statement="The report exists, sourced and dated")
    accept(client, proposal["requirement_id"], project)
    fabricate_met(project["id"], proposal["requirement_id"])
    with db_session() as session:
        row = session.get(Project, project["id"])
        assert not policy.delivery_outstanding(session, row), "fixture is not delivered"
    settle_runs(project)
    # Drain AFTER settling. A busy agent is skipped before `_wake_reasons` runs,
    # so draining first would leave the CEO's bootstrap heartbeat uncreated —
    # and it would then fire mid-test, looking exactly like a brake that leaked.
    _drain()
    settle_runs(project)
    project["requirement_id"] = proposal["requirement_id"]
    return project


def aged_leadership_post(client, project: dict) -> str:
    """One colleague message, old enough that coalescing is no longer the
    reason nothing happened. Without the delivered gate this buys a run."""
    leadership = channel_of(client, project["id"], "leadership")
    message_id = _post_message(project, leadership,
                               sender_agent_id=agent_by_name(project, "Lead"))
    with db_session() as session:
        session.get(Message, message_id).created_at = utcnow() - timedelta(
            minutes=dispatcher.MESSAGE_COALESCE_MINUTES + 1)
    return message_id


# ── the brake ───────────────────────────────────────────────────────────────────


def test_a_delivered_project_stops_waking_on_chatter(client):
    """The rule itself. An aged leadership message wakes the CEO on a running
    project (test_wake_economics pins that); on a delivered one it buys
    nothing, however long it sits there."""
    project = delivered(client, "quiet-after-delivery")
    aged_leadership_post(client, project)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0
    # Not merely deferred: ticking again a day later still buys nothing.
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0
        assert session.query(RunQueue).count() == 0


def test_the_same_message_would_have_woken_an_undelivered_project(client):
    """The control. Without this the test above could pass because the message
    was never a wake reason at all — which is how a brake gets credited with
    silence it did not cause."""
    project = delivered(client, "still-open")
    with db_session() as session:
        # One requirement back to open: the project is no longer delivered, and
        # nothing else about it changed.
        row = session.query(Requirement).filter_by(
            project_id=project["id"], key="R-01").one()
        row.status = RequirementStatus.IN_PROGRESS
        row.met_at = None
    aged_leadership_post(client, project)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def test_the_chairman_still_reaches_a_delivered_project(client):
    """The brake must not gag the one conversation that can end the project.
    He is the only person who can press Complete, and he asks questions first."""
    project = delivered(client, "chairman-gets-through")
    boardroom = channel_of(client, project["id"], "boardroom")
    with db_session() as session:
        chairman = session.get(Project, project["id"]).chairman_user_id
    _post_message(project, boardroom, sender_user_id=chairman,
                  body="are we not finished this?")
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1
        assert session.query(RunQueue).one().agent_id == project["ceo_agent_id"]


def test_the_brake_is_a_named_gate_the_chairman_can_read(client):
    """A project that stopped spending and a project with nothing to do used to
    render identically — as "nothing waiting; an idle project costs nothing",
    which is what the CEO quoted to the chairman."""
    project = delivered(client, "named-gate")
    aged_leadership_post(client, project)
    from yaaf.core.constitution import effective

    with db_session() as session:
        row = session.get(Project, project["id"])
        state = dispatcher.activity_state(session, row, effective(session, row.id))
    assert state["blocked_by"] == "delivery_ready"
    assert "confirmation" in state["brief"]["headline"]
    assert "not spending" in state["brief"]["detail"]


# ── scope may not grow back ─────────────────────────────────────────────────────


def test_a_delivered_project_cannot_accept_its_own_scope(client):
    """Scope may not grow back after delivery. That rule is unchanged; what it
    attaches to moved, 2026-09-06, and the move is worth reading.

    The incident this file was written for: 36 hours after reporting 13 of 13
    true, a CEO "opened R-14 and accepted it on its own authority". The guard
    added then refused the PROPOSAL, which was the earliest point it could be
    stopped — and which also made "I have finished early and can see the next
    move" unsayable in any form with state behind it. Scope was frozen when a
    project was late, when it had breached, AND when it had succeeded, so the
    one thing a project could never do was succeed and keep going.

    The abuse was always the second half of that sentence. A proposal creates no
    scope by construction: the chairman accepts it, and until he does no work
    may be created for it. So the guard now sits on ACCEPTANCE, where the growth
    actually happens — and sits there at every dial setting, including the ones
    that otherwise delegate scope to the CEO. That is strictly stronger than
    before on the thing that matters: previously a project whose dial said
    `scope_authority: ceo` was stopped only by the propose-time refusal, and
    anything that got a requirement onto the books by another route could still
    be self-accepted.
    """
    project = delivered(client, "no-regrowth")
    token = ceo_token(project)
    # `accept` is held by plan_authority. This is the case the guard exists
    # for: under the default dial acceptance is already a 403, so a test that
    # never turns this on proves nothing about post-delivery growth.
    amend(client, project["id"], plan_authority="ceo")

    proposed = client.post(
        "/agent/requirements",
        json={"statement": "One more thing should be true before this ships",
              "acceptance_test": CMD_TEST, "est_runs": 2},
        headers=bearer(token))
    assert proposed.status_code == 200, (
        "a delivered project may put the next move on the table: " + proposed.text
    )

    refused = client.post(
        "/agent/scope",
        json={"action": {"kind": "accept",
                         "requirement_key": proposed.json()["key"]},
              "reason": "it is obviously worth doing and I am here now"},
        headers=bearer(token))
    assert refused.status_code == 409, (
        "a delivered project accepted its own scope: " + refused.text
    )
    assert "may not accept" in refused.json()["error"]


def test_the_proposal_is_not_scope_until_he_says_so(client):
    """The protection, stated as the thing that actually matters: whatever the
    CEO proposes after delivering, the delivery is unchanged and no work can be
    created for it."""
    from yaaf.core.db import db_session
    from yaaf.core.models import Requirement, RequirementStatus

    project = delivered(client, "no-rewording")
    token = ceo_token(project)
    for statement in ("A documentation-accuracy gap in section 10 is closed",
                      "The shipped plan states the asymmetric case correctly",
                      "Verification signs off the corrected figure"):
        response = client.post(
            "/agent/requirements",
            json={"statement": statement, "acceptance_test": CMD_TEST,
                  "est_runs": 1},
            headers=bearer(token))
        assert response.status_code == 200, response.text
        requirement_id = response.json()["requirement_id"]

        with db_session() as session:
            assert session.get(Requirement, requirement_id).status == (
                RequirementStatus.PROPOSED
            ), "a post-delivery proposal landed as accepted scope"

        # ...and no work may be created for it, which is what "creates no scope"
        # means in practice.
        task = client.post("/agent/tasks", json={
            "team_id": project["teams"][0]["id"], "title": "start on it",
            "goal": "get ahead", "success_test": CMD_TEST,
            "requirement_id": requirement_id,
        }, headers=bearer(token))
        assert task.status_code >= 400, (
            f"work was created against an unaccepted post-delivery proposal: {task.text}"
        )


def test_reopening_lets_scope_move_again(client):
    """The refusal is a consequence of being finished, not a one-way door. The
    chairman reopens; the project is no longer delivered; work resumes."""
    project = delivered(client, "reopened")
    token = ceo_token(project)
    with db_session() as session:
        row = session.query(Requirement).filter_by(
            project_id=project["id"], key="R-01").one()
        row.status = RequirementStatus.IN_PROGRESS
        row.met_at = None
    assert propose(client, token, statement="A second thing is true")["requirement_id"]


# ── the CEO can see its own finish line ─────────────────────────────────────────


def test_the_ceo_can_see_that_it_delivered(client):
    """The pack used to go SILENT here: `requirements` is proposed-or-open, and
    `scope`, `velocity` and `standing` all hid behind it, so a CEO whose every
    requirement had gone true lost the only evidence it had finished. It read an
    empty board as an ordinary quiet day and went looking for work."""
    project = delivered(client, "ceo-can-see")
    from yaaf.core.models import Agent, Run
    from yaaf.server.contextpack import build_context_pack

    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        row = session.get(Project, project["id"])
        pack = build_context_pack(session, session.get(Run, run_id),
                                  session.get(Agent, project["ceo_agent_id"]), row)
    assert pack["scope"]["delivered"] is True
    assert pack["scope"]["met"] == 1
    assert pack["scope"]["open"] == 0
    note = pack["scope"]["note"]
    assert "FINISHED" in note
    assert "REFUSED" in note
    assert "do not manufacture work" in note.lower()


def test_pending_says_what_it_is_made_of(client):
    """The whole misreading turned on one unlabelled integer. `pending` is
    work + notices + unread; the CEO decided it was a notice count and told the
    chairman nothing was happening. Both were unread messages."""
    project = delivered(client, "labelled-pending")
    aged_leadership_post(client, project)
    from yaaf.core.constitution import effective

    with db_session() as session:
        row = session.get(Project, project["id"])
        state = dispatcher.activity_state(session, row, effective(session, row.id))
    ceo = next(a for a in state["agents"] if a["kind"] == "ceo")
    assert ceo["pending"] == sum(ceo["pending_detail"].values()), (
        "the total must be the parts, or the breakdown is decoration")
    assert ceo["pending_detail"]["unread"] == 1, (
        "the leadership message is the one thing the CEO could not name")
    assert ceo["pending_detail"]["work"] == 0


# ── telling him, and keeping on telling him ─────────────────────────────────────


def _readiness_lines(project: dict) -> list[str]:
    with db_session() as session:
        boardroom = session.query(Message).filter_by(project_id=project["id"])
        return [m.body for m in boardroom
                if m.sender_agent_id is None and m.sender_user_id is None]


def test_readiness_reaches_the_boardroom_and_the_ceo(client):
    """It used to go only to SMTP. The chairman was sitting in the boardroom
    asking his CEO why the project was still running, and the CEO had no way to
    know a readiness signal existed at all."""
    project = delivered(client, "told-properly")
    run_mode_tick(project)
    lines = _readiness_lines(project)
    assert len(lines) == 1, lines
    assert "Confirm completion" in lines[0]
    with db_session() as session:
        notices = session.query(Notice).filter_by(
            project_id=project["id"], kind=NoticeKind.DELIVERY_READY).all()
        assert len(notices) == 1
        assert "Do not invent work" in notices[0].body


def test_readiness_refires_after_a_reopen(client):
    """The dedup was once per PROJECT, ever. The incident's project went ready,
    was announced, had its last requirement reopened forty minutes later for
    real defects, and became genuinely ready again two hours after that — with
    the single shot already spent on a state that no longer held. Nobody was
    ever told about the delivery that actually happened."""
    project = delivered(client, "refires")
    run_mode_tick(project)
    assert len(_readiness_lines(project)) == 1

    with db_session() as session:
        row = session.query(Requirement).filter_by(
            project_id=project["id"], key="R-01").one()
        row.status = RequirementStatus.IN_PROGRESS
        row.met_at = None
    run_mode_tick(project)
    assert len(_readiness_lines(project)) == 1, "an open project is not announced"

    fabricate_met(project["id"], project["requirement_id"])
    run_mode_tick(project)
    assert len(_readiness_lines(project)) == 2, "the second delivery was never announced"


def test_an_unconfirmed_delivery_is_chased_but_not_every_tick(client):
    """He is reminded, on a rhythm. A janitor tick runs every fifteen seconds,
    so a nag with no beat behind it would be its own kind of spam."""
    from yaaf.server import janitor

    project = delivered(client, "chased")
    run_mode_tick(project)
    for _ in range(5):
        run_mode_tick(project)
    assert len(_readiness_lines(project)) == 1, "chased on every tick"

    from yaaf.core.models import Heartbeat

    with db_session() as session:
        beat = session.query(Heartbeat).filter_by(
            agent_id=project["ceo_agent_id"], name=janitor.DELIVERY_BEAT).one()
        beat.last_fired_at = utcnow() - timedelta(
            hours=janitor.DELIVERY_NAG_HOURS + 1)
    run_mode_tick(project)
    lines = _readiness_lines(project)
    assert len(lines) == 2
    assert "Still waiting on you" in lines[1]


def test_the_chase_never_wakes_an_agent(client):
    """The nag is platform-authored and sender-less, which is the whole reason
    it can repeat: the dispatcher skips those lines, so reminding the chairman
    forever costs nothing. A nag that bought a run would be the bug wearing a
    different hat.

    The readiness NOTICE does buy one run, deliberately — the CEO has to learn
    it finished and say so. So consume it first, the way a run would, and then
    prove the boardroom lines themselves are free however long they sit there.
    """
    from yaaf.server import janitor

    project = delivered(client, "free-chase")
    run_mode_tick(project)
    with db_session() as session:
        for notice in session.query(Notice).filter_by(project_id=project["id"],
                                                      consumed_at=None):
            notice.consumed_at = utcnow()
        beat = session.query(Heartbeat).filter_by(
            agent_id=project["ceo_agent_id"], name=janitor.DELIVERY_BEAT).one()
        beat.last_fired_at = utcnow() - timedelta(hours=janitor.DELIVERY_NAG_HOURS + 1)
    run_mode_tick(project)   # a second line: the chase
    assert len(_readiness_lines(project)) == 2

    with db_session() as session:
        for message in session.query(Message).filter_by(project_id=project["id"]):
            if message.sender_agent_id is None and message.sender_user_id is None:
                message.created_at = utcnow() - timedelta(
                    minutes=dispatcher.MESSAGE_COALESCE_MINUTES + 1)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0, (
            "a platform boardroom line bought a run")


# ── the rhythm stops too ────────────────────────────────────────────────────────


def _amend(client, project: dict, changes: dict) -> None:
    from tests.conftest import ADMIN

    response = client.post(f"/admin/projects/{project['id']}/amendments", headers=ADMIN,
                           json={"reason": "putting the CEO on a rhythm", "changes": changes})
    assert response.status_code == 200, response.text


def _a_minute_ago_utc() -> str:
    """A wall-clock time the daily wake is already past, whenever this runs."""
    then = utcnow() - timedelta(minutes=1)
    return f"{then.hour:02d}:{then.minute:02d}"


def test_a_delivered_project_stops_keeping_its_rhythm(client):
    """The chatter brake was only half of it. A standup and a planning beat are
    not messages, so they walked straight past it — and unlike chatter they
    re-arm forever: every morning at ceo_daily_at, every ceo_heartbeat_hours,
    on a project the chairman was told is not spending. Nothing is left to plan
    (every requirement is true and new scope is refused), so the whole run is
    the CEO deciding not to invent work."""
    project = delivered(client, "rhythm-stops")
    _amend(client, project, {"ceo_daily_at": _a_minute_ago_utc(),
                             "timezone": "UTC",
                             "ceo_heartbeat_always": True})
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0
        assert session.query(RunQueue).count() == 0


def test_the_same_rhythm_wakes_an_undelivered_project(client):
    """The control, and the one that would catch this brake being turned into a
    permanent gag: the standup is a real wake reason on a live project."""
    project = delivered(client, "rhythm-runs")
    _amend(client, project, {"ceo_daily_at": _a_minute_ago_utc(),
                             "timezone": "UTC",
                             "ceo_heartbeat_always": True})
    with db_session() as session:
        row = session.query(Requirement).filter_by(
            project_id=project["id"], key="R-01").one()
        row.status = RequirementStatus.IN_PROGRESS
        row.met_at = None
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def test_the_slept_rhythm_does_not_burst_when_he_confirms(client):
    """The beat has to advance while it is suppressed. If it did not, a project
    that sat delivered for a week would replay every morning it missed the
    moment its last requirement reopened — a burst of runs caused by the brake
    that was supposed to save them."""
    project = delivered(client, "no-burst")
    _amend(client, project, {"ceo_daily_at": _a_minute_ago_utc(), "timezone": "UTC"})
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0
        beat = session.query(Heartbeat).filter_by(
            agent_id=project["ceo_agent_id"], name="daily").one_or_none()
        assert beat is not None, "the suppressed standup left no mark to dedup against"
        assert beat.last_fired_at >= utcnow() - timedelta(minutes=5)


def test_ordinary_notices_stop_buying_runs_but_the_readiness_one_does_not(client):
    """Notices walked past the chatter brake the same way the rhythm did. Three
    routine ones — an amendment note, a memo — coalesce into a wake on any
    project, delivered or not, which is a run spent reading mail about work
    that is finished. The readiness notice is the exception on purpose: it is
    the news itself, and the CEO answering "we are done" in the boardroom is
    how the chairman finds out from the agent he asks."""
    project = delivered(client, "quiet-mail")
    ceo = project["ceo_agent_id"]
    with db_session() as session:
        for i in range(dispatcher.NOTICE_COALESCE_COUNT):
            session.add(Notice(
                id=f"ntc_routine_{i}", project_id=project["id"], agent_id=ceo,
                kind=NoticeKind.CONSTITUTION_AMENDED, body=f"routine {i}"))
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0, "routine mail bought a run"

    with db_session() as session:
        session.add(Notice(id="ntc_ready", project_id=project["id"], agent_id=ceo,
                           kind=NoticeKind.DELIVERY_READY, body="you are finished"))
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1, (
            "the CEO was never told it delivered")


def test_urgent_notices_still_get_through_to_a_delivered_project(client):
    """The brake is about routine mail, not about news. A budget breach or a
    decided approval on a delivered project is exactly what cannot wait for a
    click that may not come until Monday."""
    project = delivered(client, "urgent-mail")
    with db_session() as session:
        session.add(Notice(id="ntc_urgent", project_id=project["id"],
                           agent_id=project["ceo_agent_id"],
                           kind=NoticeKind.BUDGET_EXHAUSTED, body="the cap is gone"))
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1
