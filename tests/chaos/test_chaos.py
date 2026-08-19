"""Chaos suite (M5): the platform under worker death, lease races, connection
loss, and mid-queue lifecycle changes.

Every scenario drives the real machinery — `lease_and_prepare` exactly as
`runner_loop` calls it, the janitor exactly as its loop calls it — and asserts
the invariants the plan promises: one lease per job, reaping without erasing
side effects, no zombie finalize, loops that survive a dropped database.
"""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.db import db_session, engine
from yaaf.core.models import LedgerEntry, Run, RunKind, RunQueue, Task, utcnow
from yaaf.runner.daemon import finalize, lease_and_prepare
from yaaf.runner.types import RunResult
from yaaf.server.janitor import janitor_once

OBS_TEST = {"type": "observable", "target": "https://example.org/thing exists and loads"}


def enqueue(
    project_id: str,
    agent_id: str,
    task_id: str | None = None,
    attempt: int = 1,
    prior_run_id: str | None = None,
) -> str:
    """Insert a run_queue row exactly as the dispatcher does (dispatcher.py)."""
    reasons = [{"kind": "team_task_ready", "task_id": task_id}] if task_id else []
    with db_session() as session:
        row = RunQueue(
            id=ids.new_id(ids.RUN),
            project_id=project_id,
            agent_id=agent_id,
            kind=RunKind.AGENT_RUN,
            trigger={"type": "wake", "reasons": reasons},
            task_id=task_id,
            attempt=attempt,
            prior_run_id=prior_run_id,
        )
        session.add(row)
        queue_id = row.id
    return queue_id


def _race_two_workers() -> list:
    """Call lease_and_prepare from two threads at once — separate sessions, the
    way two runner daemons would — and return every job leased."""
    barrier = threading.Barrier(2)
    results: list[list | None] = [None, None]
    errors: list[Exception] = []

    def worker(slot: int) -> None:
        try:
            barrier.wait(timeout=10)
            results[slot] = lease_and_prepare(limit=1)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(slot,)) for slot in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, f"a racing worker crashed: {errors!r}"
    assert all(result is not None for result in results), "a racing worker never returned"
    return [job for result in results for job in (result or [])]


def test_two_workers_race_one_lease(client):
    if engine().dialect.name != "postgresql":
        pytest.skip(
            "FOR UPDATE SKIP LOCKED needs Postgres; SQLite ignores row locks and "
            "serializes writers, so the lease race cannot be exercised there"
        )
    project = make_project(client, slug="race", integrations={"runtime": "mock"})

    # Several rounds so the threads genuinely overlap inside the transaction at
    # least once; the invariant must hold every time regardless of interleaving.
    for round_no in range(5):
        queue_id = enqueue(project["id"], project["ceo_agent_id"])
        won = _race_two_workers()
        assert len(won) == 1, (
            f"round {round_no}: exactly one worker must lease the job, got {len(won)} "
            f"(a double lease means SKIP LOCKED is broken; zero means the job was lost)"
        )
        with db_session() as session:
            run = session.get(Run, queue_id)
            assert run is not None and run.status == "running"
            assert run.id == won[0].run_id
            leftovers = session.query(RunQueue).count()
            assert leftovers == 0, "the claimed queue row must be gone"


def test_worker_death_reap_then_retry_sees_side_effects(client):
    project = make_project(client, slug="death", integrations={"runtime": "mock"})
    team_id = project["teams"][0]["id"]
    lead_id = agent_by_name(project, "Lead")

    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    task = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "doomed worker's task",
              "goal": "will die mid-run", "success_test": OBS_TEST},
        headers=bearer(ceo_token),
    ).json()
    assert "id" in task, task

    # A real lease, exactly as runner_loop takes it.
    enqueue(project["id"], lead_id, task_id=task["id"])
    jobs = lease_and_prepare(limit=10)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.api_token, "an agent_run lease must carry a minted run token"

    # The worker claims the task and fires an external side effect...
    claim = client.post(f"/agent/tasks/{task['id']}/claim", headers=bearer(job.api_token))
    assert claim.status_code == 200, claim.text
    effect = client.post(
        "/agent/side_effects",
        json={"kind": "email", "idempotency_key": "chaos-death:bob@example.com",
              "task_id": task["id"], "payload": {"to": "bob@example.com"}},
        headers=bearer(job.api_token),
    ).json()
    finished = client.post(
        f"/agent/side_effects/{effect['id']}/finish",
        json={"ok": True, "result": {"sent": True}},
        headers=bearer(job.api_token),
    )
    assert finished.status_code == 200, finished.text

    # ...then the process dies. No finalize ever runs; the heartbeat goes stale.
    with db_session() as session:
        run = session.get(Run, job.run_id)
        run.heartbeat_at = utcnow() - timedelta(seconds=settings().lease_ttl_s + 30)

    with db_session() as session:
        janitor_once(session)

    with db_session() as session:
        assert session.get(Run, job.run_id).status == "reaped"
        released = session.get(Task, task["id"])
        assert released.status == "ready", "the dead run's claim must be released"
        assert released.claimed_by_agent_id is None
        assert released.claim_run_id is None
    # The dead run's token is dead with it.
    assert client.get("/agent/board", headers=bearer(job.api_token)).status_code == 401

    # The retry comes through the same front door — and its context pack
    # carries the predecessor's side-effects ledger and the reap notice.
    enqueue(project["id"], lead_id, task_id=task["id"], attempt=2, prior_run_id=job.run_id)
    retry_jobs = lease_and_prepare(limit=10)
    assert len(retry_jobs) == 1
    pack = retry_jobs[0].context_pack
    fired = pack["task"]["side_effects_already_fired"]
    assert any(
        e["idempotency_key"] == "chaos-death:bob@example.com" and e["status"] == "completed"
        for e in fired
    ), fired
    assert any(n["kind"] == "agent_run_reaped" for n in pack["notices"]), pack["notices"]


def test_reaped_run_cannot_finalize(client):
    project = make_project(client, slug="zombie", integrations={"runtime": "mock"})
    enqueue(project["id"], project["ceo_agent_id"])
    jobs = lease_and_prepare(limit=10)
    assert len(jobs) == 1
    job = jobs[0]

    with db_session() as session:
        run = session.get(Run, job.run_id)
        run.heartbeat_at = utcnow() - timedelta(seconds=settings().lease_ttl_s + 30)
    with db_session() as session:
        janitor_once(session)

    with db_session() as session:
        run = session.get(Run, job.run_id)
        assert run.status == "reaped"
        reaped_at = run.finished_at
        assert (
            session.query(LedgerEntry).filter(LedgerEntry.run_id == job.run_id).count() == 0
        )

    # The worker was only slow, not dead — it comes back and reports success.
    # The janitor already won; finalize must be a no-op (daemon.finalize returns
    # early for any non-running run).
    finalize(job, RunResult(ok=True, summary="zombie worker reporting success",
                            tokens_in=12345, tokens_out=678, cost_usd=9.99))

    with db_session() as session:
        run = session.get(Run, job.run_id)
        assert run.status == "reaped", "finalize must not overwrite a reaped status"
        assert run.finished_at == reaped_at
        assert not run.summary, "the zombie's summary must not land"
        assert not run.tokens_in and not run.tokens_out and not run.cost_usd
        booked = session.query(LedgerEntry).filter(LedgerEntry.run_id == job.run_id).count()
        assert booked == 0, "a reaped run's late finalize must not book the ledger"


def test_db_connection_drop_recovers(client):
    project = make_project(client, slug="dropdb", integrations={"runtime": "mock"})

    with db_session() as session:
        janitor_once(session)  # a healthy tick first

    # Every pooled connection dies between ticks (Postgres restart, failover).
    engine().dispose()

    # The next tick opens a fresh session and just works.
    with db_session() as session:
        janitor_once(session)

    # So does the runner's lease loop, and the API.
    enqueue(project["id"], project["ceo_agent_id"])
    engine().dispose()
    jobs = lease_and_prepare(limit=10)
    assert len(jobs) == 1
    with db_session() as session:
        assert session.get(Run, jobs[0].run_id).status == "running"
        assert session.query(RunQueue).count() == 0
    runs = client.get(f"/observe/projects/{project['id']}/runs", headers=ADMIN)
    assert runs.status_code == 200, runs.text


def test_project_pause_mid_queue_drops_jobs(client):
    project = make_project(client, slug="pausey", integrations={"runtime": "mock"})
    lead_id = agent_by_name(project, "Lead")
    queued_ceo = enqueue(project["id"], project["ceo_agent_id"])
    queued_lead = enqueue(project["id"], lead_id)

    paused = client.post(f"/admin/projects/{project['id']}/pause",
                         json={"reason": "chairman pulled the cord"}, headers=ADMIN)
    assert paused.status_code == 200, paused.text

    # daemon.py:88 — a queued job for a non-running project is dropped at lease
    # time, silently: no run row, no execution, no requeue.
    jobs = lease_and_prepare(limit=10)
    assert jobs == []
    with db_session() as session:
        assert session.query(RunQueue).count() == 0, "paused project's queue must drain"
        assert session.get(Run, queued_ceo) is None, "dropped jobs must never materialize"
        assert session.get(Run, queued_lead) is None
        running = (
            session.query(Run)
            .filter(Run.project_id == project["id"], Run.status == "running")
            .count()
        )
        assert running == 0
