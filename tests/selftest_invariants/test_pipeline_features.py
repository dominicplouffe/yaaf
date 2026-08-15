"""Invariant selftests: role-scoped secret injection, the daily wall-clock
wake, and failed-run retries that give triggers back."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from tests.conftest import ADMIN, bearer, live_token, make_project, seed_role
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Heartbeat, Notice, Run, RunQueue, RunStatus, utcnow
from yaaf.runner.daemon import finalize, lease_and_prepare
from yaaf.runner.types import RunJob, RunResult
from yaaf.server.dispatcher import dispatch_once


def test_role_scoped_secrets_reach_the_sandbox_env(client):
    client.post("/admin/users", json={"email": "chair@example.com", "display_name": "Chair"},
                headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "publisher", secret_scopes=["wordpress"], model="claude-sonnet-5")
    body = {
        "slug": "secretproj", "name": "Secretproj", "goal": "test secret injection end to end",
        "chairman_email": "chair@example.com", "ceo_role_slug": "ceo",
        "teams": [{"role_slug": "publisher", "name": "Pub", "employees": ["Poster"]}],
    }
    project = client.post("/admin/projects", json=body, headers=ADMIN).json()
    client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN)
    client.post("/admin/secrets", json={
        "project_id": project["id"], "name": "wordpress",
        "value": "dominic:app-pass-1234", "role_scopes": [],
    }, headers=ADMIN)

    poster = project["teams"][0]["agents"][0]["id"]
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=poster, kind="agent_run",
                             trigger={"type": "wake", "reasons": []}))
    jobs = lease_and_prepare(limit=5)
    job = next(j for j in jobs if j.agent_id == poster)
    assert job.billing_env.get("WORDPRESS") == "dominic:app-pass-1234"
    assert "WORDPRESS" in job.system_prompt  # the agent is told the var exists
    assert "app-pass-1234" not in job.system_prompt  # ...but never the value

    # The CEO's role has no scopes: nothing leaks to it.
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=project["ceo_agent_id"], kind="agent_run",
                             trigger={"type": "wake", "reasons": []}))
    ceo_job = next(j for j in lease_and_prepare(limit=5)
                   if j.agent_id == project["ceo_agent_id"])
    assert "WORDPRESS" not in ceo_job.billing_env


def test_daily_wake_fires_once_per_local_day(client):
    project = make_project(
        client,
        config={"runtime": "mock", "ceo_daily_at": "00:00", "timezone": "UTC",
                "ceo_heartbeat_hours": 9999},
    )
    from tests.conftest import finish_runs

    def drain_queue() -> None:
        """Consume enqueued wakes so 'agent busy' can't mask the once-per-day rule."""
        finish_runs(project["id"])
        with db_session() as session:
            for queued in session.execute(select(RunQueue)).scalars():
                session.delete(queued)

    with db_session() as session:
        first = dispatch_once(session)
    assert first == 1  # midnight UTC has always passed: fires immediately

    # Same day: never twice, even with the agent fully idle.
    drain_queue()
    with db_session() as session:
        again = dispatch_once(session)
    assert again == 0

    # Next day: fires again.
    with db_session() as session:
        beat = session.execute(
            select(Heartbeat).where(Heartbeat.name == "daily")
        ).scalar_one()
        beat.last_fired_at = utcnow() - timedelta(days=1)
    with db_session() as session:
        tomorrow = dispatch_once(session)
    assert tomorrow == 1


def test_failed_run_requeues_and_returns_notices(client):
    project = make_project(client)
    ceo = project["ceo_agent_id"]
    with db_session() as session:
        session.add(Notice(id=ids.new_id(ids.NOTICE), project_id=project["id"],
                           agent_id=ceo, kind="drought", body="test notice"))

    run_id, token = live_token(project["id"], ceo)
    # The run consumes the notice via its context pack...
    pack = client.get("/agent/context", headers=bearer(token)).json()
    assert any(n["body"] == "test notice" for n in pack["notices"])

    # ...then dies without doing anything.
    job = RunJob(run_id=run_id, project_id=project["id"], agent_id=ceo,
                 kind="agent_run", trigger={"type": "wake", "reasons": []},
                 task_id=None, attempt=1)
    finalize(job, RunResult(ok=False, summary="sandbox exploded"))

    with db_session() as session:
        assert session.get(Run, run_id).status == RunStatus.FAILED
        # The notice is unconsumed again.
        notice = session.execute(select(Notice).where(Notice.agent_id == ceo)).scalar_one()
        assert notice.consumed_at is None
        # And a retry with backoff is queued, carrying the same trigger.
        retry = session.execute(
            select(RunQueue).where(RunQueue.prior_run_id == run_id)
        ).scalar_one()
        assert retry.attempt == 2
        assert retry.not_before is not None

    # Attempt 3 failing = give up (no infinite failure loop).
    job3 = RunJob(run_id=run_id, project_id=project["id"], agent_id=ceo,
                  kind="agent_run", trigger={}, task_id=None, attempt=3)
    with db_session() as session:
        run = session.get(Run, run_id)
        run.status = RunStatus.RUNNING  # re-arm for the helper call
        run.attempt = 3
    finalize(job3, RunResult(ok=False, summary="still exploding"))
    with db_session() as session:
        retries = session.execute(
            select(RunQueue).where(RunQueue.prior_run_id == run_id)
        ).scalars().all()
        assert len(retries) == 1  # only the attempt-2 row; no attempt-4
