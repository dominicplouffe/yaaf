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


def test_the_roadmap_tells_the_chairman_scope_waits_on_him_in_his_own_words(client):
    """The plan gate's reasons are written to the CEO ("waiting on him, not on
    you") and were shown verbatim on the Roadmap, beside rows badged "waiting
    for you". The chairman's screen says it to him; the CEO's is untouched."""
    from tests.selftest_invariants.test_requirements import (
        ceo_token,
        deliver_project,
        propose,
    )

    project = deliver_project(client, slug="scope")
    propose(client, ceo_token(project))
    plan = client.get(f"/observe/projects/{project['id']}/plan", headers=ADMIN).json()
    gaps = " ".join(plan["plan"]["outstanding"])
    assert "you decide what is in scope" in gaps
    assert "waiting on him" not in gaps

    pack = client.get("/agent/context", headers={
        "Authorization": f"Bearer {ceo_token(project)}"}).json()
    assert "waiting on him, not on you" in str(pack)


def test_the_health_card_reads_a_question_s_deadline_as_a_time_not_a_timestamp(client):
    """It read "the first stopping at 2026-10-25T12:04+00:00". Every other clock
    the chairman is held to is said in his timezone; this one now is too."""
    import re

    from tests.selftest_invariants.test_asks import _ask

    project = make_project(client, constitution={"timezone": "America/Toronto"})
    assert _ask(client, project).status_code == 200
    health = client.get(f"/observe/projects/{project['id']}/health", headers=ADMIN).json()
    text = str(health)
    assert "question(s) from your CEO waiting on you" in text
    assert not re.search(r"stopping at \d{4}-\d{2}-\d{2}T", text)
    assert re.search(r"stopping (?:\w{3} )?\d{1,2} \w{3}|stopping \w{3}", text), text[:600]
