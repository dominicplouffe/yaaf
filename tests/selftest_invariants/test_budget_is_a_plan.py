"""Invariant selftest: the budget is a plan, and going over is a conversation.

One number used to do two jobs — the guess you make before the work starts,
and the wall that stops a runaway. Because crossing the plan paused the
project, revoked its tokens and purged its queue, the plan was never a plan; it
was a cliff, and every CEO stood well back from it. Three separate ones refused
money their chairman offered:

    "I am not recommending you raise 'Runs per day, whole project'."
    "I'm not asking for more tokens or headcount right now."
    "My recommendation is the opposite of what you're offering."

That is not timidity. It is the correct play when going over your own budget is
fatal. The cost was that a safe number and an honest number look identical from
outside, and the chairman said what that did: "the confidence in the entire
harness of yaaf is lost."

So there are three thresholds now and only the last one stops anything, there
is a field for how much the project matters, and there is a third move that did
not exist — spend it, and account for it. What this file defends is that the
platform writes the facts, the CEO writes the reasoning, and the chairman
writes the decision; none of the three can do another's job.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core import ids
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    BudgetOverrun,
    Flag,
    LedgerEntry,
    OverrunOutcome,
    Project,
    ProjectStatus,
    Run,
    utcnow,
)
from yaaf.server.budget import proposed_budget
from yaaf.server.contextpack import build_context_pack, constitution_prose
from yaaf.server.janitor import enforce_budget


def _book(project_id: str, tokens: int = 0, usd: float | None = None,
          months_ago: int = 0) -> None:
    at = utcnow()
    for _ in range(months_ago):
        at = at.replace(day=1) - timedelta(days=1)
    with db_session() as session:
        session.add(LedgerEntry(
            id=ids.new_id(ids.LEDGER), project_id=project_id, kind="model_usage",
            tokens_in=0, tokens_out=0, cache_creation_tokens=0,
            cache_read_tokens=tokens, usd=usd, at=at,
        ))


def _enforce(project_id: str) -> None:
    with db_session() as session:
        p = session.get(Project, project_id)
        enforce_budget(session, p, effective(session, p.id))


def _overrun(project_id: str) -> BudgetOverrun | None:
    with db_session() as session:
        row = session.execute(
            select(BudgetOverrun).where(BudgetOverrun.project_id == project_id)
        ).scalars().first()
        if row is not None:
            session.expunge(row)
        return row


def _pack(project: dict) -> dict:
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        return build_context_pack(
            session, session.get(Run, run_id),
            session.get(Agent, project["ceo_agent_id"]),
            session.get(Project, project["id"]),
        )


def _over_budget(client, **cons):
    project = make_project(client, billing_mode="subscription",
                           constitution={"monthly_token_cap": 100_000, **cons})
    _book(project["id"], tokens=140_000)
    _enforce(project["id"])
    return project


# ── the platform's half: facts nobody negotiates ────────────────────────────────


def test_crossing_the_plan_records_an_overrun_with_the_platforms_own_breakdown(client):
    project = _over_budget(client)

    overrun = _overrun(project["id"])

    assert overrun is not None
    assert overrun.budget == 100_000
    assert overrun.spent_at_crossing == 140_000
    assert overrun.outcome == OverrunOutcome.PENDING
    assert overrun.ceo_account is None, "the account is the CEO's to write, later"
    assert "by_run_kind" in overrun.breakdown
    assert "does not restate them" in overrun.breakdown["note"]


def test_one_overrun_per_month_however_many_ticks_run(client):
    """A budget crossed on the 3rd and still crossed on the 4th is one overrun,
    not thirty. The janitor ticks every fifteen seconds."""
    project = _over_budget(client)
    for _ in range(5):
        _enforce(project["id"])

    with db_session() as session:
        rows = session.execute(
            select(BudgetOverrun).where(BudgetOverrun.project_id == project["id"])
        ).scalars().all()
    assert len(rows) == 1


def test_the_running_total_keeps_updating_after_the_crossing(client):
    """The interesting number is where the month ended up, not where it was
    first noticed."""
    project = _over_budget(client)
    _book(project["id"], tokens=60_000)
    _enforce(project["id"])

    overrun = _overrun(project["id"])

    assert overrun.spent_at_crossing == 140_000, "the crossing point is history"
    assert overrun.final_spent == 200_000


def test_the_overrun_opens_a_flag_the_ceo_cannot_close_by_silence(client):
    project = _over_budget(client)

    with db_session() as session:
        flags = list(session.execute(
            select(Flag).where(Flag.project_id == project["id"],
                               Flag.kind == "budget_overrun")).scalars())

    assert flags, "an overrun that nobody has to speak about is just a number"
    assert "Nothing has stopped" in flags[0].summary
    assert "do not restate it, explain it" in flags[0].summary


# ── the CEO's half: the move that did not exist ─────────────────────────────────


def test_the_ceo_carries_the_owed_account_on_every_wake(client):
    project = _over_budget(client)

    block = _pack(project)["overrun"]

    assert block["over_by"] == 40_000
    assert block["where_it_went"] is not None
    assert "Going over is not a failure to apologise for" in block["note"]


def test_the_ceo_files_an_account_and_may_recommend_a_number(client):
    project = _over_budget(client)
    overrun = _overrun(project["id"])
    _run_id, token = live_token(project["id"], project["ceo_agent_id"])

    filed = client.post("/agent/overrun_account", json={
        "overrun_id": overrun.id,
        "account": ("The extra went on re-pricing every position after the FX error "
                    "was found; it bought a book that reconciles to the cent, and I "
                    "would spend it again. Steady state is well under the plan."),
        "recommend_budget": 150_000,
    }, headers=bearer(token))

    assert filed.status_code == 200, filed.text
    after = _overrun(project["id"])
    assert after.ceo_account.startswith("The extra went on")
    assert after.accounted_at is not None
    assert after.breakdown["ceo_recommended_budget"] == 150_000


def test_an_account_cannot_be_rewritten_after_the_fact(client):
    project = _over_budget(client)
    overrun = _overrun(project["id"])
    _run_id, token = live_token(project["id"], project["ceo_agent_id"])
    body = {"overrun_id": overrun.id,
            "account": "A" * 100}
    assert client.post("/agent/overrun_account", json=body,
                       headers=bearer(token)).status_code == 200

    again = client.post("/agent/overrun_account", json=body, headers=bearer(token))

    assert again.status_code == 409
    assert "stops being one" in again.json()["error"]


def test_an_employee_cannot_account_for_the_months_spend(client):
    from tests.conftest import agent_by_name

    project = _over_budget(client)
    overrun = _overrun(project["id"])
    _run_id, token = live_token(project["id"], agent_by_name(project, "Lead"))

    refused = client.post("/agent/overrun_account", json={
        "overrun_id": overrun.id, "account": "A" * 100}, headers=bearer(token))

    assert refused.status_code == 403


def test_the_ceo_cannot_raise_its_own_budget(client):
    """A CEO that could raise its own budget has no budget. The recommendation
    is a recommendation."""
    project = _over_budget(client)
    overrun = _overrun(project["id"])
    _run_id, token = live_token(project["id"], project["ceo_agent_id"])

    client.post("/agent/overrun_account", json={
        "overrun_id": overrun.id, "account": "A" * 100,
        "recommend_budget": 999_000_000}, headers=bearer(token))

    with db_session() as session:
        assert effective(session, project["id"]).monthly_token_cap == 100_000


# ── the chairman's half ─────────────────────────────────────────────────────────


def test_the_chairman_decides_and_corrects_the_budget_in_one_motion(client):
    """"Fine, carry on at that level" and "and here is the budget that reflects
    it" are the same thought."""
    project = _over_budget(client)
    overrun = _overrun(project["id"])

    decided = client.post(f"/admin/overruns/{overrun.id}/decide", json={
        "outcome": "continue",
        "note": "Right call — the book had to be re-priced. Budget was too low.",
        "new_budget": 200_000,
    }, headers=ADMIN)

    assert decided.status_code == 200, decided.text
    assert decided.json()["budget_amended"] is True
    with db_session() as session:
        assert effective(session, project["id"]).monthly_token_cap == 200_000
    after = _overrun(project["id"])
    assert after.outcome == OverrunOutcome.CONTINUE
    assert after.decided_at is not None


def test_a_budget_moved_this_way_still_goes_through_the_amendment_path(client):
    """Audited, versioned and disclosed to the CEO like every other rule change.
    A budget moved by a side door is one the CEO cannot see move."""
    from yaaf.core.models import Amendment

    project = _over_budget(client)
    overrun = _overrun(project["id"])

    client.post(f"/admin/overruns/{overrun.id}/decide", json={
        "outcome": "continue", "note": "fine", "new_budget": 250_000}, headers=ADMIN)

    with db_session() as session:
        amendments = list(session.execute(
            select(Amendment).where(Amendment.project_id == project["id"])).scalars())
    assert any("monthly_token_cap" in (a.changes or {}) for a in amendments)


def test_holding_the_line_changes_no_number(client):
    project = _over_budget(client)
    overrun = _overrun(project["id"])

    client.post(f"/admin/overruns/{overrun.id}/decide", json={
        "outcome": "hold", "note": "One-off. The plan stands."}, headers=ADMIN)

    with db_session() as session:
        assert effective(session, project["id"]).monthly_token_cap == 100_000
    assert _overrun(project["id"]).outcome == OverrunOutcome.HOLD


def test_an_overrun_is_decided_once(client):
    project = _over_budget(client)
    overrun = _overrun(project["id"])
    body = {"outcome": "hold", "note": "stands"}
    assert client.post(f"/admin/overruns/{overrun.id}/decide", json=body,
                       headers=ADMIN).status_code == 200

    again = client.post(f"/admin/overruns/{overrun.id}/decide", json=body,
                        headers=ADMIN)

    assert again.status_code == 409


# ── stakes: a LinkedIn post and a real trading account are not the same ─────────


def test_high_stakes_tells_the_ceo_not_to_thin_the_work(client):
    project = make_project(client, billing_mode="subscription",
                           constitution={"stakes": "high"})
    with db_session() as session:
        prose = constitution_prose(
            effective(session, project["id"]), session.get(Project, project["id"]))

    assert "STAKES: HIGH" in prose
    assert "running out of room is worse than going over" in prose
    assert "you have the trade backwards" in prose


def test_low_stakes_says_ask_before_spending_past_the_plan(client):
    project = make_project(client, slug="low", billing_mode="subscription",
                           constitution={"stakes": "low"})
    with db_session() as session:
        prose = constitution_prose(
            effective(session, project["id"]), session.get(Project, project["id"]))

    assert "STAKES: LOW" in prose
    assert "ask him before spending past it" in prose


def test_every_project_is_told_the_plan_is_not_the_wall(client):
    """The sentence that used to read "at 100% the project auto-pauses", which
    is what made the plan a cliff."""
    for stakes in ("low", "normal", "high"):
        project = make_project(client, slug=f"s-{stakes}", billing_mode="subscription",
                               constitution={"stakes": stakes})
        with db_session() as session:
            prose = constitution_prose(
                effective(session, project["id"]), session.get(Project, project["id"]))
        assert "That is a PLAN" in prose
        assert "not a wall" in prose
        assert "auto-pauses" not in prose


# ── the learning half ───────────────────────────────────────────────────────────


def test_the_budget_advice_sizes_to_the_worst_month_not_the_average(client):
    """A budget exists to cover the month you would not want to be caught short
    in. Averaging is how you get caught short half the time."""
    project = make_project(client, billing_mode="subscription",
                           constitution={"stakes": "high"})
    _book(project["id"], tokens=50_000_000, months_ago=1)
    _book(project["id"], tokens=150_000_000, months_ago=2)

    with db_session() as session:
        advice = proposed_budget(
            session, session.get(Project, project["id"]),
            effective(session, project["id"]))

    assert advice is not None
    assert advice["worst_settled_month"] == 150_000_000
    # high stakes widens it further still
    assert advice["suggested_budget"] > 150_000_000
    assert "not to the average one" in advice["note"]


def test_no_settled_month_means_no_advice_rather_than_a_guess(client):
    project = make_project(client, billing_mode="subscription")
    _book(project["id"], tokens=5_000_000)

    with db_session() as session:
        advice = proposed_budget(
            session, session.get(Project, project["id"]),
            effective(session, project["id"]))

    assert advice is None, "the running month is not evidence about a full one"


def test_the_chairman_can_read_the_advice_and_the_whole_conversation(client):
    project = _over_budget(client)
    overrun = _overrun(project["id"])
    _run_id, token = live_token(project["id"], project["ceo_agent_id"])
    client.post("/agent/overrun_account", json={
        "overrun_id": overrun.id, "account": "A" * 100}, headers=bearer(token))
    client.post(f"/admin/overruns/{overrun.id}/decide", json={
        "outcome": "hold", "note": "one-off"}, headers=ADMIN)

    listed = client.get(f"/admin/projects/{project['id']}/overruns", headers=ADMIN)
    advice = client.get(f"/admin/projects/{project['id']}/budget-advice", headers=ADMIN)

    assert listed.status_code == 200
    row = listed.json()["overruns"][0]
    assert row["over_by"] == 40_000
    assert row["ceo_account"] and row["chairman_note"] and row["outcome"] == "hold"
    assert advice.status_code == 200


# ── the protection that must survive all of this ────────────────────────────────


def test_the_hard_stop_is_the_only_thing_that_pauses(client):
    project = make_project(client, billing_mode="subscription",
                           constitution={"monthly_token_cap": 100_000,
                                         "hard_stop_multiple": 3.0})
    _book(project["id"], tokens=250_000)
    _enforce(project["id"])
    with db_session() as session:
        assert session.get(Project, project["id"]).status == ProjectStatus.RUNNING

    _book(project["id"], tokens=100_000)  # now past 300_000
    _enforce(project["id"])

    with db_session() as session:
        p = session.get(Project, project["id"])
        assert p.status == ProjectStatus.PAUSED
        assert "hard stop" in p.status_reason


def test_the_pause_notice_distinguishes_the_wall_from_the_plan(client):
    """Reaching the wall means something went wrong that reporting does not
    cover — and the CEO should not read it as "you overspent"."""
    from yaaf.core.models import Notice

    project = make_project(client, billing_mode="subscription",
                           constitution={"monthly_token_cap": 100_000,
                                         "hard_stop_multiple": 2.0})
    _book(project["id"], tokens=300_000)
    _enforce(project["id"])

    with db_session() as session:
        bodies = list(session.execute(select(Notice.body)).scalars())
    wall = [b for b in bodies if "HARD STOP" in b]
    assert wall
    assert "This is not the budget" in wall[0]
