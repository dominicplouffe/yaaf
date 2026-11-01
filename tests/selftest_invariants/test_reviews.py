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


def roll_once(project: dict) -> None:
    """Close the open cycle the way the janitor does: age it past its roll and
    tick. On a weekly-or-longer Operate rhythm the roll IS the meeting."""
    from yaaf.core.constitution import effective
    from yaaf.core.models import Cycle, CycleStatus
    from yaaf.server import janitor

    with db_session() as session:
        row = session.get(Project, project["id"])
        cons = effective(session, row.id)
        janitor.roll_cycles(session, row, cons)  # opens the first cycle if none
        cycle = session.query(Cycle).filter_by(
            project_id=project["id"], status=CycleStatus.OPEN).one()
        cycle.opened_at = utcnow() - timedelta(days=40)
        janitor.roll_cycles(session, row, cons)


def open_review(project: dict) -> dict:
    make_due(project)
    with db_session() as session:
        mode = session.get(Project, project["id"]).mode
    if mode == "operate":
        roll_once(project)
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


# ── what was done, from the record ─────────────────────────────────────────────


def _open_view(client, project: dict) -> dict:
    return client.get(f"/observe/projects/{project['id']}/reviews", headers=ADMIN).json()["open"]


def test_the_meeting_opens_on_what_was_done_from_the_platforms_own_events(client):
    """The first thing on the meeting is what happened, by name — requirements
    the platform settled (and how), cuts with the reason the chairman gave —
    and none of it was written by an agent. R-00 lifting is held out on the
    grounds velocity holds it out: the plan starting to add up is not scope
    landing."""
    from tests.selftest_invariants.test_scope_verbs import settle

    project = deliver_project(client, slug="recorded")
    token = ceo_token(project)
    done = propose(client, token, statement="The landing page converts a test signup")
    accept(client, done["requirement_id"], project)
    gone = propose(client, token, statement="A webinar is scheduled")
    accept(client, gone["requirement_id"], project)
    settle(done["requirement_id"])
    cut = client.post(f"/admin/requirements/{gone['requirement_id']}/cut",
                      json={"reason": "no audience to test on yet"}, headers=ADMIN)
    assert cut.status_code == 200, cut.text

    open_review(project)
    view = _open_view(client, project)
    record = view["record"]
    met = {r["key"]: r for r in record["requirements_met"]}
    assert done["key"] in met
    assert met[done["key"]]["how"] == "verified"   # a command test: the platform ran it
    assert "R-00" not in met
    assert record["requirements_cut"] == [
        {"key": gone["key"], "statement": "A webinar is scheduled",
         "reason": "no audience to test on yet"}]
    assert record["spot_checks"]["considered"] == 0
    assert "requirements true" in view["headline"]
    assert "spend" in view["headline"]

    with db_session() as session:
        frozen = session.query(Review).filter_by(project_id=project["id"]).one().record
    assert frozen["requirements_met"] == record["requirements_met"]


def _ceo_pack(project: dict) -> dict:
    import uuid

    from yaaf.core.models import Agent, Run, RunStatus
    from yaaf.server.contextpack import build_context_pack

    with db_session() as session:
        # A fresh id each time: one test reads the pack before and after he speaks.
        run = Run(id="run_" + uuid.uuid4().hex[:24], project_id=project["id"],
                  agent_id=project["ceo_agent_id"], status=RunStatus.RUNNING,
                  trigger={"reasons": []})
        session.add(run)
        session.flush()
        return build_context_pack(
            session, run, session.get(Agent, project["ceo_agent_id"]),
            session.get(Project, project["id"]))


def test_the_ceo_is_handed_the_record_and_told_to_explain_it(client):
    """Step 1 kept the record out of the CEO's pack; step 2 hands it over with
    one instruction — explain it, do not restate it — because the report's
    `done` is meaning on top of the platform's list, not a second list."""
    project = deliver_project(client, slug="handed")
    open_review(project)
    block = _ceo_pack(project)["review"]
    assert block["record"] is not None
    assert "requirements_met" in block["record"]
    assert "review_report" in block["note"]
    assert "do not restate it" in block["note"]
    assert "headline" not in block   # the sentence is the chairman's, composed for him


def test_a_meeting_opened_before_the_record_still_has_a_headline(client):
    """Meetings from before this change carry numbers and no record. The
    screen says so; the headline is a function of the numbers and still
    speaks, rather than a record being invented after the fact."""
    project = deliver_project(client, slug="older")
    review = open_review(project)
    with db_session() as session:
        session.get(Review, review["id"]).record = None
    view = _open_view(client, project)
    assert view["record"] is None
    assert view["headline"]


def test_an_explore_meeting_speaks_of_bets_not_requirements(client):
    """Explore has no requirements and no date, so the Deliver-shaped tiles
    read "—". Its scope is bets: the record says which lived, which were
    killed and which died on their date, and the headline counts them."""
    project = make_project(client, slug="betting")  # explore
    token = ceo_token(project)
    kill = utcnow() + timedelta(days=3)

    def bet(statement: str) -> str:
        filed = client.post("/agent/hypotheses",
                            json={"statement": statement,
                                  "kill_condition": "under 10 replies in 200 sends",
                                  "kill_date": kill.isoformat()},
                            headers=bearer(token))
        assert filed.status_code == 200, filed.text
        return filed.json()["id"]

    dead = bet("cold email converts at two percent")
    alive = bet("accountants will pay forty-nine a month")
    killed = client.post(f"/agent/hypotheses/{dead}/kill",
                         json={"reason": "zero replies in two hundred sends"},
                         headers=bearer(token))
    assert killed.status_code == 200, killed.text

    open_review(project)
    view = _open_view(client, project)
    bets = view["record"]["bets"]
    assert [h["id"] for h in bets["alive"]] == [alive]
    assert [h["id"] for h in bets["killed"]] == [dead]
    assert len(bets["added"]) == 2
    assert view["headline"].startswith("1 bet alive, 1 killed this period.")
    assert "requirement" not in view["headline"]


# ── the status report: every section, or a refusal ────────────────────────────


def _report(serves: str, ask: dict | None = None, **over) -> dict:
    body = {
        "done": "The week settled the two cheapest channels and bought us a first "
                "real date for a customer conversation.",
        "variance": {"what": "The date moved because the Ads channel went dark.",
                     "largest_cause": "The Google Ads account was suspended on Oct 29 "
                                      "and nine runs died retrying it."},
        "next": [{"what": "Send the cold sequence to the 140-name list",
                  "serves": serves, "by": "2026-11-07T00:00:00Z"}],
        "goal_effect": "Takes scope to all requirements true by Nov 2.",
        "ask": ask or {"kind": "proceed", "levers": [], "why": None},
    }
    body.update(over)
    return body


def _scoped(client, slug: str) -> tuple[dict, str, str]:
    """A Deliver project with one accepted requirement and an open review."""
    project = deliver_project(client, slug=slug)
    token = ceo_token(project)
    req = propose(client, token, statement="The landing page converts a test signup")
    accept(client, req["requirement_id"], project)
    open_review(project)
    return project, token, req["key"]


def test_the_report_is_the_ceos_files_once_and_renders_the_old_filing(client):
    """One filing feeds both readers: the structured report for the screen,
    and the free text every older reader of the meeting keeps reading — so
    the green rule, the cycle narrative and the closing mail cannot disagree
    with the report."""
    project, token, key = _scoped(client, "reported")
    lead = agent_by_name(project, "Lead")
    _, lead_token = live_token(project["id"], lead)
    assert client.post("/agent/review_report", json=_report(key),
                       headers=bearer(lead_token)).status_code == 403

    filed = client.post("/agent/review_report",
                        json=_report(key, ask={"kind": "change", "why": "no proof yet",
                                               "levers": [{"kind": "accept_slip",
                                                           "detail": "accept Nov 2"}]}),
                        headers=bearer(token))
    assert filed.status_code == 200, filed.text
    assert filed.json()["next_items"] == 1

    view = _open_view(client, project)
    assert view["report"]["ask"]["kind"] == "change"
    assert view["report"]["next"][0]["serves"] == key
    assert "Largest cause: The Google Ads account" in view["narrative"]
    assert view["recommended"].startswith("Accept the slip: accept Nov 2")

    again = client.post("/agent/review_report", json=_report(key), headers=bearer(token))
    assert again.status_code == 409
    old = client.post("/agent/review_narrative", json={"narrative": "x" * 60},
                      headers=bearer(token))
    assert old.status_code == 409, "the two filings are one meeting, not two"


def test_a_next_item_must_serve_something_the_platform_holds_open(client):
    """A plan of things that serve nothing is how a project stays busy without
    finishing — the task rule, applied to the report. The refusal names what
    the item said and what it could have said."""
    project, token, key = _scoped(client, "serves")
    refused = client.post("/agent/review_report", json=_report("R-99"),
                          headers=bearer(token))
    assert refused.status_code == 422, refused.text
    assert "'R-99'" in refused.json()["error"]
    assert key in refused.json()["error"]

    empty = client.post("/agent/review_report", json=_report(key, next=[]),
                        headers=bearer(token))
    assert empty.status_code == 422
    assert "`next` is empty" in empty.json()["error"]

    with db_session() as session:
        assert session.query(Review).filter_by(project_id=project["id"]).one().report is None


def test_the_ask_names_real_levers_or_none(client):
    project, token, key = _scoped(client, "asks")
    hollow = client.post("/agent/review_report",
                         json=_report(key, ask={"kind": "change", "levers": [], "why": "x"}),
                         headers=bearer(token))
    assert hollow.status_code == 422
    assert "names at least one lever" in hollow.json()["error"]

    mixed = client.post("/agent/review_report",
                        json=_report(key, ask={"kind": "proceed", "why": None,
                                               "levers": [{"kind": "cut", "detail": "cut it"}]}),
                        headers=bearer(token))
    assert mixed.status_code == 422
    assert "carries no levers" in mixed.json()["error"]

    bogus = client.post("/agent/review_report",
                        json=_report(key, ask={"kind": "change", "why": None,
                                               "levers": [{"kind": "fire_everyone",
                                                           "detail": "now"}]}),
                        headers=bearer(token))
    assert bogus.status_code == 422


def test_a_report_that_asks_to_proceed_is_a_green_meeting(client, monkeypatch):
    """`proceed` renders no recommendation, so the rule that closes a meeting
    nobody needs reads it exactly as it read "recommended nothing"."""
    from yaaf.server import janitor

    project, token, key = _scoped(client, "greenlit")
    filed = client.post("/agent/review_report", json=_report(key), headers=bearer(token))
    assert filed.status_code == 200, filed.text
    with db_session() as session:
        review = session.query(Review).filter_by(project_id=project["id"]).one()
        assert review.recommended is None
        assert janitor._review_needs_nobody(review) is not None


def test_a_report_that_asks_for_change_is_a_meeting_worth_having(client):
    from yaaf.server import janitor

    project, token, key = _scoped(client, "chased")
    filed = client.post("/agent/review_report",
                        json=_report(key, ask={"kind": "change", "why": None,
                                               "levers": [{"kind": "move_date",
                                                           "detail": "to Nov 2"}]}),
                        headers=bearer(token))
    assert filed.status_code == 200, filed.text
    with db_session() as session:
        review = session.query(Review).filter_by(project_id=project["id"]).one()
        assert janitor._review_needs_nobody(review) is None


def test_an_explore_report_serves_bets_and_an_operate_report_may_serve_the_cycle(client):
    from tests.selftest_invariants.test_modes import operate_project

    explore = make_project(client, slug="bets-report")
    token = ceo_token(explore)
    filed = client.post("/agent/hypotheses",
                        json={"statement": "accountants will pay forty-nine a month",
                              "kill_condition": "under 3 of 10 say yes",
                              "kill_date": (utcnow() + timedelta(days=5)).isoformat()},
                        headers=bearer(token))
    bet = filed.json()["id"]
    open_review(explore)
    assert client.post("/agent/review_report", json=_report("R-01"),
                       headers=bearer(token)).status_code == 422
    ok = client.post("/agent/review_report", json=_report(bet), headers=bearer(token))
    assert ok.status_code == 200, ok.text

    operate = operate_project(client, slug="cycle-report")
    token = ceo_token(operate)
    open_review(operate)
    ok = client.post("/agent/review_report", json=_report("cycle"), headers=bearer(token))
    assert ok.status_code == 200, ok.text
    assert _open_view(client, operate)["report"]["next"][0]["serves"] == "cycle"


# ── plan versus actual ──────────────────────────────────────────────────────────


def _second_meeting(client, slug: str):
    """Meeting #1 filed with a two-item plan (one requirement each), closed;
    then one requirement settled true and meeting #2 opened on the plan."""
    from tests.selftest_invariants.test_scope_verbs import settle

    project = deliver_project(client, slug=slug)
    token = ceo_token(project)
    landed = propose(client, token, statement="The landing page converts a test signup")
    accept(client, landed["requirement_id"], project)
    stuck = propose(client, token, statement="The Ads account is unblocked")
    accept(client, stuck["requirement_id"], project)
    first = open_review(project)
    body = _report(landed["key"])
    body["next"] = [
        {"what": "Ship the landing page and prove a signup", "serves": landed["key"],
         "by": "2026-11-07T00:00:00Z"},
        {"what": "Get the Ads account unblocked", "serves": stuck["key"], "by": None},
    ]
    filed = client.post("/agent/review_report", json=body, headers=bearer(token))
    assert filed.status_code == 200, filed.text
    client.post(f"/admin/reviews/{first['id']}/close", json={}, headers=ADMIN)
    settle(landed["requirement_id"])
    make_due(project, days=16)
    run_review_tick(project)
    return project, token, landed["key"], stuck["key"]


def test_the_next_meeting_opens_on_what_the_ceo_said_it_would_do(client):
    """The levers were accountable — shown beside what they bought. The CEO's
    plan now is too: each item of last meeting's `next`, marked from the
    record at open, before the CEO has said a word about it."""
    project, _, landed, stuck = _second_meeting(client, "held-to-it")
    view = _open_view(client, project)
    assert view["number"] == 2
    plan = view["numbers"]["prior"]["plan"]
    assert [(p["item"], p["serves"], p["mark"]) for p in plan] == [
        (0, landed, "true"), (1, stuck, "open")]
    assert plan[0]["says"] == "true · verified"
    assert plan[0]["kind"] == "requirement"
    block = _ceo_pack(project)["review"]
    assert "last_plan" in block["note"]


def test_the_report_must_answer_for_every_item_of_the_last_plan(client):
    project, token, _, stuck = _second_meeting(client, "answer-all")
    body = _report(stuck)   # no last_plan at all
    refused = client.post("/agent/review_report", json=body, headers=bearer(token))
    assert refused.status_code == 422, refused.text
    assert "says nothing about item 0" in refused.json()["error"]

    body["last_plan"] = [{"item": 0, "outcome": "done", "note": None},
                         {"item": 1, "outcome": "not_done", "note": "account suspended"},
                         {"item": 7, "outcome": "dropped", "note": None}]
    refused = client.post("/agent/review_report", json=body, headers=bearer(token))
    assert refused.status_code == 422
    assert "names item 7" in refused.json()["error"]


def test_done_against_a_requirement_the_platform_holds_open_is_refused(client):
    """Done is never self-graded. The platform's mark is beside the item; a
    CEO that contradicts it is refused naming the key, and the honest line
    — partly, not done — is accepted."""
    project, token, _, stuck = _second_meeting(client, "self-graded")
    body = _report(stuck)
    body["last_plan"] = [{"item": 0, "outcome": "done", "note": None},
                         {"item": 1, "outcome": "done", "note": "I am sure it is"}]
    refused = client.post("/agent/review_report", json=body, headers=bearer(token))
    assert refused.status_code == 422, refused.text
    assert stuck in refused.json()["error"]
    assert "never self-graded" in refused.json()["error"]

    body["last_plan"][1] = {"item": 1, "outcome": "not_done",
                            "note": "the account was suspended on Oct 29"}
    filed = client.post("/agent/review_report", json=body, headers=bearer(token))
    assert filed.status_code == 200, filed.text
    view = _open_view(client, project)
    assert view["report"]["last_plan"][1]["outcome"] == "not_done"
    assert "Last plan: item 0 done; item 1 not done — the account" in view["narrative"]


def test_a_last_plan_is_refused_when_there_was_no_last_plan(client):
    """A first meeting has nothing to answer for, and a filing that answers
    for a plan nobody recorded is a filing inventing its own record."""
    project, token, key = _scoped(client, "no-prior")
    body = _report(key)
    body["last_plan"] = [{"item": 0, "outcome": "done", "note": None}]
    refused = client.post("/agent/review_report", json=body, headers=bearer(token))
    assert refused.status_code == 422
    assert "recorded no plan" in refused.json()["error"]


# ── the ask, answered: say something back, and what a lever moved ──────────────


def _ceo_reasons(project: dict) -> list[dict]:
    from yaaf.core.constitution import effective
    from yaaf.core.models import Agent
    from yaaf.server import dispatcher

    with db_session() as session:
        row = session.get(Project, project["id"])
        agent = session.get(Agent, project["ceo_agent_id"])
        return dispatcher._wake_reasons(session, row, effective(session, row.id), agent)


def _ceo_says(client, project: dict, token: str, thread_id: str, body: str) -> None:
    from yaaf.core.models import Channel, ChannelKind

    with db_session() as session:
        channel = session.query(Channel).filter_by(
            project_id=project["id"], kind=ChannelKind.BOARDROOM).one().id
    posted = client.post("/agent/messages",
                         json={"channel_id": channel, "thread_id": thread_id, "body": body},
                         headers=bearer(token))
    assert posted.status_code == 200, posted.text


def test_saying_something_back_is_one_boardroom_thread_per_meeting(client):
    """The chairman's words go where they always went — the boardroom, the
    same CEO wake — kept in one thread per meeting so the meeting's record
    is a thread and not the whole room. A closed meeting takes no more."""
    project, token, key = _scoped(client, "spoken-to")
    review = _open_view(client, project)
    first = client.post(f"/admin/reviews/{review['id']}/say",
                        json={"body": "If Ads is dead, do we need R-29 at all?"}, headers=ADMIN)
    assert first.status_code == 200, first.text
    assert first.json()["thread_id"] == first.json()["id"], "the first word opens the thread"
    second = client.post(f"/admin/reviews/{review['id']}/say",
                         json={"body": "And what would you cut instead?"}, headers=ADMIN)
    assert second.json()["thread_id"] == first.json()["id"]

    view = _open_view(client, project)
    assert [m["from"] for m in view["thread"]["messages"]] == ["you", "you"]
    assert view["thread"]["unanswered"] is True
    assert "thread" in _ceo_pack(project)["review"]
    assert _ceo_pack(project)["review"]["thread"]["reserve_replies_left"] == 2

    _ceo_says(client, project, token, first.json()["id"], "No. Cut it; the sequence covers it.")
    view = _open_view(client, project)
    assert [m["from"] for m in view["thread"]["messages"]] == ["you", "you", "ceo"]
    assert view["thread"]["unanswered"] is False

    client.post(f"/admin/reviews/{review['id']}/close", json={}, headers=ADMIN)
    late = client.post(f"/admin/reviews/{review['id']}/say", json={"body": "one more"},
                       headers=ADMIN)
    assert late.status_code == 409


def test_an_unanswered_word_on_the_meeting_rides_the_reserve_until_the_cap(client):
    """The answer is governance-classed — it lands on the day the allowance is
    gone — for `review_replies` answers per meeting. Past the cap it is an
    ordinary unread message and waits like one; nothing is refused."""
    project, token, key = _scoped(client, "reserved-replies")
    review = _open_view(client, project)
    thread = client.post(f"/admin/reviews/{review['id']}/say",
                         json={"body": "Why did nine runs die on one account?"},
                         headers=ADMIN).json()["thread_id"]
    reasons = _ceo_reasons(project)
    kinds = [r["kind"] for r in reasons]
    assert "messages" in kinds, "the boardroom wake is the carrier"
    assert "review_reply" in kinds
    from yaaf.server import dispatcher
    assert dispatcher._is_governance(reasons)

    for i in range(2):
        _ceo_says(client, project, token, thread, f"Answer {i}: the appeal loop retried itself.")
        client.post(f"/admin/reviews/{review['id']}/say",
                    json={"body": f"Follow-up {i}"}, headers=ADMIN)
    reasons = _ceo_reasons(project)
    assert "messages" in [r["kind"] for r in reasons], "still unread, still answered"
    assert "review_reply" not in [r["kind"] for r in reasons], "but on the ordinary allowance"
    assert _ceo_pack(project)["review"]["thread"]["reserve_replies_left"] == 0


def test_a_lever_records_what_it_moved(client):
    """Pressing a lever was recorded as what was pressed. The decision now
    carries what it changed on the record too, so the loop closes on the
    meeting and not only on the Roadmap."""
    project, token, key = _scoped(client, "moved")
    review = _open_view(client, project)
    with db_session() as session:
        rid = session.query(Requirement).filter_by(project_id=project["id"], key=key).one().id
    pulled = client.post(f"/admin/reviews/{review['id']}/lever",
                         json={"kind": "cut", "requirement_id": rid,
                               "reason": "the sequence covers it"}, headers=ADMIN)
    assert pulled.status_code == 200, pulled.text
    decision = _open_view(client, project)["decisions"][0]
    assert decision["effect"] == "1 → 0 requirements open"


def test_the_green_rule_reads_the_report_s_ask_itself(client):
    """`proceed` is green and `change` is a meeting worth having, read off the
    report rather than off the rendered recommendation — so a future change
    to how the text is rendered cannot change what closes itself."""
    from yaaf.server import janitor

    project, token, key = _scoped(client, "asked-outright")
    filed = client.post("/agent/review_report", json=_report(key), headers=bearer(token))
    assert filed.status_code == 200, filed.text
    with db_session() as session:
        review = session.query(Review).filter_by(project_id=project["id"]).one()
        assert janitor._review_needs_nobody(review) is not None
        review.recommended = "a stray line that is not the ask"
        assert janitor._review_needs_nobody(review) is not None, "the report is the authority"
        review.report = {**review.report, "ask": {"kind": "change", "levers": [], "why": None}}
        assert janitor._review_needs_nobody(review) is None



# ── on an Operate rhythm the roll is the meeting ────────────────────────────────


def test_an_operate_meeting_opens_at_the_roll_not_on_the_interval(client):
    """A weekly cycle and a seven-day review landed a day apart and were the
    same conversation held twice. On a rhythm of a week or more the meeting
    opens when the cycle rolls, and the interval alone opens nothing."""
    from tests.selftest_invariants.test_modes import operate_project

    project = operate_project(client, slug="at-the-roll")
    make_due(project, days=30)
    run_review_tick(project)
    with db_session() as session:
        assert session.query(Review).filter_by(project_id=project["id"]).count() == 0, \
            "thirty days old and no cycle closed: no meeting"
    agenda = client.get(f"/observe/projects/{project['id']}/agenda", headers=ADMIN).json()
    meeting = next(e for e in agenda["events"] if e["kind"] == "review")
    assert "rolls" in meeting["detail"]

    roll_once(project)
    run_review_tick(project)
    with db_session() as session:
        review = session.query(Review).filter_by(project_id=project["id"]).one()
        assert review.number == 1
        assert review.record["cycle"]["ordinal"] == 1, "opened on the cycle that just closed"


def test_a_daily_operate_rhythm_keeps_the_interval(client):
    """Seven meetings a week is a feed, not a meeting."""
    from tests.selftest_invariants.test_modes import operate_project

    project = operate_project(client, slug="daily-ops", constitution={"cycle_frequency": "daily"})
    make_due(project)
    run_review_tick(project)
    with db_session() as session:
        assert session.query(Review).filter_by(project_id=project["id"]).count() == 1
