"""Invariant selftests: a project that cannot pay for a run does not read as
running.

Starting a project with no billing credential is allowed — it is "not
configured yet", and the suite relies on that — so its status is honestly
`running`. What it can do is nothing: its first run dies on the missing key.
Home, the portfolio and the switcher drew a green "Running" over it while the
Overview, one click away, said "agents cannot run". The rollup now carries the
fact, so every row that shows a status can say it.
"""

from __future__ import annotations

from tests.conftest import ADMIN, make_project


def _row(client, project_id: str) -> dict:
    desk = client.get("/observe/desk", headers=ADMIN).json()
    return next(p for p in desk["projects"] if p["id"] == project_id)


def test_a_running_project_with_nothing_to_pay_says_so(client):
    project = make_project(client)
    row = _row(client, project["id"])
    assert row["status"] == "running"
    assert row["unpaid"] is True


def test_the_mock_runtime_needs_nothing_to_pay(client):
    project = make_project(client, integrations={"runtime": "mock"})
    assert _row(client, project["id"])["unpaid"] is False


def test_a_project_with_a_credential_is_paid_for(client):
    created = client.post("/admin/settings/llm-credentials", headers=ADMIN, json={
        "name": "Production", "value": "test-credential-value", "mode": "api_key"})
    assert created.status_code == 201, created.text
    project = make_project(client, billing_secret_id=created.json()["id"])
    assert _row(client, project["id"])["unpaid"] is False
