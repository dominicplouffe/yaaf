"""The Founder answers in seconds, not minutes, and loses nothing for it.

Measured on 2026-11-13 on one brief, a first turn took 160-180s and the
chairman waited 84-97s for a question. None of it was the model being slow:

  * it wrote the goal out for the grader and waited on the grade before it
    asked anything (~60s);
  * the draft tool accepted `{"project": dict}`, so it guessed at a team's
    shape and was refused twice a turn, rewriting the whole draft each time;
  * its first calls fetched the credentials and the libraries, facts the
    install could simply have handed over.

These pin the fixes, each by the thing it must not give up:

  * the install arrives with the turn, so the first call can be the questions;
  * the draft tool shows the model a team, so it is not guessed;
  * every accepted draft is still graded — by the platform, beside the turn —
    and the verdict still reaches the Founder, at the start of the next turn;
  * the grade never holds the turn, and a verdict on a goal the draft no
    longer carries is never shown as if it were about this one;
  * the breakdown counts what the model really wrote, thinking included.
"""

from __future__ import annotations

import threading
import time

from tests.conftest import ADMIN
from tests.selftest_invariants import test_founder
from tests.selftest_invariants.test_founder import PROJECT, start
from yaaf.founder import timing

founder = test_founder.founder


def _graded(thread_id: str, timeout: float = 5.0) -> dict | None:
    from yaaf.core.db import db_session
    from yaaf.core.models import FoundingThread

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with db_session() as session:
            grade = session.get(FoundingThread, thread_id).goal_grade
        if grade:
            return grade
        time.sleep(0.05)
    return None


def test_the_install_arrives_with_the_turn(client, founder):
    """The credentials, roles and crews are in the prompt, so the Founder's
    first call can be the questions rather than three lookups."""
    calls = founder(tool_calls=[("propose_bundle", {"project": PROJECT})])
    start(client, "I want to find an idea and sell it.")
    prompt = calls[0]["prompt"]

    assert prompt.startswith("<install>")
    assert '"slug": "worker"' in prompt
    assert '"credentials"' in prompt


def test_the_founder_runs_at_low_effort(client, founder):
    calls = founder(tool_calls=[("propose_bundle", {"project": PROJECT})])
    start(client, "I want to find an idea and sell it.")
    assert calls[0]["opts"].effort == "low"


def test_the_draft_tool_shows_the_model_a_team():
    """The two shapes the model guessed wrong on every brief, spelled out."""
    from yaaf.core.db import db_session
    from yaaf.founder import tools

    with db_session() as session:
        built = tools.build_server(session, {}, {}, "claude-opus-5-5")
    schema = next(s for name, _, s, _ in built.specs if name == "propose_bundle")
    team = schema["properties"]["project"]["properties"]["teams"]["items"]

    assert "role_slug" in team["required"]
    assert team["properties"]["employees"]["items"] == {"type": "string"}


def test_every_accepted_draft_is_graded_and_the_verdict_opens_the_next_turn(
        client, founder):
    calls = founder(tool_calls=[("propose_bundle", {"project": PROJECT})])
    thread = start(client, "I want to find an idea and sell it.")

    grade = _graded(thread["id"])
    assert grade is not None, "the draft was never graded"
    assert grade["goal"] == PROJECT["goal"]

    founder(reply="Revised.")
    start(client, "Sounds right.", thread["id"])
    prompt = next(c["prompt"] for c in reversed(calls) if "prompt" in c)
    assert "<grade-of-current-draft>" in prompt


def test_the_grade_never_holds_the_turn(client, founder, monkeypatch):
    """The draft reaches the page while its grade is still being made."""
    release = threading.Event()

    async def slow_grade(goal, *, env, model):
        import anyio

        from yaaf.founder.grade import GoalGrade

        await anyio.to_thread.run_sync(release.wait, 10)
        return GoalGrade(elements=[], summary="late")

    monkeypatch.setattr("yaaf.founder.grade.grade_goal", slow_grade)
    founder(tool_calls=[("propose_bundle", {"project": PROJECT})])
    try:
        thread = start(client, "I want to find an idea and sell it.")
        assert thread["draft_bundle"]["project"]["slug"] == "sell-something"
        assert _graded(thread["id"], timeout=0.2) is None
    finally:
        release.set()
    assert _graded(thread["id"])["summary"] == "late"


def test_a_verdict_on_a_goal_the_draft_no_longer_carries_is_not_shown():
    from yaaf.core.models import FoundingThread
    from yaaf.founder import agent

    thread = FoundingThread(
        transcript=[], draft_bundle={"project": {"goal": "The goal as it is now."}},
        goal_grade={"goal": "The goal as it was.", "ready": False, "elements": []})
    assert agent.last_grade(thread) is None
    thread.goal_grade = {**thread.goal_grade, "goal": "The goal as it is now."}
    assert agent.last_grade(thread) is not None


def test_the_breakdown_counts_what_the_model_really_wrote():
    """Each message carries the usage of the moment its reply began — a handful
    of tokens. The run's closing usage is the true count, thinking included."""
    clock = timing.Turn()
    opening = {"input_tokens": 2, "output_tokens": 8}
    clock.model_message("m1", opening, tools=["mcp__founder__ask"])
    clock.model_message("m2", opening, tools=["mcp__founder__propose_bundle"])
    clock.result(30_000, {"output_tokens": 7095,
                          "output_tokens_details": {"thinking_tokens": 2072},
                          "iterations": [{"output_tokens": 1621}]})

    tokens = clock.as_dict()["tokens"]
    assert tokens["output"] == 7095
    assert tokens["thinking"] == 2072
    assert "2.1k thinking" in clock.line()


def test_closing_usage_per_request_lands_on_its_call():
    clock = timing.Turn()
    clock.model_message("m1", {"output_tokens": 8}, tools=["mcp__founder__ask"])
    clock.model_message("m2", {"output_tokens": 8}, tools=[])
    clock.result(None, {"output_tokens": 3000, "iterations": [
        {"output_tokens": 2500, "output_tokens_details": {"thinking_tokens": 400}},
        {"output_tokens": 500}]})

    calls = clock.as_dict()["calls"]
    assert [c["output"] for c in calls] == [2500, 500]
    assert calls[0]["thinking"] == 400


def test_a_new_thread_starts_clean(client, founder):
    """Nothing about grading leaks into a thread before it has a draft."""
    founder(reply="Tell me more.")
    thread = start(client, "Hello.")
    assert _graded(thread["id"], timeout=0.2) is None
    assert client.get(f"/admin/founder/threads/{thread['id']}",
                      headers=ADMIN).status_code == 200
