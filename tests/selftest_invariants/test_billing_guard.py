"""Invariant selftests: billing credential guards — mismatched mode×credential
refused at attach, preflight without burning a run, and dead credentials that
pause the project instead of failing runs silently forever."""

from __future__ import annotations

import httpx

from tests.conftest import ADMIN, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Project
from yaaf.runner import daemon
from yaaf.runner.types import RunJob, RunResult


def _billing_secret(client, project_id, name, value):
    return client.post('/admin/settings/llm-credentials', json={'name': name, 'value': value, 'mode': 'subscription' if value.startswith('sk-ant-oat') else 'api_key'}, headers=ADMIN).json()


def test_mode_credential_mismatch_is_refused(client):
    project = make_project(client, start=False)
    oauth = _billing_secret(client, project["id"], "sub-token", "sk-ant-oat01-fake")
    api = _billing_secret(client, project["id"], "api-key", "sk-ant-api03-fake")

    wrong = client.patch(f"/admin/projects/{project['id']}",
                         json={"billing_mode": "api_key", "billing_secret_id": oauth["id"]},
                         headers=ADMIN)
    assert wrong.status_code == 422 and "OAuth token" in wrong.json()["detail"]

    wrong = client.patch(f"/admin/projects/{project['id']}",
                         json={"billing_mode": "subscription", "billing_secret_id": api["id"]},
                         headers=ADMIN)
    assert wrong.status_code == 422 and "not a subscription token" in wrong.json()["detail"]

    # Correct pairs pass — and switching mode alone re-validates the pair.
    ok = client.patch(f"/admin/projects/{project['id']}",
                      json={"billing_mode": "api_key", "billing_secret_id": api["id"]},
                      headers=ADMIN)
    assert ok.status_code == 200
    assert client.patch(f"/admin/projects/{project['id']}",
                        json={"billing_mode": "subscription"},
                        headers=ADMIN).status_code == 422


def test_billing_check_preflights_without_a_run(client, monkeypatch):
    project = make_project(client, start=False)
    no_secret = client.post(f"/admin/projects/{project['id']}/billing_check", headers=ADMIN)
    assert no_secret.status_code == 422

    api = _billing_secret(client, project["id"], "api-key", "sk-ant-api03-fake")
    client.patch(f"/admin/projects/{project['id']}",
                 json={"billing_mode": "api_key", "billing_secret_id": api["id"]},
                 headers=ADMIN)

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(200))
    good = client.post(f"/admin/projects/{project['id']}/billing_check", headers=ADMIN).json()
    assert good["ok"] is True and "verified" in good["detail"]

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(401))
    bad = client.post(f"/admin/projects/{project['id']}/billing_check", headers=ADMIN).json()
    assert bad["ok"] is False and "rejected" in bad["detail"]

    # Subscription tokens can only be shape-checked here; the check says so.
    oauth = _billing_secret(client, project["id"], "sub-token", "sk-ant-oat01-fake")
    client.patch(f"/admin/projects/{project['id']}",
                 json={"billing_mode": "subscription", "billing_secret_id": oauth["id"]},
                 headers=ADMIN)
    shape = client.post(f"/admin/projects/{project['id']}/billing_check", headers=ADMIN).json()
    assert shape["ok"] is True and "first run" in shape["detail"]


def test_three_credential_failures_pause_the_project(client, monkeypatch):
    project = make_project(client)
    notified: list[str] = []
    monkeypatch.setattr("yaaf.server.notify.chairman_notify",
                        lambda session, proj, subject, body, **kw: notified.append(subject))

    def fail_once():
        run_id, _ = live_token(project["id"], project["ceo_agent_id"])
        job = RunJob(run_id=run_id, project_id=project["id"],
                     agent_id=project["ceo_agent_id"], kind="agent_run",
                     trigger={}, task_id=None, attempt=1)
        daemon.finalize(job, RunResult(ok=False,
                                       summary="Not logged in · Please run /login"))

    fail_once()
    with db_session() as session:
        assert session.get(Project, project["id"]).status == "running"
    assert len(notified) == 1  # a heads-up, throttled to one per day

    fail_once()
    assert len(notified) == 1  # still throttled

    fail_once()
    with db_session() as session:
        row = session.get(Project, project["id"])
        assert row.status == "paused"
        assert "billing credential invalid" in row.status_reason
    assert any("auto-paused" in subject for subject in notified)
