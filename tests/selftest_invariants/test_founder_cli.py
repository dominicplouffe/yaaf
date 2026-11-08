"""`yaaf create-project`: the Founder conversation, from a terminal.

The dashboard's "Describe it" drafts a project from a brief and then asks about
what it guessed. The CLI holds the same conversation through the same endpoints,
so the attacks are the ones that would make the two diverge:

  * a draft the terminal creates is the draft the Founder wrote, and its CEO is
    briefed with the conversation — not asked for it again
  * a round answered at the prompt reads exactly as a round answered by clicks:
    Enter agrees, a letter picks, words are words, and "who pays" is never
    picked for you
  * the round is asked the moment the Founder asks it, not after the draft —
    and answers given mid-turn go in once, when the turn can take them
  * leaving keeps the conversation; no brief picks it back up
  * a file path is never sent to the Founder as a brief: that is what this
    command took before the conversation became its default
"""

from __future__ import annotations

import contextlib
import threading

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from tests.conftest import ADMIN
from tests.selftest_invariants import test_founder as founding
from tests.selftest_invariants.test_founder import PROJECT, ROUND, start
from yaaf.cli import main as cli

runner = CliRunner()
# The page's own Founder stub — a scripted turn behind the real endpoints — so
# the terminal and the page are held to one conversation.
founder = founding.founder


@pytest.fixture()
def terminal(client, monkeypatch):
    as_admin = TestClient(client.app, headers=ADMIN)
    monkeypatch.setattr(cli, "_client", lambda: contextlib.nullcontext(as_admin))
    monkeypatch.setattr(cli, "_FOUNDER_POLL_SECONDS", 0.02)
    return as_admin


def test_a_brief_becomes_the_project_the_founder_drafted(client, founder, terminal):
    founder(reply="Explore, because you are still choosing what to build.",
            tool_calls=[("propose_bundle", {"project": PROJECT})])

    result = runner.invoke(cli.app, ["create-project", "I want to find an idea and sell it."],
                           input="c\nn\n")

    assert result.exit_code == 0, result.output
    assert "Explore, because you are still choosing" in result.output
    assert "Draft: Sell Something (sell-something) — explore" in result.output
    assert "Created sell-something" in result.output
    assert "yaaf start sell-something" in result.output
    # The thread became that project, and its CEO was handed the conversation.
    threads = client.get("/admin/founder/threads", headers=ADMIN).json()["threads"]
    assert threads[0]["created_project_id"]
    assert "briefing in the boardroom" in result.output


def test_a_round_is_answered_at_the_prompt_as_the_page_answers_it(client, founder, terminal):
    founder(tool_calls=[("ask", ROUND), ("propose_bundle", {"project": PROJECT})])
    thread = start(client, "Audit my PPC account.")
    founder(reply="Operate it is.")

    # Enter on the first (agree), b on the second, words on the third, a note,
    # then leave.
    result = runner.invoke(cli.app, ["create-project"],
                           input="\nb\nthe side key\nKeep it cheap.\nq\n")

    assert result.exit_code == 0, result.output
    assert "Picking up" in result.output, "no brief means the open conversation"
    assert "(recommended)" in result.output
    assert "Operate it is." in result.output
    state = client.get(f"/admin/founder/threads/{thread['id']}", headers=ADMIN).json()
    said = state["turns"][-2]
    assert said["answers"] == [
        {"id": "reader", "choice": 0, "text": None},
        {"id": "shape", "choice": 1, "text": None},
        {"id": "pays", "choice": None, "text": "the side key"},
    ]
    assert "Keep it cheap." in said["text"]
    assert state["created_project_id"] is None, "quitting keeps the draft, founds nothing"


def test_who_pays_is_left_open_by_enter_not_picked(client, founder, terminal):
    founder(tool_calls=[("ask", ROUND), ("propose_bundle", {"project": PROJECT})])
    thread = start(client, "Audit my PPC account.")
    founder(reply="Noted.")

    result = runner.invoke(cli.app, ["create-project"], input="\n\n\n\nq\n")

    assert result.exit_code == 0, result.output
    state = client.get(f"/admin/founder/threads/{thread['id']}", headers=ADMIN).json()
    assert state["turns"][-2]["answers"][2] == {"id": "pays", "choice": None, "text": None}


def test_without_a_founder_key_it_says_who_sets_one(client, terminal):
    result = runner.invoke(cli.app, ["create-project", "I want to find an idea and sell it."])

    assert result.exit_code == 1
    assert "yaaf set-platform-key" in result.output
    assert "Traceback" not in result.output


def test_the_round_is_asked_mid_turn_and_answered_once(client, founder, terminal, monkeypatch):
    """The draft takes minutes; the questions are reading time. The page shows a
    round the moment `ask` is called and holds the answers until the turn ends —
    the server takes no message while one runs — and so does the terminal."""
    gate = threading.Event()
    founder(blocks_after=[("ask", ROUND)], blocks_on=gate,
            tool_calls=[("propose_bundle", {"project": PROJECT})])
    real = cli._answer_round
    asked_while_running = []

    def answer_then_let_the_draft_finish(asked):
        thread = client.get("/admin/founder/threads", headers=ADMIN).json()["threads"][0]
        live = client.get(f"/admin/founder/threads/{thread['id']}", headers=ADMIN).json()
        asked_while_running.append(live["running"])
        payload = real(asked)
        # The next turn is a plain reply, then the held turn is let go.
        founder(reply="Operate it is.")
        gate.set()
        return payload

    monkeypatch.setattr(cli, "_answer_round", answer_then_let_the_draft_finish)
    try:
        result = runner.invoke(cli.app, ["create-project", "Audit my PPC account."],
                               input="\nb\nthe side key\nKeep it cheap.\nq\n")
    finally:
        gate.set()

    assert result.exit_code == 0, result.output
    assert asked_while_running == [True], "asked once, while the draft was still being written"
    assert "go in as soon as the draft is done" in result.output
    assert "Operate it is." in result.output
    thread = client.get("/admin/founder/threads", headers=ADMIN).json()["threads"][0]
    said = client.get(f"/admin/founder/threads/{thread['id']}",
                      headers=ADMIN).json()["turns"][-2]
    assert said["answers"] == [
        {"id": "reader", "choice": 0, "text": None},
        {"id": "shape", "choice": 1, "text": None},
        {"id": "pays", "choice": None, "text": "the side key"},
    ]


def test_a_file_is_not_a_brief(client, founder, terminal, tmp_path):
    """`create-project SPEC` was the file command. Sent to the Founder, the path
    would be a paid turn drafting a project about a filename."""
    spec = tmp_path / "spec.json"
    spec.write_text("{}")

    result = runner.invoke(cli.app, ["create-project", str(spec)])

    assert result.exit_code == 2
    assert f"yaaf create-project --from-file {spec}" in result.output
    assert client.get("/admin/founder/threads", headers=ADMIN).json()["threads"] == []

    both = runner.invoke(cli.app, ["create-project", "a brief", "--from-file", str(spec)])
    assert both.exit_code == 2
