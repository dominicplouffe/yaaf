"""Invariant selftests: Explore, Deliver, Operate — and the learning loop.

The three project types the chairman asked for, with the two things earlier
plans lost: an open-ended project that can still carry a date, and a
recurring project that actually learns. Attacked here:

  * Explore's deadline: settable only there, told to both parties exactly
    once when it passes, and pausing nothing — the judgment was always his
  * Deliver completion: detected by the janitor, confirmed by the chairman,
    refused while anything is open, and receipted with the load-bearing row
    "failed once before"
  * Operate cycles: the 29th–31st refused not clamped; rolls happen in the
    janitor; at most one cycle is ever open (a long pause resumes into one,
    not twenty-one); two skipped cycles pause the project and email him
  * the learning loop: the review writes the cycle narrative, and cycle N's
    pack carries cycles N-3..N-1 verbatim
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from tests.conftest import ADMIN, bearer, live_token, make_project, seed_role
from tests.selftest_invariants.test_requirements import (
    accept,
    ceo_token,
    deliver_project,
    propose,
    tick,
)
from yaaf.core.constitution import ConstitutionDoc, effective
from yaaf.core.db import db_session
from yaaf.core.models import (
    Cycle,
    CycleStatus,
    Notice,
    NoticeKind,
    Project,
    ProjectStatus,
    Requirement,
    RequirementStatus,
    utcnow,
)


def run_mode_tick(project: dict) -> None:
    from yaaf.server import janitor

    with db_session() as session:
        row = session.get(Project, project["id"])
        cons = effective(session, row.id)
        janitor.roll_cycles(session, row, cons)
        janitor.check_explore_deadline(session, row)
        janitor.check_delivery_ready(session, row)


def fabricate_met(project_id: str, requirement_id: str) -> None:
    from yaaf.core import ids
    from yaaf.core.models import RequirementEvent

    with db_session() as session:
        row = session.get(Requirement, requirement_id)
        row.status = RequirementStatus.MET
        row.met_at = utcnow()
        session.add(RequirementEvent(
            id=ids.new_id(ids.REQUIREMENT_EVENT), project_id=project_id,
            requirement_id=requirement_id, from_status=RequirementStatus.IN_PROGRESS,
            to_status=RequirementStatus.MET, actor="platform"))


def backdate_cycle(project_id: str, days: float) -> None:
    with db_session() as session:
        cycle = session.query(Cycle).filter_by(
            project_id=project_id, status=CycleStatus.OPEN).one()
        cycle.opened_at = utcnow() - timedelta(days=days)


# ── explore: a window, not a scope ──────────────────────────────────────────────


def test_a_bare_deadline_is_explore_shaped_and_a_dated_deliver_cites_its_source(client):
    """Deliver may hold a target date; it may not hold an anonymous one.

    This used to refuse a Deliver deadline outright, on the reasoning that
    "Deliver promises through a commitment". The refusal did not remove the
    date — the founder still had one in mind and wrote it into the goal PROSE,
    where nothing validates it, nothing re-projects it, and nothing compares it
    to the platform's own arithmetic. It then paced the work anyway. Every
    commitment on the install that produced this change was met the day it was
    made, against promises of up to 127 days.

    So the rule is now provenance rather than prohibition, and it is the rule
    `set_cycle_goal` has always applied to a cycle's due-by: say where the date
    came from. Explore's bare window is untouched.
    """
    deadline = (utcnow() + timedelta(days=60)).isoformat()
    explore = make_project(client, slug="windowed", deadline=deadline, start=False)
    with db_session() as session:
        assert session.get(Project, explore["id"]).deadline is not None

    def _deliver(slug: str, **extra):
        return client.post("/admin/projects", json={
            "slug": slug, "name": "X", "goal": "Ship a thing, datedly",
            "chairman_email": "chair@example.com", "ceo_role_slug": "ceo",
            "mode": "deliver", "deadline": deadline,
            "teams": [{"role_slug": "worker", "name": "A", "employees": ["E1"]}],
            **extra,
        }, headers=ADMIN)

    anonymous = _deliver("dated-deliver")
    assert anonymous.status_code == 422
    assert "provenance" in anonymous.json()["detail"]

    # A fact the world imposes, with a citation: accepted, and recorded as one.
    sourced = _deliver("booked-deliver", deadline_source={
        "kind": "external", "note": "the client's board meeting",
        "url": "https://example.com/agenda"})
    assert sourced.status_code == 200, sourced.text
    with db_session() as session:
        row = session.get(Project, sourced.json()["id"])
        assert row.deadline is not None
        assert row.deadline_source["kind"] == "external"
        assert row.deadline_source["url"] == "https://example.com/agenda"

    # A date the chairman simply wants: also fine, and NOT recorded as a fact.
    wished = _deliver("wished-deliver", deadline_source={
        "kind": "chairman", "note": "I want it before the holidays"})
    assert wished.status_code == 200, wished.text
    with db_session() as session:
        assert session.get(Project, wished.json()["id"]).deadline_source["kind"] == "chairman"


def _seed_chairman(client) -> None:
    """The users and roles `make_project` would have created. Needed by the
    tests below, which post to /admin/projects directly to assert on the
    refusal rather than the project."""
    client.post("/admin/users",
                json={"email": "chair@example.com", "display_name": "Chair"},
                headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")


def test_an_external_deadline_must_actually_cite_something(client):
    """"external" without a citation is a wish wearing a fact's clothes, which
    is the exact confusion the field exists to end."""
    _seed_chairman(client)
    refused = client.post("/admin/projects", json={
        "slug": "uncited", "name": "X", "goal": "Ship a thing",
        "chairman_email": "chair@example.com", "ceo_role_slug": "ceo",
        "mode": "deliver", "deadline": (utcnow() + timedelta(days=30)).isoformat(),
        "deadline_source": {"kind": "external"},
        "teams": [{"role_slug": "worker", "name": "A", "employees": ["E1"]}],
    }, headers=ADMIN)
    assert refused.status_code == 422
    assert "cites its source" in refused.json()["detail"]


def test_operate_still_refuses_a_project_date_because_its_cycles_hold_one(client):
    """The one mode where a second date really would be ambiguity: a cycle
    already carries a due-by and its provenance."""
    _seed_chairman(client)
    refused = client.post("/admin/projects", json={
        "slug": "dated-operate", "name": "X", "goal": "Run a desk",
        "chairman_email": "chair@example.com", "ceo_role_slug": "ceo",
        "mode": "operate", "deadline": (utcnow() + timedelta(days=30)).isoformat(),
        "deadline_source": {"kind": "chairman", "note": "why not"},
        "teams": [{"role_slug": "worker", "name": "A", "employees": ["E1"]}],
    }, headers=ADMIN)
    assert refused.status_code == 422
    assert "cycle" in refused.json()["detail"].lower()


def test_the_window_closing_tells_both_parties_once_and_pauses_nothing(client):
    project = make_project(
        client, slug="closing-window",
        deadline=(utcnow() + timedelta(days=1)).isoformat())
    with db_session() as session:
        session.get(Project, project["id"]).deadline = utcnow() - timedelta(hours=1)
    run_mode_tick(project)
    run_mode_tick(project)  # the second look must not nag again
    with db_session() as session:
        row = session.get(Project, project["id"])
        assert row.status == ProjectStatus.RUNNING, "the judgment is his, not a date's"
        notices = session.query(Notice).filter_by(
            kind=NoticeKind.EXPLORE_DEADLINE, project_id=project["id"]).count()
        assert notices == 1

    # And the project still works: task creation is not frozen.
    token = ceo_token(project)
    response = client.post("/agent/tasks", json={
        "team_id": project["teams"][0]["id"], "title": "keep going",
        "goal": "the chairman has not decided yet",
        "success_test": {"type": "observable",
                         "target": "https://example.org/anything exists"},
    }, headers=bearer(token))
    assert response.status_code == 200, response.text


# ── deliver: completion is his gate, receipted ──────────────────────────────────


def test_completion_is_refused_while_anything_is_open(client):
    project = deliver_project(client, slug="undone")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    refused = client.post(f"/admin/projects/{project['id']}/complete", headers=ADMIN)
    assert refused.status_code == 409
    assert "R-01" in refused.json()["detail"]


def test_completion_writes_the_receipt_and_failed_before_is_on_it(client):
    project = deliver_project(client, slug="delivered")
    token = ceo_token(project)
    first = propose(client, token, statement="The easy half exists", est_runs=2)
    second = propose(client, token, statement="The hard half exists", est_runs=3)
    accept(client, first["requirement_id"], project)
    accept(client, second["requirement_id"], project)

    with db_session() as session:
        session.get(Requirement, second["requirement_id"]).failure_load = 4
    fabricate_met(project["id"], first["requirement_id"])
    fabricate_met(project["id"], second["requirement_id"])

    run_mode_tick(project)  # readiness detected → chairman emailed once (log-only here)

    completed = client.post(f"/admin/projects/{project['id']}/complete", headers=ADMIN)
    assert completed.status_code == 200, completed.text
    receipt = completed.json()["receipt"]
    assert receipt["met"] == 2
    by_key = {r["key"]: r for r in receipt["requirements"]}
    assert by_key["R-02"]["failed_before"] is True, "the load-bearing row"
    assert by_key["R-01"]["failed_before"] is False
    with db_session() as session:
        assert session.get(Project, project["id"]).status == ProjectStatus.COMPLETED

    # Terminal: it cannot be restarted into a zombie.
    assert client.post(f"/admin/projects/{project['id']}/start",
                       headers=ADMIN).status_code == 409


def test_explore_and_operate_do_not_complete(client):
    explore = make_project(client, slug="nocomplete")
    refused = client.post(f"/admin/projects/{explore['id']}/complete", headers=ADMIN)
    assert refused.status_code == 422
    assert "Operate finishes and starts again" in refused.json()["detail"]


# ── operate: the rhythm ─────────────────────────────────────────────────────────


def operate_project(client, slug="ops", **kwargs):
    return make_project(client, slug=slug, mode="operate", **kwargs)


def test_the_29th_is_refused_not_clamped(client):
    with pytest.raises(ValidationError):
        ConstitutionDoc(cycle_frequency="monthly", cycle_day=29)
    response = client.post("/admin/projects", json={
        "slug": "day29", "name": "X", "goal": "Recurring, impossibly dated",
        "chairman_email": "chair@example.com", "ceo_role_slug": "ceo",
        "mode": "operate",
        "constitution": {"cycle_frequency": "monthly", "cycle_day": 29},
        "teams": [{"role_slug": "worker", "name": "A", "employees": ["E1"]}],
    }, headers=ADMIN)
    # For the right reason. This used to pass on a 422 about the chairman
    # account not existing, while the date itself was a 500.
    assert response.status_code == 422
    assert "cycle_day" in response.json()["detail"]


def test_starting_an_operate_project_opens_cycle_one(client):
    project = operate_project(client, slug="cycle-one")
    with db_session() as session:
        cycle = session.query(Cycle).filter_by(project_id=project["id"]).one()
        assert cycle.ordinal == 1 and cycle.status == CycleStatus.OPEN


def test_a_roll_carries_unsettled_scope_and_reopens_the_plan(client):
    project = operate_project(client, slug="roller")
    token = ceo_token(project)
    done = propose(client, token, statement="This one gets finished")
    carried = propose(client, token, statement="This one does not")
    accept(client, done["requirement_id"], project)
    accept(client, carried["requirement_id"], project)
    fabricate_met(project["id"], done["requirement_id"])
    tick(project)  # plan holds

    backdate_cycle(project["id"], days=9)  # weekly rhythm: well past the boundary
    run_mode_tick(project)
    with db_session() as session:
        cycles = session.query(Cycle).filter_by(project_id=project["id"]) \
            .order_by(Cycle.ordinal).all()
        assert [c.status for c in cycles] == [CycleStatus.MISSED, CycleStatus.OPEN]
        second = cycles[1]
        carried_row = session.get(Requirement, carried["requirement_id"])
        assert carried_row.cycle_id == second.id, "unsettled scope carries forward"
        done_row = session.get(Requirement, done["requirement_id"])
        assert done_row.cycle_id == cycles[0].id, "history stays where it happened"
        plan = session.query(Requirement).filter_by(
            project_id=project["id"], key="R-00").one()
        assert plan.status == RequirementStatus.PENDING, "each cycle plans fresh"
        rolled = session.query(Notice).filter_by(
            kind=NoticeKind.CYCLE_ROLLED, project_id=project["id"]).count()
        assert rolled == 1


def test_a_long_pause_resumes_into_one_cycle_not_twenty_one(client):
    project = operate_project(client, slug="long-pause")
    backdate_cycle(project["id"], days=21)
    assert client.post(f"/admin/projects/{project['id']}/pause",
                       json={}, headers=ADMIN).status_code == 200
    run_mode_tick(project)  # janitor never touches paused projects, but be sure
    assert client.post(f"/admin/projects/{project['id']}/start",
                       headers=ADMIN).status_code == 200
    run_mode_tick(project)
    run_mode_tick(project)
    with db_session() as session:
        open_count = session.query(Cycle).filter_by(
            project_id=project["id"], status=CycleStatus.OPEN).count()
        total = session.query(Cycle).filter_by(project_id=project["id"]).count()
        assert open_count == 1, "at most one open cycle, ever"
        assert total <= 3, f"three weeks away must not mint a backlog of cycles ({total})"


def test_two_skipped_cycles_pause_the_project_and_email_him(client):
    project = operate_project(client, slug="skipper")
    backdate_cycle(project["id"], days=9)
    run_mode_tick(project)  # skip #1: nothing was ever accepted
    backdate_cycle(project["id"], days=9)
    run_mode_tick(project)  # skip #2 → pause
    with db_session() as session:
        row = session.get(Project, project["id"])
        assert row.status == ProjectStatus.PAUSED
        assert "skipped" in (row.status_reason or "") or "nothing made true" in row.status_reason
        statuses = [c.status for c in session.query(Cycle)
                    .filter_by(project_id=project["id"]).order_by(Cycle.ordinal)]
        assert statuses == [CycleStatus.SKIPPED, CycleStatus.SKIPPED]

    # Resume opens a fresh cycle and the project breathes again.
    assert client.post(f"/admin/projects/{project['id']}/start",
                       headers=ADMIN).status_code == 200
    with db_session() as session:
        assert session.query(Cycle).filter_by(
            project_id=project["id"], status=CycleStatus.OPEN).one().ordinal == 3


# ── the learning loop ───────────────────────────────────────────────────────────


def test_cycle_sevens_pack_carries_four_five_and_six_verbatim(client):
    from yaaf.core import ids
    from yaaf.core.models import Agent, Run, RunStatus
    from yaaf.server.contextpack import build_context_pack

    project = operate_project(client, slug="learner")
    lessons = {
        4: "Cycle 4: the Tuesday send got 3x the replies of the Friday one.",
        5: "Cycle 5: the short subject line doubled opens; keep under 40 chars.",
        6: "Cycle 6: every reply came from the plain-text variant, none from HTML.",
    }
    with db_session() as session:
        current = session.query(Cycle).filter_by(project_id=project["id"]).one()
        for n in range(1, 7):
            session.add(Cycle(
                id=ids.new_id(ids.CYCLE), project_id=project["id"], ordinal=n + 100,
                status=CycleStatus.COMPLETED,
                opened_at=utcnow() - timedelta(days=8 * (7 - n)),
                closed_at=utcnow() - timedelta(days=8 * (7 - n) - 7),
                narrative=lessons.get(n, f"Cycle {n}: routine."),
            ))
        current.ordinal = 107

    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        run = session.get(Run, run_id)
        run.status = RunStatus.RUNNING
        pack = build_context_pack(
            session, run, session.get(Agent, project["ceo_agent_id"]),
            session.get(Project, project["id"]))

    narratives = pack["cycles"]["recent_narratives"]
    assert [c["narrative"] for c in narratives] == [lessons[4], lessons[5], lessons[6]], \
        "the last three, verbatim, oldest first — not summaries, not clips"
    assert pack["cycles"]["current"]["ordinal"] == 107


def test_the_review_writes_the_cycle_narrative(client):
    from tests.selftest_invariants.test_reviews import open_review

    project = operate_project(client, slug="review-writes")
    token = ceo_token(project)
    review = open_review(project)
    filed = client.post("/agent/review_narrative", json={
        "narrative": "Nothing shipped because the plan spent the week unaccepted; "
                     "the single largest cause is R-01 waiting on the chairman."},
        headers=bearer(token))
    assert filed.status_code == 200, filed.text
    client.post(f"/admin/reviews/{review['id']}/lever",
                json={"kind": "accept_slip", "reason": "my fault; noted"}, headers=ADMIN)
    closed = client.post(f"/admin/reviews/{review['id']}/close", json={}, headers=ADMIN)
    assert closed.status_code == 200
    with db_session() as session:
        cycle = session.query(Cycle).filter_by(
            project_id=project["id"], status=CycleStatus.OPEN).one()
        assert "single largest cause is R-01" in cycle.narrative, "verbatim, not summarized"
        assert "accept_slip" in cycle.narrative


def test_the_cycle_plan_wake_is_governance_classed(client):
    from yaaf.core.models import Agent
    from yaaf.server import dispatcher

    project = operate_project(client, slug="cycle-wake")
    with db_session() as session:
        row = session.get(Project, project["id"])
        agent = session.get(Agent, project["ceo_agent_id"])
        reasons = dispatcher._wake_reasons(session, row, effective(session, row.id), agent)
    assert "cycle_plan" in [r["kind"] for r in reasons]
    assert dispatcher._is_governance(reasons)
