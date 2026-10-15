"""Invariant selftests: what reaches the chairman can be read cold.

The complaint was not that the CEO asked too much or said too little. It was
that a question arrived as a line that made sense to its author, who had just
spent a run inside the work, and to nobody who had not: no reason it mattered,
no options, no recommendation, a deadline in UTC ISO. A prompt can ask for
better writing, and does. These pin the half a prompt cannot: the context the
platform refuses to file without, and the clocks it states in his own words.

Only the chairman's side is held to this. Agents talking to agents are not.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Approval,
    Ask,
    Message,
    Requirement,
    RequirementStatus,
    utcnow,
)
from yaaf.server import janitor
from yaaf.server.rows import chairman_time

ZONE = {"chairman_report_interval_hours": 1, "timezone": "America/New_York"}

QUESTION = {
    "title": "Which designer should do the launch page?",
    "body": "I shortlisted two designers for the launch page and both can start Monday.",
    "answer_by_hours": 6,
    "default_action": "Book Ana on Monday morning and tell you I did.",
}


class _Sent:
    def __init__(self) -> None:
        self.mail: list[dict] = []

    def __call__(self, session, project, subject, body, *, attention,
                 kind=None, data=None):  # noqa: ARG002
        self.mail.append({"subject": subject, "body": body})


def _ceo(project: dict) -> dict:
    _, token = live_token(project["id"], project["ceo_agent_id"])
    return bearer(token)


# ── a question ──────────────────────────────────────────────────────────────────


def test_a_question_without_a_reason_is_refused(client):
    """Why it matters is the half only the asker knows. It cannot be added later."""
    project = make_project(client, slug="noreason", constitution=ZONE)
    refused = client.post("/agent/asks", headers=_ceo(project), json=QUESTION)
    assert refused.status_code == 422
    assert "why" in refused.text


def test_a_question_reaches_him_labelled_with_its_deadline_in_his_time(client):
    project = make_project(client, slug="labelled", constitution=ZONE)
    asked = client.post("/agent/asks", headers=_ceo(project), json={
        **QUESTION,
        "why": "The page has to be live by the 30th and design takes two weeks.",
        "options": ["Ana — $2,400, has done two launches like ours",
                    "Ben — $1,500, no launch work yet"],
        "recommendation": "Ana. The $900 difference is less than one bad week of signups.",
    })
    assert asked.status_code == 200, asked.text

    with db_session() as session:
        ask = session.query(Ask).filter(Ask.project_id == project["id"]).one()
        posted = session.get(Message, ask.asked_in_message_id).body
        answer_by = ask.answer_by
        stored = ask.body
    for part in ("Why it matters: The page has to be live by the 30th",
                 "The options:\n- Ana — $2,400",
                 "\n- Ben — $1,500",
                 "What I recommend: Ana."):
        assert part in posted, part
        assert part in stored, f"the desk and the email show the stored case: {part}"
    assert chairman_time(answer_by, "America/New_York") in posted
    assert "+00:00" not in posted, "no ISO timestamps in a message a person reads"

    desk = client.get("/observe/desk", headers=ADMIN).json()
    row = next(p for p in desk["projects"] if p["id"] == project["id"])
    assert "Why it matters" in row["pending_asks"][0]["body"]


def test_a_question_of_fact_needs_no_options_or_recommendation(client):
    """Required where it is always meaningful, optional where it is not —
    otherwise the field gets filled with "n/a" and teaches him to skip it."""
    project = make_project(client, slug="factual", constitution=ZONE)
    asked = client.post("/agent/asks", headers=_ceo(project), json={
        **QUESTION,
        "title": "Which email should customers reply to?",
        "body": "The welcome email needs a reply-to address and I do not have one.",
        "why": "It goes to the first 40 signups tomorrow at 9 am.",
    })
    assert asked.status_code == 200, asked.text
    with db_session() as session:
        stored = session.query(Ask).filter(Ask.project_id == project["id"]).one().body
    assert "The options" not in stored and "What I recommend" not in stored


def test_the_chase_for_a_question_reads_in_his_time(client, monkeypatch):
    project = make_project(client, slug="chased", constitution=ZONE)
    asked = client.post("/agent/asks", headers=_ceo(project), json={
        **QUESTION, "why": "The page has to be live by the 30th and design takes two weeks."})
    assert asked.status_code == 200, asked.text
    with db_session() as session:
        ask = session.query(Ask).filter(Ask.project_id == project["id"]).one()
        ask.created_at = utcnow() - timedelta(hours=5)
        ask.answer_by = utcnow() + timedelta(hours=1)
        answer_by = ask.answer_by

    sent = _Sent()
    monkeypatch.setattr(janitor, "chairman_notify", sent)
    with db_session() as session:
        janitor.janitor_once(session)

    chase = next(m for m in sent.mail if m["subject"].startswith("your CEO is waiting"))
    assert "Why it matters: The page has to be live" in chase["body"]
    assert f"by {chairman_time(answer_by, 'America/New_York')}" in chase["body"]
    assert "UTC" not in chase["body"]
    assert "your CEO will: Book Ana" in chase["body"]


# ── an approval ─────────────────────────────────────────────────────────────────


SPEND = {"kind": "spend", "title": "Buy a month of competitor price data",
         "body": "A month of daily competitor prices, so the pricing test compares like "
                 "with like.", "amount_usd": 40}


def test_an_approval_without_what_no_costs_is_refused(client):
    """On most projects silence means no, so what "no" costs is the one thing he
    must know to decide — and the line its author finds too obvious to write."""
    project = make_project(client, slug="nocost", constitution=ZONE)
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    refused = client.post("/agent/approvals", headers=bearer(token), json=SPEND)
    assert refused.status_code == 422
    assert "if_declined" in refused.text

    filed = client.post("/agent/approvals", headers=bearer(token), json={
        **SPEND, "if_declined": "We price from last quarter's numbers, which are six "
                                "weeks stale."})
    assert filed.status_code == 200, filed.text
    listing = client.get(f"/observe/projects/{project['id']}/approvals", headers=ADMIN)
    row = next(r for r in listing.json() if r["id"] == filed.json()["id"])
    assert row["body"].endswith(
        "What happens without it: We price from last quarter's numbers, which are six "
        "weeks stale.")


def test_the_chase_for_an_approval_reads_in_his_time(client, monkeypatch):
    project = make_project(client, slug="expiring", constitution=ZONE)
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    filed = client.post("/agent/approvals", headers=bearer(token), json={
        **SPEND, "if_declined": "We price from last quarter's numbers."})
    assert filed.status_code == 200, filed.text
    with db_session() as session:
        approval = session.get(Approval, filed.json()["id"])
        approval.expires_at = utcnow() + timedelta(hours=3)
        expires_at = approval.expires_at

    sent = _Sent()
    monkeypatch.setattr(janitor, "chairman_notify", sent)
    with db_session() as session:
        janitor.janitor_once(session)

    chase = next(m for m in sent.mail if m["subject"].startswith("approval expiring"))
    assert chase["body"].startswith("A month of daily competitor prices"), (
        "the case first; the clock is what it is for, not what it is")
    assert f"by {chairman_time(expires_at, 'America/New_York')}, the answer is no" in chase["body"]
    assert "UTC" not in chase["body"] and "DENY" not in chase["body"]


# ── the platform's own words ────────────────────────────────────────────────────


def _requirement(project: dict, key: str, status: str, meta: dict | None) -> None:
    with db_session() as session:
        session.add(Requirement(
            id=ids.new_id(ids.REQUIREMENT), project_id=project["id"], key=key,
            statement=f"{key}: the search-term export lists spend and orders per term.",
            acceptance_test={"type": "observable", "target": "the export in the shared drive"},
            status=status, stage=1, est_runs=4, meta=meta,
            created_at=utcnow() - timedelta(hours=6),
        ))


def test_requirements_reach_him_by_the_name_written_for_him(client, monkeypatch):
    """`meta.title` is the name the CEO writes "for the person who hired you".
    It was stored and drawn nowhere, so every email named the work "R-07"."""
    project = make_project(client, slug="named")
    _requirement(project, "R-07", RequirementStatus.CHECKING,
                 {"title": "Wasted-spend list",
                  "description": "every search term with spend and no sales"})
    _requirement(project, "R-08", RequirementStatus.PROPOSED, {"title": "Weekly price check"})
    sent = _Sent()
    monkeypatch.setattr(janitor, "chairman_notify", sent)
    with db_session() as session:
        janitor.janitor_once(session)

    verdict = next(m for m in sent.mail if "verdict" in m["subject"])
    assert verdict["subject"] == "waiting on your verdict: Wasted-spend list"
    assert "What has to be true: R-07: the search-term export" in verdict["body"], (
        "the checker's sentence stays — it is what he is ruling on")
    proposal = next(m for m in sent.mail if "to accept" in m["subject"])
    assert "  - Weekly price check" in proposal["body"]
    assert "R-08" not in proposal["body"]

    desk = client.get("/observe/desk", headers=ADMIN).json()
    row = next(p for p in desk["projects"] if p["id"] == project["id"])
    assert row["awaiting_you"]["verdicts"][0]["name"] == "Wasted-spend list"


def test_a_requirement_without_an_owners_title_keeps_its_statement(client):
    project = make_project(client, slug="unnamed")
    _requirement(project, "R-09", RequirementStatus.CHECKING, None)
    desk = client.get("/observe/desk", headers=ADMIN).json()
    row = next(p for p in desk["projects"] if p["id"] == project["id"])
    verdict = row["awaiting_you"]["verdicts"][0]
    assert verdict["name"] == verdict["statement"]


def test_a_capacity_request_names_the_dial_the_way_his_settings_page_does(client, monkeypatch):
    from tests.selftest_invariants.test_asking_for_more import ask
    from tests.selftest_invariants.test_requirements import ceo_token, deliver_project
    from yaaf.server.api import agent as agent_api

    project = deliver_project(client, constitution={"runs_per_project_per_day": 18})
    sent = _Sent()
    monkeypatch.setattr(agent_api, "chairman_notify", sent)
    assert ask(client, ceo_token(project)).status_code == 200

    with db_session() as session:
        approval = session.query(Approval).filter(
            Approval.project_id == project["id"]).one()
        line = session.query(Message).filter(
            Message.project_id == project["id"], Message.sender_agent_id.is_(None),
            Message.sender_user_id.is_(None)).one().body
        title, body = approval.title, approval.body
    assert title == "More room: Runs per day, whole project, 18 → 60"
    assert body.splitlines()[-1].startswith("What happens without it: "), (
        "last, where the Decisions screen reads it as what saying no costs")
    for text in (title, body, line, sent.mail[0]["subject"], sent.mail[0]["body"]):
        assert "runs_per_project_per_day" not in text, text
