"""Invariant selftests: the status meeting, and six levers.

The chairman described it: "status meetings to discuss the goal and
hypothesis, alter requirements, and judge whether the deadline will be met" —
all three clauses. What is attacked here:

  * the janitor opens, numbers and emails the review with ZERO tokens — a
    project with an empty board, or none of its daily runs left, still gets
    its meeting
  * the CEO's narrative rides a governance-classed wake, files exactly once,
    and only the CEO files it
  * six levers — cut, add, reword, move the date, raise the budget, accept
    the slip — each acts through the same mechanism it would outside the
    meeting and is recorded on the review with the chairman's reason
  * the next review shows last review's decisions beside what they bought
  * a missed review nags, then escalates, and never stops the work
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from tests.selftest_invariants.test_requirements import (
    accept,
    ceo_token,
    deliver_project,
    propose,
)
from yaaf.core.db import db_session
from yaaf.core.models import (
    Notice,
    NoticeKind,
    Project,
    Requirement,
    RequirementStatus,
    Review,
    ReviewStatus,
    utcnow,
)


def run_review_tick(project: dict) -> None:
    from yaaf.core.constitution import effective
    from yaaf.server import janitor

    with db_session() as session:
        row = session.get(Project, project["id"])
        cons = effective(session, row.id)
        janitor.open_reviews(session, row, cons)
        janitor.nag_reviews(session, row)


def make_due(project: dict, days: float = 8) -> None:
    """Age the project (or its latest review) past the interval."""
    with db_session() as session:
        row = session.get(Project, project["id"])
        row.created_at = utcnow() - timedelta(days=days)
        latest = session.query(Review).filter_by(project_id=project["id"]) \
            .order_by(Review.number.desc()).first()
        if latest is not None:
            latest.scheduled_for = utcnow() - timedelta(days=days)
            if latest.closed_at is not None:
                latest.closed_at = utcnow() - timedelta(days=days - 1)


def open_review(project: dict) -> dict:
    make_due(project)
    run_review_tick(project)
    with db_session() as session:
        review = session.query(Review).filter_by(
            project_id=project["id"], status=ReviewStatus.OPEN).one()
        return {"id": review.id, "number": review.number}


# ── the meeting always happens ──────────────────────────────────────────────────


def test_a_review_with_an_empty_board_still_fires(client):
    """That is the one the chairman most needs."""
    project = make_project(client, slug="quiet")  # explore: no scope, no tasks
    make_due(project)
    run_review_tick(project)
    with db_session() as session:
        review = session.query(Review).filter_by(project_id=project["id"]).one()
        assert review.number == 1
        assert review.status == ReviewStatus.OPEN
        assert review.numbers["scope"]["open"] == 0
        ceo_notices = session.query(Notice).filter_by(
            kind=NoticeKind.REVIEW_OPENED, project_id=project["id"]).count()
        assert ceo_notices == 1


def test_the_meeting_is_not_due_early_and_does_not_stack(client):
    project = make_project(client, slug="early")
    run_review_tick(project)  # young project: nothing due
    with db_session() as session:
        assert session.query(Review).filter_by(project_id=project["id"]).count() == 0

    open_review(project)
    make_due(project, days=30)
    run_review_tick(project)  # one is already open; another never stacks on it
    with db_session() as session:
        assert session.query(Review).filter_by(project_id=project["id"]).count() == 1


def test_the_narrative_wake_is_governance_classed(client):
    """The reserve exists for exactly this run: the day ordinary work burned
    the allowance is the day the meeting matters most."""
    from yaaf.core.constitution import effective
    from yaaf.core.models import Agent
    from yaaf.server import dispatcher

    project = deliver_project(client, slug="reserve-review")
    open_review(project)
    with db_session() as session:
        row = session.get(Project, project["id"])
        agent = session.get(Agent, project["ceo_agent_id"])
        reasons = dispatcher._wake_reasons(session, row, effective(session, row.id), agent)
    kinds = [r["kind"] for r in reasons]
    assert "review" in kinds
    assert dispatcher._is_governance(reasons)


# ── the narrative: the CEO's, once ──────────────────────────────────────────────


def test_only_the_ceo_files_and_only_once(client):
    project = deliver_project(client, slug="narrated")
    open_review(project)

    lead = agent_by_name(project, "Lead")
    _, lead_token = live_token(project["id"], lead)
    body = {"narrative": "The pace fell because R-01 ate every run we gave it this "
                         "week; I recommend cutting its gold-plating half."}
    assert client.post("/agent/review_narrative", json=body,
                       headers=bearer(lead_token)).status_code == 403

    token = ceo_token(project)
    filed = client.post("/agent/review_narrative",
                        json={**body, "recommended": "cut the gold-plating"},
                        headers=bearer(token))
    assert filed.status_code == 200, filed.text

    again = client.post("/agent/review_narrative", json=body, headers=bearer(token))
    assert again.status_code == 409
    assert "already" in again.json()["error"]


def test_no_review_open_is_said_plainly(client):
    project = deliver_project(client, slug="unopened")
    token = ceo_token(project)
    response = client.post("/agent/review_narrative",
                           json={"narrative": "x" * 60}, headers=bearer(token))
    assert response.status_code == 409
    assert "No review is open" in response.json()["error"]


# ── six levers, each recorded ───────────────────────────────────────────────────


def test_the_levers_act_and_are_recorded_with_reasons(client):
    project = deliver_project(client, slug="levers")
    token = ceo_token(project)
    first = propose(client, token, statement="The heavy half nobody asked for")
    accept(client, first["requirement_id"], project)
    review = open_review(project)

    def pull(body: dict) -> dict:
        response = client.post(f"/admin/reviews/{review['id']}/lever",
                               json=body, headers=ADMIN)
        assert response.status_code == 200, response.text
        return response.json()

    pull({"kind": "cut", "reason": "gold-plating; the client never asked",
          "requirement_id": first["requirement_id"]})
    added = pull({"kind": "add", "reason": "the demo needs a landing page",
                  "statement": "A landing page exists and loads in under a second",
                  "acceptance_test": {"type": "command",
                                      "command": "curl -sf https://example.com/"},
                  "stage": 1, "est_runs": 4})
    assert "R-02" in added["recorded"]
    pull({"kind": "accept_slip", "reason": "late beats churn this month"})

    with db_session() as session:
        row = session.get(Review, review["id"])
        assert [d["kind"] for d in row.decisions] == ["cut", "add", "accept_slip"]
        assert all(d["reason"] for d in row.decisions)
        cut_row = session.get(Requirement, first["requirement_id"])
        assert cut_row.status == RequirementStatus.CUT, "the lever acts, not just records"
        added_row = session.query(Requirement).filter_by(
            project_id=project["id"], key="R-02").one()
        assert added_row.status == RequirementStatus.PENDING
        assert added_row.accepted_by_user_id is not None, \
            "a chairman-authored requirement is accepted at birth"


def test_reword_changes_words_and_nothing_else(client):
    project = deliver_project(client, slug="rewords")
    token = ceo_token(project)
    rid = propose(client, token, statement="A report exiists, sourced and dated",
                  est_runs=7)["requirement_id"]
    accept(client, rid, project)
    review = open_review(project)

    response = client.post(f"/admin/reviews/{review['id']}/lever",
                           json={"kind": "reword", "requirement_id": rid,
                                 "statement": "A report exists, sourced, dated and signed",
                                 "reason": "typo, and the client wants a signature"},
                           headers=ADMIN)
    assert response.status_code == 200, response.text
    with db_session() as session:
        row = session.get(Requirement, rid)
        assert "signed" in row.statement
        assert row.key == "R-01" and row.est_runs == 7, \
            "identity and estimate survive a reword — no laundering"


def test_raise_budget_rides_the_amendment_rails(client):
    project = deliver_project(client, slug="raiser")
    review = open_review(project)
    response = client.post(f"/admin/reviews/{review['id']}/lever",
                           json={"kind": "raise_budget", "amount": 2500,
                                 "reason": "the good kind of on-fire"},
                           headers=ADMIN)
    assert response.status_code == 200, response.text
    from yaaf.core.constitution import effective

    with db_session() as session:
        assert effective(session, project["id"]).monthly_usd_cap == 2500
        amended = session.query(Notice).filter_by(
            kind=NoticeKind.CONSTITUTION_AMENDED, project_id=project["id"]).count()
        assert amended == 1, "a budget raise is an amendment: recorded, disclosed"


def test_closing_records_no_change_and_a_closed_review_takes_no_levers(client):
    project = deliver_project(client, slug="closer")
    review = open_review(project)
    closed = client.post(f"/admin/reviews/{review['id']}/close", json={}, headers=ADMIN)
    assert closed.status_code == 200
    with db_session() as session:
        summary = session.query(Notice).filter(
            Notice.project_id == project["id"],
            Notice.body.contains("no changes")).count()
        assert summary == 1, "an explicit 'no change' is a record, not an absence"

    refused = client.post(f"/admin/reviews/{review['id']}/lever",
                          json={"kind": "accept_slip", "reason": "too late now"},
                          headers=ADMIN)
    assert refused.status_code == 409


# ── the next review remembers ───────────────────────────────────────────────────


def test_the_next_review_shows_decisions_beside_what_they_bought(client):
    project = deliver_project(client, slug="memoried")
    review = open_review(project)
    client.post(f"/admin/reviews/{review['id']}/lever",
                json={"kind": "accept_slip", "reason": "we ship what is done"},
                headers=ADMIN)
    client.post(f"/admin/reviews/{review['id']}/close", json={}, headers=ADMIN)

    make_due(project, days=16)
    run_review_tick(project)
    with db_session() as session:
        second = session.query(Review).filter_by(
            project_id=project["id"], number=2).one()
        prior = second.numbers["prior"]
        assert prior["number"] == 1
        assert prior["decisions"][0]["kind"] == "accept_slip"
        assert "met_since" in prior["bought"] and "pace_now" in prior["bought"]


# ── a missed review nags, then escalates, and never stops the work ─────────────


def test_a_missed_review_nags_then_escalates_and_blocks_nothing(client):
    project = deliver_project(client, slug="missed")
    token = ceo_token(project)
    review = open_review(project)

    with db_session() as session:
        session.get(Review, review["id"]).opened_at = utcnow() - timedelta(hours=30)
    run_review_tick(project)
    with db_session() as session:
        row = session.get(Review, review["id"])
        assert row.nag_sent_at is not None
        assert row.escalated_at is None

    with db_session() as session:
        session.get(Review, review["id"]).opened_at = utcnow() - timedelta(hours=80)
    run_review_tick(project)
    with db_session() as session:
        row = session.get(Review, review["id"])
        assert row.escalated_at is not None
        assert row.status == ReviewStatus.OPEN, "escalation is a louder email, not a brake"

    # The work never stopped: scope can still be proposed and accepted.
    proposed = propose(client, token, statement="Work continues under an open meeting")
    assert proposed["key"] == "R-01"


# ── a review nobody needed closes itself ────────────────────────────────────────


def _narrated(client, project: dict, review: dict, **extra) -> None:
    filed = client.post("/agent/review_narrative", headers=bearer(ceo_token(project)), json={
        "narrative": "Pace held at plan. The one slow requirement was the data pull, "
                     "which landed on Tuesday; nothing else moved against the plan.",
        **extra})
    assert filed.status_code == 200, filed.text


def _age(review: dict, hours: float) -> None:
    with db_session() as session:
        session.get(Review, review["id"]).opened_at = utcnow() - timedelta(hours=hours)


def test_a_green_review_with_nothing_recommended_closes_itself_and_says_so(client, monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr("yaaf.server.janitor.chairman_notify",
                        lambda session, proj, subject, body, **kw: sent.append((subject, body)))
    project = deliver_project(client, slug="green")
    review = open_review(project)
    _narrated(client, project, review)

    _age(review, 10)
    run_review_tick(project)
    with db_session() as session:
        assert session.get(Review, review["id"]).status == ReviewStatus.OPEN, "his day first"

    _age(review, 30)
    run_review_tick(project)
    with db_session() as session:
        row = session.get(Review, review["id"])
        assert row.status == ReviewStatus.CLOSED
        assert row.closed_by_user_id is None
        assert row.nag_sent_at is None, "closed, not chased"
    subject, body = next((s, b) for s, b in sent if "closed itself" in s)
    assert "Pace held at plan" in body, "what he would have read in the meeting reaches him"


def test_a_recommendation_is_a_meeting_worth_having(client):
    project = deliver_project(client, slug="recommends")
    review = open_review(project)
    _narrated(client, project, review, recommended="Cut the glossary to make the date.")
    _age(review, 30)
    run_review_tick(project)
    with db_session() as session:
        row = session.get(Review, review["id"])
        assert row.status == ReviewStatus.OPEN
        assert row.nag_sent_at is not None


def test_a_failed_spot_check_is_a_meeting_worth_having(client):
    project = deliver_project(client, slug="failed-audit")
    review = open_review(project)
    _narrated(client, project, review)
    with db_session() as session:
        row = session.get(Review, review["id"])
        row.numbers = {**row.numbers, "audit_coverage": {**row.numbers["audit_coverage"],
                                                          "failed": 1}}
    _age(review, 30)
    run_review_tick(project)
    with db_session() as session:
        assert session.get(Review, review["id"]).status == ReviewStatus.OPEN
