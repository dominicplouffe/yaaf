"""Invariant selftest: a budget for the whole job, spent on the job.

The chairman's complaint, in his words: "when creating a project I want it
done asap ... within the budget that I gave it. No matter how many runs,
messages, and everything in between." The form could not say that. The
budget was a monthly estimate the project only stopped at three times over,
beside a pace whose run ceilings could reach about a fifth of it.

So a finishing project may carry a budget for the whole job, and a pace —
Sprint — whose volume ceilings are lifted so that budget is what paces it.
This file attacks the four promises that makes:

1. Sprint is never a pace without a pot. Refused at every door that writes a
   constitution, because with no job budget nothing would stop it.
2. The job budget is a wall with a hand-in before it: past the reserve only
   the CEO runs; at the total the project pauses. Counted from creation, so a
   month boundary does not refill it.
3. The pot is to be SPENT. A board left empty with scope open opens a flag
   that wakes the CEO in a work run, and it closes when the work is back —
   on every project, because the failure that taught it had no job budget.
4. Existing projects are asked, never rewritten: one health row, two answers.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core import ids
from yaaf.core.constitution import TEMPO_PRESETS, effective, tempo_name
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    AgentKind,
    Flag,
    FlagKind,
    FlagStatus,
    LedgerEntry,
    Notice,
    NoticeKind,
    Project,
    ProjectStatus,
    Requirement,
    RequirementStatus,
    Run,
    RunQueue,
    Task,
    TaskStatus,
    utcnow,
)
from yaaf.server import dispatcher, flags
from yaaf.server.contextpack import constitution_prose
from yaaf.server.janitor import enforce_budget

SPRINT = dict(TEMPO_PRESETS["sprint"])


def _book(project_id: str, usd: float, *, at=None) -> None:
    with db_session() as session:
        session.add(LedgerEntry(
            id=ids.new_id(ids.LEDGER), project_id=project_id, kind="model_usage",
            tokens_in=0, tokens_out=0, cache_creation_tokens=0, cache_read_tokens=0,
            usd=usd, at=at or utcnow()))


def _enforce(project_id: str) -> None:
    with db_session() as session:
        project = session.get(Project, project_id)
        enforce_budget(session, project, effective(session, project.id))


def _rules(project: dict) -> dict:
    with db_session() as session:
        return effective(session, project["id"]).model_dump()


def _amend(client, project: dict, changes: dict, reason: str = "The pot changed."):
    return client.post(f"/admin/projects/{project['id']}/amendments",
                       json={"reason": reason, "changes": changes}, headers=ADMIN)


def _job(client, slug: str = "job", budget: float = 1000.0, **cons) -> dict:
    return make_project(client, slug=slug, mode="deliver",
                        constitution={"project_usd_budget": budget, **cons})


def _status(project: dict) -> str:
    with db_session() as session:
        return session.get(Project, project["id"]).status


def _notices(project: dict, kind: str) -> list[str]:
    with db_session() as session:
        return list(session.execute(
            select(Notice.body).where(Notice.project_id == project["id"],
                                      Notice.kind == kind)).scalars())


# ── 1. Sprint is never a pace without a pot ─────────────────────────────────────


def test_sprint_without_a_job_budget_is_refused(client):
    """At creation: the card names the money, and there is none to name."""
    make_project(client, slug="seed")  # the chairman and roles exist
    body = {"slug": "nopot", "name": "Nopot", "goal": "Ship the thing.",
            "chairman_email": "chair@example.com", "ceo_role_slug": "ceo",
            "ceo_name": "CEO", "mode": "deliver", "constitution": SPRINT,
            "teams": [{"role_slug": "worker", "name": "Alpha",
                       "employees": ["Lead", "Emp2"], "lead": "Lead"}]}
    refused = client.post("/admin/projects", json=body, headers=ADMIN)
    assert refused.status_code == 422, refused.text
    assert "Sprint" in refused.text and "budget for the whole job" in refused.text


def test_the_pot_cannot_be_amended_out_from_under_a_sprint(client):
    """The other door onto the same rule: a Sprint that loses its budget is
    the unwalled state, reached by amendment instead of at creation."""
    project = _job(client)
    assert tempo_name(_rules(project)) == "sprint"
    removed = _amend(client, project, {"project_usd_budget": 0.0})
    assert removed.status_code == 422, removed.text
    assert _rules(project)["project_usd_budget"] == 1000.0

    # Leaving Sprint first is the honest order, and then the pot may go.
    assert _amend(client, project, dict(TEMPO_PRESETS["live"])).status_code == 200
    assert _amend(client, project, {"project_usd_budget": 0.0}).status_code == 200


def test_a_job_budget_in_a_unit_the_project_cannot_measure_is_refused(client):
    """A dollar pot on a subscription project validates in the abstract and
    is never measured, so it would never stop anything."""
    make_project(client, slug="seed")
    body = {"slug": "tokens", "name": "Tokens", "goal": "Ship the thing.",
            "chairman_email": "chair@example.com", "ceo_role_slug": "ceo",
            "ceo_name": "CEO", "mode": "deliver", "billing_mode": "subscription",
            "constitution": {"project_usd_budget": 500.0},
            "teams": [{"role_slug": "worker", "name": "Alpha",
                       "employees": ["Lead", "Emp2"], "lead": "Lead"}]}
    refused = client.post("/admin/projects", json=body, headers=ADMIN)
    assert refused.status_code == 422, refused.text
    assert "project_token_budget" in refused.text

    project = make_project(client, slug="sub", mode="deliver",
                           billing_mode="subscription")
    assert _amend(client, project, {"project_usd_budget": 500.0}).status_code == 422
    assert _amend(client, project, {"project_token_budget": 5_000_000}).status_code == 200


def test_the_ceo_cannot_give_itself_a_bigger_pot(client):
    project = _job(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    forged = client.post(f"/admin/projects/{project['id']}/amendments",
                         json={"reason": "More money would help.",
                               "changes": {"project_usd_budget": 9000.0}},
                         headers=bearer(token))
    assert forged.status_code in (401, 403), forged.text
    assert _rules(project)["project_usd_budget"] == 1000.0


# ── the defaults: silence plus a pot means Sprint ──────────────────────────────


def test_a_finishing_project_given_a_pot_sprints_and_its_month_matches(client):
    project = _job(client, budget=2500.0)
    rules = _rules(project)
    assert tempo_name(rules) == "sprint"
    # The month is set to the pot so its warning and overrun never speak
    # before the job's own lines do.
    assert rules["monthly_usd_cap"] == 2500.0


def test_without_a_pot_nothing_about_the_old_default_moves(client):
    """Every project that exists, and every one created without a job budget,
    is paced exactly as before. A new default would re-pace them silently."""
    project = make_project(client, slug="plain", mode="deliver")
    rules = _rules(project)
    assert tempo_name(rules) == "live"
    assert rules["project_usd_budget"] == 0.0
    with db_session() as session:
        row = session.get(Project, project["id"])
        state = dispatcher.activity_state(session, row, effective(session, row.id))
        prose = constitution_prose(effective(session, row.id), row)
    assert state["job_budget"] is None
    assert "That is a PLAN" in prose and "WHOLE JOB" not in prose


def test_a_monthly_plan_somebody_wrote_is_left_alone(client):
    project = _job(client, budget=2500.0, monthly_usd_cap=400.0)
    assert _rules(project)["monthly_usd_cap"] == 400.0


# ── 2. the wall, and the hand-in before it ─────────────────────────────────────


def test_the_ceo_is_told_the_pot_is_to_be_spent(client):
    project = _job(client)
    with db_session() as session:
        row = session.get(Project, project["id"])
        prose = constitution_prose(effective(session, row.id), row)
    assert "$1,000 for the whole job" in prose
    assert "WHOLE JOB" in prose and "not a number to come in under" in prose
    assert "The last 50 is held back" in prose
    assert "Finishing with money unspent while scope is unmet is a miss" in prose
    # The monthly plan's sentence is about a different object and is not said.
    assert "That is a PLAN" not in prose


def test_past_the_reserve_only_the_ceo_runs(client):
    project = _job(client)
    _book(project["id"], 960.0)  # past 950, the line a 5% reserve draws
    _enforce(project["id"])
    assert _status(project) == ProjectStatus.RUNNING
    reserve = _notices(project, NoticeKind.BUDGET_RESERVE)
    assert len(reserve) == 1 and "hand in" in reserve[0]

    with db_session() as session:
        # Every agent has a reason to wake: a message each.
        for agent in session.query(Agent).filter(Agent.project_id == project["id"]):
            session.add(Notice(id=ids.new_id(ids.NOTICE), project_id=project["id"],
                               agent_id=agent.id, kind=NoticeKind.BUDGET_WARNING,
                               body="Wake up."))
    with db_session() as session:
        dispatcher.dispatch_once(session)
    with db_session() as session:
        woken = {session.get(Agent, q.agent_id).kind
                 for q in session.query(RunQueue).filter(RunQueue.project_id == project["id"])}
        row = session.get(Project, project["id"])
        state = dispatcher.activity_state(session, row, effective(session, row.id))
    assert woken == {AgentKind.CEO}
    assert state["job_budget"]["in_reserve"] is True

    # Said once per job, however many ticks run.
    _enforce(project["id"])
    assert len(_notices(project, NoticeKind.BUDGET_RESERVE)) == 1


def test_at_the_total_the_project_pauses_as_a_hard_stop(client):
    project = _job(client)
    _book(project["id"], 1000.0)
    _enforce(project["id"])
    with db_session() as session:
        row = session.get(Project, project["id"])
        assert row.status == ProjectStatus.PAUSED
        # The same prefix the monthly wall uses: only the chairman restarts it,
        # and `yaaf signal` lights the bar on exactly this.
        assert row.status_reason.startswith("hard stop")
        assert "job budget" in row.status_reason
    paused = _notices(project, NoticeKind.BUDGET_EXHAUSTED)
    assert paused and "not a failure" in paused[0]


def test_a_new_month_does_not_refill_the_pot(client):
    """Counted from creation. A monthly reset is the exact thing a budget for
    the whole job is not."""
    project = _job(client)
    last_month = utcnow().replace(day=1) - timedelta(days=2)
    _book(project["id"], 700.0, at=last_month)
    _book(project["id"], 300.0)
    _enforce(project["id"])
    assert _status(project) == ProjectStatus.PAUSED


def test_the_monthly_plan_below_the_pot_does_not_stop_it_early(client):
    """No reserve notice, no pause, no overrun, below either of the pot's lines."""
    project = _job(client)
    _book(project["id"], 500.0)
    _enforce(project["id"])
    assert _status(project) == ProjectStatus.RUNNING
    assert not _notices(project, NoticeKind.BUDGET_RESERVE)


def test_runs_left_are_counted_against_the_pot(client):
    from yaaf.server import policy

    project = _job(client)
    _book(project["id"], 400.0)
    with db_session() as session:
        row = session.get(Project, project["id"])
        for _ in range(5):
            session.add(Run(id=ids.new_id(ids.RUN), project_id=row.id,
                            agent_id=project["ceo_agent_id"], kind="agent_run",
                            trigger={}, status="done", cost_usd=1.0,
                            started_at=utcnow(), finished_at=utcnow()))
        session.flush()
        left = policy.affordable_runs(session, row, effective(session, row.id))
    assert left["usd_remaining"] == 600.0


# ── 3. the board is not left empty ─────────────────────────────────────────────


def _empty_board(client, slug: str, minutes_ago: int, **cons) -> dict:
    """A delivery whose last task closed `minutes_ago`, with its requirement
    still open: the shape of the morning six seats sat idle."""
    project = make_project(client, slug=slug, mode="deliver",
                           constitution={"plan_authority": "ceo", **cons})
    then = utcnow() - timedelta(minutes=minutes_ago)
    with db_session() as session:
        session.query(RunQueue).delete()
        req = Requirement(
            id=ids.new_id(ids.REQUIREMENT), project_id=project["id"], key="R-04",
            statement="The home and links pages run on the merged design.",
            acceptance_test={"type": "observable", "target": "the pages render"},
            status=RequirementStatus.IN_PROGRESS, stage=1)
        session.add(req)
        session.add(Task(
            id=ids.new_id(ids.TASK), project_id=project["id"],
            team_id=project["teams"][0]["id"], requirement_id=req.id,
            created_by_agent_id=project["ceo_agent_id"],
            title="Container and CI", goal="Merge the CI change.",
            success_test={"type": "command", "command": "true"},
            status=TaskStatus.DONE, created_at=then, updated_at=then))
    return project


def _evaluate(project: dict) -> None:
    with db_session() as session:
        row = session.get(Project, project["id"])
        flags.evaluate(session, row, effective(session, row.id))


def _flag(project: dict) -> Flag | None:
    with db_session() as session:
        row = session.execute(select(Flag).where(
            Flag.project_id == project["id"], Flag.kind == FlagKind.BOARD_EMPTY,
            Flag.status.in_([FlagStatus.OPEN, FlagStatus.RAISED]))).scalars().first()
        if row is not None:
            session.expunge(row)
        return row


def test_an_empty_board_with_open_scope_opens_a_flag(client):
    project = _empty_board(client, "idle", minutes_ago=90)
    _evaluate(project)
    flag = _flag(project)
    assert flag is not None
    assert "nothing on the board" in flag.summary
    assert "not one at a time" in flag.summary


def test_it_is_measured_on_the_board_not_on_runs(client):
    """The CEO that prompted this ran 29 times that morning and created no
    tasks. Runs spent answering messages are not work on the board."""
    project = _empty_board(client, "busy-ceo", minutes_ago=90)
    with db_session() as session:
        for _ in range(3):
            session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"],
                            agent_id=project["ceo_agent_id"], kind="agent_run",
                            trigger={}, status="done", cost_usd=0.2,
                            started_at=utcnow(), finished_at=utcnow()))
    _evaluate(project)
    assert _flag(project) is not None


def test_the_flag_wakes_the_ceo_in_a_work_run(client):
    """Through FLAG_OPENED, which is urgent — not through the boardroom, where a
    question from the chairman bars the CEO from creating anything."""
    from yaaf.server.janitor import evaluate_flags

    project = _empty_board(client, "wake", minutes_ago=90)
    with db_session() as session:
        row = session.get(Project, project["id"])
        evaluate_flags(session, row, effective(session, row.id))
    assert _notices(project, NoticeKind.FLAG_OPENED)
    assert NoticeKind.FLAG_OPENED in dispatcher.URGENT_NOTICE_KINDS


def test_putting_work_back_closes_it_without_a_word(client):
    project = _empty_board(client, "refill", minutes_ago=90)
    _evaluate(project)
    assert _flag(project) is not None
    with db_session() as session:
        req = session.query(Requirement).filter(
            Requirement.project_id == project["id"], Requirement.key == "R-04").one()
        session.add(Task(
            id=ids.new_id(ids.TASK), project_id=project["id"],
            team_id=project["teams"][0]["id"], requirement_id=req.id,
            created_by_agent_id=project["ceo_agent_id"],
            title="Build the home page", goal="On the merged design.",
            success_test={"type": "command", "command": "true"},
            status=TaskStatus.READY, created_at=utcnow(), updated_at=utcnow()))
    _evaluate(project)
    assert _flag(project) is None


def test_a_board_that_just_emptied_is_given_its_minutes(client):
    project = _empty_board(client, "fresh", minutes_ago=5)
    _evaluate(project)
    assert _flag(project) is None


def test_the_floor_can_be_turned_off(client):
    project = _empty_board(client, "off", minutes_ago=600, idle_board_minutes=0)
    _evaluate(project)
    assert _flag(project) is None


def test_scope_waiting_on_the_chairman_is_not_the_ceos_idle(client):
    """Scope only he can accept: the empty board is his move, and the flag
    would make the CEO account for a silence he is producing."""
    project = _empty_board(client, "his-move", minutes_ago=90, plan_authority="chairman")
    with db_session() as session:
        req = session.query(Requirement).filter(
            Requirement.project_id == project["id"], Requirement.key == "R-04").one()
        req.status = RequirementStatus.PROPOSED
    _evaluate(project)
    assert _flag(project) is None


def test_on_a_job_budget_the_flag_names_the_unspent_money(client):
    project = _empty_board(client, "pot-idle", minutes_ago=90,
                           project_usd_budget=800.0)
    _book(project["id"], 100.0)
    _evaluate(project)
    flag = _flag(project)
    assert flag is not None and "$700.00 of the job budget is unspent" in flag.summary


# ── 4. existing projects are asked, never rewritten ────────────────────────────


def _checks(client, project) -> dict[str, dict]:
    response = client.get(f"/observe/projects/{project['id']}/health", headers=ADMIN)
    assert response.status_code == 200, response.text
    return {c["key"]: c for c in response.json()["checks"]}


def test_a_finishing_project_on_a_monthly_plan_is_offered_the_switch(client):
    project = make_project(client, slug="legacy", mode="deliver")
    _book(project["id"], 120.0)
    row = _checks(client, project)["job-budget"]
    action = row["action"]
    assert action["kind"] == "job_budget" and action["spent"] == 120.0
    assert action["rule"] == "project_usd_budget"
    assert "$120 has been spent so far" in row["detail"]
    # Offered, not applied.
    assert tempo_name(_rules(project)) == "live"

    switched = _amend(client, project, {**action["sprint"], action["rule"]: 900.0,
                                        action["month_rule"]: 900.0})
    assert switched.status_code == 200, switched.text
    assert tempo_name(_rules(project)) == "sprint"
    assert "job-budget" not in _checks(client, project)


def test_keeping_the_monthly_budget_answers_for_the_project(client):
    project = make_project(client, slug="keeper", mode="deliver")
    assert "job-budget" in _checks(client, project)
    kept = client.post(f"/admin/projects/{project['id']}/job-budget/keep", headers=ADMIN)
    assert kept.status_code == 200, kept.text
    assert "job-budget" not in _checks(client, project)
    assert _rules(project)["project_usd_budget"] == 0.0


def test_a_project_on_a_rhythm_is_never_offered_one(client):
    project = make_project(client, slug="rhythm", mode="operate")
    assert "job-budget" not in _checks(client, project)
