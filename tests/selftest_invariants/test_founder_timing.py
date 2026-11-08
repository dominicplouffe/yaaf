"""Where a Founder turn's minutes go, measured rather than guessed.

The step list on the page starts a step when a tool runs — after the model has
finished writing that tool's input — so the slow part of a turn sat in the gaps,
unlabelled. The attacks on the measurement:

  * every model call is counted once, with its tokens and the tools it asked
    for — the CLI sends one reply as several messages, and counting each would
    double every number
  * a tool that runs a model of its own (the goal grade) reports that model
    inside the tool, not as the Founder's own calls
  * both roads are measured: the CLI's stream and a provider's own wire
  * the breakdown is logged for every turn and kept on the turn, so a slow
    turn can be read after it happened
"""

from __future__ import annotations

import asyncio
import logging
import time

import claude_agent_sdk as sdk
import httpx

from tests.conftest import ADMIN, seed_role
from tests.selftest_invariants import test_founder, test_founder_cli
from tests.selftest_invariants.test_founder_providers import DRAFT, PLATFORM, _settle
from yaaf.founder import session, timing
from yaaf.runtimes import wire

# The page's own Founder stub and the terminal's, for the CLI's view of it.
founder = test_founder.founder
terminal = test_founder_cli.terminal

USAGE = {"input_tokens": 1200, "cache_read_input_tokens": 9000,
         "cache_creation_input_tokens": 0, "output_tokens": 3400}


def test_the_cli_stream_is_timed_per_call_not_per_message(monkeypatch):
    def stream(prompt, options):
        async def messages():
            yield sdk.SystemMessage(subtype="init", data={})
            # One reply, sent as two messages sharing an id and a usage.
            yield sdk.AssistantMessage(content=[sdk.ThinkingBlock("x" * 400, "sig")],
                                       model="m", message_id="m1", usage=USAGE)
            await asyncio.sleep(0.05)
            yield sdk.AssistantMessage(
                content=[sdk.ToolUseBlock("t1", "mcp__founder__propose_bundle", {})],
                model="m", message_id="m1", usage=USAGE)
            yield sdk.AssistantMessage(content=[sdk.TextBlock("Explore it is.")],
                                       model="m", message_id="m2",
                                       usage={**USAGE, "output_tokens": 60})
            yield sdk.ResultMessage(subtype="success", duration_ms=5000,
                                    duration_api_ms=4200, is_error=False, num_turns=2,
                                    session_id="s")
        return messages()

    monkeypatch.setattr(sdk, "query", stream)
    with timing.turn() as clock:
        said = asyncio.run(session.run("draft it", None))

    assert said == "Explore it is."
    out = clock.as_dict()
    assert len(out["calls"]) == 2, "one reply in two messages is one call"
    first, second = out["calls"]
    assert first["tools"] == ["mcp__founder__propose_bundle"]
    assert first["thinking_chars"] == 400
    assert first["seconds"] >= 0.05
    assert out["tokens"]["output"] == 3400 + 60
    assert out["tokens"]["cache_read"] == 18000
    assert out["api_seconds"] == 4.2
    assert out["cli_start_seconds"] is not None
    assert "slowest calls" in clock.line()


def test_a_tool_that_runs_a_model_reports_it_inside_the_tool():
    with timing.turn() as clock, timing.tool("grade_goal"):
        timing.current().reply(2.0, wire.Usage(input=900, output=700))

    out = clock.as_dict()
    assert out["calls"] == [], "the grade's call is not the Founder's own"
    (grade,) = out["tools"]
    assert grade["name"] == "grade_goal"
    assert grade["model"]["calls"][0]["output"] == 700
    assert out["tokens"]["output"] == 700, "but it counts toward the turn's total"


def test_outside_a_turn_nothing_is_recorded():
    assert timing.current() is None
    with timing.tool("search_roles") as run:
        assert run is None


def test_a_turn_says_where_its_time_went_and_keeps_it(client, monkeypatch, caplog):
    """End to end on a provider's own wire: the calls with their tokens, the
    tool each asked for, the tool's own run, the log line, and the same
    breakdown on the stored turn."""
    client.post("/admin/users", headers=ADMIN, json={
        "email": "chair@example.com", "display_name": "Chair", "is_admin": True})
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")
    assert client.put(PLATFORM, headers=ADMIN, json={
        "provider": "google", "value": "AIza-platform-gemini-key"}).status_code == 200
    replies = [[{"functionCall": {"name": "propose_bundle", "args": {"project": DRAFT}},
                 "thoughtSignature": "c2lnbmVk"}],
               [{"text": "Explore, because you are still choosing what to build."}]]
    sent = []

    def gemini(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        time.sleep(0.1)  # a model that takes a moment to answer
        return httpx.Response(200, json={
            "candidates": [{"content": {"role": "model", "parts": replies[len(sent) - 1]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}})

    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(gemini))
    with caplog.at_level(logging.INFO, logger="yaaf.founder"):
        thread = _settle(client, "I want to find an idea and sell it.")

    assert thread["last_error"] is None, thread
    kept = thread["turns"][-1]["timing"]
    assert [c["tools"] for c in kept["calls"]] == [["propose_bundle"], []]
    assert kept["tokens"]["output"] == 10
    assert kept["model_seconds"] >= 0.2
    assert [t["name"] for t in kept["tools"]] == ["propose_bundle"]
    assert kept["seconds"] >= kept["model_seconds"]
    logged = [r.getMessage() for r in caplog.records if "founder turn" in r.getMessage()]
    assert len(logged) == 1 and "propose_bundle" in logged[0]
    assert thread["id"] in logged[0]


def test_the_terminal_shows_the_breakdown_when_asked(client, founder, terminal):
    from typer.testing import CliRunner

    from tests.selftest_invariants.test_founder import PROJECT
    from yaaf.cli import main as cli

    founder(reply="Explore.", tool_calls=[("search_roles", {}),
                                          ("propose_bundle", {"project": PROJECT})])
    shown = CliRunner().invoke(cli.app, ["create-project", "Sell something.", "--timing"],
                               input="q\n")
    assert shown.exit_code == 0, shown.output
    assert "[founder turn" in shown.output
    assert "propose_bundle" in shown.output and "search_roles" in shown.output

    quiet = CliRunner().invoke(cli.app, ["create-project"], input="q\n")
    assert "[founder turn" not in quiet.output, "only when asked"
