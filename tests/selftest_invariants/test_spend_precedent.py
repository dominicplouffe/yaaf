"""Invariant selftests: the third yes to the same kind of spend offers to stop
asking.

A chairman who approves a $40 data pull every week is answering the same
question every week, and every one is an interruption with a default-deny
clock on it. After enough yeses the platform offers the amendment that would
have let each of them through — raised to just above the largest he approved.

Attacked here: that it is only ever an offer (nothing moves until he amends),
that a no inside the line kills it, that silence is not a yes, and that the
wall is never part of the offer.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, bearer, live_token, make_project
from tests.selftest_invariants.test_spend_envelope import begin
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import Approval, Project, utcnow
from yaaf.server import janitor

ENVELOPE = {"external_monthly_usd_cap": 500.0, "max_usd_per_purchase": 100.0,
            "approval_threshold_usd": 250.0}


def _project(client, **constitution) -> dict:
    return make_project(client, slug="wallet", constitution={**ENVELOPE, **constitution})


def _file(client, project: dict, usd: float) -> str:
    _, tok = live_token(project["id"], project["ceo_agent_id"])
    response = client.post("/agent/approvals", headers=bearer(tok), json={
        "kind": "spend", "title": f"Pull the ${usd:g} dataset",
        "body": "Recommended: it is the input to this week's model.",
        "if_declined": "The model runs on last week's data.", "amount_usd": usd})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _decide(client, approval_id: str, approve: bool = True) -> dict:
    decided = client.post(f"/admin/approvals/{approval_id}/decide", headers=ADMIN,
                          json={"approve": approve, "note": "" if approve else "not this one"})
    assert decided.status_code == 200, decided.text
    return decided.json()


def test_two_yeses_are_not_a_precedent(client):
    project = _project(client)
    for usd in (120, 130):
        assert "offer" not in _decide(client, _file(client, project, usd))


def test_the_third_yes_offers_the_line_just_above_the_largest(client):
    project = _project(client)
    for usd in (120, 130):
        _decide(client, _file(client, project, usd))
    offer = _decide(client, _file(client, project, 140))["offer"]
    # Only the line that asked: the threshold already sits above $150.
    assert offer["changes"] == {"max_usd_per_purchase": 150.0}
    assert "under $150 without asking" in offer["question"]
    assert "$500" in offer["keeps"]
    # An offer, not a change.
    with db_session() as session:
        assert effective(session, project["id"]).max_usd_per_purchase == 100.0


def test_taking_the_offer_lets_the_next_one_through_and_no_further(client):
    project = _project(client)
    offer = None
    for usd in (120, 130, 140):
        offer = _decide(client, _file(client, project, usd)).get("offer")
    taken = client.post(f"/admin/projects/{project['id']}/amendments", headers=ADMIN,
                        json={"reason": offer["reason"], "changes": offer["changes"]})
    assert taken.status_code == 200, taken.text

    assert begin(client, project, 145, key="under").status_code == 200
    over = begin(client, project, 155, key="over")
    assert over.status_code == 403
    assert "per-purchase line" in over.text


def test_a_no_inside_the_line_is_a_judgment_still_being_made(client):
    project = _project(client)
    _decide(client, _file(client, project, 125), approve=False)
    for usd in (120, 130):
        _decide(client, _file(client, project, usd))
    assert "offer" not in _decide(client, _file(client, project, 140))


def test_silence_is_not_a_yes(client):
    """An approval the janitor approved on timeout was never his decision."""
    project = _project(client, approval_timeout_decision="approve")
    for usd in (120, 130):
        approval_id = _file(client, project, usd)
        with db_session() as session:
            session.get(Approval, approval_id).expires_at = utcnow() - timedelta(minutes=1)
    with db_session() as session:
        row = session.get(Project, project["id"])
        janitor.expire_approvals(session, row, effective(session, row.id))
    assert "offer" not in _decide(client, _file(client, project, 140))


def test_the_wall_is_never_part_of_the_offer(client):
    """A line above the month's wall would offer a purchase the wall refuses."""
    project = _project(client, external_monthly_usd_cap=140.0)
    for usd in (120, 130):
        _decide(client, _file(client, project, usd))
    assert "offer" not in _decide(client, _file(client, project, 135))


def test_a_no_never_carries_an_offer(client):
    project = _project(client)
    for usd in (120, 130, 140):
        _decide(client, _file(client, project, usd))
    assert "offer" not in _decide(client, _file(client, project, 110), approve=False)
