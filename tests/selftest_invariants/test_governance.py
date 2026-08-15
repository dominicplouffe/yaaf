"""Invariant selftests: automatic state changes — expiries, budgets, reaping,
amendments, drought, evidence verification."""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import (
    ADMIN,
    agent_by_name,
    backdate_project,
    bearer,
    live_token,
    make_project,
)
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Evidence,
    Hypothesis,
    LedgerEntry,
    Notice,
    Project,
    Run,
    Task,
    utcnow,
)
from yaaf.server.janitor import janitor_once

CMD_TEST = {"type": "command", "command": "echo everything is fine"}
OBS_TEST = {"type": "observable", "target": "https://example.org/thing exists and loads"}


def test_hypothesis_kill_date_is_automatic(client):
    project = make_project(client)
    team_id = project["teams"][0]["id"]
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])

    hypothesis = client.post(
        "/agent/hypotheses",
        json={"statement": "People will pay for X", "kill_condition": "No signup in 30 days",
              "kill_date": (utcnow() + timedelta(days=1)).isoformat()},
        headers=bearer(ceo_token),
    ).json()
    task = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "bet work", "goal": "work under the bet",
              "success_test": CMD_TEST, "hypothesis_id": hypothesis["id"]},
        headers=bearer(ceo_token),
    ).json()

    # Time passes: the kill date arrives.
    with db_session() as session:
        row = session.get(Hypothesis, hypothesis["id"])
        row.kill_date = utcnow() - timedelta(minutes=1)
    with db_session() as session:
        janitor_once(session)

    with db_session() as session:
        assert session.get(Hypothesis, hypothesis["id"]).status == "expired"
        blocked = session.get(Task, task["id"])
        assert blocked.status == "blocked"
        assert blocked.blocked_reason == "hypothesis_expired"

    # New work under the dead bet is refused.
    refused = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "more bet work", "goal": "necromancy",
              "success_test": CMD_TEST, "hypothesis_id": hypothesis["id"]},
        headers=bearer(ceo_token),
    )
    assert refused.status_code == 422
    assert "expired" in refused.json()["error"]

    # The chairman extends once — tasks unblock.
    extend = client.post(
        f"/admin/hypotheses/{hypothesis['id']}/extend",
        json={"new_kill_date": (utcnow() + timedelta(days=7)).isoformat(),
              "reason": "two qualified replies arrived on the last day"},
        headers=ADMIN,
    )
    assert extend.status_code == 200
    with db_session() as session:
        assert session.get(Task, task["id"]).status == "ready"

    # ... and only once.
    with db_session() as session:
        session.get(Hypothesis, hypothesis["id"]).kill_date = utcnow() - timedelta(minutes=1)
    with db_session() as session:
        janitor_once(session)
    again = client.post(
        f"/admin/hypotheses/{hypothesis['id']}/extend",
        json={"new_kill_date": (utcnow() + timedelta(days=30)).isoformat(),
              "reason": "just a little longer, I promise"},
        headers=ADMIN,
    )
    assert again.status_code == 409


def test_approval_expires_to_deny_and_wakes_requester(client):
    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    approval = client.post(
        "/agent/approvals",
        json={"kind": "spend", "title": "Big spend", "body": "I recommend it strongly.",
              "amount_usd": 500},
        headers=bearer(ceo_token),
    ).json()

    from yaaf.core.models import Approval

    with db_session() as session:
        session.get(Approval, approval["id"]).expires_at = utcnow() - timedelta(minutes=1)
    with db_session() as session:
        janitor_once(session)

    with db_session() as session:
        row = session.get(Approval, approval["id"])
        assert row.status == "expired"
        notices = session.query(Notice).filter(
            Notice.agent_id == project["ceo_agent_id"],
            Notice.kind == "approval_expired",
        ).all()
        assert notices, "requester must be woken with the default-deny"


def test_budget_cap_auto_pauses_and_revokes(client):
    project = make_project(client, constitution={"monthly_usd_cap": 100})
    run_id, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project["id"],
                                kind="external_spend", usd=150.0, note="over the cap"))
    with db_session() as session:
        janitor_once(session)

    with db_session() as session:
        assert session.get(Project, project["id"]).status == "paused"
        assert session.get(Run, run_id).token_revoked is True

    # The revoked token is dead.
    assert client.get("/agent/board", headers=bearer(ceo_token)).status_code == 401


def test_reaper_releases_claims_and_preserves_side_effects(client):
    project = make_project(client)
    team_id = project["teams"][0]["id"]
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    lead_id = agent_by_name(project, "Lead")
    lead_run, lead_token = live_token(project["id"], lead_id)

    task = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "doomed run's task", "goal": "will be reaped",
              "success_test": OBS_TEST},
        headers=bearer(ceo_token),
    ).json()
    client.post(f"/agent/tasks/{task['id']}/claim", headers=bearer(lead_token))
    effect = client.post(
        "/agent/side_effects",
        json={"kind": "email", "idempotency_key": "reap-test:bob@example.com",
              "task_id": task["id"], "payload": {"to": "bob@example.com"}},
        headers=bearer(lead_token),
    ).json()
    client.post(f"/agent/side_effects/{effect['id']}/finish",
                json={"ok": True, "result": {"sent": True}}, headers=bearer(lead_token))

    # The run dies silently: stale heartbeat.
    with db_session() as session:
        run = session.get(Run, lead_run)
        run.heartbeat_at = utcnow() - timedelta(hours=1)
    with db_session() as session:
        janitor_once(session)

    with db_session() as session:
        assert session.get(Run, lead_run).status == "reaped"
        released = session.get(Task, task["id"])
        assert released.status == "ready"
        assert released.claimed_by_agent_id is None
        reap_notices = session.query(Notice).filter(
            Notice.agent_id == lead_id, Notice.kind == "agent_run_reaped"
        ).all()
        assert reap_notices

    # The retry sees the completed side effect in its context pack.
    retry_run, retry_token = live_token(project["id"], lead_id)
    client.post(f"/agent/tasks/{task['id']}/claim", headers=bearer(retry_token))
    pack = client.get("/agent/context", headers=bearer(retry_token)).json()
    fired = pack["task"]["side_effects_already_fired"]
    assert any(e["idempotency_key"] == "reap-test:bob@example.com"
               and e["status"] == "completed" for e in fired)


def test_drought_refuses_internal_tasks(client):
    project = make_project(client, constitution={"drought_hours": 1,
                                                 "external_ratio_min": 0.0})
    backdate_project(project["id"], hours=48)  # escape the grace period
    team_id = project["teams"][0]["id"]
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])

    internal = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "plumbing during drought",
              "goal": "internal work while starving", "success_test": CMD_TEST,
              "produces_external_evidence": False},
        headers=bearer(ceo_token),
    )
    assert internal.status_code == 409
    assert "drought" in internal.json()["error"].lower()

    external = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "reach outside", "goal": "external work",
              "success_test": OBS_TEST, "produces_external_evidence": True},
        headers=bearer(ceo_token),
    )
    assert external.status_code == 200

    # Verified external evidence breaks the drought.
    with db_session() as session:
        session.add(Evidence(
            id=ids.new_id(ids.EVIDENCE), project_id=project["id"], kind="external",
            provenance="platform_attested", verification="not_applicable",
            description="a stranger replied",
        ))
    ok_again = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "plumbing after rain",
              "goal": "internal work is legal again", "success_test": CMD_TEST},
        headers=bearer(ceo_token),
    )
    assert ok_again.status_code == 200


def test_amendment_cooling_off(client):
    from yaaf.core.constitution import effective
    from yaaf.core.models import Amendment

    project = make_project(client, constitution={"queue_ceiling": 5})
    amendment = client.post(
        f"/admin/projects/{project['id']}/amendments",
        json={"reason": "the team is drowning, raise the ceiling",
              "changes": {"queue_ceiling": 12}},
        headers=ADMIN,
    ).json()["amendments"][0]

    # Not in force during the cooling-off.
    with db_session() as session:
        janitor_once(session)
        assert effective(session, project["id"]).queue_ceiling == 5

    # Time passes.
    with db_session() as session:
        session.get(Amendment, amendment["id"]).effective_at = utcnow() - timedelta(minutes=1)
    with db_session() as session:
        janitor_once(session)
    with db_session() as session:
        assert effective(session, project["id"]).queue_ceiling == 12
        disclosure = session.query(Notice).filter(
            Notice.project_id == project["id"],
            Notice.kind == "constitution_amended",
        ).all()
        assert disclosure, "the CEO must be told the rules changed"

    # An invalid amendment is refused up front.
    bad = client.post(
        f"/admin/projects/{project['id']}/amendments",
        json={"reason": "break the schema", "changes": {"queue_ceiling": -1}},
        headers=ADMIN,
    )
    assert bad.status_code == 422


def test_budget_amendments_apply_immediately(client):
    """Chairman's ruling (2026-08-15): budgets are business reality — a
    customer changes their mind, a mistake needs correcting NOW. Budget fields
    skip the cooling-off entirely; behavioral guardrails never do. Both halves
    are still recorded as amendments and disclosed to the CEO."""
    from yaaf.core.constitution import effective

    project = make_project(client)
    response = client.post(
        f"/admin/projects/{project['id']}/amendments",
        json={"reason": "customer doubled the retainer for Q4",
              "changes": {"monthly_usd_cap": 2500.0, "queue_ceiling": 12}},
        headers=ADMIN,
    ).json()
    assert {a["applied_now"] for a in response["amendments"]} == {True, False}
    assert "NOW" in response["note"]

    # The budget is in force in the very same request — no janitor involved;
    # the guardrail half of the same save still waits out its cooling-off.
    with db_session() as session:
        cons = effective(session, project["id"])
        assert cons.monthly_usd_cap == 2500.0
        assert cons.queue_ceiling != 12

    # Disclosure of the immediate half is immediate too.
    with db_session() as session:
        notices = session.query(Notice).filter(
            Notice.project_id == project["id"],
            Notice.kind == "constitution_amended",
        ).all()
        assert len(notices) == 1 and "monthly_usd_cap" in notices[0].body

    listed = client.get(f"/admin/projects/{project['id']}/amendments", headers=ADMIN).json()
    assert sorted(a["status"] for a in listed) == ["applied", "pending"]


def test_evidence_verifier_rules(client):
    from yaaf.server.verifier import verify_pending_evidence

    project = make_project(client, controlled_domains=["ourproduct.example"])
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])

    own_domain = client.post(
        "/agent/evidence",
        json={"kind": "external", "description": "traffic on our own site",
              "source_url": "https://blog.ourproduct.example/post"},
        headers=bearer(ceo_token),
    ).json()
    stranger = client.post(
        "/agent/evidence",
        json={"kind": "external", "description": "a stranger reviewed us",
              "source_url": "https://independent.example.org/review"},
        headers=bearer(ceo_token),
    ).json()

    def fake_fetch(url: str) -> tuple[int, bytes]:
        return 200, b"<html>a real page about the project</html>"

    with db_session() as session:
        verify_pending_evidence(session, fetch=fake_fetch)

    with db_session() as session:
        assert session.get(Evidence, own_domain["id"]).verification == "failed"
        verified = session.get(Evidence, stranger["id"])
        assert verified.verification == "verified"
        assert verified.snapshot_sha256


def test_agents_cannot_reach_admin_or_observe(client):
    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    assert client.get("/observe/projects", headers=bearer(ceo_token)).status_code == 401
    assert client.post(f"/admin/projects/{project['id']}/start",
                       headers=bearer(ceo_token)).status_code == 401
