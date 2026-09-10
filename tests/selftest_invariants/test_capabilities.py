"""Invariant selftests: credentials are capabilities (CEO Charter, stage D).

Whatever the chairman stores, the project can use; what the platform owes in
return is to make it usable, to carry the asking, and to keep income honest.

The lines these tests hold:

  * a need rides the approval machinery AND lands in the boardroom (which the
    Slack bridge mirrors), so it reaches the chairman where he reads
  * fulfilling a need is ONE motion: credential stored, roles scoped, need
    approved, CEO woken with the env-var name
  * delegation is dial-gated: under the default the CEO files needs; under
    capability_authority: ceo it re-scopes existing service credentials —
    never billing, never values
  * the recognizer tells a granted role what its credential is and how to
    speak to it; an unrecognized one still injects, named only
  * revenue is booked ONLY by a signed webhook — no agent verb writes it, and
    an unsigned poster learns nothing
"""

from __future__ import annotations

import hashlib
import hmac
import json

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import (
    LedgerEntry,
    Message,
    Notice,
    NoticeKind,
    Project,
    Secret,
)


def token(project: dict, agent_id: str | None = None) -> str:
    _, tok = live_token(project["id"], agent_id or project["ceo_agent_id"])
    return tok


def file_need(client, project: dict, **overrides):
    body = {"title": "AWS access", "why": "R-04 needs a bucket for the exports.",
            "est_usd_per_month": 5.0, "suggested_secret_name": "aws-key",
            "roles": ["worker"], **overrides}
    return client.post("/agent/needs", json=body, headers=bearer(token(project)))


# ── the needs loop ──────────────────────────────────────────────────────────────


def test_a_need_rides_approvals_and_lands_in_the_boardroom(client):
    project = make_project(client, slug="needy")
    filed = file_need(client, project)
    assert filed.status_code == 200, filed.text
    with db_session() as session:
        # A system boardroom line — no sender, so it spends no cadence and the
        # Slack bridge mirrors it like any other boardroom message.
        line = session.query(Message).filter(
            Message.project_id == project["id"],
            Message.sender_agent_id.is_(None),
            Message.sender_user_id.is_(None)).one()
        assert "NEED: AWS access" in line.body


def test_only_the_ceo_files_needs(client):
    project = make_project(client, slug="needy2")
    worker = project["teams"][0]["agents"][0]["id"]
    refused = client.post("/agent/needs", headers=bearer(token(project, worker)),
                          json={"title": "AWS access", "why": "I want a bucket now."})
    assert refused.status_code == 403


def test_fulfillment_is_one_motion(client):
    project = make_project(client, slug="fulfil")
    need_id = file_need(client, project).json()["need_id"]
    done = client.post(f"/admin/approvals/{need_id}/fulfill",
                       json={"secret_name": "aws-key", "value": "AKIA-test-123",
                             "roles": ["worker"], "note": "here you go"},
                       headers=ADMIN)
    assert done.status_code == 200, done.text
    assert done.json()["env_var_hint"] == "AWS_KEY"
    with db_session() as session:
        secret = session.query(Secret).filter(
            Secret.project_id == project["id"], Secret.name == "aws-key").one()
        assert secret.role_scopes == ["worker"] and secret.kind == "service"
        woken = session.query(Notice).filter(
            Notice.project_id == project["id"],
            Notice.kind == NoticeKind.APPROVAL_DECIDED).one()
        assert "FULFILLED" in woken.body and "aws-key" in woken.body

    # Fulfilled is decided: deciding again is a 409, like any settled approval.
    again = client.post(f"/admin/approvals/{need_id}/fulfill",
                        json={"secret_name": "aws-key", "value": "x",
                              "roles": ["worker"]}, headers=ADMIN)
    assert again.status_code == 409


def test_only_a_need_is_fulfillable_with_a_credential(client):
    project = make_project(client, slug="fulfil2")
    spend = client.post("/agent/approvals", headers=bearer(token(project)),
                        json={"kind": "spend", "title": "a big buy",
                              "body": "Recommended for the launch.",
                              "amount_usd": 300.0})
    refused = client.post(f"/admin/approvals/{spend.json()['id']}/fulfill",
                          json={"secret_name": "x-key", "value": "v",
                                "roles": ["worker"]}, headers=ADMIN)
    assert refused.status_code == 422


# ── delegation under the dial ───────────────────────────────────────────────────


def _stored_credential(client, project: dict, name: str = "smtp-password",
                       roles: list[str] | None = None) -> None:
    created = client.post("/admin/secrets",
                          json={"project_id": project["id"], "name": name,
                                "value": "hunter2", "role_scopes": roles or ["ceo"]},
                          headers=ADMIN)
    assert created.status_code == 200, created.text


def test_delegation_is_dial_gated(client):
    project = make_project(client, slug="delegate")
    _stored_credential(client, project)
    refused = client.post("/agent/credentials/smtp-password/scope",
                          headers=bearer(token(project)),
                          json={"add_roles": ["worker"],
                                "reason": "the writer sends the digest now"})
    assert refused.status_code == 403
    assert "capability_authority" in refused.text


def test_the_ceo_delegates_under_the_dial_and_it_is_never_quiet(client):
    project = make_project(client, slug="delegate2",
                           constitution={"capability_authority": "ceo"})
    _stored_credential(client, project)
    roster = client.get("/agent/credentials", headers=bearer(token(project)))
    assert roster.status_code == 200
    assert {"name": "smtp-password", "role_scopes": ["ceo"], "file": False} in roster.json()

    done = client.post("/agent/credentials/smtp-password/scope",
                       headers=bearer(token(project)),
                       json={"add_roles": ["worker"], "remove_roles": ["ceo"],
                             "reason": "the writer sends the digest now"})
    assert done.status_code == 200, done.text
    assert done.json()["role_scopes"] == ["worker"]
    with db_session() as session:
        secret = session.query(Secret).filter(
            Secret.project_id == project["id"],
            Secret.name == "smtp-password").one()
        assert secret.role_scopes == ["worker"]


def test_a_grant_to_a_role_that_does_not_exist_grants_nothing(client):
    """A typo'd slug would silently scope the credential to nobody — the
    refusal names the unknown slug instead, and the scopes do not move."""
    project = make_project(client, slug="delegate4",
                           constitution={"capability_authority": "ceo"})
    _stored_credential(client, project)
    refused = client.post("/agent/credentials/smtp-password/scope",
                          headers=bearer(token(project)),
                          json={"add_roles": ["wrker"],
                                "reason": "the writer sends the digest now"})
    assert refused.status_code == 422, refused.text
    assert "wrker" in refused.text
    with db_session() as session:
        secret = session.query(Secret).filter(
            Secret.project_id == project["id"],
            Secret.name == "smtp-password").one()
        assert secret.role_scopes == ["ceo"], "nothing moved"


def test_a_platform_boardroom_line_wakes_nobody(client):
    """The need's announcement is addressed to the chairman. If it counted as
    an unread message for the CEO, every need would urgently buy a run to
    read its own announcement."""
    from yaaf.core.constitution import effective
    from yaaf.core.models import Agent
    from yaaf.server import dispatcher

    project = make_project(client, slug="needy3")
    assert file_need(client, project).status_code == 200
    with db_session() as session:
        row = session.get(Project, project["id"])
        agent = session.get(Agent, project["ceo_agent_id"])
        reasons = dispatcher._wake_reasons(session, row, effective(session, row.id), agent)
    assert not [r for r in reasons if r["kind"] == "messages"], reasons


def test_billing_credentials_are_never_delegable(client):
    project = make_project(client, slug="delegate3",
                           constitution={"capability_authority": "ceo"})
    created = client.post('/admin/settings/llm-credentials', json={'name': 'model-key', 'value': 'sk-ant-xxx', 'mode': 'api_key'}, headers=ADMIN)
    assert created.status_code == 201, created.text
    # Not on the roster, and not scopable.
    roster = client.get("/agent/credentials", headers=bearer(token(project)))
    assert all(row["name"] != "model-key" for row in roster.json())
    refused = client.post("/agent/credentials/model-key/scope",
                          headers=bearer(token(project)),
                          json={"add_roles": ["worker"],
                                "reason": "let the team bill the model directly"})
    assert refused.status_code == 404


# ── the recognizer ──────────────────────────────────────────────────────────────


def test_recognized_credentials_arrive_with_their_manual(client):
    from yaaf.server.recognizers import credentials_prompt

    text = credentials_prompt(["SMTP_PASSWORD", "AWS_SECRET_KEY", "MYSTERY_TOKEN"])
    assert "SMTP_PASSWORD: email credentials" in text
    assert "smtplib" in text
    assert "AWS_SECRET_KEY: AWS" in text
    assert "pip install boto3" in text
    # Unrecognized still injects, named only — recognition is never a fence.
    assert "- MYSTERY_TOKEN" in text
    assert credentials_prompt([]) == ""


def test_the_run_prompt_carries_the_manual(client):
    from yaaf.core import ids
    from yaaf.core.models import RunKind, RunQueue
    from yaaf.runner import daemon

    project = make_project(client, slug="manual")
    _stored_credential(client, project, name="smtp-password", roles=["worker"])
    employee = project["teams"][0]["agents"][0]["id"]
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=employee, kind=RunKind.AGENT_RUN,
                             trigger={"reasons": []}, attempt=1))
    job = daemon.lease_and_prepare(limit=1)[0]
    assert "SMTP_PASSWORD: email credentials" in job.system_prompt
    assert "never print, log, or write them to files" in job.system_prompt


# ── revenue ─────────────────────────────────────────────────────────────────────


def _signed_post(client, project: dict, payload: dict):
    with db_session() as session:
        secret = (session.get(Project, project["id"]).integrations or {})[
            "webhook_secret"]
    raw = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(f"/webhooks/generic/{project['id']}", content=raw,
                       headers={"X-Yaaf-Signature": signature,
                                "Content-Type": "application/json"})


def test_revenue_books_only_through_the_signature(client):
    project = make_project(client, slug="income")
    unsigned = client.post(f"/webhooks/generic/{project['id']}",
                           json={"description": "a customer paid us $29",
                                 "revenue_usd": 29.0})
    assert unsigned.status_code in (401, 403)

    signed = _signed_post(client, project,
                          {"description": "stripe: invoice 0042 paid",
                           "revenue_usd": 29.0})
    assert signed.status_code == 200, signed.text
    assert signed.json()["ledger_id"] is not None
    with db_session() as session:
        row = session.query(LedgerEntry).filter(
            LedgerEntry.project_id == project["id"],
            LedgerEntry.kind == "revenue").one()
        assert row.usd == 29.0
        told = session.query(Notice).filter(
            Notice.project_id == project["id"],
            Notice.kind == NoticeKind.REVENUE).one()
        assert "$29.00" in told.body


def test_no_agent_verb_books_revenue(client):
    """book_spend writes external_spend by construction; there is no route an
    agent could aim at kind=revenue at all."""
    project = make_project(client, slug="income2")
    booked = client.post("/agent/ledger/spend", headers=bearer(token(project)),
                         json={"usd": 29.0, "note": "revenue from a customer!"})
    assert booked.status_code == 200
    with db_session() as session:
        kinds = {r.kind for r in session.query(LedgerEntry).filter(
            LedgerEntry.project_id == project["id"]).all()}
        assert "revenue" not in kinds