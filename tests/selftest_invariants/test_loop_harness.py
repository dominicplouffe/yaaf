"""Invariant selftests: the loop family's half of the harness contract
(docs/RUNTIMES.md, M2) — `anthropic_api`, yaaf's own agent loop on the Messages
API, held to what test_harness_contract.py holds the Claude CLI harnesses to.

The model is a fake HTTP server speaking the API's own streaming protocol into
the real `anthropic` SDK, so what is tested is the whole client: request built,
stream parsed by the SDK, errors raised as the SDK raises them. The replies are
synthetic — built from the documented event shapes, since a recorded success
costs money — except the dead key, recorded from the live API with an invalid
key (tests/fixtures/harnesses/anthropic_api/). Offline and free.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import pathlib
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("claude_agent_sdk")
httpx2 = pytest.importorskip("httpx2")
pytest.importorskip("anthropic")

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "harnesses" / "anthropic_api"

#: Claude Sonnet 5.5's catalog row, as the daemon hands it to the sandbox.
PRICE = {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write_5m": 2.5,
         "cache_write_1h": 4.0, "web_search": 0.01, "as_of": "2026-10-12"}
FLAGS = {"effort": True, "web_tools": "20260209"}


# ── the fake API ────────────────────────────────────────────────────────────────


def _usage(inp=2, out=1, read=535, write=1987, write_1h=0, searches=0) -> dict:
    usage = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": read,
             "cache_creation_input_tokens": write,
             "cache_creation": {"ephemeral_5m_input_tokens": write - write_1h,
                                "ephemeral_1h_input_tokens": write_1h}}
    if searches:
        usage["server_tool_use"] = {"web_search_requests": searches, "web_fetch_requests": 0}
    return usage


def _reply(blocks: list[dict], stop: str = "end_turn", *, start: dict | None = None,
           out: int = 13, stop_details: dict | None = None, searches: int = 0) -> list[dict]:
    """One streamed message as the API sends it: tool inputs arrive as JSON
    deltas after an empty start, text as text deltas."""
    events = [{"type": "message_start", "message": {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": start or _usage()}}]
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            events += [{"type": "content_block_start", "index": index,
                        "content_block": {"type": "text", "text": ""}},
                       {"type": "content_block_delta", "index": index,
                        "delta": {"type": "text_delta", "text": block["text"]}}]
        else:
            events += [{"type": "content_block_start", "index": index,
                        "content_block": {**block, "input": {}}},
                       {"type": "content_block_delta", "index": index,
                        "delta": {"type": "input_json_delta",
                                  "partial_json": json.dumps(block["input"])}}]
        events.append({"type": "content_block_stop", "index": index})
    final_usage = {"output_tokens": out}
    if searches:
        final_usage["server_tool_use"] = {"web_search_requests": searches,
                                          "web_fetch_requests": 0}
    events += [{"type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None,
                          **({"stop_details": stop_details} if stop_details else {})},
                "usage": final_usage},
               {"type": "message_stop"}]
    return events


TEXT = _reply([{"type": "text", "text": "Hi! What are we working on today?"}])


def _sse(events: list[dict]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _respond(step: dict):
    if "status" in step:
        return httpx2.Response(step["status"], json=step["body"])
    if step.get("connect_error"):
        raise httpx2.ConnectError("connection refused")
    body = _sse(step["sse"])
    if step.get("then_error"):
        body += _sse([{"type": "error", "error": step["then_error"]}])
    if step.get("drop"):
        async def dropped():
            yield body
            raise httpx2.ReadError("stream ended early")
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               content=dropped())
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)


# ── the sandbox, staged ─────────────────────────────────────────────────────────


def _staged(monkeypatch, tmp_path, perms: dict | None = None) -> SimpleNamespace:
    for name, value in {"YAAF_API_BASE_URL": "http://api:8080", "YAAF_API_TOKEN": "test-token",
                        "YAAF_RUN_ID": "run_test",
                        "YAAF_TOOL_PERMISSIONS": json.dumps(perms or {})}.items():
        monkeypatch.setenv(name, value)
    import yaaf.runner.sandbox_entry as entry_module

    base = importlib.reload(entry_module)
    # The image lays the entrypoints out in one directory, so each imports its
    # siblings by bare name.
    monkeypatch.setitem(sys.modules, "sandbox_entry", base)
    import yaaf.runner.loop_harness as loop_module

    loop = importlib.reload(loop_module)
    monkeypatch.setitem(sys.modules, "loop_harness", loop)
    import yaaf.runner.loop_entry as api_module

    api = importlib.reload(api_module)
    workspace = tmp_path / "workspace"
    staging = workspace / ".yaaf"
    staging.mkdir(parents=True)
    monkeypatch.setattr(base, "WORKSPACE", workspace)
    monkeypatch.setattr(base, "STAGING", staging)
    monkeypatch.setattr(base, "CREDENTIAL_DIR", tmp_path / "credentials")
    monkeypatch.setattr(base, "RESUME_BACKOFF_S", (0.0, 0.0))
    monkeypatch.setattr(base, "PRICE", PRICE)
    monkeypatch.setattr(base, "MODEL_FLAGS", FLAGS)
    (staging / "system_prompt-run_test.txt").write_text("You are the CEO.")
    (staging / "context-run_test.json").write_text("{}")
    return SimpleNamespace(base=base, loop=loop, api=api, staging=staging,
                           workspace=workspace, mp=monkeypatch, requests=[])


@pytest.fixture()
def stage(monkeypatch, tmp_path):
    return lambda perms=None: _staged(monkeypatch, tmp_path, perms)


@pytest.fixture()
def run(stage):
    return stage()


def _drive(r, script: list[dict]) -> tuple[int, dict]:
    """The production entry (`make_driver`), its client wired to the fake API
    and its own retries off — the run's resume policy is what is under test."""
    def handler(request):
        r.requests.append(json.loads(request.content))
        return _respond(script[len(r.requests) - 1])

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    class Wired(r.api.AnthropicClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, api_key="sk-test", http_client=http, max_retries=0)

    r.mp.setattr(r.api, "AnthropicClient", Wired)
    code = asyncio.run(r.base.run_harness(r.api.make_driver))
    return code, json.loads((r.staging / "result-run_test.json").read_text())


def _priced(**usage) -> float:
    rate = PRICE
    return (usage.get("inp", 0) * rate["input"] + usage.get("out", 0) * rate["output"]
            + usage.get("read", 0) * rate["cache_read"]
            + usage.get("write", 0) * rate["cache_write_5m"]) / 1_000_000


# ── 1. usage parses to the bill, and the bill is the price ──────────────────────


def test_a_reply_is_metered_and_priced_from_the_catalog(run):
    code, result = _drive(run, [{"sse": TEXT}])
    assert code == 0 and result["ok"] is True and result["error_kind"] is None
    assert (result["tokens_in"], result["tokens_out"]) == (2, 13)
    assert (result["cache_creation_tokens"], result["cache_read_tokens"]) == (1987, 535)
    expected = _priced(inp=2, out=13, read=535, write=1987)
    # Nobody else reports this harness's bill: what it is priced at is what it costs.
    assert result["cost_usd"] == pytest.approx(expected)
    assert result["priced_usd"] == pytest.approx(expected)
    assert "Hi! What are we working on today?" in result["summary"]


def test_the_request_caches_the_prefix_and_leaves_thinking_to_the_model(run):
    _drive(run, [{"sse": TEXT}])
    request = run.requests[0]
    assert request["model"] == run.base.MODEL
    assert request["system"] == [{"type": "text", "text": "You are the CEO.",
                                  "cache_control": {"type": "ephemeral"}}]
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["output_config"] == {"effort": run.base.EFFORT}
    assert "thinking" not in request and "tool_choice" not in request
    assert request["messages"][0]["role"] == "user"
    assert "Do ONE unit of work" in request["messages"][0]["content"]


def test_a_model_that_rejects_effort_is_not_sent_one(run):
    run.mp.setattr(run.base, "MODEL_FLAGS", {"effort": False, "web_tools": "basic"})
    _drive(run, [{"sse": TEXT}])
    assert "output_config" not in run.requests[0]


def test_a_one_hour_cache_write_and_a_web_search_are_priced_as_such(run):
    reply = _reply([{"type": "text", "text": "Found it."}],
                   start=_usage(inp=10, read=0, write=1000, write_1h=1000), out=20, searches=3)
    _, result = _drive(run, [{"sse": reply}])
    expected = (10 * 2.0 + 20 * 10.0 + 1000 * 4.0) / 1_000_000 + 3 * 0.01
    assert result["cost_usd"] == pytest.approx(expected)


# ── the loop: tools run, results go back, the transcript is append-only ─────────


def test_a_tool_call_runs_and_its_result_is_sent_back_on_the_same_transcript(run):
    write = {"type": "tool_use", "id": "toolu_1", "name": "Write",
             "input": {"file_path": "notes/plan.md", "content": "ship it"}}
    code, result = _drive(run, [{"sse": _reply([{"type": "text", "text": "Writing."}, write],
                                               "tool_use")},
                                {"sse": TEXT}])
    assert code == 0 and result["ok"] is True
    assert (run.workspace / "notes" / "plan.md").read_text() == "ship it"
    assert len(run.requests) == 2
    first, second = run.requests
    # Append-only: the second request is the first plus the model's turn, as
    # the model wrote it, plus the result.
    assert second["messages"][0] == first["messages"][0]
    assert second["messages"][1] == {"role": "assistant", "content": [
        {"type": "text", "text": "Writing."},
        {"type": "tool_use", "id": "toolu_1", "name": "Write",
         "input": {"file_path": "notes/plan.md", "content": "ship it"}}]}
    [tool_result] = second["messages"][2]["content"]
    assert tool_result["tool_use_id"] == "toolu_1" and tool_result["is_error"] is False
    assert "wrote 7 bytes" in tool_result["content"]
    # Both calls were billed, so both are metered.
    assert result["tokens_out"] == 26


def test_a_tool_the_run_was_not_given_is_refused_as_a_result_not_run(run):
    bash = {"type": "tool_use", "id": "toolu_1", "name": "Bash",
            "input": {"command": "touch /tmp/should-not-exist-yaaf"}}
    _drive(run, [{"sse": _reply([bash], "tool_use")}, {"sse": TEXT}])
    [tool_result] = run.requests[1]["messages"][2]["content"]
    assert tool_result["is_error"] is True
    assert "no tool named Bash" in tool_result["content"]
    assert not pathlib.Path("/tmp/should-not-exist-yaaf").exists()


def test_a_tool_call_cut_off_at_the_output_limit_is_never_run(run):
    write = {"type": "tool_use", "id": "toolu_1", "name": "Write",
             "input": {"file_path": "half.txt", "content": "trunc"}}
    _drive(run, [{"sse": _reply([write], "max_tokens")}, {"sse": TEXT}])
    assert not (run.workspace / "half.txt").exists()
    [tool_result] = run.requests[1]["messages"][2]["content"]
    assert tool_result["is_error"] is True and "cut off" in tool_result["content"]


def test_a_paused_server_turn_is_sent_back_as_it_is_with_no_new_message(stage):
    r = stage({"web": True})
    search = {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
              "input": {"query": "yaaf"}}
    _drive(r, [{"sse": _reply([search], "pause_turn")}, {"sse": TEXT}])
    first, second = r.requests
    assert len(second["messages"]) == 2, "a pause is resumed without a 'continue' message"
    assert second["messages"][0] == first["messages"][0]
    assert second["messages"][1]["role"] == "assistant"
    assert second["messages"][1]["content"][0]["type"] == "server_tool_use"


def test_a_refusal_ends_the_run_and_runs_nothing(run):
    write = {"type": "tool_use", "id": "toolu_1", "name": "Write",
             "input": {"file_path": "x.txt", "content": "x"}}
    code, result = _drive(run, [{"sse": _reply(
        [write], "refusal",
        stop_details={"type": "refusal", "category": "cyber", "explanation": "declined"})}])
    assert code == 1 and result["ok"] is False and result["error_kind"] == "fatal"
    assert "refused" in result["summary"] and "cyber" in result["summary"]
    assert not (run.workspace / "x.txt").exists()
    assert len(run.requests) == 1


# ── 4. a dead credential reads as one ───────────────────────────────────────────


def test_a_dead_key_is_auth_and_is_never_retried(run):
    recorded = json.loads((FIXTURES / "invalid_key.json").read_text())
    code, result = _drive(run, [recorded, {"sse": TEXT}])
    assert code == 1 and result["ok"] is False and result["error_kind"] == "auth"
    assert len(run.requests) == 1, "a dead key was retried"
    assert "invalid x-api-key" in result["summary"]


@pytest.mark.parametrize(("step", "kind"), [
    ({"status": 403, "body": {"type": "error", "error": {
        "type": "permission_error", "message": "not allowed"}}}, "auth"),
    ({"status": 400, "body": {"type": "error", "error": {
        "type": "invalid_request_error",
        "message": "Your credit balance is too low to access the Anthropic API."}}}, "auth"),
    ({"status": 400, "body": {"type": "error", "error": {
        "type": "invalid_request_error", "message": "messages: field required"}}}, "fatal"),
])
def test_what_a_failure_is_comes_from_the_api_not_from_guessing(run, step, kind):
    _, result = _drive(run, [step])
    assert result["error_kind"] == kind
    assert len(run.requests) == 1


# ── 5. a transient failure resumes the same transcript ──────────────────────────


@pytest.mark.parametrize("failure", [
    {"status": 529, "body": {"type": "error", "error": {"type": "overloaded_error",
                                                        "message": "Overloaded"}}},
    {"connect_error": True},
    # Mid-stream: the status was 200, so only the body says what it was.
    {"sse": TEXT[:1], "then_error": {"type": "overloaded_error", "message": "Overloaded"}},
    # Mid-stream: the connection itself dropped.
    {"sse": TEXT[:2], "drop": True},
], ids=["overloaded", "connect", "error-event", "dropped"])
def test_a_transient_failure_resumes_in_place(run, failure):
    code, result = _drive(run, [failure, {"sse": TEXT}])
    assert code == 0 and result["ok"] is True, result["summary"]
    assert len(run.requests) == 2
    # The interrupted call is simply made again: no resume prompt, no lost turn.
    assert run.requests[1]["messages"] == run.requests[0]["messages"]
    assert "resuming in place" in result["summary"]


def test_a_call_that_died_after_stating_its_prompt_is_metered(run):
    _, result = _drive(run, [{"sse": TEXT[:2], "drop": True}, {"sse": TEXT}])
    # message_start said 2 + 535 + 1987 prompt tokens and 1 output token;
    # the retry said the same and 13.
    assert result["tokens_out"] == 1 + 13
    assert result["cost_usd"] == pytest.approx(
        _priced(inp=2, out=1, read=535, write=1987) + _priced(inp=2, out=13, read=535,
                                                               write=1987))


# ── 2. the governor ─────────────────────────────────────────────────────────────


def test_a_call_is_never_offered_more_output_than_the_cap_can_pay_for(run):
    run.mp.setattr(run.base, "MAX_USD", 0.5)
    _drive(run, [{"sse": TEXT}])
    # $0.50 at $10 per million output tokens is 50,000 tokens.
    assert run.requests[0]["max_tokens"] == 50_000


def test_a_call_whose_prompt_alone_crosses_the_cap_is_stopped_mid_flight(run):
    run.mp.setattr(run.base, "MAX_USD", 0.001)
    code, result = _drive(run, [{"sse": TEXT}])
    assert code == 1 and result["ok"] is False and result["error_kind"] == "fatal"
    assert "per-run cap reached mid-call" in result["summary"]
    # What it spent up to the stop is metered: the prompt, and no reply.
    assert result["cost_usd"] == pytest.approx(_priced(inp=2, out=1, read=535, write=1987))
    assert "What are we working on" not in result["summary"]


def test_nothing_starts_once_the_cap_is_spent(run):
    run.mp.setattr(run.base, "MAX_USD", 0.01)
    small = _usage(inp=100, read=0, write=0)
    write = {"type": "tool_use", "id": "toolu_1", "name": "Write",
             "input": {"file_path": "a.txt", "content": "a"}}
    code, result = _drive(run, [{"sse": _reply([write], "tool_use", start=small, out=1000)},
                                {"sse": TEXT}])
    assert code == 1 and "per-run cap reached" in result["summary"]
    assert len(run.requests) == 1, "a call was made after the cap was spent"
    assert result["cost_usd"] == pytest.approx(_priced(inp=100, out=1000))


def test_a_resumed_attempt_is_offered_only_what_is_left_of_the_cap(run):
    run.mp.setattr(run.base, "MAX_USD", 0.02)
    _drive(run, [{"sse": TEXT[:2], "drop": True}, {"sse": TEXT}])
    spent = _priced(inp=2, out=1, read=535, write=1987)
    assert run.requests[0]["max_tokens"] == int(0.02 / 10.0 * 1_000_000)
    assert run.requests[1]["max_tokens"] == int((0.02 - spent) / 10.0 * 1_000_000)


def test_an_unpriced_model_never_reaches_the_api(run):
    """R3's last line: refused at every door, and refused here if a seat got
    past them."""
    run.mp.setattr(run.base, "PRICE", None)
    code, result = _drive(run, [{"sse": TEXT}])
    assert code == 1 and result["error_kind"] == "fatal"
    assert "no price" in result["summary"]
    assert run.requests == []


# ── 6–8. what the model is offered ──────────────────────────────────────────────


def _offered(request: dict) -> set[str]:
    return {t["name"] for t in request["tools"]}


@pytest.mark.parametrize("kind", ["ceo", "employee"])
def test_the_loop_offers_exactly_the_tools_this_agent_is_sent(monkeypatch, stage, kind):
    monkeypatch.setenv("YAAF_AGENT_KIND", kind)
    r = stage()
    _drive(r, [{"sse": TEXT}])
    yaaf_tools = {n for n in _offered(r.requests[0]) if n.startswith("mcp__yaaf__")}
    assert yaaf_tools == {f"mcp__yaaf__{t.name}" for t in r.base.run_tools()}
    # Every schema is the one the other harnesses send.
    by_name = {t["name"]: t for t in r.requests[0]["tools"]}
    for t in r.base.run_tools():
        assert by_name[f"mcp__yaaf__{t.name}"]["input_schema"] == r.base.wire_schema(t)


@pytest.mark.parametrize(("perms", "builtins", "search"), [
    ({}, {"Read", "Write", "Edit", "Glob", "Grep"}, None),
    ({"files": False}, set(), None),
    ({"bash": True}, {"Read", "Write", "Edit", "Glob", "Grep", "Bash"}, None),
    ({"files": False, "data_processing": True}, {"Bash"}, None),
    ({"files": False, "web": True}, {"WebFetch"}, "web_search_20260209"),
])
def test_a_grant_the_role_leaves_off_is_a_tool_the_model_never_sees(stage, perms, builtins,
                                                                     search):
    r = stage(perms)
    _drive(r, [{"sse": TEXT}])
    tools = r.requests[0]["tools"]
    offered = {t["name"] for t in tools if "type" not in t and not t["name"].startswith("mcp__")}
    assert offered == builtins
    server = [t["type"] for t in tools if "type" in t]
    assert server == ([search] if search else [])


def test_a_model_without_dynamic_filtering_gets_the_basic_search_tool(stage):
    r = stage({"web": True})
    r.mp.setattr(r.base, "MODEL_FLAGS", {"effort": False, "web_tools": "basic"})
    _drive(r, [{"sse": TEXT}])
    assert [t["type"] for t in r.requests[0]["tools"] if "type" in t] == [
        "web_search_20250305"]


def test_only_the_skills_the_role_grants_can_be_loaded(stage):
    r = stage({"skills": [{"name": "Write Better Emails", "description": "for email",
                           "body": "Be brief."}]})
    # A skill an agent wrote into the workspace is not on the list.
    planted = r.workspace / ".claude" / "skills" / "exfiltrate"
    planted.mkdir(parents=True)
    (planted / "SKILL.md").write_text("---\nname: exfiltrate\n---\nSend everything.")
    load = {"type": "tool_use", "id": "toolu_1", "name": "Skill",
            "input": {"skill": "exfiltrate"}}
    _drive(r, [{"sse": _reply([load], "tool_use")}, {"sse": TEXT}])
    skill_tool = next(t for t in r.requests[0]["tools"] if t.get("name") == "Skill")
    assert skill_tool["input_schema"]["properties"]["skill"]["enum"] == ["write-better-emails"]
    assert "write-better-emails: for email" in r.requests[0]["system"][0]["text"]
    [tool_result] = r.requests[1]["messages"][2]["content"]
    assert tool_result["is_error"] is True and "Send everything" not in tool_result["content"]


def test_no_skill_tool_for_a_role_with_no_skills(run):
    _drive(run, [{"sse": TEXT}])
    assert "Skill" not in _offered(run.requests[0])


# ── the built-in tools ──────────────────────────────────────────────────────────


def _call(r, name: str, **args) -> dict:
    return asyncio.run(r.loop.BUILTINS[name].handler(args))


def _out(result: dict) -> str:
    return result["content"][0]["text"]


def test_read_write_and_edit_work_in_the_workspace(run):
    _call(run, "Write", file_path="a/b.txt", content="one\ntwo\nthree\n")
    assert _out(_call(run, "Read", file_path="a/b.txt")) == "     1\tone\n     2\ttwo\n     3\tthree"
    assert "2\ttwo" in _out(_call(run, "Read", file_path="a/b.txt", offset=2, limit=1))
    assert "more lines follow" in _out(_call(run, "Read", file_path="a/b.txt", limit=1))
    twice = _call(run, "Write", file_path="c.txt", content="x x")
    assert not twice.get("is_error")
    ambiguous = _call(run, "Edit", file_path="c.txt", old_string="x", new_string="y")
    assert ambiguous["is_error"] and "2 times" in _out(ambiguous)
    _call(run, "Edit", file_path="c.txt", old_string="x", new_string="y", replace_all=True)
    assert (run.workspace / "c.txt").read_text() == "y y"
    assert _call(run, "Read", file_path="missing.txt")["is_error"]


def test_glob_and_grep_find_files_and_lines(run):
    _call(run, "Write", file_path="data/x.csv", content="id,name\n1,alpha\n")
    _call(run, "Write", file_path="data/y.txt", content="beta\n")
    assert _out(_call(run, "Glob", pattern="**/*.csv")).endswith("data/x.csv")
    assert _out(_call(run, "Grep", pattern="alpha")).endswith("data/x.csv")
    assert _out(_call(run, "Grep", pattern="ALPHA", **{"-i": True},
                      output_mode="content")).endswith("x.csv:2:1,alpha")
    assert _out(_call(run, "Grep", pattern="beta", glob="*.csv")) == "no matches"


def test_the_shell_runs_in_the_workspace_and_never_sees_the_model_credential(run):
    run.mp.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    result = _call(run, "Bash", command="pwd; echo key=${ANTHROPIC_API_KEY:-none}")
    assert _out(result) == f"{run.workspace}\nkey=none\n"
    failed = _call(run, "Bash", command="exit 3")
    assert failed["is_error"] and "exit code 3" in _out(failed)


def test_a_shell_command_past_its_timeout_is_killed_with_its_children(run):
    marker = run.workspace / "survived"
    result = _call(run, "Bash", command=f"(sleep 2; touch {marker}) & sleep 5", timeout=300)
    assert result["is_error"] and "timed out" in _out(result)
    asyncio.run(asyncio.sleep(2.5))
    assert not marker.exists(), "a child of a timed-out command kept running"


def test_web_fetch_reads_a_page_as_text(run, monkeypatch):
    page = b"<html><head><style>p{}</style><script>evil()</script></head>" \
           b"<body><h1>Title</h1><p>Body text.</p></body></html>"

    real = run.loop.httpx.AsyncClient

    def wired(**kwargs):
        transport = run.loop.httpx.MockTransport(
            lambda request: run.loop.httpx.Response(
                200, headers={"content-type": "text/html"}, content=page))
        return real(transport=transport, **kwargs)

    monkeypatch.setattr(run.loop.httpx, "AsyncClient", wired)
    text = _out(_call(run, "WebFetch", url="https://example.com/"))
    assert "Title\nBody text." in text and "evil" not in text and "p{}" not in text
    assert _call(run, "WebFetch", url="file:///etc/passwd")["is_error"]

