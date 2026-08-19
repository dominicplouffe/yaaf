"""Test harness.

SQLite file DB per test, the real FastAPI app via TestClient, and helpers that
mint real run tokens — so the invariant selftests attack the rules exactly the
way an agent would: over HTTP, with a scoped principal.
"""

from __future__ import annotations

import os

# Must land before any yaaf import resolves settings.
import tempfile

os.environ.setdefault("YAAF_SANDBOX_MODE", "subprocess")
os.environ.setdefault("YAAF_ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("YAAF_MASTER_KEY", "kkP1p6Rt0DZ2rWv1w5Zq3o8y6bT4uY9cQ1eS7gJ0aXk=")
os.environ.setdefault("YAAF_WORKSPACES_ROOT", tempfile.mkdtemp(prefix="yaaf-test-ws-"))
os.environ.setdefault("YAAF_ARTIFACTS_ROOT", tempfile.mkdtemp(prefix="yaaf-test-art-"))

import pytest
from fastapi.testclient import TestClient

from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.db import db_session, engine, reset_for_tests
from yaaf.core.models import Agent, Base, Project, Run, RunStatus, utcnow
from yaaf.core.tokens import mint

settings.cache_clear()

ADMIN = {"Authorization": "Bearer test-admin-token"}

ROLE_MIN = {
    "description": "test role",
    "system_prompt": "You are a test agent.",
    "model": "claude-sonnet-5",
}


@pytest.fixture()
def client(tmp_path):
    # SQLite by default; set YAAF_TEST_DATABASE_URL to run the same suite
    # against real Postgres (exercises SKIP LOCKED, JSONB, NOTIFY).
    override = os.environ.get("YAAF_TEST_DATABASE_URL")
    if override:
        reset_for_tests(override)
        Base.metadata.drop_all(engine())
    else:
        reset_for_tests(f"sqlite:///{tmp_path}/yaaf-test.db")
    Base.metadata.create_all(engine())
    from yaaf.server.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def seed_role(client: TestClient, slug: str, is_ceo: bool = False, **overrides) -> None:
    body = {"slug": slug, "name": slug.title(), "is_ceo_role": is_ceo, **ROLE_MIN, **overrides}
    response = client.post("/admin/roles", json=body, headers=ADMIN)
    assert response.status_code == 200, response.text


def make_project(
    client: TestClient,
    slug: str = "acme",
    teams: list[dict] | None = None,
    constitution: dict | None = None,
    start: bool = True,
    **project_overrides,
) -> dict:
    """Create chairman + roles + a project with one two-employee team by default."""
    client.post("/admin/users",
                json={"email": "chair@example.com", "display_name": "Chair"},
                headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")
    body = {
        "slug": slug,
        "name": slug.title(),
        "goal": "Prove the framework works end to end.",
        "chairman_email": "chair@example.com",
        "ceo_role_slug": "ceo",
        "ceo_name": "CEO",
        "constitution": constitution or {},
        "teams": teams
        if teams is not None
        else [{"role_slug": "worker", "name": "Alpha", "employees": ["Lead", "Emp2"],
               "lead": "Lead"}],
        **project_overrides,
    }
    response = client.post("/admin/projects", json=body, headers=ADMIN)
    assert response.status_code == 200, response.text
    project = response.json()
    if start:
        started = client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN)
        assert started.status_code == 200, started.text
    return project


def amend(client: TestClient, project_id: str, reason: str = "test amendment", **changes) -> dict:
    """Change constitution rules the way the chairman does — over HTTP, with a
    reason, audited, and disclosed to the CEO. Every limit lives in the
    constitution, so this is the only route a test may move one."""
    response = client.post(
        f"/admin/projects/{project_id}/amendments",
        json={"reason": reason, "changes": changes},
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text
    return response.json()


def agent_by_name(project: dict, name: str) -> str:
    if name == "CEO":
        return project["ceo_agent_id"]
    for team in project["teams"]:
        for agent in team["agents"]:
            if agent["name"] == name:
                return agent["id"]
    raise KeyError(name)


def live_token(project_id: str, agent_id: str, kind: str = "agent_run") -> tuple[str, str]:
    """Mint a real running run + token for an agent, exactly as the runner would."""
    plaintext, token_hash = mint()
    with db_session() as session:
        run = Run(
            id=ids.new_id(ids.RUN),
            project_id=project_id,
            agent_id=agent_id,
            kind=kind,
            trigger={"type": "test"},
            status=RunStatus.RUNNING,
            token_hash=token_hash,
            started_at=utcnow(),
            heartbeat_at=utcnow(),
        )
        session.add(run)
        run_id = run.id
    return run_id, plaintext


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def channel_of(client: TestClient, project_id: str, kind: str, team_id: str | None = None) -> str:
    channels = client.get(f"/observe/projects/{project_id}/channels", headers=ADMIN).json()
    for channel in channels:
        if channel["kind"] == kind and (team_id is None or channel["team_id"] == team_id):
            return channel["id"]
    raise KeyError(kind)


def backdate_project(project_id: str, hours: float) -> None:
    """Make a project look older, so drought grace periods can be escaped."""
    from datetime import timedelta

    with db_session() as session:
        project = session.get(Project, project_id)
        project.created_at = utcnow() - timedelta(hours=hours)


def get_agent(agent_id: str) -> Agent:
    with db_session() as session:
        return session.get(Agent, agent_id)


def finish_runs(project_id: str) -> None:
    """Mark a project's live runs done — the dispatcher treats agents with a
    running run as busy, exactly as in production."""
    with db_session() as session:
        for run in session.query(Run).filter(
            Run.project_id == project_id, Run.status == RunStatus.RUNNING
        ):
            run.status = "done"
            run.finished_at = utcnow()
