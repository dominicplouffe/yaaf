"""Invariant selftests: a finished delivery closes itself, and can be reopened.

A Deliver project whose every requirement was true waited on the chairman's
Complete forever, chased in the boardroom every twelve hours. Completing it
is now automatic `complete_after_hours` after he is told — with the receipt
his own button writes — and safe to automate only because it can be taken
back: Reopen, for REOPEN_DAYS.

Attacked here: the clock (not before it runs out), the off switch (0), the
undo (reopen runs it again, and it does not close straight back), a paused
project, scope that moves, and the reopen window.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN
from tests.selftest_invariants.test_modes import fabricate_met, run_mode_tick
from tests.selftest_invariants.test_requirements import (
    accept,
    ceo_token,
    deliver_project,
    propose,
)
from yaaf.core.db import db_session
from yaaf.core.models import (
    AuditLog,
    Decision,
    Notice,
    NoticeKind,
    Project,
    ProjectStatus,
    RequirementEvent,
    utcnow,
)


def _finished(client, slug: str = "finished", **constitution) -> dict:
    project = deliver_project(client, slug=slug, constitution=constitution or None)
    rid = propose(client, ceo_token(project))["requirement_id"]
    accept(client, rid, project)
    fabricate_met(project["id"], rid)
    run_mode_tick(project)  # announced
    return project


def _age_announcement(project: dict, hours: float) -> None:
    """Move the whole finished episode into the past — the announcement and
    the scope events before it — so it is the clock that has run, not the
    scope that has moved."""
    with db_session() as session:
        for row in session.query(AuditLog).filter_by(
                project_id=project["id"], action="project.completion_ready"):
            row.at = row.at - timedelta(hours=hours)
        for event in session.query(RequirementEvent).filter_by(project_id=project["id"]):
            event.at = event.at - timedelta(hours=hours)


def _status(project: dict) -> str:
    with db_session() as session:
        return session.get(Project, project["id"]).status


def test_it_closes_itself_once_the_window_has_run_and_not_before(client):
    project = _finished(client)
    run_mode_tick(project)
    assert _status(project) == ProjectStatus.RUNNING, "told, not yet closed"

    _age_announcement(project, 25)
    run_mode_tick(project)
    assert _status(project) == ProjectStatus.COMPLETED
    with db_session() as session:
        record = session.query(Decision).filter_by(project_id=project["id"]).one()
        assert record.body.startswith("Completed by the platform")
        assert record.by_user_id is None
        audit = session.query(AuditLog).filter_by(
            project_id=project["id"], action="project.auto_complete").one()
        assert audit.actor == "system:janitor"
        assert audit.detail["met"] == 1


def test_zero_leaves_it_to_his_button(client):
    project = _finished(client, slug="manual", complete_after_hours=0)
    _age_announcement(project, 24 * 30)
    run_mode_tick(project)
    assert _status(project) == ProjectStatus.RUNNING


def test_a_paused_project_is_left_alone(client):
    project = _finished(client, slug="held")
    client.post(f"/admin/projects/{project['id']}/pause",
                json={"reason": "I want to read it first"}, headers=ADMIN)
    _age_announcement(project, 25)
    run_mode_tick(project)
    assert _status(project) == ProjectStatus.PAUSED


def test_reopening_runs_it_again_and_it_does_not_close_straight_back(client):
    project = _finished(client, slug="undo")
    _age_announcement(project, 25)
    run_mode_tick(project)
    assert _status(project) == ProjectStatus.COMPLETED

    reopened = client.post(f"/admin/projects/{project['id']}/reopen", headers=ADMIN)
    assert reopened.status_code == 200, reopened.text
    assert _status(project) == ProjectStatus.RUNNING
    with db_session() as session:
        told = session.query(Notice).filter_by(
            project_id=project["id"], kind=NoticeKind.DELIVERY_REOPENED).one()
        assert "something is still missing" in told.body

    # The same finished scope, a day later: still open, because he said no.
    _age_announcement(project, 48)
    run_mode_tick(project)
    assert _status(project) == ProjectStatus.RUNNING


def test_a_reopen_is_refused_after_the_window_and_on_a_running_project(client):
    project = _finished(client, slug="late")
    assert client.post(f"/admin/projects/{project['id']}/reopen",
                       headers=ADMIN).status_code == 409
    _age_announcement(project, 25)
    run_mode_tick(project)
    with db_session() as session:
        for row in session.query(AuditLog).filter_by(
                project_id=project["id"], action="project.auto_complete"):
            row.at = utcnow() - timedelta(days=15)
    refused = client.post(f"/admin/projects/{project['id']}/reopen", headers=ADMIN)
    assert refused.status_code == 409
    assert "14 days" in refused.json()["detail"]


def test_the_announcement_says_when_it_will_close(client, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr("yaaf.server.janitor.chairman_notify",
                        lambda session, proj, subject, body, **kw: sent.append(body))
    _finished(client, slug="told")
    assert any("it completes itself" in body and "reopen it for 14 days" in body
               for body in sent), sent
