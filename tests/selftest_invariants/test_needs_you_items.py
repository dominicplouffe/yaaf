"""Invariant selftests: "needs you" is one object, from one producer, and
reading it as a decider is how the platform knows it told you.

docs/ux-redesign/decide-anywhere/REQUIREMENTS.md, A1 and A2. The rules under
attack:

1. Every family the Inbox draws is produced on the server, with the Inbox's
   own keys, and the number of items is `policy.needs_you`'s total — one
   definition of "needs you", whoever counts.
2. Every item says what silence does, following the project's own rule.
3. `/observe/needs-you` lists only what the reader may open, and says whether
   they may decide it.
4. A decider's read records that the item reached them; an observer's records
   nothing. A notice records a delivery only when it actually left for a
   configured channel.
"""

from __future__ import annotations

from tests.conftest import ADMIN, amend, bearer, headers_of, live_token, make_project, person
from tests.selftest_invariants.test_approval_notices import _file, _wire
from tests.selftest_invariants.test_asks import _ask, _project
from tests.selftest_invariants.test_delivered_stands_down import delivered
from yaaf.core.db import db_session
from yaaf.core.models import ReachReceipt
from yaaf.server import needs_you


def _row(client, project: dict) -> dict:
    desk = client.get("/observe/desk", params={"only": project["id"]}, headers=ADMIN).json()
    (row,) = desk["projects"]
    return row


def _items(client, project: dict, headers=ADMIN) -> list[dict]:
    response = client.get("/observe/needs-you", params={"project_id": project["id"]},
                          headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _receipts(project_id: str, **where) -> list[ReachReceipt]:
    with db_session() as session:
        rows = session.query(ReachReceipt).filter_by(project_id=project_id, **where).all()
        session.expunge_all()
        return rows


def test_every_family_on_the_desk_is_an_item_and_the_count_is_one_count(client):
    from tests.selftest_invariants.test_requirements import ceo_token, deliver_project, propose

    project = deliver_project(client, slug="items-every-family")
    propose(client, ceo_token(project), statement="The archive exists, every filing saved")
    _file(client, project)
    assert _ask(client, project, options=["Number one", "Number three"],
                recommendation="Number one").status_code == 200

    row = _row(client, project)
    families = sorted(i["family"] for i in row["needs_you_items"])
    assert families == ["approval", "ask", "scope"]
    assert len(row["needs_you_items"]) == row["needs_you"]["total"] == 3
    # The Inbox's own keys: an approval and an ask by id, scope by project.
    keys = {i["family"]: i["key"] for i in row["needs_you_items"]}
    assert keys["scope"] == f"{project['id']}-plan"
    assert keys["approval"] == row["pending_approvals"][0]["id"]
    assert keys["ask"] == row["pending_asks"][0]["id"]
    # And the listing a non-dashboard surface reads is the same items.
    assert {i["key"] for i in _items(client, project)} == set(keys.values())


def test_a_finished_delivery_is_an_item_with_its_one_option(client):
    project = delivered(client, "items-signoff")
    (item,) = [i for i in _items(client, project) if i["family"] == "signoff"]
    assert item["key"] == f"{project['id']}-signoff"
    assert [o["id"] for o in item["options"]] == ["complete"]
    assert item["commit"] is True


def test_an_approval_says_what_silence_decides_on_this_project(client):
    project = make_project(client, slug="items-silence-default")
    _file(client, project)
    (item,) = _items(client, project)
    assert item["silence"].startswith("It is declined ")
    assert item["deadline"] and item["filed_at"]
    assert [o["id"] for o in item["options"]] == ["approve", "decline"]
    approve = item["options"][0]
    assert approve["label"] == "Approve $199"
    assert approve["irreversible"] is True  # a purchase is not taken back

    loose = make_project(client, slug="items-silence-approves")
    amend(client, loose["id"], approval_timeout_decision="approve")
    _file(client, loose)
    (item,) = _items(client, loose)
    assert item["silence"].startswith("It is approved ")


def test_a_question_offers_its_options_and_your_own_words(client):
    project = _project(client, slug="items-ask-options")
    assert _ask(client, project, options=["Number one", "Number three"],
                recommendation="Number one").status_code == 200
    (item,) = _items(client, project)
    ids = [o["id"] for o in item["options"]]
    assert ids == ["option:0", "option:1", "reply"]
    assert item["options"][0]["style"] == "approve"  # the recommended one
    assert item["options"][2]["needs_note"] is True
    assert "Take the top-ranked candidate" in item["silence"]


def test_nobody_lists_a_project_they_have_no_standing_on(client):
    project = make_project(client, slug="items-private")
    _file(client, project)
    stranger = person("stranger@example.com")
    listed = client.get("/observe/needs-you", headers=headers_of(stranger)).json()
    assert listed["items"] == []
    refused = client.get("/observe/needs-you", params={"project_id": project["id"]},
                         headers=headers_of(stranger))
    assert refused.status_code == 404


def test_an_observer_reads_the_items_decides_none_and_reaches_nobody(client):
    project = make_project(client, slug="items-observer")
    _file(client, project)
    watcher = person("watcher@example.com")
    assert client.post(f"/admin/projects/{project['id']}/members", headers=ADMIN,
                       json={"email": "watcher@example.com", "role": "observer"}
                       ).status_code == 200
    (item,) = _items(client, project, headers=headers_of(watcher))
    assert item["decider"] is False
    assert not _receipts(project["id"], kind="seen")
    # The chairman's own read is a reach, through the door it came by.
    (item,) = _items(client, project)
    assert item["decider"] is True
    (seen,) = _receipts(project["id"], kind="seen")
    assert seen.item_key == item["key"] and seen.channel == "cli"


def test_the_inbox_poll_reaches_the_chairman_once(client):
    project = make_project(client, slug="items-seen-once")
    _file(client, project)
    _row(client, project)
    _row(client, project)
    seen = _receipts(project["id"], kind="seen")
    assert len(seen) == 1
    (item,) = _row(client, project)["needs_you_items"]
    assert item["reached"]["ok"] is True


def test_a_notice_with_nowhere_to_go_reaches_nobody(client):
    project = make_project(client, slug="items-no-channel")
    _file(client, project)
    assert not _receipts(project["id"], kind="delivered")


def test_a_notice_that_left_for_a_channel_is_a_delivery(client, monkeypatch):
    _wire(monkeypatch, public_base_url="https://yaaf.example.com")
    project = make_project(client, slug="items-mailed")
    filed = _file(client, project)
    (row,) = _receipts(project["id"], kind="delivered")
    assert row.item_key == filed["id"]
    assert row.channel == "email"


def test_a_question_notice_is_a_delivery_too(client, monkeypatch):
    _wire(monkeypatch, public_base_url="https://yaaf.example.com")
    project = _project(client, slug="items-ask-mailed")
    asked = _ask(client, project).json()
    (row,) = _receipts(project["id"], kind="delivered")
    assert row.item_key == asked["ask_id"]


def test_the_lead_a_reach_needs_is_a_quarter_of_the_window_capped_at_six_hours():
    from datetime import UTC, datetime, timedelta

    filed = datetime(2026, 11, 1, tzinfo=UTC)
    assert needs_you.lead(filed, filed + timedelta(days=7)) == timedelta(hours=6)
    assert needs_you.lead(filed, filed + timedelta(hours=8)) == timedelta(hours=2)
    assert needs_you.lead(filed, filed + timedelta(days=7), extended=True) == timedelta(hours=1)
    due = filed + timedelta(hours=8)
    assert needs_you._state(due - timedelta(hours=2), False, filed, due)["ok"] is True
    assert needs_you._state(due - timedelta(hours=1), False, filed, due)["ok"] is False
    assert needs_you._state(None, False, filed, due)["ok"] is False


def test_an_agent_cannot_read_the_listing(client):
    project = make_project(client, slug="items-agent")
    _, token = live_token(project["id"], project["ceo_agent_id"])
    assert client.get("/observe/needs-you", headers=bearer(token)).status_code == 401
