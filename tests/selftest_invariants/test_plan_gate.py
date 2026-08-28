"""Invariant selftests: the plan is the first task, and it has to add up.

The chairman's words were that a Deliver or Operate project's first task is a
waterfall-type plan "built from the budget, team, and all the arguments that
have been set by the user (i.e. number of daily runs, budget, number of open
tasks allowed, and so on)."

An earlier design deleted the arithmetic half of that — "the feasibility check,
replaced by a definition of done you set yourself" — on the correct grounds
that per-phase budgets were a guess the CEO could satisfy by writing smaller
numbers. That objection is answerable, and the answer is what these tests pin:
the estimate is recorded, compared against what the work actually cost, and the
next plan is judged at the CEO's own historical ratio.

What is attacked here:

  * building anything before there is a plan
  * a plan with no estimates, or one that does not fit the money
  * buying headroom by writing a smaller number
  * jumping a stage the chairman put later
"""

from __future__ import annotations

from tests.conftest import ADMIN, bearer, make_project
from tests.selftest_invariants.test_requirements import (
    CMD_TEST,
    accept,
    ceo_token,
    deliver_project,
    make_task,
    propose,
    tick,
)
from yaaf.core.db import db_session
from yaaf.core.models import (
    Project,
    Requirement,
    RequirementStatus,
    Run,
    RunStatus,
    utcnow,
)
from yaaf.server import policy


def plan_row(project: dict) -> Requirement:
    with db_session() as session:
        return session.get(
            Requirement, policy.plan_requirement(session, project["id"]).id
        )


# ── nothing gets built before there is a plan ───────────────────────────────────


def test_a_deliver_project_starts_owing_a_plan(client):
    project = deliver_project(client)
    plan = plan_row(project)
    assert plan.key == "R-00"
    assert plan.status == RequirementStatus.PENDING
    assert plan.acceptance_test == {"type": "plan"}
    assert plan.stage == 0, "the plan sits before stage 1, not inside it"


def test_an_explore_project_owes_no_plan(client):
    """The open-ended arm is exempt: a plan for work whose whole point is that
    the plan is not known yet would be theatre."""
    project = make_project(client, slug="explorer")
    with db_session() as session:
        assert policy.plan_requirement(session, project["id"]) is None


def test_no_task_may_be_created_until_the_plan_holds(client):
    project = deliver_project(client)
    token = ceo_token(project)

    refused = make_task(client, token, project, None)
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert "no plan" in error
    assert "no requirements proposed yet" in error, "the refusal must say what is missing"

    # Proposing alone is not enough — the chairman has not accepted anything.
    proposed = propose(client, token)
    tick(project)
    assert plan_row(project).status == RequirementStatus.PENDING
    still_refused = make_task(client, token, project, proposed["requirement_id"])
    assert still_refused.status_code == 409
    assert "waiting on him" in still_refused.json()["error"]

    # He accepts; the plan holds; the board opens without anyone marking it done.
    accept(client, proposed["requirement_id"], project)
    assert plan_row(project).status == RequirementStatus.MET
    allowed = make_task(client, token, project, proposed["requirement_id"])
    assert allowed.status_code == 200, allowed.text


def test_the_plan_closes_itself_and_no_agent_can_close_it(client):
    project = deliver_project(client)
    token = ceo_token(project)
    plan_id = plan_row(project).id

    for path in (f"/agent/requirements/{plan_id}/complete",
                 f"/agent/requirements/{plan_id}/met"):
        response = client.post(path, json={"evidence": "planned it"}, headers=bearer(token))
        assert response.status_code in (404, 405)

    accept(client, propose(client, token)["requirement_id"], project)
    plan = plan_row(project)
    assert plan.status == RequirementStatus.MET
    assert plan.met_at is not None
    assert plan.check_output and "holds" in plan.check_output


# ── it has to add up ────────────────────────────────────────────────────────────


def test_a_requirement_without_an_estimate_is_refused(client):
    project = deliver_project(client)
    token = ceo_token(project)
    refused = client.post(
        "/agent/requirements",
        json={"statement": "Something true happens eventually", "acceptance_test": CMD_TEST},
        headers=bearer(token),
    )
    assert refused.status_code == 422
    assert "est_runs" in refused.json()["error"]


def test_a_plan_that_does_not_fit_the_money_is_refused_with_the_arithmetic(client):
    """The refusal shows its working, so the CEO can cut rather than guess."""
    project = deliver_project(client, constitution={"monthly_usd_cap": 10.0})
    token = ceo_token(project)
    _price_some_runs(project, count=6, usd=1.0)   # $1 a run, $10 left → ~10 runs

    ok = propose(client, token, statement="A modest thing is true", est_runs=4)
    assert ok["key"] == "R-01"

    refused = client.post(
        "/agent/requirements",
        json={"statement": "An enormous thing is also true",
              "acceptance_test": CMD_TEST, "est_runs": 40},
        headers=bearer(token),
    )
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert "does not fit the money" in error
    assert "44 runs" in error, f"the sum should be spelled out: {error}"
    assert "R-01" not in error   # the sum, not a lecture about which one
    assert "$1.0 a run" in error or "$1.0" in error
    assert "over by" in error


def test_the_money_check_is_skipped_honestly_when_there_is_no_price(client):
    """Under subscription billing there is no per-run price to divide by, and
    inventing one would be worse than not checking."""
    project = deliver_project(client, billing_mode="subscription")
    token = ceo_token(project)
    huge = propose(client, token, statement="An enormous thing is true", est_runs=9_000)
    assert huge["key"] == "R-01"
    with db_session() as session:
        row = session.get(Project, project["id"])
        sums = policy.plan_arithmetic(session, row, _cons(project))
    assert sums["budget"]["known"] is False
    assert "subscription" in sums["budget"]["why"]
    assert sums["affordable"] is True


# ── writing a smaller number buys one round ─────────────────────────────────────


def test_a_ceo_that_underestimates_is_planned_for_at_its_own_ratio(client):
    """The answer to the objection that killed the feasibility check the first
    time. An estimate is not trusted — it is scored."""
    project = deliver_project(client, constitution={"monthly_usd_cap": 1000.0})
    token = ceo_token(project)

    # Two requirements estimated at 5 runs each that actually took 15.
    settled = []
    for n in range(2):
        r = propose(client, token, statement=f"Historic thing {n} is true", est_runs=5)
        accept(client, r["requirement_id"], project)
        settled.append(r["requirement_id"])
    for rid in settled:
        _price_some_runs(project, count=15, usd=0.01, requirement_id=rid)
        client.post(f"/admin/requirements/{rid}/cut",
                    json={"reason": "done with it for the purposes of history"},
                    headers=ADMIN)

    with db_session() as session:
        multiplier, detail = policy.estimate_bias(session, project["id"])
    assert multiplier == 3.0, detail
    assert detail["estimated"] == 10 and detail["actual"] == 30

    # The next plan is read at 3x, and the refusal says so in the CEO's own numbers.
    with db_session() as session:
        row = session.get(Project, project["id"])
        sums = policy.plan_arithmetic(session, row, _cons(project), extra_est=10)
    assert sums["adjusted_runs"] == 30, sums
    assert sums["multiplier"] == 3.0


def test_the_multiplier_never_shrinks_an_estimate(client):
    """A ratio below 1 would let a CEO buy headroom by over-estimating once and
    under-estimating everything after — the same gaming, run backwards."""
    project = deliver_project(client, constitution={"monthly_usd_cap": 1000.0})
    token = ceo_token(project)
    for n in range(2):
        r = propose(client, token, statement=f"Overestimated thing {n} is true", est_runs=50)
        accept(client, r["requirement_id"], project)
        _price_some_runs(project, count=2, usd=0.01, requirement_id=r["requirement_id"])
        client.post(f"/admin/requirements/{r['requirement_id']}/cut",
                    json={"reason": "history"}, headers=ADMIN)

    with db_session() as session:
        multiplier, _ = policy.estimate_bias(session, project["id"])
    assert multiplier == 1.0


# ── the waterfall, enforced ─────────────────────────────────────────────────────


def test_work_may_not_jump_a_stage(client):
    project = deliver_project(client)
    token = ceo_token(project)
    early = propose(client, token, statement="The groundwork is laid", stage=1)
    late = propose(client, token, statement="The polish is applied", stage=2)
    accept(client, early["requirement_id"], project)
    accept(client, late["requirement_id"], project)

    refused = make_task(client, token, project, late["requirement_id"])
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert "stage 2" in error and "stage 1" in error
    assert "chairman owns this order" in error

    assert make_task(client, token, project, early["requirement_id"]).status_code == 200

    # Settling stage 1 opens stage 2 with nobody deciding anything.
    client.post(f"/admin/requirements/{early['requirement_id']}/cut",
                json={"reason": "the client already had this"}, headers=ADMIN)
    assert make_task(client, token, project, late["requirement_id"]).status_code == 200


# ── the plan can stop holding without anyone touching it ────────────────────────


def test_a_plan_that_stops_adding_up_reopens_by_itself(client):
    """The reason this lives in the janitor: the chairman lowering a budget can
    make a plan false, and a gate that only re-checked when someone poked it
    would let a project build all month against arithmetic that broke on the
    3rd."""
    project = deliver_project(client, constitution={"monthly_usd_cap": 1000.0})
    token = ceo_token(project)
    _price_some_runs(project, count=6, usd=1.0)
    accept(client, propose(client, token, est_runs=20)["requirement_id"], project)
    assert plan_row(project).status == RequirementStatus.MET

    client.post(f"/admin/projects/{project['id']}/amendments",
                json={"reason": "money is tighter than I thought", "changes":
                      {"monthly_usd_cap": 5.0}}, headers=ADMIN)
    tick(project)

    plan = plan_row(project)
    assert plan.status == RequirementStatus.PENDING, "the plan must reopen when it breaks"
    assert plan.met_at is None

    refused = make_task(client, token, project, None)
    assert refused.status_code == 409
    assert "no plan" in refused.json()["error"]


# ── helpers ─────────────────────────────────────────────────────────────────────


def _cons(project: dict):
    from yaaf.core.constitution import effective

    with db_session() as session:
        return effective(session, project["id"])


def _price_some_runs(project: dict, count: int, usd: float,
                     requirement_id: str | None = None) -> None:
    """Finished, priced runs — what the ledger and the estimate scorer read."""
    from yaaf.core import ids
    from yaaf.core.models import LedgerEntry

    with db_session() as session:
        for _ in range(count):
            run_id = ids.new_id(ids.RUN)
            session.add(Run(
                id=run_id, project_id=project["id"], agent_id=project["ceo_agent_id"],
                kind="agent_run", trigger={}, status=RunStatus.DONE,
                requirement_id=requirement_id,
                cost_usd=usd, started_at=utcnow(), finished_at=utcnow(),
            ))
            session.add(LedgerEntry(
                id=ids.new_id(ids.LEDGER), project_id=project["id"], run_id=run_id,
                kind="model_usage", usd=usd, tokens_in=0, tokens_out=0,
            ))


def test_speculation_cannot_freeze_the_board(client):
    """Proposals the chairman never took must not make the plan unaffordable —
    a CEO that could do that could lock its own project out of working by
    thinking out loud."""
    project = deliver_project(client, constitution={"monthly_usd_cap": 100.0})
    token = ceo_token(project)
    _price_some_runs(project, count=6, usd=1.0)   # ~100 runs affordable

    real = propose(client, token, statement="The thing that is actually wanted", est_runs=10)
    accept(client, real["requirement_id"], project)
    assert plan_row(project).status == RequirementStatus.MET

    # A pile of speculation, each individually affordable.
    for n in range(8):
        propose(client, token, statement=f"Speculative idea {n} might be worth it",
                est_runs=10)
    tick(project)

    assert plan_row(project).status == RequirementStatus.MET, \
        "unaccepted proposals are not scope and must not reopen the plan"
    assert make_task(client, token, project, real["requirement_id"]).status_code == 200

    # But while planning, the CEO is still told the pile does not fit.
    with db_session() as session:
        row = session.get(Project, project["id"])
        planning = policy.plan_arithmetic(session, row, _cons(project), include_proposed=True)
        holding = policy.plan_arithmetic(session, row, _cons(project))
    assert planning["estimated_runs"] == 90
    assert holding["estimated_runs"] == 10
