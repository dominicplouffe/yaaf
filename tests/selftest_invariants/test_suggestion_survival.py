"""A suggestion is an employee's only authority over the backlog.

policy._check_top_level_create refuses task creation to everyone but the CEO
and points them at POST /agent/suggestions — "that is the whole of your
authority over the backlog". Pack construction then consumes suggestions the
same way it consumes notices, but only notices were given back when the run
that ate them died. So a crash the employee had no part in silently deleted
the one thing it is allowed to say about what should be worked on.
"""

from __future__ import annotations

from tests.conftest import agent_by_name, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Run, RunStatus, Suggestion
from yaaf.runner import daemon


def _suggest(client, project, text: str = "The pricing page 404s on mobile.") -> None:
    worker = agent_by_name(project, "Emp2")
    _, token = live_token(project["id"], worker)
    response = client.post("/agent/suggestions", headers=bearer(token), json={"text": text})
    assert response.status_code == 200, response.text


def _live(project) -> list[Suggestion]:
    with db_session() as session:
        return list(
            session.query(Suggestion)
            .filter(Suggestion.project_id == project["id"], Suggestion.consumed_at.is_(None))
            .all()
        )


def _build_ceo_pack(project) -> str:
    """One CEO run builds its pack, which consumes the suggestion."""
    from yaaf.core.models import Agent, Project
    from yaaf.server.contextpack import build_context_pack

    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        run = session.get(Run, run_id)
        pack = build_context_pack(
            session, run, session.get(Agent, project["ceo_agent_id"]),
            session.get(Project, project["id"]),
        )
        assert pack.get("suggestions"), "the pack should have carried it"
    return run_id


def test_building_a_pack_consumes_the_suggestion(client):
    project = make_project(client)
    _suggest(client, project)
    assert len(_live(project)) == 1
    _build_ceo_pack(project)
    assert _live(project) == [], "consumed, as designed"


def test_a_failed_run_gives_the_suggestion_back(client):
    """The regression. Without consumed_by_run_id there is no way to find it."""
    project = make_project(client)
    _suggest(client, project)
    run_id = _build_ceo_pack(project)

    with db_session() as session:
        run = session.get(Run, run_id)
        run.status = RunStatus.FAILED
        daemon._retry_failed_run(session, run)

    survivors = _live(project)
    assert len(survivors) == 1, "a crash must not delete an employee's only say"
    assert survivors[0].consumed_by_run_id is None


def test_a_retry_does_not_resurrect_another_runs_suggestion(client):
    """Give back only what THIS run ate. Un-consuming everything would replay
    suggestions the CEO has already acted on, every time any run failed."""
    project = make_project(client)
    _suggest(client, project, "First: the pricing page 404s on mobile.")
    first_run = _build_ceo_pack(project)
    _suggest(client, project, "Second: checkout drops the promo code.")
    second_run = _build_ceo_pack(project)
    assert _live(project) == []

    with db_session() as session:
        run = session.get(Run, second_run)
        run.status = RunStatus.FAILED
        daemon._retry_failed_run(session, run)

    survivors = _live(project)
    assert [s.text for s in survivors] == ["Second: checkout drops the promo code."]
    with db_session() as session:
        eaten = session.query(Suggestion).filter(Suggestion.text.like("First:%")).one()
        assert eaten.consumed_by_run_id == first_run
