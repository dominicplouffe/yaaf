"""Invariant selftests: in the talk lane, the CEO acts only on an order.

26 November 2026, the dplouffe.ca blog. He vented that the hero images were
ugly and asked how to fix it; the CEO drew new ones on a paid key and uploaded
one to his live site. He wrote "you may not change anything"; it drafted a new
brief. The 24 November rule had read his first message right and barred only
the brief. Now his newest line is read for what it is (yaaf.server.intent), and
while it is not an order or an approval, the talk lane can answer him and
nothing else — at the API, and in the sandbox for scripts and file writes.
Each test attacks it as the CEO would: with its own run token, over HTTP.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import ADMIN, bearer, channel_of, live_token, make_project
from tests.selftest_invariants.test_chat_lane import _chat_entry
from tests.selftest_invariants.test_independent_read import OBSERVABLE
from yaaf.core.db import db_session
from yaaf.core.models import LedgerEntry, Message
from yaaf.server import intent

#: Verbatim, as he wrote them that morning.
VENT = ("all the hero images, for today's post and for all future posts are bad, ugly and "
        "did not follow our orders.\n\nNow tell me honestly, how should we correct your "
        "error, if we have no runs left?")
HOLD = "you may not change anything, let's get the fucking shit right before optimizing"
ORDER = "Publish the Friday post now."


def _says(client, project: dict, body: str) -> str:
    response = client.post(f"/admin/projects/{project['id']}/boardroom",
                           json={"body": body}, headers=ADMIN)
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _make_task(client, project: dict, token: str):
    team = project["teams"][0]
    return client.post("/agent/tasks", headers=bearer(token), json={
        "team_id": team["id"], "title": "Redraw every hero image",
        "goal": "Five new hero images, uploaded.", "success_test": OBSERVABLE})


def _chat_token(project: dict, kind: str = "chat_session") -> str:
    _, token = live_token(project["id"], project["ceo_agent_id"], kind=kind)
    return token


# ── the talk lane waits for an order ───────────────────────────────────────────


@pytest.mark.parametrize("said", [VENT, HOLD])
def test_after_venting_or_a_hold_the_talk_lane_changes_nothing(client, said):
    project = make_project(client)
    _says(client, project, said)
    token = _chat_token(project)

    refused = _make_task(client, project, token)
    assert refused.status_code == 409
    assert refused.json()["error"].startswith("Not done.")
    assert "is not an order" in refused.json()["error"]


def test_it_still_answers_him_in_the_boardroom_but_does_not_brief_the_team(client):
    project = make_project(client)
    _says(client, project, VENT)
    token = _chat_token(project)
    boardroom = channel_of(client, project["id"], "boardroom")
    team = channel_of(client, project["id"], "team")

    answered = client.post("/agent/messages", headers=bearer(token), json={
        "channel_id": boardroom, "body": "The heroes reuse one prop set. I can show why."})
    assert answered.status_code == 200, answered.text
    told = client.post("/agent/messages", headers=bearer(token), json={
        "channel_id": team, "body": "Eric, redraw every hero tonight."})
    assert told.status_code == 409
    assert told.json()["error"].startswith("Not done. Telling the team")


def test_an_order_lets_it_act(client):
    project = make_project(client)
    _says(client, project, VENT)
    _says(client, project, ORDER)
    assert _make_task(client, project, _chat_token(project)).status_code == 200


def test_the_queued_reply_run_is_held_the_same_way(client):
    """The talk lane's other vehicle, when no session is live."""
    project = make_project(client)
    _says(client, project, HOLD)
    assert _make_task(client, project, _chat_token(project, "boardroom_run")).status_code == 409


def test_a_work_run_is_not_held_by_what_he_last_said_in_the_room(client):
    """A work run is doing what was already decided; a vent in the boardroom
    must not stall the day's post."""
    project = make_project(client)
    _says(client, project, VENT)
    assert _make_task(client, project, _chat_token(project, "agent_run")).status_code == 200


# ── his correction outranks the reading ────────────────────────────────────────


def test_one_click_from_him_turns_a_vent_into_an_order(client):
    project = make_project(client)
    message_id = _says(client, project, VENT)
    token = _chat_token(project)
    assert _make_task(client, project, token).status_code == 409

    corrected = client.post(
        f"/admin/projects/{project['id']}/boardroom/messages/{message_id}/read_as",
        json={"label": "order"}, headers=ADMIN)
    assert corrected.status_code == 200
    assert corrected.json() == {"label": "order", "confidence": 1.0, "by": "chairman",
                                "acts": True}
    assert _make_task(client, project, token).status_code == 200

    boardroom = channel_of(client, project["id"], "boardroom")
    rows = client.get(f"/observe/channels/{boardroom}/messages", headers=ADMIN).json()
    shown = next(m for m in rows if m["id"] == message_id)
    assert shown["read_as"] == {"label": "order", "confidence": 1.0, "by": "chairman",
                                "acts": True}


def test_a_correction_is_only_his_and_only_a_known_reading(client):
    project = make_project(client, slug="read-as-mine")
    other = make_project(client, slug="read-as-other")
    message_id = _says(client, project, VENT)
    url = f"/admin/projects/{project['id']}/boardroom/messages/{message_id}/read_as"
    assert client.post(url, json={"label": "go-ahead"}, headers=ADMIN).status_code == 422
    elsewhere = f"/admin/projects/{other['id']}/boardroom/messages/{message_id}/read_as"
    assert client.post(elsewhere, json={"label": "order"}, headers=ADMIN).status_code == 404
    _, ceo = live_token(project["id"], project["ceo_agent_id"], kind="chat_session")
    assert client.post(url, json={"label": "order"}, headers=bearer(ceo)).status_code in (401, 403)


# ── the reading itself ─────────────────────────────────────────────────────────


def test_the_word_rules_catch_the_hold_the_old_rule_read_as_leave_to_act():
    assert intent.rules(HOLD).label == "hold"
    assert intent.rules("Stay paused: nothing is spent and the draft stays as it is").label \
        == "hold"
    assert intent.rules("ok, got it").label == "info"
    assert intent.rules("why are we out of runs again!!").label == "question"
    assert intent.rules(ORDER).acts


def test_an_unsure_order_does_not_act(client, monkeypatch):
    project = make_project(client)
    monkeypatch.setattr(intent, "READER",
                        lambda *a: (intent.Reading("order", 0.55, "test/fast"), None))
    _says(client, project, "maybe redo the pictures")
    assert _make_task(client, project, _chat_token(project)).status_code == 409


def test_a_failed_reading_does_not_act(client, monkeypatch):
    project = make_project(client)

    def broken(*a):
        raise TimeoutError("the fast model did not answer")

    monkeypatch.setattr(intent, "READER", broken)
    _says(client, project, ORDER)
    refused = _make_task(client, project, _chat_token(project))
    assert refused.status_code == 409


def test_a_line_is_read_once_and_the_reading_is_paid_for(client, monkeypatch):
    project = make_project(client)
    calls = []

    class Usage:
        input, output, cache_read, cost_usd = 300, 12, 0, 0.0002

    def reader(session, project_row, body, ceo_before):
        calls.append(body)
        return intent.Reading("venting", 0.9, "deepseek/deepseek-v4-flash"), Usage()

    monkeypatch.setattr(intent, "READER", reader)
    message_id = _says(client, project, VENT)
    token = _chat_token(project)
    for _ in range(3):
        assert _make_task(client, project, token).status_code == 409
    assert calls == [VENT]
    with db_session() as session:
        row = session.get(Message, message_id)
        assert (row.read_as, row.read_by) == ("venting", "deepseek/deepseek-v4-flash")
        booked = session.query(LedgerEntry).filter_by(project_id=project["id"]).all()
        assert any("read the chairman's message" in (e.note or "") and e.usd == 0.0002
                   for e in booked)


def test_the_session_is_told_how_his_line_was_read(client):
    project = make_project(client)
    _says(client, project, HOLD)
    token = _chat_token(project)
    boardroom = channel_of(client, project["id"], "boardroom")
    rows = client.get("/agent/messages", headers=bearer(token),
                      params={"channel_id": boardroom}).json()
    newest = [m for m in rows if m["from_user"]][-1]
    assert newest["read_as"]["label"] == "hold" and newest["read_as"]["acts"] is False


# ── the sandbox half: scripts and file writes ──────────────────────────────────


def test_a_turn_that_only_talks_refuses_scripts_under_yaafs_loop(monkeypatch):
    chat_entry = _chat_entry(monkeypatch)
    base = chat_entry.base
    held = chat_entry._held([{"id": "m1", "from_user": "u1",
                              "read_as": {"label": "venting", "acts": False}}])
    assert held and "THIS TURN ONLY TALKS" in held
    assert chat_entry._held([{"id": "m1", "from_user": "u1",
                              "read_as": {"label": "order", "acts": True}}]) is None
    # A line that arrives unread is not leave to act.
    assert chat_entry._held([{"id": "m1", "from_user": "u1"}])

    with chat_entry._talk_only(held):
        assert base.talk_only_refusal("Bash") == held
        assert base.talk_only_refusal("Write") == held
        assert base.talk_only_refusal("run_shell_command") == held
        assert base.talk_only_refusal("Read") is None
        assert base.talk_only_refusal("mcp__yaaf__message_post") is None
        hook = asyncio.run(base._refuse_while_talking({"tool_name": "Bash"}, "t1", None))
        assert hook["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert base.TALK_ONLY is None and base.talk_only_refusal("Bash") is None


def test_the_cli_harnesses_lose_the_grant_for_the_turn(monkeypatch):
    chat_entry = _chat_entry(monkeypatch)
    monkeypatch.setattr(chat_entry, "CHAT_ENTRY", "codex_cli_entry.py")
    chat_entry.base.PERMS = {"bash": True, "files": True, "web": True}
    with chat_entry._talk_only("THIS TURN ONLY TALKS"):
        assert chat_entry.base.PERMS["bash"] is False
        assert chat_entry.base.PERMS["files"] is False
        assert chat_entry.base.PERMS["web"] is True
    assert chat_entry.base.PERMS == {"bash": True, "files": True, "web": True}


def test_yaafs_loop_refuses_the_script_when_it_runs(monkeypatch):
    _chat_entry(monkeypatch)
    import sys

    from yaaf.runner import loop_harness

    base = sys.modules["sandbox_entry"]
    monkeypatch.setattr(loop_harness, "base", base)
    driver = loop_harness.LoopDriver.__new__(loop_harness.LoopDriver)
    ran = []

    async def handler(args):
        ran.append(args)
        return {"content": [{"type": "text", "text": "drew 5 pictures"}]}

    driver.tools = {"Bash": base.Tool("Bash", "run", {}, handler)}
    monkeypatch.setattr(base, "TALK_ONLY", "THIS TURN ONLY TALKS: he asked you something.")
    out = asyncio.run(driver._run(loop_harness.ToolCall("c1", "Bash", {"command": "draw"})))
    assert out.is_error and "ONLY TALKS" in out.text and ran == []
