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
# The other half of the line above, and it has to be said out loud. A success
# test can be a command an *agent* wrote, so the platform refuses to run one on
# a host holding the Docker socket unless somebody states they meant it — right
# on a deployed runner, where that socket is root on the box; wrong here, where
# the agent is a fixture and the host is a throwaway. Saying only the first
# half is what made the suite pass on a laptop without Docker and fail on one
# with it: same commit, same command, and nothing on screen to say why.
os.environ.setdefault("YAAF_ALLOW_HOST_SUCCESS_TESTS", "1")
os.environ.setdefault("YAAF_ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("YAAF_MASTER_KEY", "kkP1p6Rt0DZ2rWv1w5Zq3o8y6bT4uY9cQ1eS7gJ0aXk=")
os.environ.setdefault("YAAF_WORKSPACES_ROOT", tempfile.mkdtemp(prefix="yaaf-test-ws-"))
os.environ.setdefault("YAAF_ARTIFACTS_ROOT", tempfile.mkdtemp(prefix="yaaf-test-art-"))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.engine import Engine, make_url

from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.constitution import AUTONOMY_PRESETS
from yaaf.core.db import db_session, engine, reset_for_tests
from yaaf.core.models import Agent, Base, Project, Run, RunStatus, utcnow
from yaaf.core.tokens import mint

settings.cache_clear()

ADMIN = {"Authorization": "Bearer test-admin-token"}

ROLE_MIN = {
    "description": "test role",
    "system_prompt": "You are a test agent.",
    "model": "claude-sonnet-5-5",
}


@pytest.fixture(scope="session")
def _database() -> str:
    """Build the schema ONCE per worker, not once per test.

    The suite is 760-odd integration tests and each one used to drop and
    rebuild 47 tables before it ran — a third of the wall clock on Postgres
    spent on DDL that never varies. The schema is a session cost; only the
    *rows* are per-test, and `client` empties those instead.

    SQLite by default; set YAAF_TEST_DATABASE_URL to run the same suite
    against real Postgres (exercises SKIP LOCKED, JSONB, NOTIFY). Under
    pytest-xdist each worker gets a database of its own — the workers share a
    server but must never share rows.
    """
    override = os.environ.get("YAAF_TEST_DATABASE_URL")
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    if override:
        url = _worker_database(override, worker) if worker else override
        reset_for_tests(url)
        Base.metadata.drop_all(engine())
    else:
        directory = tempfile.mkdtemp(prefix=f"yaaf-test-db-{worker or 'main'}-",
                                     dir=_fastest_scratch_dir())
        url = f"sqlite:///{directory}/yaaf-test.db"
        reset_for_tests(url)
    Base.metadata.create_all(engine())
    return url


def _fastest_scratch_dir() -> str | None:
    """RAM if the kernel is offering some, otherwise wherever tempfile goes.

    Returning None means "let tempfile decide", which is the correct answer
    everywhere /dev/shm is missing or unwritable — a container with no shm
    mount, a mac, a CI image that shrank it to nothing.
    """
    shm = "/dev/shm"
    return shm if os.path.isdir(shm) and os.access(shm, os.W_OK) else None


@event.listens_for(Engine, "connect")
def _unsafe_but_fast_sqlite(dbapi_connection, connection_record):  # noqa: ANN001
    """Trade SQLite's durability for speed — in the test harness only.

    Every test writes dozens of rows and SQLite fsyncs on each commit by
    default. A test database that does not survive a power cut is not a
    problem: it does not survive the end of the test either.

    This lives in the test harness and never in `yaaf.core.db`, so a
    deployment cannot inherit it. It is keyed on the dialect rather than on
    the URL because the fire drill swaps in throwaway SQLite files of its own,
    and those deserve the same treatment.
    """
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=MEMORY")
        cursor.execute("PRAGMA synchronous=OFF")
        cursor.close()


def _worker_database(base_url: str, worker: str) -> str:
    """Give an xdist worker its own database on the shared Postgres server."""
    import psycopg

    parsed = make_url(base_url)
    name = f"{parsed.database}_{worker}"
    # psycopg directly rather than an AUTOCOMMIT engine: CREATE DATABASE cannot
    # run inside a transaction, and the `SET TIME ZONE` connect listener in
    # yaaf.core.db opens one before SQLAlchemy can turn autocommit on.
    dsn = parsed.render_as_string(hide_password=False).replace("postgresql+psycopg://",
                                                              "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        connection.execute(f'CREATE DATABASE "{name}"')
    return parsed.set(database=name).render_as_string(hide_password=False)


def _empty_every_table() -> None:
    """Return the database to the state a test expects to find it in.

    Deleting rows is what a test actually needs, and it is an order of
    magnitude cheaper than rebuilding the schema. FKs are off on SQLite and
    CASCADE handles them on Postgres, so neither needs a topological order.
    """
    tables = list(Base.metadata.sorted_tables)
    with db_session() as session:
        if session.get_bind().dialect.name == "postgresql":
            names = ", ".join(f'"{table.name}"' for table in tables)
            session.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
        else:
            for table in reversed(tables):
                session.execute(table.delete())


@pytest.fixture(scope="session")
def _app(_database):
    """Build the app ONCE per worker. Measured at 27.7ms a time, which across
    the suite is twenty-odd seconds spent registering the same routes onto a
    fresh FastAPI object over and over.

    Sharing it is only sound because the app holds no request state: every
    handler reads `settings()` and opens its session when the request arrives,
    so the `public` fixture's monkeypatch still lands on a long-lived app. The
    one thing read at build time is the log line about an unroutable
    YAAF_PUBLIC_BASE_URL, which decides nothing.
    """
    from yaaf.server.app import create_app

    return create_app()


@pytest.fixture()
def client(_app):
    _empty_every_table()
    with TestClient(_app) as test_client:
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
    """Create chairman + roles + a project with one two-employee team by default.

    **Supervised unless the test says otherwise.** A project created with no
    constitution is autonomous in the product (`constitution.DEFAULT_AUTONOMY`),
    but most of this suite exists to assert what the chairman-held verbs do —
    scope waiting on a click, a verdict parking on the desk — and rounding all
    of that to the new default would change what several hundred tests mean
    rather than what they check. Tests about the dial itself pass the rules
    they want; `test_authority_dial.py` covers both ends and the default.
    """
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
        "constitution": {**AUTONOMY_PRESETS["supervised"], **(constitution or {})},
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


@pytest.fixture()
def public(monkeypatch):
    """Give the install a browser-facing origin, as a real deployment must.

    Every OAuth token is audience-bound to it, so a suite that skipped this
    would be testing a different server than the one anybody deploys.
    """
    monkeypatch.setenv("YAAF_PUBLIC_BASE_URL", "http://testserver")
    settings.cache_clear()
    yield "http://testserver"
    monkeypatch.delenv("YAAF_PUBLIC_BASE_URL", raising=False)
    settings.cache_clear()
