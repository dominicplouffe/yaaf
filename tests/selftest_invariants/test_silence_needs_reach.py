"""Invariant selftests: silence decides only for somebody who was told.

docs/ux-redesign/decide-anywhere/REQUIREMENTS.md, A4 and A5. A desktop install
with no mail configured only logged its notices, and a laptop that slept
through a clock expired it on waking; an approval could approve itself, on a
project whose rules said so, without anyone ever having seen it. The rules
under attack:

1. An unreached approval or question at its deadline is extended once, by a
   day, with an alarm in the boardroom and a notice to the agent that filed it.
2. Still unreached at the extended deadline, it settles the safe way: an
   approval is declined whatever the project's timeout rule says; a question
   takes the CEO's stated default.
3. A reached one expires exactly as it always did (the rest of the suite).
4. A finished delivery closes itself only if its sign-off reached somebody.
5. Time the box was down moves the clocks; the first sweep and a short gap
   move nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import amend, make_project
from tests.selftest_invariants.test_approval_notices import _file
from tests.selftest_invariants.test_asks import _ask, _project
from yaaf.core.db import db_session
from yaaf.core.models import (
    Approval,
    Ask,
    AuditLog,
    Message,
    Notice,
    PlatformSetting,
    ReachReceipt,
    utcnow,
)
from yaaf.server import janitor, needs_you


def _due(approval_id: str) -> None:
    with db_session() as session:
        session.get(Approval, approval_id).expires_at = utcnow() - timedelta(minutes=1)


def _tick() -> None:
    with db_session() as session:
        janitor.janitor_once(session)


def _approval(approval_id: str) -> Approval:
    with db_session() as session:
        row = session.get(Approval, approval_id)
        session.expunge(row)
        return row


def test_an_unreached_approval_waits_a_day_and_says_why(client):
    project = make_project(client, slug="reach-extend")
    filed = _file(client, project)
    _due(filed["id"])
    _tick()

    row = _approval(filed["id"])
    assert row.status == "pending"
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    assert expires > utcnow() + timedelta(hours=23)
    with db_session() as session:
        assert session.query(ReachReceipt).filter_by(item_key=filed["id"],
                                                     kind="extended").count() == 1
        assert session.query(AuditLog).filter_by(action="needs_you.extended").count() == 1
        lines = [m.body for m in session.query(Message).filter(
            Message.project_id == project["id"], Message.sender_user_id.is_(None))]
        assert any("could not reach you" in body for body in lines)
        (notice,) = session.query(Notice).filter_by(
            agent_id=project["ceo_agent_id"], kind="decision_extended").all()
        assert "Buy the $199 projections feed" in notice.body


def test_it_is_extended_once_and_then_declined_even_where_silence_approves(client):
    project = make_project(client, slug="reach-decline")
    amend(client, project["id"], approval_timeout_decision="approve")
    filed = _file(client, project)
    _due(filed["id"])
    _tick()
    assert _approval(filed["id"]).status == "pending"
    _due(filed["id"])
    _tick()

    row = _approval(filed["id"])
    assert row.status == "expired"  # declined — never approved on a silence nobody heard
    assert "never reached" in row.decision_note
    with db_session() as session:
        assert session.query(ReachReceipt).filter_by(item_key=filed["id"],
                                                     kind="extended").count() == 1
        assert session.query(AuditLog).filter_by(action="needs_you.unreached").count() == 1


def test_seeing_it_inside_the_extension_lets_silence_count_again(client):
    project = make_project(client, slug="reach-seen-late")
    amend(client, project["id"], approval_timeout_decision="approve")
    filed = _file(client, project)
    _due(filed["id"])
    _tick()  # extended a day
    # He opens his Inbox with the day ahead of him, and does nothing.
    assert client.get("/observe/needs-you", headers={
        "Authorization": "Bearer ypt_test-chair-personal-token"}).status_code == 200
    _due(filed["id"])
    with db_session() as session:
        for receipt in session.query(ReachReceipt).filter_by(kind="seen"):
            receipt.at = utcnow() - timedelta(hours=3)
    _tick()
    assert _approval(filed["id"]).status == "approved"  # his rule, now that he knew


def test_an_unreached_question_waits_a_day_then_takes_its_default(client):
    project = _project(client, slug="reach-ask")
    asked = _ask(client, project).json()

    def due() -> None:
        with db_session() as session:
            session.get(Ask, asked["ask_id"]).answer_by = utcnow() - timedelta(minutes=1)

    due()
    _tick()
    with db_session() as session:
        assert session.get(Ask, asked["ask_id"]).status == "open"
    due()
    _tick()
    with db_session() as session:
        assert session.get(Ask, asked["ask_id"]).status == "lapsed"
        (notice,) = session.query(Notice).filter_by(
            agent_id=project["ceo_agent_id"], kind="ask_lapsed").all()
        assert "Nothing shows the question ever reached him" in notice.body


def test_a_finished_delivery_nobody_was_told_about_does_not_close_itself(client):
    from tests.selftest_invariants.test_delivery_closes_itself import (
        _age_announcement,
        _finished,
        _status,
    )
    from yaaf.core.models import RequirementEvent

    project = _finished(client, "reach-signoff")
    # The same aging the delivery tests do, without anybody having seen it.
    with db_session() as session:
        for row in session.query(AuditLog).filter_by(
                project_id=project["id"], action="project.completion_ready"):
            row.at = row.at - timedelta(hours=25)
        for event in session.query(RequirementEvent).filter_by(project_id=project["id"]):
            event.at = event.at - timedelta(hours=25)
    _tick()
    assert _status(project) == "running"
    # Once it reached him, the rule does what it always did.
    _age_announcement(project, 0)
    _tick()
    assert _status(project) == "completed"


def _stamp_last_sweep(at: datetime) -> None:
    with db_session() as session:
        row = session.query(PlatformSetting).filter_by(key=needs_you.LAST_SWEEP).one_or_none()
        if row is None:
            needs_you.pause_for_downtime(session, now=at)
        else:
            row.value = at.isoformat()


def test_the_time_the_box_was_down_does_not_count(client):
    project = make_project(client, slug="reach-downtime")
    filed = _file(client, project)
    with db_session() as session:
        session.get(Approval, filed["id"]).expires_at = utcnow() + timedelta(hours=2)
    _stamp_last_sweep(utcnow() - timedelta(days=2))
    _tick()

    row = _approval(filed["id"])
    assert row.status == "pending"
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    assert expires > utcnow() + timedelta(days=1, hours=23)
    with db_session() as session:
        (paused,) = session.query(AuditLog).filter_by(action="clock.paused").all()
        assert paused.detail["gap_seconds"] >= 2 * 86_400 - 60


def test_the_first_sweep_and_a_short_gap_move_nothing(client):
    project = make_project(client, slug="reach-no-downtime")
    filed = _file(client, project)
    before = _approval(filed["id"]).expires_at
    _tick()  # the first sweep ever: nothing to measure a gap from
    _stamp_last_sweep(utcnow() - timedelta(minutes=2))
    _tick()
    assert _approval(filed["id"]).expires_at == before
    with db_session() as session:
        assert session.query(AuditLog).filter_by(action="clock.paused").count() == 0


# ── the person's own numbers (A6) ───────────────────────────────────────────────


def test_your_numbers_count_what_you_decided_and_what_silence_did(client):
    from tests.conftest import ADMIN

    project = make_project(client, slug="reach-stats")
    decided = _file(client, project)
    assert client.post(f"/admin/approvals/{decided['id']}/decide", headers=ADMIN,
                       json={"approve": True, "note": ""}).status_code == 200
    unseen = _file(client, project)
    for _ in range(2):  # extended once, then settled without reaching anyone
        _due(unseen["id"])
        _tick()

    stats = client.get("/me/decision-stats", headers=ADMIN).json()
    assert stats["projects"] == 1
    assert stats["filed"]["approvals"] == 2
    assert stats["decided_by_you"] == 1
    assert stats["median_minutes_to_decide"] is not None
    assert stats["by_silence"]["approvals"] == 1
    assert stats["extended_unreached"] == 1
    assert stats["resolved_unreached"] == 1
    assert client.get("/me/decision-stats", params={"days": 0},
                      headers=ADMIN).status_code == 422
