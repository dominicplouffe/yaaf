"""Invariant selftests: whatever is waiting on him is one tap from the mail.

A chase that says "a delivery date is waiting on your signature" and stops
there sends him to open the app, find the project, then find the row. On a
phone that is where the decision dies. So every mail about something waiting
on him ends on a link to that one row of his Inbox, spelled the way the Inbox
spells it, when the install knows its browser-facing origin, and on nothing
when it does not.

And the link only ever opens the signed-in dashboard, where deciding is one
click. Nothing is decided by opening it: a GET that approved would be a
decision a forwarded mail, or a scanner prefetching its links, could take for
him.
"""

from __future__ import annotations

import pathlib
from datetime import timedelta

import pytest

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core import config, ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Ask,
    Commitment,
    CommitmentStatus,
    Message,
    Project,
    Requirement,
    RequirementStatus,
    utcnow,
)
from yaaf.server import janitor, notify

ORIGIN = "https://yaaf.example.com"
INBOX = f"{ORIGIN}/app/#/inbox?item="
DESK_TSX = pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "desk.tsx"


class _Sent:
    def __init__(self) -> None:
        self.mail: list[dict] = []

    def __call__(self, session, project, subject, body, *, attention,
                 kind=None, data=None, waits_on=None):  # noqa: ARG002
        self.mail.append({"subject": subject, "body": body})

    def about(self, words: str) -> str:
        return next(m["body"] for m in self.mail if words in m["subject"])


@pytest.fixture
def origin(monkeypatch):
    monkeypatch.setattr(config.settings(), "public_base_url", ORIGIN)


@pytest.fixture
def sent(monkeypatch) -> _Sent:
    caught = _Sent()
    monkeypatch.setattr(janitor, "chairman_notify", caught)
    return caught


def _tick() -> None:
    with db_session() as session:
        janitor.janitor_once(session)


# ── the keys are the Inbox's own ────────────────────────────────────────────────


@pytest.mark.parametrize(("built_there", "built_here"), [
    ("`${p.id}-signoff`", notify.signoff_item("PID")),
    ("`${p.id}-plan`", notify.scope_item("PID")),
    ("`${p.id}-commitment`", notify.commitment_item("PID")),
    ("`${p.id}-verdict-${v.key}`", notify.verdict_item("PID", "R-01")),
    ("`${p.id}-frozen`", notify.frozen_item("PID")),
])
def test_a_link_names_the_row_the_way_the_inbox_names_it(built_there, built_here):
    """The Inbox selects `?item=` by exact match and falls back to its first
    row, so a key spelled differently here does not fail — it quietly opens
    some other decision. Held against the file that builds them."""
    assert built_there in DESK_TSX.read_text(), (
        f"the Inbox no longer builds {built_there}; change notify's spelling with it")
    assert built_there.strip("`").replace("${p.id}", "PID").replace("${v.key}", "R-01") \
        == built_here


def test_a_link_opens_the_dashboard_and_decides_nothing(origin):
    """Signed in, one click from the decision — never an endpoint that acts."""
    link = notify.inbox_link("apr_1", "Decide it")
    assert link == f"\n\nDecide it: {INBOX}apr_1"
    assert "/admin/" not in link and "/agent/" not in link


def test_without_a_public_origin_there_is_no_link(monkeypatch):
    monkeypatch.setattr(config.settings(), "public_base_url", "")
    assert notify.inbox_link("apr_1", "Decide it") == ""


# ── each thing that waits on him ────────────────────────────────────────────────


def test_a_finished_delivery_mails_the_sign_off_row(client, origin, sent):
    from tests.selftest_invariants.test_delivered_stands_down import delivered

    project = delivered(client, "signoff-link")
    with db_session() as session:
        janitor.check_delivery_ready(session, session.get(Project, project["id"]))

    mail = sent.about("ready to complete")
    assert mail.endswith(f"Sign it off: {INBOX}{project['id']}-signoff")
    assert "Confirm completion in your Inbox" in mail
    # The boardroom line is the CEO's reading too, so it keeps its words.
    with db_session() as session:
        (line,) = [m.body for m in session.query(Message).filter_by(project_id=project["id"])
                   if m.sender_agent_id is None and m.sender_user_id is None]
    assert "Confirm completion on the project page" in line
    assert "inbox?item=" not in line


def test_the_chase_for_a_question_opens_the_question(client, origin, sent):
    from tests.selftest_invariants.test_written_for_the_chairman import QUESTION

    project = make_project(client, slug="asked")
    _, token = live_token(project["id"], project["ceo_agent_id"])
    asked = client.post("/agent/asks", headers=bearer(token), json={
        **QUESTION, "why": "The page has to be live by the 30th and design takes two weeks."})
    assert asked.status_code == 200, asked.text
    with db_session() as session:
        ask = session.query(Ask).filter(Ask.project_id == project["id"]).one()
        ask.created_at = utcnow() - timedelta(hours=5)
        ask.answer_by = utcnow() + timedelta(hours=1)
        ask_id = ask.id
    _tick()

    mail = sent.about("your CEO is waiting on you")
    assert mail.endswith(f"Answer it: {INBOX}{ask_id}")
    assert "Answer it in your Inbox or the boardroom by" in mail


def test_proposed_scope_and_an_unsigned_date_each_open_their_row(client, origin, sent):
    project = make_project(client, slug="supervised")
    with db_session() as session:
        session.add(Requirement(
            id=ids.new_id(ids.REQUIREMENT), project_id=project["id"], key="R-09",
            statement="R-09: five named write-ups are published and reachable.",
            acceptance_test={"type": "observable", "target": "https://example.org/x"},
            status=RequirementStatus.PROPOSED, stage=1, est_runs=4,
            created_at=utcnow() - timedelta(hours=6)))
        session.add(Commitment(
            id=ids.new_id(ids.COMMITMENT), project_id=project["id"],
            status=CommitmentStatus.PROPOSED, deliver_by=utcnow() + timedelta(days=14),
            scope_snapshot=["R-01"], created_at=utcnow() - timedelta(hours=6)))
    _tick()

    assert sent.about("waiting on you to accept").endswith(
        f"Open it: {INBOX}{project['id']}-plan")
    assert sent.about("waiting on your signature").endswith(
        f"Open it: {INBOX}{project['id']}-commitment")


def test_each_verdict_in_one_mail_opens_its_own_row(client, origin, sent):
    """One mail for the batch, but each requirement is its own Inbox row."""
    project = make_project(client, slug="verdicts")
    with db_session() as session:
        for key in ("R-07", "R-08"):
            session.add(Requirement(
                id=ids.new_id(ids.REQUIREMENT), project_id=project["id"], key=key,
                statement=f"{key}: the export lists spend and orders per term.",
                acceptance_test={"type": "observable", "target": "the shared drive"},
                status=RequirementStatus.CHECKING, stage=1, est_runs=4))
    _tick()

    mail = sent.about("waiting on your verdict")
    for key in ("R-07", "R-08"):
        assert f"Judge it: {INBOX}{project['id']}-verdict-{key}" in mail


def test_an_open_review_links_to_the_meeting(client, origin, sent):
    """A review is not an Inbox row — it is a meeting with levers — so its
    chase opens the page that holds it."""
    project = make_project(client, slug="reviewed", constitution={"review_interval_days": 1})
    with db_session() as session:
        session.get(Project, project["id"]).created_at = utcnow() - timedelta(days=2)
    _tick()

    assert sent.about("is open").endswith(
        f"Take the meeting: {ORIGIN}/app/#/projects/{project['id']}/details/reviews")


def test_a_write_to_his_account_links_to_the_approval(client, origin, monkeypatch):
    from tests.selftest_invariants.test_data_source_allowlist import (
        READS,
        SOURCE,
        WRITE,
        _agent,
        _attach,
    )
    from yaaf.server.api import agent as agent_api

    caught = _Sent()
    monkeypatch.setattr(agent_api, "chairman_notify", caught)
    project = make_project(client)
    _attach(client, project["id"], READS)
    filed = client.post("/agent/data_writes", headers=_agent(client, project), json={
        "source": SOURCE, "tool": WRITE, "arguments": {"campaigns": []},
        "why": "Launch the Q4 campaign."})
    assert filed.status_code == 200, filed.text

    mail = caught.about("Approve a change to your")
    assert mail.endswith(f"Decide it in your Inbox: {INBOX}{filed.json()['id']}")


def test_a_brief_to_confirm_links_to_the_approval(client, origin, monkeypatch):
    from yaaf.server.api import agent as agent_api

    caught = _Sent()
    monkeypatch.setattr(agent_api, "chairman_notify", caught)
    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    drafted = client.post("/agent/brief", headers=bearer(token), json={
        "goal": "Run the desk and send the chairman tomorrow's ticket each evening.",
        "standing_orders": ["Send the ticket by 8pm"],
        "what_changed": "He asked for the ticket by 8pm from now on."})
    assert drafted.status_code == 200, drafted.text

    approvals = client.get(f"/observe/projects/{project['id']}/approvals",
                           headers=ADMIN).json()
    (brief,) = [a for a in approvals if a["kind"] == "brief"]
    mail = caught.about("confirm the brief")
    assert mail.endswith(f"Decide it in your Inbox: {INBOX}{brief['id']}")
    # The link is the mail's, not the brief's: the case he confirms is unchanged.
    assert "inbox?item=" not in brief["body"]


# ── told when it is asked, not when it is nearly too late ───────────────────────


def test_a_new_question_is_mailed_when_asked_with_its_default_and_link(client, origin,
                                                                        monkeypatch):
    """An approval was mailed the moment it was filed; a question only when its
    chase came round near the deadline, so the CEO's default could land before
    the chairman had read the question at all."""
    from yaaf.server.api import agent as agent_api

    caught = _Sent()
    monkeypatch.setattr(agent_api, "chairman_notify", caught)
    project = make_project(client, slug="asked-mailed",
                           constitution={"chairman_report_interval_hours": 1})
    _, token = live_token(project["id"], project["ceo_agent_id"])
    response = client.post("/agent/asks", headers=bearer(token), json={
        "title": "Which baseline year do you want?",
        "body": "Three filers changed their year-end.",
        "why": "Every table depends on it and redoing them costs two days.",
        "answer_by_hours": 36,
        "default_action": "Use each filer's own FY2019 and say so on every table.",
    })
    assert response.status_code == 200, response.text
    body = caught.about("a question from your CEO")
    assert "Use each filer's own FY2019" in body
    assert f"{INBOX}{response.json()['ask_id']}" in body


def test_a_heads_up_mail_opens_it_where_it_is_noted(client, origin, monkeypatch):
    """A heads-up is noted on the Acknowledge tab, so its link opens that tab
    on that row — not Decide, where it is not."""
    from yaaf.server.api import agent as agent_api

    caught = _Sent()
    monkeypatch.setattr(agent_api, "chairman_notify", caught)
    project = make_project(client, slug="heads-mailed")
    client.patch(f"/admin/projects/{project['id']}", headers=ADMIN, json={
        "standing_orders": ["Tell me when a data source changes its terms."]})
    _, token = live_token(project["id"], project["ceo_agent_id"])
    response = client.post("/agent/heads_up", headers=bearer(token), json={
        "title": "A source changed its terms",
        "body": "The filings mirror now requires an API key from next month.",
        "standing_order": "Tell me when a data source changes its terms.",
    })
    assert response.status_code == 200, response.text
    body = caught.about("Heads-up")
    assert f"{ORIGIN}/app/#/inbox?tab=ack&item=hu-{response.json()['heads_up_id']}" in body


def test_the_delivered_mail_links_the_page_it_says_reopen_is_on(client, origin, sent):
    """"If something is missing, reopen it on the project page" linked only to
    Deliverables, where there is no Reopen. Each place the sentence names has
    its link, and neither link decides anything by being opened."""
    from tests.selftest_invariants.test_delivery_closes_itself import (
        _age_announcement,
        _finished,
    )
    from tests.selftest_invariants.test_modes import run_mode_tick

    project = _finished(client, slug="mail-reopen")
    _age_announcement(project, 25)
    run_mode_tick(project)
    body = sent.about("delivered and closed")
    page = f"{ORIGIN}/app/#/projects/{project['id']}"
    assert f"Reopen it from the project page: {page}\n" in body + "\n"
    assert f"{page}/details/deliverables" in body
    assert "requirement(s)" not in body
