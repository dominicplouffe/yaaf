"""Invariant selftests: the M3 completions — amendment cancellation, approval
nags and timeout decisions, completed-task spot-audit, the CEO judgment metric."""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Amendment,
    Approval,
    Evidence,
    Notice,
    Project,
    Task,
    utcnow,
)
from yaaf.server.janitor import janitor_once

OBS_TEST = {"type": "observable", "target": "https://example.org/thing exists and loads"}


def test_a_still_pending_amendment_can_be_cancelled(client):
    """Amendments now land the instant they are saved, so nothing new is ever
    pending. Rows written while the old cooling-off existed can still be sitting
    with a future effective_at, and they must stay both applicable and
    cancellable rather than becoming stranded by the change."""
    project = make_project(client)
    # A pre-removal amendment, exactly as one would have been written: dated
    # into the future, waiting for the janitor.
    legacy = ids.new_id(ids.AMENDMENT)
    with db_session() as session:
        chairman = session.get(Project, project["id"]).chairman_user_id
        session.add(Amendment(
            id=legacy, project_id=project["id"], proposed_by_user_id=chairman,
            reason="loosen the queue for the launch push", changes={"queue_ceiling": 20},
            effective_at=utcnow() + timedelta(hours=72),
        ))

    listed = client.get(f"/admin/projects/{project['id']}/amendments", headers=ADMIN).json()
    assert [a["status"] for a in listed] == ["pending"]

    cancelled = client.post(f"/admin/amendments/{legacy}/cancel", headers=ADMIN)
    assert cancelled.status_code == 200

    # Even once its date arrives, a cancelled amendment never applies.
    with db_session() as session:
        session.get(Amendment, legacy).effective_at = utcnow() - timedelta(minutes=1)
    with db_session() as session:
        janitor_once(session)
    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert dash["constitution"]["queue_ceiling"] != 20
    listed = client.get(f"/admin/projects/{project['id']}/amendments", headers=ADMIN).json()
    assert listed[0]["status"] == "cancelled"

    # Cancelling twice — or cancelling an applied amendment — is refused.
    assert client.post(f"/admin/amendments/{legacy}/cancel", headers=ADMIN).status_code == 409


def test_a_pending_legacy_amendment_still_lands(client):
    """The other half of the same promise: not cancelled, so the janitor applies
    it on its original schedule instead of leaving it in limbo forever."""
    project = make_project(client)
    with db_session() as session:
        chairman = session.get(Project, project["id"]).chairman_user_id
        session.add(Amendment(
            id=ids.new_id(ids.AMENDMENT), project_id=project["id"],
            proposed_by_user_id=chairman, reason="written before the delay was removed",
            changes={"queue_ceiling": 20}, effective_at=utcnow() - timedelta(minutes=1),
        ))
    with db_session() as session:
        janitor_once(session)
    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert dash["constitution"]["queue_ceiling"] == 20


def test_approval_nag_fires_once_before_expiry(client):
    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    approval = client.post(
        "/agent/approvals",
        json={"kind": "spend", "title": "Conference booth", "body": "Worth it for the leads.",
              "if_declined": "We find leads online instead.", "amount_usd": 400},
        headers=bearer(ceo_token),
    ).json()

    # Default expiry is days out — no nag yet.
    with db_session() as session:
        janitor_once(session)
    with db_session() as session:
        assert session.get(Approval, approval["id"]).nag_sent_at is None

    # Inside the nag window: nagged, exactly once.
    with db_session() as session:
        session.get(Approval, approval["id"]).expires_at = utcnow() + timedelta(hours=12)
    with db_session() as session:
        janitor_once(session)
    with db_session() as session:
        first = session.get(Approval, approval["id"]).nag_sent_at
        assert first is not None
    with db_session() as session:
        janitor_once(session)
    with db_session() as session:
        assert session.get(Approval, approval["id"]).nag_sent_at == first


def test_approval_timeout_honors_constitution_decision(client):
    project = make_project(client, constitution={"approval_timeout_decision": "approve"})
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    approval = client.post(
        "/agent/approvals",
        json={"kind": "spend", "title": "Small spend", "body": "Routine renewal, low risk.",
              "if_declined": "The licence lapses.", "amount_usd": 300},
        headers=bearer(ceo_token),
    ).json()
    with db_session() as session:
        session.get(Approval, approval["id"]).expires_at = utcnow() - timedelta(minutes=1)
    with db_session() as session:
        janitor_once(session)
    with db_session() as session:
        row = session.get(Approval, approval["id"])
        assert row.status == "approved"
        assert "auto-APPROVES" in row.decision_note


def test_completed_task_audit_marks_and_wakes_ceo(client):
    project = make_project(client)
    team_id = project["teams"][0]["id"]
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    lead_id = agent_by_name(project, "Lead")
    _, lead_token = live_token(project["id"], lead_id)

    task = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "ship the widget", "goal": "make it real",
              "success_test": OBS_TEST},
        headers=bearer(ceo_token),
    ).json()

    # Auditing an unfinished task is refused — there is nothing to audit yet.
    early = client.post(f"/admin/tasks/{task['id']}/audit", json={"ok": False}, headers=ADMIN)
    assert early.status_code == 409

    assert client.post(f"/agent/tasks/{task['id']}/claim",
                       headers=bearer(lead_token)).status_code == 200
    done = client.post(
        f"/agent/tasks/{task['id']}/complete",
        json={"evidence": "https://example.org/thing is live and loads"},
        headers=bearer(lead_token),
    )
    assert done.status_code == 200, done.text

    failed = client.post(
        f"/admin/tasks/{task['id']}/audit",
        json={"ok": False, "note": "the target 404s"},
        headers=ADMIN,
    )
    assert failed.status_code == 200
    with db_session() as session:
        row = session.get(Task, task["id"])
        assert row.audit_status == "failed"
        assert row.audit_note == "the target 404s"
        notices = session.query(Notice).filter(
            Notice.agent_id == project["ceo_agent_id"], Notice.kind == "audit_failed"
        ).all()
        assert notices, "a failed completion audit must reach the CEO"

    # The spot-audit sample carries the verdict.
    audit = client.get(f"/observe/projects/{project['id']}/spot_audit", headers=ADMIN).json()
    assert any(t["audit_status"] == "failed" for t in audit["completed_tasks"])


def test_ceo_judgment_metric_counts_subtask_evidence(client):
    project = make_project(client)
    team_id = project["teams"][0]["id"]
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    lead_id = agent_by_name(project, "Lead")

    tasks = [
        client.post(
            "/agent/tasks",
            json={"team_id": team_id, "title": f"bet {i}", "goal": "produce signal",
                  "success_test": OBS_TEST, "produces_external_evidence": True},
            headers=bearer(ceo_token),
        ).json()
        for i in range(2)
    ]

    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert dash["ceo_judgment"] == {"tasks_created": 2, "tasks_with_verified_external": 0,
                                    "conversion": 0.0}

    # Verified external evidence on a *subtask* credits the CEO's top-level bet.
    with db_session() as session:
        session.add(Task(
            id=ids.new_id(ids.TASK), project_id=project["id"], team_id=team_id,
            parent_task_id=tasks[0]["id"], created_by_agent_id=lead_id,
            assignee_agent_id=lead_id, title="sub", goal="do part of bet 0",
            success_test=OBS_TEST, status="done",
        ))
        sub_id = session.query(Task).filter(Task.parent_task_id == tasks[0]["id"]).one().id
        session.add(Evidence(
            id=ids.new_id(ids.EVIDENCE), project_id=project["id"], task_id=sub_id,
            created_by_agent_id=lead_id, kind="external", provenance="agent_claimed",
            description="an unaffiliated user filed an issue", verification="verified",
        ))

    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert dash["ceo_judgment"] == {"tasks_created": 2, "tasks_with_verified_external": 1,
                                    "conversion": 0.5}
