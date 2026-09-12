"""The receipt: what a project cost, beside what the platform could verify.

Two invariants carry it, and both are about trust rather than arithmetic.

The first is that the dollar figure agrees with every other dollar figure in
the product, because it reads the same ledger the budget meter does. A receipt
meant to be pasted in public that disagreed with the spend page would be worse
than publishing nothing.

The second is that the half of the receipt doing the persuading is the half no
agent can move, and that the payload says so out loud. A receipt whose good
numbers are the agent's own is an invoice.
"""

from __future__ import annotations

from tests.conftest import ADMIN, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Decision,
    Evidence,
    EvidenceKind,
    EvidenceProvenance,
    LedgerEntry,
    LedgerKind,
    Run,
    RunStatus,
    VerificationStatus,
    utcnow,
)
from yaaf.server.receipt import render


def _run(project_id: str, agent_id: str, cost: float, status=RunStatus.DONE) -> str:
    run_id = ids.new_id(ids.RUN)
    with db_session() as session:
        session.add(Run(id=run_id, project_id=project_id, agent_id=agent_id,
                        trigger={"type": "test"}, status=status, model="claude-opus-5",
                        cost_usd=cost, started_at=utcnow(), finished_at=utcnow()))
        session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project_id,
                                kind=LedgerKind.MODEL_USAGE, usd=cost, run_id=run_id))
    return run_id


def _evidence(project_id: str, verification: str, kind=EvidenceKind.EXTERNAL,
              provenance=EvidenceProvenance.AGENT_CLAIMED, description="claimed a thing") -> None:
    with db_session() as session:
        session.add(Evidence(
            id=ids.new_id(ids.EVIDENCE), project_id=project_id, kind=kind,
            provenance=provenance, description=description,
            source_url="https://example.test/filing", verification=verification))


def _get(client, project, **params) -> dict:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"/observe/projects/{project['id']}/receipt" + (f"?{query}" if query else "")
    response = client.get(url, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_dollar_figure_is_the_one_the_spend_page_prints(client):
    """Same ledger, so the two cannot disagree.

    This is the bug the spend endpoint already had once and fixed: it summed
    the run table, which misses spend a run row never recorded and double-counts
    where the ledger books a row per turn. A receipt that reintroduced it would
    put two numbers on one page that could not both be true.
    """
    project = make_project(client)
    ceo = project["ceo_agent_id"]
    _run(project["id"], ceo, 2.0)
    _run(project["id"], ceo, 1.25)

    receipt = _get(client, project)
    rollup = client.get(f"/observe/projects/{project['id']}/spend?window=month",
                        headers=ADMIN).json()

    assert receipt["cost"]["usd"] == 3.25
    assert receipt["cost"]["usd"] == round(sum(r["usd"] for r in rollup["rows"]), 2)


def test_revenue_is_not_counted_as_spend(client):
    """Money coming in is not money going out — the same exclusion the meter
    and the monthly wall apply, for the same reason."""
    project = make_project(client)
    _run(project["id"], project["ceo_agent_id"], 1.0)
    with db_session() as session:
        session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project["id"],
                                kind=LedgerKind.REVENUE, usd=500.0))

    assert _get(client, project)["cost"]["usd"] == 1.0


def test_runs_that_died_are_counted_where_the_money_went(client):
    """A killed run spent its money. Putting it in a footnote rather than the
    cost column is how a receipt flatters itself."""
    project = make_project(client)
    ceo = project["ceo_agent_id"]
    _run(project["id"], ceo, 3.0)
    _run(project["id"], ceo, 1.0, status=RunStatus.REAPED)
    _run(project["id"], ceo, 0.5, status=RunStatus.TERMINATED_REFUSALS)

    cost = _get(client, project)["cost"]
    assert cost["usd"] == 4.5, "dead runs stay in the total"
    assert cost["runs"] == 3
    assert cost["runs_ended_early"] == 2
    assert cost["usd_on_runs_that_died"] == 1.5


def test_only_verified_external_claims_count_as_verified(client):
    project = make_project(client)
    _evidence(project["id"], VerificationStatus.VERIFIED)
    _evidence(project["id"], VerificationStatus.VERIFIED)
    _evidence(project["id"], VerificationStatus.FAILED)
    _evidence(project["id"], VerificationStatus.PENDING)
    # Internal evidence is not an external claim and must not inflate the count.
    _evidence(project["id"], VerificationStatus.NOT_APPLICABLE, kind=EvidenceKind.INTERNAL)

    evidence = _get(client, project)["verified"]["evidence"]
    assert evidence["external"] == 4
    assert evidence["verified"] == 2
    assert evidence["failed"] == 1, "a failed check is reported, not dropped"


def test_the_payload_says_which_half_an_agent_could_not_move(client):
    """The claim the receipt rests on, asserted rather than left to the prose.

    If `self_reported` ever flips true, or the requirement block stops naming
    the platform as its author, the artifact is still readable and no longer
    means anything.
    """
    project = make_project(client)
    data = _get(client, project)

    assert data["self_reported"] is False
    assert data["verified"]["requirements"]["written_by"] == "platform"


def test_the_receipt_carries_no_agent_written_text(client):
    """It is meant to be pasted in public.

    Every other read surface wraps agent prose in an untrusted envelope because
    the reader is a model that might act on it. This one has no envelope, which
    is only safe while it carries no agent prose at all — so the goal, evidence
    descriptions and decision bodies must never appear. Counts and enum values
    only.
    """
    project = make_project(client)
    _evidence(project["id"], VerificationStatus.VERIFIED,
              description="TOTALLY-DISTINCTIVE-AGENT-PROSE")
    with db_session() as session:
        session.add(Decision(id=ids.new_id(ids.DECISION), project_id=project["id"],
                             by_user_id="user_test", subject="ANOTHER-DISTINCTIVE-STRING",
                             body="and a body nobody vetted"))

    blob = str(_get(client, project))
    assert "TOTALLY-DISTINCTIVE-AGENT-PROSE" not in blob
    assert "ANOTHER-DISTINCTIVE-STRING" not in blob
    assert "and a body nobody vetted" not in blob


def test_a_chairman_decision_is_what_it_cost_you(client):
    project = make_project(client)
    with db_session() as session:
        session.add(Decision(id=ids.new_id(ids.DECISION), project_id=project["id"],
                             by_user_id="user_test", subject="cut scope", body="too big"))
        # An agent's own decision row is not the chairman's attention.
        session.add(Decision(id=ids.new_id(ids.DECISION), project_id=project["id"],
                             by_agent_id="agent_x", subject="chose a bet", body="because"))

    assert _get(client, project)["chairman"]["decisions"] == 1


def test_an_unknown_project_is_a_404(client):
    assert client.get("/observe/projects/proj_nope/receipt", headers=ADMIN).status_code == 404


def test_it_renders_to_something_a_person_can_paste(client):
    project = make_project(client)
    _run(project["id"], project["ceo_agent_id"], 2.0)
    _evidence(project["id"], VerificationStatus.VERIFIED)

    text = render(_get(client, project))

    assert "WHAT IT COST" in text and "WHAT THE PLATFORM VERIFIED" in text
    assert "$2.00 total" in text
    assert "1 of 1 external claims verified" in text
    assert "Nothing in the right column was written by an agent" in text


def test_a_subscription_project_is_not_given_a_dollar_figure(client):
    """Tokens are the unit there, and inventing dollars for a plan that does
    not price them would be the receipt's first lie."""
    data = {
        "project": {"name": "Sub", "mode": "deliver", "days": 3, "teams": 1,
                    "seats": 1, "billing_mode": "subscription"},
        "cost": {"usd": 0.0, "usd_on_runs_that_died": 0.0, "external_usd": 0.0,
                 "runs": 4, "runs_ended_early": 0, "tokens_in": 10, "tokens_out": 2},
        "verified": {"requirements": {"met": 1, "open": 0, "cut": 0, "written_by": "platform"},
                     "evidence": {"external": 0, "verified": 0, "failed": 0,
                                  "platform_attested": 0},
                     "audit": {"sampled": 0, "failed": 0, "expired_unreviewed": 0},
                     "commitment": None, "dates_promised": 0},
        "chairman": {"decisions": 0},
        "caveats": [],
    }
    text = render(data)
    assert "$" not in text
    assert "(subscription)" in text
