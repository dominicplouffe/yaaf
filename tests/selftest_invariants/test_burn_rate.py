"""Invariant selftest: the burn rate is measured, and refuses to guess.

Three numbers reached one chairman inside two hours — 3M/day, then 5M, then
35M — and all three were means over a window that did not match the question.
The desk's real series was 12.4M, 90.7M, 21.6M, 15.9M: one build day was 64% of
everything ever spent, and the two most recent days were falling. No single
average described it, and every average offered was presented as though one did.

The first version of this file was part of the problem. It divided
month-to-date by the day of the month, so a project begun on the 27th reported
4.7M/day against a real 18M — the small, comfortable, wrong number, which is
exactly the complaint that produced this rewrite.

So: a series and a median, plus an explicit refusal to project when the days
are not alike or there are too few of them. "I do not know yet, and here is
what I need to find out" is now a supported answer with words the platform
supplies — which it had never been.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import live_token, make_project
from yaaf.core import ids
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import Agent, LedgerEntry, Project, Run, utcnow
from yaaf.server.contextpack import build_context_pack
from yaaf.server.janitor import burn_rate, month_to_date, run_cost_profile


def _spend(project_id: str, *, usd: float | None = None, tokens: int = 0,
           days_ago: int = 0) -> None:
    with db_session() as session:
        session.add(LedgerEntry(
            id=ids.new_id(ids.LEDGER), project_id=project_id, kind="model_usage",
            tokens_in=tokens, tokens_out=0,
            cache_creation_tokens=0, cache_read_tokens=0, usd=usd,
            at=utcnow() - timedelta(days=days_ago),
        ))


def _rate(project_id: str) -> dict:
    with db_session() as session:
        cons = effective(session, project_id)
        usd_mode = session.get(Project, project_id).billing_mode == "api_key"
        cap = cons.monthly_usd_cap if usd_mode else cons.monthly_token_cap
        usd, tokens = month_to_date(session, project_id)
        return burn_rate(session, project_id, cap, usd if usd_mode else tokens)


def _budget(project: dict) -> dict:
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        pack = build_context_pack(
            session, session.get(Run, run_id),
            session.get(Agent, project["ceo_agent_id"]),
            session.get(Project, project["id"]),
        )
    return pack["situation"]["budget"]


def test_two_days_of_spend_is_not_a_rate_and_says_so(client):
    """The answer the platform never let an agent give."""
    project = make_project(client, billing_mode="api_key")
    _spend(project["id"], usd=10.0, days_ago=1)
    _spend(project["id"], usd=12.0, days_ago=0)

    rate = _rate(project["id"])

    assert rate["days_observed"] == 2
    assert rate["projectable"] is False
    assert rate["lands_on_cap_at"] is None
    assert "do not know yet" in rate["note"]
    assert "measurement window" in rate["note"], (
        "the honest answer names what would settle it, not merely that it is unknown"
    )


def test_one_build_day_does_not_get_to_speak_for_the_window(client):
    """The desk's own shape: 12.4, 90.7, 21.6, 15.9. Mean 35, median 18 — and
    the CEO quoted the mean to its chairman as the run rate."""
    project = make_project(client, billing_mode="subscription")
    for days_ago, millions in ((3, 12.4), (2, 90.7), (1, 21.6), (0, 15.9)):
        _spend(project["id"], tokens=int(millions * 1_000_000), days_ago=days_ago)

    rate = _rate(project["id"])

    assert rate["days_observed"] == 4
    assert rate["days_are_alike"] is False
    assert rate["projectable"] is False, "no single number describes these days"
    assert rate["unit_per_day_median"] < rate["unit_per_day_mean"], (
        "the median is the honest headline precisely because the mean is inflated"
    )
    assert "do NOT average them" in rate["note"]
    assert "still unproven" in rate["note"]


def test_a_project_that_started_late_in_the_month_is_not_divided_by_thirty(client):
    """The bug this file shipped, and then caught. Four days of spend on a
    project begun on the 27th must not be spread across the whole month."""
    project = make_project(client, billing_mode="subscription")
    for days_ago in range(4):
        _spend(project["id"], tokens=18_000_000, days_ago=days_ago)

    rate = _rate(project["id"])

    assert rate["days_observed"] == 4, "only days that actually spent are days"
    assert rate["unit_per_day_median"] == 18_000_000, (
        "the median day is the median day, whatever the calendar says"
    )


def test_an_even_history_projects_a_landing(client):
    """Where the days really are alike, a rate is a rate and the cap has a date."""
    project = make_project(client, billing_mode="api_key")
    with db_session() as session:
        cap = effective(session, project["id"]).monthly_usd_cap
    for days_ago in range(5):
        _spend(project["id"], usd=cap * 0.19, days_ago=days_ago)

    rate = _rate(project["id"])

    assert rate["days_observed"] == 5
    assert rate["days_are_alike"] is True
    assert rate["projectable"] is True
    assert rate["lands_on_cap_at"] is not None
    assert "reaches the cap" in rate["note"]


def test_an_unspent_project_reports_no_rate_at_all(client):
    project = make_project(client, billing_mode="api_key")

    rate = _rate(project["id"])

    assert rate["days_observed"] == 0
    assert rate["series"] == []
    assert rate["lands_on_cap_at"] is None
    assert "no rate to report" in rate["note"]


def test_the_series_is_carried_so_the_outlier_can_be_seen_rather_than_trusted(client):
    """A median is a claim; the series is the evidence. The CEO that averaged
    four days into 35M had never been shown the four days."""
    project = make_project(client, billing_mode="subscription")
    for days_ago, millions in ((2, 5.0), (1, 60.0), (0, 6.0)):
        _spend(project["id"], tokens=int(millions * 1_000_000), days_ago=days_ago)

    rate = _rate(project["id"])

    assert len(rate["series"]) == 3
    assert [row["spent"] for row in rate["series"]] == [5e6, 60e6, 6e6]
    assert rate["busiest_day"] == 60e6


# ── what a run of each kind actually costs ──────────────────────────────────────


def test_the_ceo_is_given_cost_per_run_kind_not_one_blended_average(client):
    """A CEO with a single average told its chairman that a message-reading
    wake "costs nearly the same as a run that produces something", and priced
    forty of them at 22M. They cost a third of a work run and came to 8.9M."""
    project = make_project(client, billing_mode="subscription")
    with db_session() as session:
        for kind, tokens in (("agent_run", 1_030_000), ("inbox_run", 270_000)):
            for _ in range(3):
                session.add(Run(
                    id=ids.new_id(ids.RUN), project_id=project["id"],
                    agent_id=project["ceo_agent_id"], kind=kind, trigger={},
                    attempt=1, status="done", token_revoked=False, refusal_count=0,
                    tokens_in=0, tokens_out=tokens, cache_creation_tokens=0,
                    cache_read_tokens=0,
                ))

    with db_session() as session:
        profile = run_cost_profile(session, project["id"])

    by_kind = {row["kind"]: row for row in profile["by_kind"]}
    assert by_kind["agent_run"]["avg_tokens"] == 1_030_000
    assert by_kind["inbox_run"]["avg_tokens"] == 270_000
    assert "never from one blended average" in profile["note"]


def test_a_project_with_no_runs_says_there_is_nothing_to_estimate_from(client):
    project = make_project(client, billing_mode="subscription")

    with db_session() as session:
        profile = run_cost_profile(session, project["id"])

    assert profile["by_kind"] == []
    assert "rather than reaching for a number" in profile["note"]


def test_the_cost_profile_actually_reaches_the_pack(client):
    """Measuring it is half the job; the CEO has to be handed it."""
    project = make_project(client, billing_mode="subscription")

    profile = _budget(project)["cost_per_run"]

    assert "by_kind" in profile and "note" in profile
    # The run this very pack was built on is a real run and counts.
    assert any(row["kind"] == "agent_run" for row in profile["by_kind"])


# ── the caveats that must survive the rewrite ───────────────────────────────────


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


def test_the_rate_is_measured_in_the_unit_the_project_actually_bills_in(client):
    """A rate in dollars on a subscription project is zero forever, and reads
    as calm on a desk burning through its token cap."""
    project = make_project(client, billing_mode="subscription")
    for days_ago in range(4):
        _spend(project["id"], tokens=50_000_000, days_ago=days_ago)

    budget = _budget(project)

    assert budget["unit"] == "tokens"
    assert budget["rate"]["unit_per_day_median"] == 50_000_000
