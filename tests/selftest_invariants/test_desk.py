"""Invariant selftests: the desk shows live companies, and only those.

The desk answers one question — what breaks if I close this tab? A company
that is over cannot break, so it has no business being in the answer. It also
has no business costing anything: every aggregate the rollup computes is
frozen forever on a terminal project, and the page polls every ten seconds.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    AuditLog,
    Commitment,
    CommitmentStatus,
    Project,
    ProjectStatus,
    Requirement,
    RequirementStatus,
    utcnow,
)
from yaaf.server.janitor import janitor_once


def _desk(client, live: bool):
    url = "/observe/desk?live=true" if live else "/observe/desk"
    response = client.get(url, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_live_desk_drops_companies_that_are_over(client):
    live_project = make_project(client, slug="still-going")
    done = make_project(client, slug="all-done")
    client.post(f"/admin/projects/{done['id']}/stop",
                json={"reason": "goal met"}, headers=ADMIN)

    desk = _desk(client, live=True)
    assert [p["slug"] for p in desk["projects"]] == ["still-going"]
    assert desk["finished"] == 1

    # The archive is the Projects page's job, and it still gets everything.
    everything = _desk(client, live=False)
    assert {p["slug"] for p in everything["projects"]} == {"still-going", "all-done"}
    # Counted either way: the desk needs the number even when it is not
    # showing the rows, or "no live companies" reads as "no companies".
    assert everything["finished"] == 1
    assert live_project["id"] in {p["id"] for p in everything["projects"]}


def test_a_completed_company_leaves_the_desk_too(client):
    """Both terminal statuses, not just the one the stop button writes. A
    delivered Deliver company is the *success* end state and the most likely
    thing to accumulate, so leaking it here would fill the desk with exactly
    the companies that went right."""
    project = make_project(client, slug="delivered")
    with db_session() as session:
        session.get(Project, project["id"]).status = ProjectStatus.COMPLETED

    desk = _desk(client, live=True)
    assert desk["projects"] == []
    assert desk["finished"] == 1


def test_a_paused_company_stays_on_the_desk(client):
    """Paused is not terminal and is the whole reason the desk exists: the
    company has stopped and only the chairman restarts it."""
    project = make_project(client, slug="halted")
    client.post(f"/admin/projects/{project['id']}/pause",
                json={"reason": "rethinking the goal"}, headers=ADMIN)

    desk = _desk(client, live=True)
    assert [p["status"] for p in desk["projects"]] == ["paused"]
    assert desk["finished"] == 0


def test_the_desk_reports_yesterday_as_a_pair(client):
    """Spend alone is a vanity number and evidence alone hides what it cost.
    The desk ranks on both together — money moved, nothing proved — so the two
    fields travel with every project or the signal cannot be computed."""
    make_project(client, slug="acme")
    desk = _desk(client, live=True)
    row = desk["projects"][0]
    assert row["spend_24h"] == 0.0
    assert row["evidence_24h"] == 0


# ── the autonomy dial decides what is even eligible for a row ────────────────

AUTONOMOUS = {
    "plan_authority": "ceo",
    "scope_authority": "ceo",
    "commitment_authority": "ceo",
    "verdict_policy": "ceo_judgment",
    "capability_authority": "ceo",
}


def _stage_all_three(project_id: str) -> None:
    """One of each thing the Supervised preset says the chairman does: scope to
    accept, a date to sign, and an observable close to judge."""
    with db_session() as session:
        session.add(Requirement(
            id=ids.new_id(ids.REQUIREMENT), project_id=project_id, key="R-09",
            statement="The landing page converts at 2% over a fortnight.",
            acceptance_test={"type": "observable",
                             "target": "https://example.org/metrics shows >=2%"},
            status=RequirementStatus.PROPOSED, stage=1, est_runs=4,
        ))
        session.add(Requirement(
            id=ids.new_id(ids.REQUIREMENT), project_id=project_id, key="R-10",
            statement="Five named write-ups are published and reachable.",
            acceptance_test={"type": "observable",
                             "target": "https://example.org/notes lists five"},
            status=RequirementStatus.CHECKING, stage=1, est_runs=4,
        ))
        session.add(Commitment(
            id=ids.new_id(ids.COMMITMENT), project_id=project_id,
            status=CommitmentStatus.PROPOSED,
            deliver_by=utcnow() + timedelta(days=14), scope_snapshot=["R-01", "R-02"],
        ))


def test_a_supervised_desk_asks_for_the_three_things_it_is_for(client):
    """Accept scope, sign the date, judge the unverifiable close. These are the
    Supervised preset's whole job description, and each one blocks the company
    on a human until it happens."""
    project = make_project(client, slug="supervised")
    _stage_all_three(project["id"])

    waiting = _desk(client, live=True)["projects"][0]["awaiting_you"]
    assert waiting["plan"] == 1
    assert [v["key"] for v in waiting["verdicts"]] == ["R-10"]
    assert waiting["commitment"]["scope_count"] == 2


def test_an_autonomous_desk_asks_for_none_of_them(client):
    """Same three rows staged, same data — but the constitution put all three
    in the CEO's hands. Asking the chairman anyway would be asking for a
    decision he deliberately gave away, and the emptiness here is the dial
    working rather than nothing having happened."""
    project = make_project(client, slug="autonomous", constitution=AUTONOMOUS)
    _stage_all_three(project["id"])

    # Same three rows in the database as the supervised case above; the only
    # difference is the constitution.
    row = _desk(client, live=True)["projects"][0]
    assert row["awaiting_you"] == {"plan": 0, "verdicts": [], "commitment": None}


def test_autonomy_never_delegates_the_undelegable_approvals(client):
    """Authority is configuration; integrity is structure. Money over the
    threshold, legal ambiguity, a credential the CEO does not hold and a step
    only a human can take stay on the desk at every setting — so a fully
    autonomous project still surfaces them."""
    from yaaf.core.models import Approval, ApprovalKind, ApprovalStatus

    project = make_project(client, slug="autonomous", constitution=AUTONOMOUS)
    with db_session() as session:
        session.add(Approval(
            id=ids.new_id(ids.APPROVAL), project_id=project["id"],
            kind=ApprovalKind.SPEND, requested_by_agent_id=project["ceo_agent_id"],
            title="Filings data licence", body="I recommend it strongly.",
            amount_usd=4800, status=ApprovalStatus.PENDING,
            expires_at=utcnow() + timedelta(days=2),
        ))

    row = _desk(client, live=True)["projects"][0]
    assert [a["kind"] for a in row["pending_approvals"]] == ["spend"]


# ── the inbox and the desk agree, and neither is silent ──────────────────────


def _notices(project_id: str, action: str) -> list[dict]:
    """The nag's cadence lives in the audit trail rather than a column, so that
    is where the test reads it — the same row the chairman can read back."""
    with db_session() as session:
        return [
            {"entity_id": r.entity_id, "detail": r.detail}
            for r in session.query(AuditLog).filter(
                AuditLog.project_id == project_id, AuditLog.action == action
            ).all()
        ]


def test_scope_waiting_on_the_chairman_reaches_him_by_email(client):
    """yaaf used to run this exactly backwards. When the dial gives the CEO the
    signature it emails the chairman the moment it signs; when the chairman
    holds it, proposed scope landed and nothing was sent under any setting. It
    told him when a decision had been taken *for* him and went quiet when one
    was waiting *on* him.
    """
    project = make_project(client, slug="supervised")
    with db_session() as session:
        for key in ("R-09", "R-10"):
            session.add(Requirement(
                id=ids.new_id(ids.REQUIREMENT), project_id=project["id"], key=key,
                statement=f"{key}: five named write-ups are published and reachable.",
                acceptance_test={"type": "observable", "target": "https://example.org/x"},
                status=RequirementStatus.PROPOSED, stage=1, est_runs=4,
                created_at=utcnow() - timedelta(hours=6),
            ))

    with db_session() as session:
        janitor_once(session)

    sent = _notices(project["id"], "scope.proposal_nag")
    assert len(sent) == 1, "one mail for the batch, not one per requirement"
    assert sent[0]["detail"]["count"] == 2

    # Chased daily, not announced once: a notice that arrived while he was away
    # is indistinguishable from one never sent.
    with db_session() as session:
        janitor_once(session)
    assert len(_notices(project["id"], "scope.proposal_nag")) == 1, "not per tick"


def test_a_freshly_proposed_batch_is_left_to_settle(client):
    """A planning run proposes scope in bulk. Nagging on the first tick would
    mail the chairman mid-run and again as the rest arrived."""
    project = make_project(client, slug="supervised")
    with db_session() as session:
        session.add(Requirement(
            id=ids.new_id(ids.REQUIREMENT), project_id=project["id"], key="R-09",
            statement="Just proposed, this second.",
            acceptance_test={"type": "observable", "target": "https://example.org/x"},
            status=RequirementStatus.PROPOSED, stage=1, est_runs=4,
        ))
    with db_session() as session:
        janitor_once(session)
    assert _notices(project["id"], "scope.proposal_nag") == []


def test_an_unsigned_delivery_date_is_chased(client):
    """Until it is signed the company is working without a promise — so no slip
    can freeze new scope and nothing tells him when the date stops holding."""
    project = make_project(client, slug="supervised")
    with db_session() as session:
        session.add(Commitment(
            id=ids.new_id(ids.COMMITMENT), project_id=project["id"],
            status=CommitmentStatus.PROPOSED, deliver_by=utcnow() + timedelta(days=14),
            scope_snapshot=["R-01", "R-02"],
            created_at=utcnow() - timedelta(hours=6),
        ))
    with db_session() as session:
        janitor_once(session)
    assert len(_notices(project["id"], "commitment.proposal_nag")) == 1


def test_an_autonomous_project_is_never_chased_for_either(client):
    """The CEO holds both switches, so nothing is waiting and nothing should
    arrive. The desk and the inbox read the same definition, so an empty page
    and a quiet inbox mean the same thing rather than disagreeing."""
    project = make_project(client, slug="autonomous", constitution=AUTONOMOUS)
    with db_session() as session:
        session.add(Requirement(
            id=ids.new_id(ids.REQUIREMENT), project_id=project["id"], key="R-09",
            statement="Proposed, but the CEO accepts its own scope here.",
            acceptance_test={"type": "observable", "target": "https://example.org/x"},
            status=RequirementStatus.PROPOSED, stage=1, est_runs=4,
            created_at=utcnow() - timedelta(hours=6),
        ))
        session.add(Commitment(
            id=ids.new_id(ids.COMMITMENT), project_id=project["id"],
            status=CommitmentStatus.PROPOSED, deliver_by=utcnow() + timedelta(days=14),
            scope_snapshot=["R-01"], created_at=utcnow() - timedelta(hours=6),
        ))
    with db_session() as session:
        janitor_once(session)

    assert _notices(project["id"], "scope.proposal_nag") == []
    assert _notices(project["id"], "commitment.proposal_nag") == []
    assert _desk(client, live=True)["projects"][0]["awaiting_you"] == {
        "plan": 0, "verdicts": [], "commitment": None}
