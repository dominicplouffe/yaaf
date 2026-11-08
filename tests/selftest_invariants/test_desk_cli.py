"""Invariant selftests: `yaaf signal` and `yaaf desk` are doors, not a second desk.

The status bar and the terminal desk read the payload the dashboard reads and
draw the rows its Inbox draws. Two ways that goes wrong, both silent: the bar
counts something the badge does not (a third definition of "needs you", which
this repo has already paid for once), or the terminal settles a decision with
fewer safeguards than the browser does (a decline with no reason, a purchase
with no question). These attack both, over HTTP, against the real app.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from typer.testing import CliRunner

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from yaaf.cli import desk as cli_desk
from yaaf.cli import main as cli
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.inbox import HARD_STOP
from yaaf.core.models import (
    Approval,
    ApprovalStatus,
    AuditLog,
    Channel,
    Hypothesis,
    Message,
    Project,
    ProjectStatus,
    utcnow,
)


@pytest.fixture()
def chair(client):
    """The suite's client, speaking as the chairman's own CLI token."""
    client.headers.update(ADMIN)
    return client


def _approval(client, project: dict, *, amount: float = 40, kind: str = "spend",
              title: str = "Buy a data pull") -> str:
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    filed = client.post("/agent/approvals", headers=bearer(token), json={
        "kind": kind, "title": title, "body": "Two months of category data.",
        "recommendation": "Approve it; the case says why.",
        "if_declined": "We forecast from last year's data.", "amount_usd": amount})
    assert filed.status_code == 200, filed.text
    return filed.json()["id"]


def _ask(client, project: dict, title: str = "Which candidate?", **extra) -> str:
    _, token = live_token(project["id"], project["ceo_agent_id"])
    asked = client.post("/agent/asks", headers=bearer(token), json={
        "title": title, "body": "Five researched, ranked. My pick is #1.",
        "why": "The post goes out Friday.", "answer_by_hours": 30,
        "default_action": "Take the top-ranked candidate.", **extra})
    assert asked.status_code == 200, asked.text
    return asked.json()["ask_id"]


def _desk(client) -> dict:
    return cli_desk.fetch_desk(client)


def _row(desk: dict, project: dict) -> dict:
    return next(p for p in desk["projects"] if p["id"] == project["id"])


# ── the number is the server's ──────────────────────────────────────────────────


def test_every_decision_the_server_counts_is_a_row_and_nothing_else_is(chair):
    """The row builder is held to `policy.needs_you`, project by project. The
    day the server counts a new kind of decision, or this stops drawing one,
    the bar and the badge disagree — and this fails first."""
    project = make_project(chair, slug="counted")
    _approval(chair, project)
    _approval(chair, project, title="A second data pull", amount=15)
    _ask(chair, project)
    with db_session() as session:
        session.add(Hypothesis(id=ids.new_id(ids.HYPOTHESIS), project_id=project["id"],
                               statement="People pay for the weekly memo",
                               kill_condition="nobody pays by the kill date",
                               created_by_agent_id=project["ceo_agent_id"],
                               kill_date=utcnow() - timedelta(days=1), status="expired"))
        boardroom = session.query(Channel).filter_by(project_id=project["id"],
                                                     kind="boardroom").one()
        session.add(Message(id=ids.new_id(ids.MESSAGE), project_id=project["id"],
                            channel_id=boardroom.id, sender_agent_id=project["ceo_agent_id"],
                            body="looping", frozen_at=utcnow()))

    row = _row(_desk(chair), project)
    built = cli_desk.project_items(row)
    assert len(built) == row["needs_you"]["total"] == 5, \
        f"server counts {row['needs_you']}, the desk drew {[i.type for i in built]}"
    assert sorted(i.type for i in built) == ["approval", "approval", "ask", "bet", "frozen"]


def test_a_row_for_each_kind_on_a_synthetic_desk():
    """The kinds a short test cannot cheaply make real — scope, a verdict, a
    date, a sign-off — held to their member of the count all the same."""
    row = {
        "id": "proj_x", "name": "X", "status": "running",
        "expired_bets": [], "frozen_threads": 0, "pending_approvals": [], "pending_asks": [],
        "awaiting_you": {"plan": 2, "verdicts": [{"key": "R-01", "statement": "s", "name": "n"}],
                         "commitment": {"id": "cmt_1", "deliver_by": "2026-12-01T00:00:00+00:00",
                                        "scope_count": 3}},
        "pending_signoff": {"met": 3, "closes_at": None},
        "needs_you": {"approvals": 0, "asks": 0, "plan": 1, "verdicts": 1, "commitment": 1,
                      "signoff": 1, "bets": 0, "frozen": 0, "total": 4},
    }
    built = cli_desk.project_items(row)
    assert len(built) == row["needs_you"]["total"]
    assert {i.key for i in built} == {"proj_x-plan", "proj_x-verdict-R-01",
                                      "proj_x-commitment", "proj_x-signoff"}, \
        "the keys are the Inbox's own: a snooze or a link spelled differently misses"


def test_a_snoozed_row_leaves_the_bar_as_it_leaves_the_badge(chair):
    project = make_project(chair, slug="snoozing")
    approval_id = _approval(chair, project)
    _ask(chair, project)
    assert cli_desk.signal_view(_desk(chair))["count"] == 2

    item = next(i for i in cli_desk.items(_desk(chair)) if i.key == approval_id)
    cli_desk.snooze(chair, item, cli_desk.snooze_until(item, "hour"))
    view = cli_desk.signal_view(_desk(chair))
    assert view["count"] == 1, "a row the chairman said 'not now' to is not in the bar"
    assert "Buy a data pull" not in view["tooltip"]


def test_a_paused_project_asks_for_nothing_but_its_wall_lights_the_bar(chair):
    """Paused is a state, not a request — its rows leave the count, as on the
    badge. Paused at the hard stop is the one stopped thing only the chairman
    moves, so it lights the bar; any other pause does not."""
    project = make_project(chair, slug="walled")
    _approval(chair, project)
    with db_session() as session:
        row = session.get(Project, project["id"])
        row.status = ProjectStatus.PAUSED
        row.status_reason = "waiting on the client"
    quiet = cli_desk.signal_view(_desk(chair))
    assert quiet["count"] == 0 and quiet["class"] == "clear" and quiet["paused"] == 0

    with db_session() as session:
        session.get(Project, project["id"]).status_reason = \
            f"{HARD_STOP}: 61 USD this month against a budget of 20 and a wall at 60"
    loud = cli_desk.signal_view(_desk(chair))
    assert loud["class"] == "active" and loud["paused"] == 1 and "⏸ 1" in loud["text"]


def test_only_a_decision_deciding_itself_within_a_day_lights_the_bar(chair):
    project = make_project(chair, slug="clocked")
    approval_id = _approval(chair, project)
    with db_session() as session:
        session.get(Approval, approval_id).expires_at = utcnow() + timedelta(days=5)
    calm = cli_desk.signal_view(_desk(chair))
    assert calm["class"] == "waiting" and calm["text"] == "Y 1"

    with db_session() as session:
        session.get(Approval, approval_id).expires_at = utcnow() + timedelta(hours=3)
    assert cli_desk.signal_view(_desk(chair))["class"] == "active"


def test_nothing_waiting_is_the_mark_alone_never_an_empty_bar():
    """It used to be empty, which looked exactly like yaaf not running. The
    chairman asked for yaaf always in sight: the mark stays, quiet."""
    view = cli_desk.signal_view({"projects": [], "snoozed": []})
    assert view["text"] == "Y" and view["class"] == "clear" and view["count"] == 0
    assert "running" in view["tooltip"]


# ── the safeguards are the Inbox's ──────────────────────────────────────────────


def test_a_decline_without_a_reason_is_not_sent(chair):
    """The CEO re-plans from the reason. A bare no from a terminal would be the
    one door where declining is cheaper than it is in the browser."""
    project = make_project(chair, slug="why")
    approval_id = _approval(chair, project)
    item = next(i for i in cli_desk.items(_desk(chair)) if i.key == approval_id)
    with pytest.raises(cli_desk.Refused):
        cli_desk.decide(chair, item, approve=False, note="   ")
    with db_session() as session:
        assert session.get(Approval, approval_id).status == ApprovalStatus.PENDING

    cli_desk.decide(chair, item, approve=False, note="We have last year's data.")
    with db_session() as session:
        assert session.get(Approval, approval_id).status != ApprovalStatus.PENDING


def test_a_purchase_says_what_it_spends_before_it_is_approved(chair):
    project = make_project(chair, slug="spend")
    spend = _approval(chair, project, amount=180)
    legal = _approval(chair, project, kind="legal", title="Accept the data licence", amount=0)
    found = {i.key: i for i in cli_desk.items(_desk(chair))}
    assert "$180" in (cli_desk.purchase_warning(found[spend]) or ""), \
        "money that leaves cannot be called back, so that yes asks first"
    assert cli_desk.purchase_warning(found[legal]) is None


def test_a_decision_from_the_desk_is_named_in_the_record_as_the_cli(chair):
    """Same door as the dashboard (`/admin/approvals/{id}/decide`), and the
    record says a script's token carried it — never a click."""
    project = make_project(chair, slug="named")
    approval_id = _approval(chair, project)
    item = next(i for i in cli_desk.items(_desk(chair)) if i.key == approval_id)
    cli_desk.decide(chair, item, approve=True)
    with db_session() as session:
        assert session.get(Approval, approval_id).status == ApprovalStatus.APPROVED
        actors = {a.actor for a in session.query(AuditLog).filter_by(entity_id=approval_id)}
    assert any("token:suite" in (actor or "") for actor in actors), actors


def test_an_answer_is_a_line_in_the_boardroom(chair):
    """As from the Inbox: the CEO reads the boardroom, and a reply settles the
    questions asked before it — so the desk says so before it sends."""
    project = make_project(chair, slug="answering")
    first = _ask(chair, project, title="Which market first?")
    second = _ask(chair, project, title="Which price?", answer_by_hours=40)
    asks = [i for i in cli_desk.items(_desk(chair)) if i.type == "ask"]
    by_key = {i.key: i for i in asks}
    assert by_key[second].earlier_asks == 1 and by_key[first].earlier_asks == 0

    with pytest.raises(cli_desk.Refused):
        cli_desk.answer(chair, by_key[first], "")
    cli_desk.answer(chair, by_key[first], "Germany.")
    with db_session() as session:
        boardroom = session.query(Channel).filter_by(project_id=project["id"],
                                                     kind="boardroom").one()
        bodies = [m.body for m in session.query(Message).filter_by(channel_id=boardroom.id)]
    assert "Re: “Which market first?” — Germany." in bodies


def test_scope_and_sign_off_go_through_the_inboxs_own_doors():
    """Accepting scope accepts each proposed requirement on its own route, and
    sign-off completes the project — the paths the dashboard posts to."""
    sent: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={})

    project = {"id": "proj_x", "name": "X",
               "scope_items": [{"id": "req_1", "key": "R-01", "name": "a"},
                               {"id": "req_2", "key": "R-02", "name": "b"}]}
    with httpx.Client(base_url="http://yaaf.test", transport=httpx.MockTransport(record)) as c:
        scope = next(i for i in cli_desk.project_items(
            {**project, "awaiting_you": {"plan": 2}}) if i.type == "scope")
        assert cli_desk.accept_scope(c, scope) == ["R-01", "R-02"]
        signoff = next(i for i in cli_desk.project_items(
            {**project, "pending_signoff": {"met": 2, "closes_at": None}}))
        cli_desk.complete(c, signoff)
        approval = cli_desk.Item(key="apr_1", type="approval", tier=2, at=0, clocked=True,
                                 project=project, kind="k", title="t", consequence="c")
        with pytest.raises(cli_desk.Refused):
            cli_desk.complete(c, approval)
    assert sent == ["POST /admin/requirements/req_1/accept",
                    "POST /admin/requirements/req_2/accept",
                    "POST /admin/projects/proj_x/complete"]


def test_a_snooze_never_runs_past_the_moment_the_row_decides_itself():
    now = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    soon = cli_desk.Item(key="apr_1", type="approval", tier=2,
                         at=(now + timedelta(hours=5)).timestamp(), clocked=True,
                         project={"id": "p"}, kind="k", title="t", consequence="c")
    assert cli_desk.snooze_until(soon, "morning", now) <= now + timedelta(hours=3)
    almost = cli_desk.Item(**{**soon.__dict__, "at": (now + timedelta(hours=2)).timestamp()})
    with pytest.raises(cli_desk.Refused):
        cli_desk.snooze_until(almost, "hour", now)


def test_the_link_opens_the_row_and_decides_nothing():
    item = cli_desk.Item(key="proj_x-plan", type="scope", tier=1, at=0, clocked=False,
                         project={"id": "proj_x"}, kind="k", title="t", consequence="c")
    url = cli_desk.inbox_url("http://localhost:24247", item)
    assert url == "http://localhost:24247/app/#/inbox?item=proj_x-plan"
    assert "/admin/" not in url


# ── the bar never breaks ────────────────────────────────────────────────────────


def test_a_bar_gets_one_line_of_json_even_when_yaaf_is_down(monkeypatch):
    """A status bar reads the last line a command prints and shows nothing on a
    crash. Down or signed out, the module still gets valid JSON saying which;
    only `--format count`, which a script reads, fails loudly."""
    monkeypatch.setenv("YAAF_API", "http://127.0.0.1:9")
    runner = CliRunner()
    shown = runner.invoke(cli.app, ["signal", "--format", "waybar"])
    assert shown.exit_code == 0
    view = json.loads(shown.output.strip().splitlines()[-1])
    assert view["class"] == "offline" and view["text"] == cli_desk.OFFLINE_MARK, \
        "down is drawn — hollow — not hidden: hidden looked like all clear"
    assert runner.invoke(cli.app, ["signal", "--format", "count"]).exit_code == 1
