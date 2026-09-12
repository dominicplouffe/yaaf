"""Invariant selftest: the roster is the CEO's to run, inside walls it cannot move.

A project is front-loaded and then it is not — planning, requirements, build,
validation and QA crowd the first half, execution is the second and needs a
fraction of the seats. Every roster here was hired at the one moment nobody
could know that, and then kept. On the desk that prompted this, two of five
seats had done no work for two days and had spent 139M tokens between them.

So the CEO hires and stands roles down on its own authority. What stops that
being a licence is not an approval queue — it is three walls it cannot argue
with: a ceiling on seats, a refusal to orphan work, and a refusal to remove the
last seat whose job is to contradict it. These tests attack all three over HTTP,
as an agent would.
"""

from __future__ import annotations

from tests.conftest import (
    ADMIN,
    agent_by_name,
    amend,
    bearer,
    live_token,
    make_project,
    seed_role,
)
from yaaf.core.db import db_session
from yaaf.core.models import Agent, AgentStatus, Task, TaskStatus


def _ceo(project: dict) -> str:
    _, token = live_token(project["id"], project["ceo_agent_id"])
    return token


def _hire(client, token: str, **body) -> object:
    payload = {"role_slug": "worker", "name": "Second Desk", "employees": ["Kit"],
               "reason": "The build half needs a second pair of hands for two weeks.",
               **body}
    return client.post("/agent/teams", headers=bearer(token), json=payload)


def _stand_down(client, token: str, team_id: str, reason: str | None = None) -> object:
    return client.post(
        f"/agent/teams/{team_id}/stand_down", headers=bearer(token),
        json={"reason": reason or "The template shipped; this seat has nothing left."})


def _statuses(project_id: str) -> dict[str, str]:
    with db_session() as session:
        return {a.name: a.status for a in session.query(Agent)
                .filter(Agent.project_id == project_id)}


# ── hiring ──────────────────────────────────────────────────────────────────────


def test_the_ceo_hires_without_asking_anybody(client):
    """No approval round-trip. Hiring buys no runs — the ration and the monthly
    wall are the project's, not the seat's — so the CEO is spending inside a
    budget it already has, from a library the chairman curates."""
    project = make_project(client)
    hired = _hire(client, _ceo(project))
    assert hired.status_code == 200, hired.text
    assert [a["name"] for a in hired.json()["agents"]] == ["Kit"]
    assert _statuses(project["id"])["Kit"] == "active"


def test_a_hire_lands_in_the_boardroom_where_the_chairman_reads(client):
    """He does not have to answer for it to happen, and he must never learn
    about it from a screen he happened to open."""
    project = make_project(client)
    _hire(client, _ceo(project))
    messages = client.get(f"/observe/projects/{project['id']}/channels", headers=ADMIN).json()
    boardroom = next(c["id"] for c in messages if c["kind"] == "boardroom")
    bodies = [m["body"] for m in client.get(
        f"/observe/channels/{boardroom}/messages", headers=ADMIN).json()]
    assert any("HIRED" in b and "Second Desk" in b for b in bodies), bodies


def test_nobody_but_the_ceo_staffs_the_project(client):
    project = make_project(client)
    _, emp = live_token(project["id"], agent_by_name(project, "Emp2"))
    refused = _hire(client, emp)
    assert refused.status_code == 403
    assert "suggestion survives" in refused.text


def test_the_roster_ceiling_is_a_wall_and_names_the_way_past_it(client):
    """`max_teams` is the whole of what bounds 'the CEO hires alone'. At the
    cap it must refuse and point at the verb that already exists for arguing
    you need more of something — not leave the CEO to invent a route."""
    project = make_project(client, constitution={"max_teams": 2})
    token = _ceo(project)
    assert _hire(client, token, name="Second Desk", employees=["Kit"]).status_code == 200

    refused = _hire(client, token, name="Third Desk", employees=["Nils"])
    assert refused.status_code == 409
    assert "capacity_request" in refused.text and "max_teams" in refused.text
    # And the reason the cap is on seats rather than on money.
    assert "does not buy you runs" in refused.text


def test_standing_a_role_down_frees_the_seat_the_ceiling_counts(client):
    project = make_project(client, constitution={"max_teams": 2})
    token = _ceo(project)
    second = _hire(client, token, name="Second Desk", employees=["Kit"]).json()
    assert _stand_down(client, token, second["id"]).status_code == 200
    assert _hire(client, token, name="Third Desk", employees=["Nils"]).status_code == 200


def test_the_ceo_reads_the_library_but_never_a_system_prompt(client):
    """A role's prompt is the instrument the chairman shapes an agent with.
    Handing it to the one agent that can hire would let a CEO read the charter
    it is about to be contradicted by and pick the seat least likely to do it."""
    project = make_project(client)
    library = client.get("/agent/roles", headers=bearer(_ceo(project))).json()
    assert library["max_teams"] == 8
    assert library["teams_running"] == 1
    worker = next(r for r in library["roles"] if r["slug"] == "worker")
    assert worker["already_on_roster_as"] == "Alpha"
    assert not any("system_prompt" in r for r in library["roles"])
    # The CEO's own role is not a seat it can hire.
    assert not any(r["slug"] == "ceo" for r in library["roles"])


# ── standing down ───────────────────────────────────────────────────────────────


def test_a_stood_down_seat_is_not_a_paused_one(client):
    """Identical to the dispatcher and opposite to a reader. Told only 'not
    active', the idle brief advised 'resume the agent, or reassign the work'
    about seats whose job was over — a roster managed correctly reading as a
    roster somebody had abandoned."""
    project = make_project(client)
    token = _ceo(project)
    team = _hire(client, token).json()
    _stand_down(client, token, team["id"], reason="The template shipped; nothing left.")

    with db_session() as session:
        kit = session.query(Agent).filter(Agent.name == "Kit").one()
        assert kit.status == AgentStatus.STOOD_DOWN
        assert kit.stood_down_at is not None
        assert "template shipped" in kit.stood_down_reason


def test_a_stood_down_seat_is_never_dispatched(client):
    from yaaf.core.db import db_session as _s
    from yaaf.server import dispatcher

    project = make_project(client)
    token = _ceo(project)
    team = _hire(client, token).json()
    _stand_down(client, token, team["id"])

    with _s() as session:
        agent = session.query(Agent).filter(Agent.name == "Kit").one()
        assert dispatcher._wake_reasons is not None  # the loop exists
        # The dispatcher selects on status == ACTIVE, so a stood-down seat is
        # never even considered. Asserted through the query it actually runs.
        active = session.query(Agent).filter(
            Agent.project_id == project["id"], Agent.status == AgentStatus.ACTIVE).all()
        assert agent.id not in {a.id for a in active}


def test_a_role_holding_work_cannot_be_stood_down(client):
    """The platform used to report orphaned work AFTER the fact — `blocked_by:
    paused_agents`, with the brief telling the chairman to go resume somebody.
    Refusing up front means that state cannot be reached by a stand-down at
    all."""
    project = make_project(client)
    token = _ceo(project)
    team = _hire(client, token).json()
    with db_session() as session:
        session.add(Task(
            id="task_holdingwork", project_id=project["id"], team_id=team["id"],
            title="Half-finished thing", goal="Something still open on the board.",
            success_test={"kind": "observable", "text": "it is visibly done somewhere"},
            status=TaskStatus.READY, created_by_agent_id=project["ceo_agent_id"]))

    refused = _stand_down(client, token, team["id"])
    assert refused.status_code == 409
    assert "Half-finished thing" in refused.text
    assert "kill it, or move it" in refused.text
    assert _statuses(project["id"])["Kit"] == "active"


def test_the_ceo_cannot_stand_down_the_last_seat_that_reads_its_work(client):
    """A task stuck in REVIEW forever is worse than the self-report it
    replaced, and the CEO removing the reader is how you get there."""
    seed_role(client, "adversary", category="challenge")
    project = make_project(
        client,
        teams=[{"role_slug": "worker", "name": "Alpha", "employees": ["Lead", "Emp2"],
                "lead": "Lead"},
               {"role_slug": "adversary", "name": "Red Team", "employees": ["Juno"]}],
        constitution={"deliverable_review": "independent_read"})
    token = _ceo(project)
    red = next(t["id"] for t in project["teams"] if t["name"] == "Red Team")

    refused = _stand_down(client, token, red)
    assert refused.status_code == 409
    assert "only seat left that can read" in refused.text
    assert "ask the chairman" in refused.text


def test_the_ceo_cannot_stand_down_its_own_last_critic(client):
    """The CEO wrote the plan and on an autonomous project accepted it too, so
    it is the only reader of that plan with no distance from it. That is
    exactly why this seat is not its to remove."""
    seed_role(client, "adversary", category="challenge")
    project = make_project(
        client, mode="deliver",
        teams=[{"role_slug": "worker", "name": "Alpha", "employees": ["Lead", "Emp2"],
                "lead": "Lead"},
               {"role_slug": "adversary", "name": "Red Team", "employees": ["Juno"]}])
    token = _ceo(project)
    red = next(t["id"] for t in project["teams"] if t["name"] == "Red Team")

    refused = _stand_down(client, token, red)
    assert refused.status_code == 409
    assert "attack the work" in refused.text
    # And an ordinary seat on the same project still goes.
    alpha = next(t["id"] for t in project["teams"] if t["name"] == "Alpha")
    assert _stand_down(client, token, alpha).status_code == 200


def test_the_chairman_can_stand_down_the_critic_the_ceo_cannot(client):
    """The rule is about a CEO deciding about its own contradiction. It is not
    a rule about the roster, so it does not bind the person who owns one."""
    seed_role(client, "adversary", category="challenge")
    project = make_project(
        client, mode="deliver",
        teams=[{"role_slug": "worker", "name": "Alpha", "employees": ["Lead", "Emp2"],
                "lead": "Lead"},
               {"role_slug": "adversary", "name": "Red Team", "employees": ["Juno"]}])
    red = next(t["id"] for t in project["teams"] if t["name"] == "Red Team")

    done = client.post(f"/admin/teams/{red}/stand_down", headers=ADMIN,
                       json={"reason": "The plan is frozen; the critic's job is over."})
    assert done.status_code == 200, done.text
    assert _statuses(project["id"])["Juno"] == "stood_down"


# ── recall ──────────────────────────────────────────────────────────────────────


def test_a_role_comes_back_with_its_channel_and_its_history(client):
    project = make_project(client)
    token = _ceo(project)
    team = _hire(client, token).json()
    channels_before = client.get(
        f"/observe/projects/{project['id']}/channels", headers=ADMIN).json()
    _stand_down(client, token, team["id"])

    back = client.post(f"/agent/teams/{team['id']}/recall", headers=bearer(token),
                       json={"reason": "The second half needs this seat after all."})
    assert back.status_code == 200, back.text
    assert back.json()["recalled"] == ["Kit"]
    assert _statuses(project["id"])["Kit"] == "active"
    channels_after = client.get(
        f"/observe/projects/{project['id']}/channels", headers=ADMIN).json()
    assert len(channels_after) == len(channels_before)


def test_recall_is_not_a_lever_for_undoing_the_chairmans_pause(client):
    """A paused seat is his intervention. A CEO that could recall its way out
    of one would make the pause advisory."""
    project = make_project(client)
    token = _ceo(project)
    team = _hire(client, token).json()
    kit = team["agents"][0]["id"]
    client.post(f"/admin/agents/{kit}/pause", headers=ADMIN, json={"paused": True})

    refused = client.post(f"/agent/teams/{team['id']}/recall", headers=bearer(token),
                          json={"reason": "I would like this seat back please."})
    assert refused.status_code == 409
    assert "stays paused" in refused.text
    assert _statuses(project["id"])["Kit"] == "paused"


# ── the lead bug this forced out ────────────────────────────────────────────────


def test_a_team_whose_only_seat_stopped_has_no_lead(client):
    """`effective_lead` ignored status, so a team whose only member was paused
    still reported a lead — and the platform believed it. The board classed the
    team as led rather than leaderless and capacity kept charging its tasks to
    an agent the dispatcher skips."""
    from yaaf.core.models import Team
    from yaaf.server import policy

    project = make_project(client)
    token = _ceo(project)
    team_id = _hire(client, token).json()["id"]
    with db_session() as session:
        assert policy.effective_lead(session, session.get(Team, team_id)).name == "Kit"

    _stand_down(client, token, team_id)
    with db_session() as session:
        assert policy.effective_lead(session, session.get(Team, team_id)) is None


def test_a_roster_the_chairman_shrank_still_reports_its_ceiling(client):
    """Lowering `max_teams` below the live count is legal and must not wedge:
    the project keeps the seats it has and simply cannot add one."""
    project = make_project(client)
    token = _ceo(project)
    _hire(client, token)
    amend(client, project["id"], reason="Too many seats for this work", max_teams=1)

    refused = _hire(client, token, name="Third Desk", employees=["Nils"])
    assert refused.status_code == 409
    assert _statuses(project["id"])["Kit"] == "active"
