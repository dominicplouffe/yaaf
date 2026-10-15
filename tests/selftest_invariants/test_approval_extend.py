"""Invariant selftests: moving a pending approval's deadline.

The clock on an approval should not run while nobody could see it. A receiver
that lost contact with the chairman extends the deadline by the outage; the nag
re-arms so the new deadline gets its own chase; a decided approval is history and
cannot be reopened by moving its date.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Approval, AuditLog, utcnow


def _filed(client, project: dict) -> str:
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    response = client.post("/agent/approvals", headers=bearer(token),
                           json={"kind": "spend", "title": "Buy a data pull",
                                 "body": "Two months of category data.",
                                 "if_declined": "We forecast from last year's data.",
                                 "amount_usd": 40})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_extending_moves_the_deadline_and_rearms_the_nag(client):
    project = make_project(client)
    approval_id = _filed(client, project)
    with db_session() as session:
        row = session.get(Approval, approval_id)
        row.nag_sent_at = utcnow()
        before = row.expires_at

    response = client.post(f"/admin/approvals/{approval_id}/extend",
                           json={"days": 3, "reason": "chairman unreachable for 2 days"},
                           headers=ADMIN)
    assert response.status_code == 200, response.text
    after = datetime.fromisoformat(response.json()["expires_at"])
    assert after - before >= timedelta(days=3) - timedelta(seconds=5)

    with db_session() as session:
        row = session.get(Approval, approval_id)
        assert row.nag_sent_at is None
        audit = session.query(AuditLog).filter_by(action="approval.extend", entity_id=approval_id).one()
        assert audit.detail["days"] == 3 and "unreachable" in audit.detail["reason"]


def test_an_already_expired_clock_extends_from_now_not_from_the_past(client):
    project = make_project(client)
    approval_id = _filed(client, project)
    with db_session() as session:
        session.get(Approval, approval_id).expires_at = utcnow() - timedelta(days=10)

    response = client.post(f"/admin/approvals/{approval_id}/extend",
                           json={"days": 1, "reason": "outage"}, headers=ADMIN)
    assert response.status_code == 200
    after = datetime.fromisoformat(response.json()["expires_at"])
    assert after > utcnow() + timedelta(hours=23)


def test_a_decided_approval_cannot_be_reopened_by_moving_its_date(client):
    project = make_project(client)
    approval_id = _filed(client, project)
    decided = client.post(f"/admin/approvals/{approval_id}/decide",
                          json={"approve": False, "note": "no"}, headers=ADMIN)
    assert decided.status_code == 200
    response = client.post(f"/admin/approvals/{approval_id}/extend",
                           json={"days": 1, "reason": "too late"}, headers=ADMIN)
    assert response.status_code == 409


def test_the_extension_is_bounded(client):
    project = make_project(client)
    approval_id = _filed(client, project)
    assert client.post(f"/admin/approvals/{approval_id}/extend",
                       json={"days": 31, "reason": "forever"}, headers=ADMIN).status_code == 422
    assert client.post("/admin/approvals/apr_nonesuch/extend",
                       json={"days": 1, "reason": "gone"}, headers=ADMIN).status_code == 404
