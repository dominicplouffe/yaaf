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

from tests.conftest import ADMIN, bearer, live_token, make_project, seed_role
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


# ── 3. no team is left with nothing to do ──────────────────────────────────────


def _idle_project(client, slug: str, minutes_ago: int, *, mode: str = "deliver",
                  teams=None, **cons) -> dict:
    """A project whose last task closed `minutes_ago`, with its requirement
    still open: the shape of the morning six seats sat idle."""
    project = make_project(client, slug=slug, mode=mode, teams=teams,
                           constitution={"plan_authority": "ceo", **cons})
    then = utcnow() - timedelta(minutes=minutes_ago)
    with db_session() as session:
        session.query(RunQueue).delete()
        req = None
        if mode != "explore":
            req = Requirement(
                id=ids.new_id(ids.REQUIREMENT), project_id=project["id"], key="R-04",
                statement="The home and links pages run on the merged design.",
                acceptance_test={"type": "observable", "target": "the pages render"},
                status=RequirementStatus.IN_PROGRESS, stage=1)
            session.add(req)
        for team in project["teams"]:
            session.add(Task(
                id=ids.new_id(ids.TASK), project_id=project["id"], team_id=team["id"],
                requirement_id=req.id if req else None,
                created_by_agent_id=project["ceo_agent_id"],
                title="Container and CI", goal="Merge the CI change.",
                success_test={"type": "command", "command": "true"},
                status=TaskStatus.DONE, created_at=then, updated_at=then))
    return project


def _give_work(project: dict, team_index: int = 0) -> None:
    with db_session() as session:
        req = session.query(Requirement).filter(
            Requirement.project_id == project["id"], Requirement.key == "R-04").first()
        session.add(Task(
            id=ids.new_id(ids.TASK), project_id=project["id"],
            team_id=project["teams"][team_index]["id"],
            requirement_id=req.id if req else None,
            created_by_agent_id=project["ceo_agent_id"],
            title="Build the home page", goal="On the merged design.",
            success_test={"type": "command", "command": "true"},
            status=TaskStatus.READY, created_at=utcnow(), updated_at=utcnow()))


def _evaluate(project: dict) -> None:
    with db_session() as session:
        row = session.get(Project, project["id"])
        flags.evaluate(session, row, effective(session, row.id))


def _flag(project: dict) -> Flag | None:
    with db_session() as session:
        row = session.execute(select(Flag).where(
            Flag.project_id == project["id"], Flag.kind == FlagKind.TEAMS_IDLE,
            Flag.status.in_([FlagStatus.OPEN, FlagStatus.RAISED]))).scalars().first()
        if row is not None:
            session.expunge(row)
        return row


TWO_TEAMS = [{"role_slug": "worker", "name": "Frontend", "employees": ["Ada", "Kai"],
              "lead": "Ada"},
             {"role_slug": "worker", "name": "Backend", "employees": ["Noor", "Sol"],
              "lead": "Noor"}]


def test_a_team_with_nothing_to_do_opens_a_flag(client):
    project = _idle_project(client, "idle", minutes_ago=90)
    _evaluate(project)
    flag = _flag(project)
    assert flag is not None
    assert "Alpha" in flag.summary and "not one at a time" in flag.summary


def test_one_task_on_the_board_does_not_hide_an_idle_team(client):
    """The CEO that prompted this fed its board one task at a time. The board
    was never empty, so an empty-board rule never fired."""
    project = _idle_project(client, "one-at-a-time", minutes_ago=90, teams=TWO_TEAMS)
    _give_work(project, team_index=0)
    _evaluate(project)
    flag = _flag(project)
    assert flag is not None
    assert "Backend" in flag.summary and "Frontend" not in flag.summary


def test_it_closes_only_when_every_team_has_work(client):
    project = _idle_project(client, "fill-all", minutes_ago=90, teams=TWO_TEAMS)
    _evaluate(project)
    assert _flag(project) is not None
    _give_work(project, team_index=0)
    _evaluate(project)
    assert _flag(project) is not None, "one team fed is not the answer"
    _give_work(project, team_index=1)
    _evaluate(project)
    assert _flag(project) is None


def test_it_is_measured_on_teams_not_on_runs(client):
    """The CEO ran 29 times that morning and created no tasks. Runs spent
    answering messages are not work for a team."""
    project = _idle_project(client, "busy-ceo", minutes_ago=90)
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

    project = _idle_project(client, "wake", minutes_ago=90)
    with db_session() as session:
        row = session.get(Project, project["id"])
        evaluate_flags(session, row, effective(session, row.id))
    assert _notices(project, NoticeKind.FLAG_OPENED)
    assert NoticeKind.FLAG_OPENED in dispatcher.URGENT_NOTICE_KINDS


def test_a_team_that_just_ran_dry_is_given_its_minutes(client):
    project = _idle_project(client, "fresh", minutes_ago=5)
    _evaluate(project)
    assert _flag(project) is None


def test_a_job_budget_gives_a_team_five_minutes_not_thirty(client):
    project = _idle_project(client, "pot-fast", minutes_ago=8, project_usd_budget=800.0)
    assert _rules(project)["idle_team_minutes"] == 5
    _book(project["id"], 100.0)
    _evaluate(project)
    flag = _flag(project)
    assert flag is not None and "$700.00 of the job budget is unspent" in flag.summary


def test_the_floor_can_be_turned_off(client):
    project = _idle_project(client, "off", minutes_ago=600, idle_team_minutes=0)
    _evaluate(project)
    assert _flag(project) is None


def test_scope_waiting_on_the_chairman_is_not_the_ceos_idle(client):
    """Scope only he can accept: the idle team is his move, and the flag would
    make the CEO account for a silence he is producing."""
    project = _idle_project(client, "his-move", minutes_ago=90, plan_authority="chairman")
    with db_session() as session:
        req = session.query(Requirement).filter(
            Requirement.project_id == project["id"], Requirement.key == "R-04").one()
        req.status = RequirementStatus.PROPOSED
        plan = session.query(Requirement).filter(
            Requirement.project_id == project["id"], Requirement.key == "R-00").first()
        if plan is not None:
            plan.status = RequirementStatus.MET
    _evaluate(project)
    assert _flag(project) is None


def test_a_reviewing_team_waiting_for_work_is_not_idle(client):
    seed_role(client, "editor", category="challenge")
    project = _idle_project(client, "review-waits", minutes_ago=90, teams=[
        {"role_slug": "worker", "name": "Writers", "employees": ["Ada"]},
        {"role_slug": "editor", "name": "Review", "employees": ["Juno"]}])
    _give_work(project, team_index=0)
    _evaluate(project)
    assert _flag(project) is None


def test_explore_counts_only_with_a_pot_to_spend(client):
    """Explore has no requirements to count. Open-ended and unfunded, idle can
    be the right answer; given a budget for the whole job, it is not."""
    unfunded = _idle_project(client, "explore-free", minutes_ago=90, mode="explore")
    _evaluate(unfunded)
    assert _flag(unfunded) is None

    funded = _idle_project(client, "explore-pot", minutes_ago=90, mode="explore",
                           project_usd_budget=400.0)
    _evaluate(funded)
    assert _flag(funded) is not None


def test_a_project_on_a_rhythm_is_left_to_its_rhythm(client):
    project = _idle_project(client, "operate", minutes_ago=600, mode="operate")
    _evaluate(project)
    assert _flag(project) is None


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
    assert action["sprint"]["idle_team_minutes"] == 5
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


# ── spreading a job budget across its window ────────────────────────────────────


def _spread(client, slug: str, budget: float = 300.0, days: int = 30, **cons):
    deadline = (utcnow() + timedelta(days=days)).isoformat()
    return make_project(client, slug=slug, mode="explore", deadline=deadline,
                        constitution={"project_usd_budget": budget,
                                      "spread_job_budget": True, "budget_reserve_ratio": 0.0,
                                      **cons})


def _share(project: dict):
    from yaaf.server import budget as budget_lib

    with db_session() as session:
        row = session.get(Project, project["id"])
        return budget_lib.daily_share(session, row, effective(session, row.id))


def test_spreading_needs_a_job_budget_and_a_date(client):
    make_project(client, slug="seed")
    base = {"name": "S", "goal": "One sale in the next 60 days.",
            "chairman_email": "chair@example.com", "ceo_role_slug": "ceo",
            "ceo_name": "CEO", "mode": "explore",
            "teams": [{"role_slug": "worker", "name": "Alpha", "employees": ["Lead"]}]}
    no_pot = client.post("/admin/projects", headers=ADMIN, json={
        **base, "slug": "no-pot", "constitution": {"spread_job_budget": True}})
    assert no_pot.status_code == 422 and "no job budget" in no_pot.text
    no_date = client.post("/admin/projects", headers=ADMIN, json={
        **base, "slug": "no-date",
        "constitution": {"project_usd_budget": 300.0, "spread_job_budget": True}})
    assert no_date.status_code == 422 and "deadline" in no_date.text

    undated = make_project(client, slug="undated", mode="explore",
                           constitution={"project_usd_budget": 300.0})
    assert _amend(client, undated, {"spread_job_budget": True}).status_code == 422


def test_each_day_gets_an_equal_share_of_what_is_left(client):
    project = _spread(client, "window", budget=300.0, days=30)
    share = _share(project)
    assert share is not None and 9.5 <= share.share <= 10.5
    assert not share.spent

    # Yesterday's spend is not today's: the share is recomputed from what is left.
    _book(project["id"], 90.0, at=utcnow() - timedelta(days=1))
    assert 6.5 <= _share(project).share <= 7.5


def test_past_todays_share_work_waits_for_midnight(client):
    project = _spread(client, "spent-today", budget=300.0, days=30)
    _book(project["id"], 12.0)
    with db_session() as session:
        row = session.get(Project, project["id"])
        state = dispatcher.activity_state(session, row, effective(session, row.id))
    assert state["blocked_by"] == "daily_money"
    assert state["brief"]["clears_at"] == state["daily_money"]["resets_at"]
    assert "Today's share of the job budget is spent" in state["brief"]["headline"]

    with db_session() as session:
        for agent in session.query(Agent).filter(Agent.project_id == project["id"],
                                                 Agent.kind != AgentKind.CEO):
            session.add(Notice(id=ids.new_id(ids.NOTICE), project_id=project["id"],
                               agent_id=agent.id, kind=NoticeKind.BUDGET_WARNING,
                               body="Wake up."))
    with db_session() as session:
        session.query(RunQueue).delete()
        dispatcher.dispatch_once(session)
    with db_session() as session:
        queued = session.query(RunQueue).filter(RunQueue.project_id == project["id"]).count()
    assert queued == 0


def test_a_sprint_is_never_held_to_a_share(client):
    project = _job(client, slug="no-spread")
    _book(project["id"], 400.0)
    assert _share(project) is None


# ── a factory handed over to upkeep ─────────────────────────────────────────────


def test_a_kept_factory_on_its_build_budget_is_asked_for_a_month(client):
    from yaaf.core.models import FactoryPhase

    project = make_project(client, slug="handed-over", mode="code_factory",
                           constitution={"project_usd_budget": 900.0})
    assert "upkeep-budget" not in _checks(client, project)  # still building
    with db_session() as session:
        session.get(Project, project["id"]).factory_phase = FactoryPhase.UPKEEP
    row = _checks(client, project)["upkeep-budget"]
    action = row["action"]
    assert action["kind"] == "upkeep_budget" and "Sprint" in row["detail"]
    assert tempo_name(_rules(project)) == "sprint"  # asked, not applied

    switched = _amend(client, project, {**action["changes"], action["month_rule"]: 150.0})
    assert switched.status_code == 200, switched.text
    rules = _rules(project)
    assert rules["project_usd_budget"] == 0.0 and rules["monthly_usd_cap"] == 150.0
    assert tempo_name(rules) == "few_times_daily"
    assert "upkeep-budget" not in _checks(client, project)


def test_keeping_the_job_budget_in_upkeep_answers_it(client):
    from yaaf.core.models import FactoryPhase

    project = make_project(client, slug="kept-pot", mode="code_factory",
                           constitution={"project_usd_budget": 900.0})
    with db_session() as session:
        session.get(Project, project["id"]).factory_phase = FactoryPhase.UPKEEP
    assert client.post(f"/admin/projects/{project['id']}/upkeep-budget/keep",
                       headers=ADMIN).status_code == 200
    assert "upkeep-budget" not in _checks(client, project)
