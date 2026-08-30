"""Invariant selftest: the CEO can see its own burn rate, not only its total.

A total has never surprised anybody. What surprises people is the derivative.
One desk reported "9.9m of 200m" to its chairman at midnight, in good faith,
and stood at 93.8m ten hours later — 47% of a month in two days. Every surface
the CEO could read showed a total against a cap and none of them showed a
slope, so the chairman found it first and then had to ask for the arithmetic
by hand: "lay out the next 29 days. How many tokens per day do you need!"

The second half of this file covers the quieter version of the same problem:
under subscription billing no run carries a price, so `affordable_runs` cannot
answer and the plan-affordability refusal never fires. Nothing said so, and a
gate that is silently off reads exactly like a gate that passed.
"""

from __future__ import annotations

from tests.conftest import live_token, make_project
from yaaf.core import ids
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import Agent, LedgerEntry, Project, Run
from yaaf.server.contextpack import build_context_pack


def _spend(project_id: str, *, usd: float | None = None, tokens: int = 0) -> None:
    with db_session() as session:
        session.add(LedgerEntry(
            id=ids.new_id(ids.LEDGER), project_id=project_id, kind="model_usage",
            tokens_in=tokens, tokens_out=0,
            cache_creation_tokens=0, cache_read_tokens=0, usd=usd,
        ))


def _budget(project: dict) -> dict:
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        pack = build_context_pack(
            session, session.get(Run, run_id),
            session.get(Agent, project["ceo_agent_id"]),
            session.get(Project, project["id"]),
        )
    return pack["situation"]["budget"]


def test_the_ceo_sees_the_slope_not_only_the_total(client):
    project = make_project(client, billing_mode="api_key")
    _spend(project["id"], usd=40.0)

    budget = _budget(project)

    assert budget["spent"] > 0
    rate = budget["rate"]
    assert rate["per_day"] > 0, "a project that has spent has a rate"
    assert rate["days_elapsed"] >= 1
    assert rate["note"], "the rate always says what it means in words"


def test_a_burn_that_reaches_the_cap_this_month_names_the_day(client):
    """The sentence the CEO had to compute by hand after being shouted at.

    The month-end guard is real behaviour, not a test convenience: the cap
    refills on the 1st, so on the last day of a month there is genuinely no
    landing to warn about and the calm branch is the correct answer. The
    assertion below follows the calendar rather than pretending it doesn't.
    """
    from datetime import timedelta

    from yaaf.core.models import utcnow

    project = make_project(client, billing_mode="api_key")
    with db_session() as session:
        cap = effective(session, project["id"]).monthly_usd_cap
    # 99% gone: the remainder lasts about 1% of the days already elapsed, so a
    # landing falls inside the month on every day but the very last one.
    _spend(project["id"], usd=cap * 0.99)

    rate = _budget(project)["rate"]
    now = utcnow()
    resets_at = (now.replace(day=1) + timedelta(days=32)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)

    if resets_at - now > timedelta(days=1):
        assert rate["lands_on_cap_at"] is not None, "a 99% burn must project a landing"
        assert rate["lands_on_cap_at"] < resets_at.isoformat(), (
            "a landing after the cap refills is not a landing"
        )
        assert "hits the cap" in rate["note"]
    else:
        assert rate["lands_on_cap_at"] is None, (
            "on the last day of the month the cap refills before it is reached"
        )


def test_an_unspent_project_projects_no_landing(client):
    """Calm is calm. A date that never comes is noise on every single wake."""
    project = make_project(client, billing_mode="api_key")

    rate = _budget(project)["rate"]

    assert rate["per_day"] == 0
    assert rate["lands_on_cap_at"] is None
    assert "does not reach the cap" in rate["note"]


def test_the_rate_is_measured_in_the_unit_the_project_actually_bills_in(client):
    """Subscription projects have no dollars at all — a rate in USD would be
    zero forever and read as calm on a project burning its token cap."""
    project = make_project(client, billing_mode="subscription")
    _spend(project["id"], tokens=50_000_000)

    budget = _budget(project)

    assert budget["unit"] == "tokens"
    assert budget["spent"] == 50_000_000
    assert budget["rate"]["per_day"] > 0, "tokens are the wall here, so tokens are the rate"


def test_subscription_billing_says_the_affordability_gate_is_off(client):
    project = make_project(client, billing_mode="subscription")

    budget = _budget(project)

    assert budget["priced"] is False
    assert "CANNOT check whether a plan is affordable" in budget["note"]


def test_api_key_billing_claims_no_such_exemption(client):
    project = make_project(client, billing_mode="api_key")

    budget = _budget(project)

    assert budget["priced"] is True
    assert "note" not in budget, "the caveat belongs only where the gate is really off"


def test_the_pack_and_affordable_runs_agree_about_whether_money_is_knowable(client):
    """Two answers to "can we afford this" is worse than one. `priced` must
    track the same billing_mode branch `affordable_runs` returns known:false
    on, or the pack reassures a CEO the refusal would have stopped."""
    from yaaf.server import policy

    for mode in ("api_key", "subscription"):
        slug = "agree-apikey" if mode == "api_key" else "agree-sub"
        project = make_project(client, slug=slug, billing_mode=mode)
        with db_session() as session:
            known = policy.affordable_runs(
                session, session.get(Project, project["id"]),
                effective(session, project["id"]),
            )["known"]
        priced = _budget(project)["priced"]
        # `known` can still be false on a priced project with too little
        # history; `priced` false must never claim a gate that is actually on.
        assert priced or not known, (
            f"{mode}: the pack says money is unpriced while the gate says it can answer"
        )
