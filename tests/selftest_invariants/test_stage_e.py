"""Invariant selftests: the last mile (CEO Charter, stage E).

The browser lives in the sandbox image (a Dockerfile fact, not testable here);
what the server owes is everything around it: the human-assist protocol, the
account registry, the world's ability to wake an agent, and rhythms for
agents that are not the CEO.

The lines these tests hold:

  * any agent may file an assist; it rides the approval machinery, lands in
    the boardroom, and the chairman's answer travels back verbatim in the
    notice that wakes the asker
  * every identity the company creates is a registry row — record-only,
    linking a credential by NAME, never holding a value
  * a signed webhook can wake the CEO or a named agent; the wake is a Notice
    under the ordinary brakes; an unsigned poster wakes nobody
  * agents.daily_at generalizes the CEO's standup: same helper, same
    heartbeat dedup, fires once per local day
"""

from __future__ import annotations

import hashlib
import hmac
import json

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import (
    Account,
    Agent,
    Message,
    Notice,
    NoticeKind,
    Project,
)
from yaaf.server import dispatcher


def token(project: dict, agent_id: str | None = None) -> str:
    _, tok = live_token(project["id"], agent_id or project["ceo_agent_id"])
    return tok


# ── the assist protocol ─────────────────────────────────────────────────────────


def test_any_agent_files_an_assist_and_the_answer_travels_back(client):
    project = make_project(client, slug="helpme")
    worker = project["teams"][0]["agents"][0]["id"]
    filed = client.post("/agent/assists", headers=bearer(token(project, worker)),
                        json={"what": "Solve the captcha and tell me done — "
                                      "signup is otherwise complete.",
                              "where": "https://example.com/signup"})
    assert filed.status_code == 200, filed.text
    assist_id = filed.json()["assist_id"]
    with db_session() as session:
        line = session.query(Message).filter(
            Message.project_id == project["id"],
            Message.sender_agent_id.is_(None),
            Message.sender_user_id.is_(None)).one()
        assert "ASSIST NEEDED" in line.body

    answered = client.post(f"/admin/approvals/{assist_id}/decide",
                           json={"approve": True, "note": "done — captcha solved"},
                           headers=ADMIN)
    assert answered.status_code == 200, answered.text
    with db_session() as session:
        woken = session.query(Notice).filter(
            Notice.project_id == project["id"],
            Notice.kind == NoticeKind.APPROVAL_DECIDED).one()
        # The answer arrives verbatim, addressed to the agent that asked.
        assert woken.agent_id == worker
        assert "done — captcha solved" in woken.body


# ── the account registry ────────────────────────────────────────────────────────


def test_every_identity_lands_on_the_registry_once(client):
    project = make_project(client, slug="whoweare")
    body = {"service": "forum.example.com", "username": "yaaf-launch",
            "owner": "project", "credential_name": "forum-password",
            "note": "free tier; delete via account settings"}
    first = client.post("/agent/accounts", json=body, headers=bearer(token(project)))
    assert first.status_code == 200, first.text
    assert first.json()["duplicate"] is False
    again = client.post("/agent/accounts", json=body, headers=bearer(token(project)))
    assert again.json()["duplicate"] is True

    listed = client.get(f"/admin/projects/{project['id']}/accounts", headers=ADMIN)
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["service"] == "forum.example.com"
    assert rows[0]["owner"] == "project"
    # The registry links a credential by name; no value field exists at all.
    assert "value" not in rows[0] and "ciphertext" not in rows[0]
    with db_session() as session:
        assert session.query(Account).count() == 1


def test_owner_is_situational_and_validated(client):
    project = make_project(client, slug="whoweare2")
    chairman_owned = client.post(
        "/agent/accounts", headers=bearer(token(project)),
        json={"service": "stripe.com", "username": "chair@example.com",
              "owner": "chairman", "note": "KYC done by the chairman himself"})
    assert chairman_owned.status_code == 200
    nonsense = client.post(
        "/agent/accounts", headers=bearer(token(project)),
        json={"service": "x.com", "username": "y", "owner": "the_intern"})
    assert nonsense.status_code == 422


# ── the world wakes agents ──────────────────────────────────────────────────────


def _signed_post(client, project: dict, payload: dict):
    with db_session() as session:
        secret = (session.get(Project, project["id"]).integrations or {})[
            "webhook_secret"]
    raw = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(f"/webhooks/generic/{project['id']}", content=raw,
                       headers={"X-Yaaf-Signature": signature,
                                "Content-Type": "application/json"})


def test_a_signed_webhook_wakes_the_ceo_or_a_named_agent(client):
    project = make_project(client, slug="doorbell")
    rang = _signed_post(client, project,
                        {"description": "customer email: where is my order?",
                         "wake": "ceo"})
    assert rang.status_code == 200, rang.text
    assert rang.json()["woke"] == "CEO"

    by_name = _signed_post(client, project,
                           {"description": "monitoring: the site is down",
                            "wake": "Lead"})
    assert by_name.json()["woke"] == "Lead"

    nobody = _signed_post(client, project,
                          {"description": "shouting into the void here",
                           "wake": "No Such Agent"})
    assert nobody.status_code == 200  # evidence still lands; nobody wakes
    assert nobody.json()["woke"] is None

    with db_session() as session:
        wakes = session.query(Notice).filter(
            Notice.project_id == project["id"],
            Notice.kind == NoticeKind.EXTERNAL_EVENT).all()
        assert len(wakes) == 2
    # Urgent-classed: the dispatcher treats it like an approval decision, so
    # it wakes on the next tick instead of coalescing for half an hour.
    assert NoticeKind.EXTERNAL_EVENT in dispatcher.URGENT_NOTICE_KINDS


def test_an_unsigned_poster_wakes_nobody(client):
    project = make_project(client, slug="doorbell2")
    refused = client.post(f"/webhooks/generic/{project['id']}",
                          json={"description": "wake everyone right now!!",
                                "wake": "ceo"})
    assert refused.status_code in (401, 403)
    with db_session() as session:
        assert session.query(Notice).filter(
            Notice.kind == NoticeKind.EXTERNAL_EVENT,
            Notice.project_id == project["id"]).count() == 0


# ── rhythms for everyone ────────────────────────────────────────────────────────


def test_an_agent_daily_wake_fires_once_per_day_through_the_same_dedup(client):
    from yaaf.core.constitution import effective
    from yaaf.core.models import utcnow

    project = make_project(client, slug="rhythm")
    worker_id = project["teams"][0]["agents"][0]["id"]
    scheduled = client.patch(f"/admin/agents/{worker_id}/schedule",
                             json={"daily_at": "00:00"}, headers=ADMIN)
    assert scheduled.status_code == 200, scheduled.text

    with db_session() as session:
        agent = session.get(Agent, worker_id)
        row = session.get(Project, project["id"])
        cons = effective(session, row.id)
        first = dispatcher._daily_wake_due(session, row, cons, agent, utcnow(),
                                           raw=agent.daily_at, name="agent_daily")
        assert first is not None and first["kind"] == "heartbeat"
        again = dispatcher._daily_wake_due(session, row, cons, agent, utcnow(),
                                           raw=agent.daily_at, name="agent_daily")
        assert again is None  # once per local day, same Heartbeat dedup


def test_a_bad_schedule_is_refused_at_the_door(client):
    project = make_project(client, slug="rhythm2")
    worker_id = project["teams"][0]["agents"][0]["id"]
    bad = client.patch(f"/admin/agents/{worker_id}/schedule",
                       json={"daily_at": "25:99"}, headers=ADMIN)
    assert bad.status_code == 422
    cleared = client.patch(f"/admin/agents/{worker_id}/schedule",
                           json={"daily_at": None}, headers=ADMIN)
    assert cleared.status_code == 200
    assert cleared.json()["daily_at"] is None
