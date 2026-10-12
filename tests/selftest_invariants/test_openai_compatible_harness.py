"""Invariant selftests: the loop on the OpenAI-compatible chat API (docs/RUNTIMES.md,
M3) — DeepSeek, OpenRouter and a custom server through one client, held to the
contract test_loop_harness.py holds the Messages API client to.

The model is a fake server speaking the chat-completions stream: `data:` lines
of JSON deltas, usage on the last line, `[DONE]`. Synthetic, built from the
documented shape — a recorded success costs money, and the providers' sites are
not reachable from where this was written. Offline and free.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("claude_agent_sdk")

#: A price an install might set for DeepSeek's chat model, per million tokens.
PRICE = {"input": 0.28, "output": 0.42, "cache_read": 0.028, "cache_write_5m": 0.0,
         "cache_write_1h": 0.0, "web_search": 0.01, "as_of": "2026-10-12"}
DEEPSEEK = {"name": "deepseek", "base_url": "https://api.deepseek.com/v1",
            "key_env": "DEEPSEEK_API_KEY", "reports_cost": False, "search": False}
OPENROUTER = {"name": "openrouter", "base_url": "https://openrouter.ai/api/v1",
              "key_env": "OPENROUTER_API_KEY", "reports_cost": True, "search": False}


# ── the fake API ────────────────────────────────────────────────────────────────


def _chunks(text: str = "", calls: list[dict] | None = None, finish: str = "stop",
            usage: dict | None = None, extra: dict | None = None) -> list[dict]:
    """One streamed reply as the shape sends it: content in pieces, each tool
    call's arguments split across deltas, usage alone on the last line."""
    out = [{"model": "deepseek-chat", "choices": [{"index": 0, "delta": {"role": "assistant"}}]}]
    for piece in (text[:5], text[5:]) if text else ():
        if piece:
            out.append({"choices": [{"index": 0, "delta": {"content": piece}}]})
    for index, call in enumerate(calls or []):
        args = json.dumps(call["input"])
        out.append({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": index, "id": call["id"], "type": "function",
             "function": {"name": call["name"], "arguments": args[:4]}}]}}]})
        out.append({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": index, "function": {"arguments": args[4:]}}]}}]})
    out.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    out.append({"choices": [], "usage": usage or {
        "prompt_tokens": 1000, "completion_tokens": 50,
        "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200}, **(extra or {})})
    return out


TEXT = _chunks("Done. Nothing else to do today.")


def _sse(chunks: list[dict], done: bool = True) -> bytes:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return (body + ("data: [DONE]\n\n" if done else "")).encode()


def _respond(step: dict) -> httpx.Response:
    if "status" in step:
        return httpx.Response(step["status"], json=step["body"])
    if step.get("connect_error"):
        raise httpx.ConnectError("connection refused")
    if step.get("drop"):
        async def dropped():
            yield _sse(step["sse"], done=False)
            raise httpx.ReadError("stream ended early")
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=dropped())
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=_sse(step["sse"]))


# ── the sandbox, staged ─────────────────────────────────────────────────────────


def _staged(monkeypatch, tmp_path, perms: dict | None = None,
            spec: dict | None = None, model: str = "deepseek-chat") -> SimpleNamespace:
    spec = DEEPSEEK if spec is None else spec
    for name, value in {"YAAF_API_BASE_URL": "http://api:8080", "YAAF_API_TOKEN": "test-token",
                        "YAAF_RUN_ID": "run_test", "YAAF_MODEL": model,
                        "YAAF_TOOL_PERMISSIONS": json.dumps(perms or {}),
                        "YAAF_PROVIDER": json.dumps(spec),
                        spec.get("key_env") or "CUSTOM_LLM_API_KEY": "sk-test-key"}.items():
        monkeypatch.setenv(name, value)
    import yaaf.runner.sandbox_entry as entry_module

    base = importlib.reload(entry_module)
    monkeypatch.setitem(sys.modules, "sandbox_entry", base)
    import yaaf.runner.loop_harness as loop_module

    loop = importlib.reload(loop_module)
    monkeypatch.setitem(sys.modules, "loop_harness", loop)
    import yaaf.runner.openai_compatible_entry as api_module

    api = importlib.reload(api_module)
    workspace = tmp_path / "workspace"
    staging = workspace / ".yaaf"
    staging.mkdir(parents=True)
    monkeypatch.setattr(base, "WORKSPACE", workspace)
    monkeypatch.setattr(base, "STAGING", staging)
    monkeypatch.setattr(base, "CREDENTIAL_DIR", tmp_path / "credentials")
    monkeypatch.setattr(base, "RESUME_BACKOFF_S", (0.0, 0.0))
    monkeypatch.setattr(base, "PRICE", PRICE)
    (staging / "system_prompt-run_test.txt").write_text("You are the critic.")
    (staging / "context-run_test.json").write_text("{}")
    return SimpleNamespace(base=base, loop=loop, api=api, staging=staging,
                           workspace=workspace, mp=monkeypatch, requests=[])


@pytest.fixture()
def stage(monkeypatch, tmp_path):
    return lambda **kw: _staged(monkeypatch, tmp_path, **kw)


@pytest.fixture()
def run(stage):
    return stage()


def _drive(r, script: list[dict]) -> tuple[int, dict]:
    def handler(request: httpx.Request) -> httpx.Response:
        r.requests.append({"url": str(request.url),
                           "auth": request.headers.get("authorization"),
                           **json.loads(request.content)})
        return _respond(script[len(r.requests) - 1])

    transport = httpx.MockTransport(handler)

    class Wired(r.api.OpenAICompatibleClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=transport)

    r.mp.setattr(r.api, "OpenAICompatibleClient", Wired)
    code = asyncio.run(r.base.run_harness(r.api.make_driver))
    return code, json.loads((r.staging / "result-run_test.json").read_text())


def _priced(inp: int = 200, out: int = 50, read: int = 800) -> float:
    return (inp * PRICE["input"] + out * PRICE["output"] + read * PRICE["cache_read"]) / 1e6


# ── 1. usage parses to the bill ─────────────────────────────────────────────────


def test_a_reply_is_metered_with_its_cache_split_and_priced(run):
    code, result = _drive(run, [{"sse": TEXT}])
    assert code == 0 and result["ok"] is True and result["error_kind"] is None
    # prompt_tokens counts the cached part; the bill does not count it twice.
    assert (result["tokens_in"], result["cache_read_tokens"]) == (200, 800)
    assert (result["tokens_out"], result["cache_creation_tokens"]) == (50, 0)
    assert result["cost_usd"] == pytest.approx(_priced())
    assert result["priced_usd"] == pytest.approx(_priced())
    assert "Done. Nothing else to do today." in result["summary"]


def test_the_request_goes_to_the_providers_address_with_its_own_key_and_name(run):
    _drive(run, [{"sse": TEXT}])
    request = run.requests[0]
    assert request["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert request["auth"] == "Bearer sk-test-key"
    assert request["model"] == "deepseek-chat"
    assert request["messages"][0] == {"role": "system", "content": "You are the critic."}
    assert "Do ONE unit of work" in request["messages"][1]["content"]
    assert request["stream"] is True and request["stream_options"] == {"include_usage": True}
    # Far from the cap, the provider's own default output limit stands.
    assert "max_tokens" not in request


def test_the_cached_tokens_of_the_openai_shape_are_read_too(stage):
    r = stage(spec={**DEEPSEEK, "name": "custom", "base_url": "http://ollama:11434/v1",
                    "key_env": "CUSTOM_LLM_API_KEY"}, model="custom/llama3.1:8b")
    usage = {"prompt_tokens": 1000, "completion_tokens": 50,
             "prompt_tokens_details": {"cached_tokens": 600}}
    _, result = _drive(r, [{"sse": _chunks("ok", usage=usage)}])
    assert (result["tokens_in"], result["cache_read_tokens"]) == (400, 600)
    # A custom server: its address from the credential, the provider stripped
    # off the model's name.
    assert r.requests[0]["url"] == "http://ollama:11434/v1/chat/completions"
    assert r.requests[0]["model"] == "llama3.1:8b"


def test_usage_inside_the_last_choice_is_read_too(run):
    """Moonshot's documented place for a stream's usage: in the choice, not
    beside it. A reply whose usage went unread would be a free one."""
    chunks = _chunks("ok")
    usage = chunks.pop()["usage"]
    chunks[-1]["choices"][0]["usage"] = usage
    _, result = _drive(run, [{"sse": chunks}])
    assert (result["tokens_in"], result["cache_read_tokens"], result["tokens_out"]) == (200, 800, 50)
    assert result["cost_usd"] == pytest.approx(_priced())


@pytest.mark.parametrize(("usage", "out"), [
    # xAI: reasoning beside the completion, inside the total.
    ({"prompt_tokens": 663, "completion_tokens": 50, "total_tokens": 837,
      "completion_tokens_details": {"reasoning_tokens": 124}}, 174),
    # The OpenAI shape: reasoning inside the completion — not counted twice.
    ({"prompt_tokens": 663, "completion_tokens": 174, "total_tokens": 837,
      "completion_tokens_details": {"reasoning_tokens": 124}}, 174),
    # No total at all: the completion is the output.
    ({"prompt_tokens": 663, "completion_tokens": 50}, 50),
])
def test_reasoning_is_billed_as_output_wherever_the_provider_counts_it(run, usage, out):
    _, result = _drive(run, [{"sse": _chunks("ok", usage=usage)}])
    assert (result["tokens_in"], result["tokens_out"]) == (663, out)


def test_a_dead_key_refused_as_a_bad_request_is_auth_by_the_providers_words(stage):
    """xAI refuses a wrong key with a 400. The words it uses come with its
    address, so the run is a dead credential and not a bad request."""
    r = stage(spec={"name": "xai", "base_url": "https://api.x.ai/v1", "key_env": "XAI_API_KEY",
                    "reports_cost": False, "search": False,
                    "failure_markers": ["incorrect api key provided"]}, model="xai/grok-x")
    code, result = _drive(r, [{"status": 400, "body": {
        "code": "Client specified an invalid argument",
        "error": "Incorrect API key provided: xa***ey. You can obtain an API key from "
                 "https://console.x.ai."}}, {"sse": TEXT}])
    assert code == 1 and result["error_kind"] == "auth" and len(r.requests) == 1


def test_a_provider_that_states_its_cost_is_billed_at_it_with_no_price(stage):
    r = stage(spec=OPENROUTER, model="openrouter/qwen/qwen3-coder")
    r.mp.setattr(r.base, "PRICE", None)
    usage = {"prompt_tokens": 1000, "completion_tokens": 50, "cost": 0.0123}
    code, result = _drive(r, [{"sse": _chunks("ok", usage=usage)}])
    assert code == 0 and result["cost_usd"] == pytest.approx(0.0123)
    # Nothing to check it against: no price, so no priced figure either.
    assert result["priced_usd"] is None
    assert r.requests[0]["model"] == "qwen/qwen3-coder"
    assert r.requests[0]["usage"] == {"include": True}


def test_a_call_with_no_stated_cost_and_no_price_stops_the_run(stage):
    r = stage(spec=OPENROUTER, model="openrouter/qwen/qwen3-coder")
    r.mp.setattr(r.base, "PRICE", None)
    code, result = _drive(r, [{"sse": TEXT}, {"sse": TEXT}])
    assert code == 1 and result["error_kind"] == "fatal"
    assert "stated no cost" in result["summary"]
    assert len(r.requests) == 1


def test_an_unpriced_model_on_a_provider_that_states_nothing_never_calls_it(run):
    run.mp.setattr(run.base, "PRICE", None)
    code, result = _drive(run, [{"sse": TEXT}])
    assert code == 1 and "no price" in result["summary"]
    assert run.requests == []


def test_a_model_priced_at_zero_runs_and_costs_nothing(run):
    run.mp.setattr(run.base, "PRICE", {**PRICE, "input": 0.0, "output": 0.0,
                                       "cache_read": 0.0})
    run.mp.setattr(run.base, "MAX_USD", 1.0)
    code, result = _drive(run, [{"sse": TEXT}])
    assert code == 0 and result["cost_usd"] == 0.0


# ── the loop: tools run and go back in the provider's shape ─────────────────────


def test_a_tool_call_runs_and_goes_back_as_a_tool_message(run):
    write = {"id": "call_1", "name": "Write",
             "input": {"file_path": "critique.md", "content": "weak evidence"}}
    code, result = _drive(run, [{"sse": _chunks("Writing.", calls=[write],
                                                 finish="tool_calls")},
                                {"sse": TEXT}])
    assert code == 0 and result["ok"] is True
    assert (run.workspace / "critique.md").read_text() == "weak evidence"
    second = run.requests[1]["messages"]
    assert second[2] == {"role": "assistant", "content": "Writing.", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {
            "name": "Write", "arguments": json.dumps(write["input"])}}]}
    assert second[3]["role"] == "tool" and second[3]["tool_call_id"] == "call_1"
    assert "wrote 13 bytes" in second[3]["content"]
    tools = {t["function"]["name"] for t in run.requests[0]["tools"]}
    assert "Write" in tools and "mcp__yaaf__board" in tools
    assert result["tokens_out"] == 100


def test_a_failed_tool_says_so_in_the_text(run):
    bash = {"id": "call_1", "name": "Bash", "input": {"command": "true"}}
    _drive(run, [{"sse": _chunks(calls=[bash], finish="tool_calls")}, {"sse": TEXT}])
    tool_message = run.requests[1]["messages"][3]
    assert tool_message["content"].startswith("ERROR: no tool named Bash")


def test_tool_calls_under_a_stop_finish_are_still_run(run):
    write = {"id": "call_1", "name": "Write", "input": {"file_path": "a.txt", "content": "a"}}
    _drive(run, [{"sse": _chunks(calls=[write], finish="stop")}, {"sse": TEXT}])
    assert (run.workspace / "a.txt").exists()


def test_a_tool_call_cut_off_at_the_length_limit_is_never_run(run):
    write = {"id": "call_1", "name": "Write", "input": {"file_path": "half.txt", "content": "x"}}
    _drive(run, [{"sse": _chunks(calls=[write], finish="length")}, {"sse": TEXT}])
    assert not (run.workspace / "half.txt").exists()
    assert "cut off" in run.requests[1]["messages"][3]["content"]


def test_reasoning_the_provider_streamed_is_not_sent_back(run):
    """DeepSeek's reasoner streams `reasoning_content`, and refuses a request
    that carries it back. The transcript replays what the model said, not what
    it thought."""
    chunks = _chunks("Answer.", calls=[{"id": "call_1", "name": "Glob",
                                        "input": {"pattern": "*"}}], finish="tool_calls")
    chunks.insert(1, {"choices": [{"index": 0, "delta": {"reasoning_content": "hmm"}}]})
    _drive(run, [{"sse": chunks}, {"sse": TEXT}])
    assert "reasoning_content" not in json.dumps(run.requests[1]["messages"])


def test_a_content_filter_ends_the_run(run):
    code, result = _drive(run, [{"sse": _chunks("", finish="content_filter")}])
    assert code == 1 and result["error_kind"] == "fatal"
    assert "content filter" in result["summary"]


# ── 4. a dead credential reads as one ───────────────────────────────────────────


@pytest.mark.parametrize(("status", "body"), [
    (401, {"error": {"message": "Authentication Fails, Your api key: ****t-key is invalid",
                     "type": "authentication_error"}}),
    (402, {"error": {"message": "Insufficient Balance", "type": "unknown_error"}}),
    (403, {"error": {"message": "forbidden"}}),
])
def test_a_dead_key_or_an_empty_balance_is_auth_and_never_retried(run, status, body):
    code, result = _drive(run, [{"status": status, "body": body}, {"sse": TEXT}])
    assert code == 1 and result["error_kind"] == "auth"
    assert len(run.requests) == 1


def test_a_bad_request_is_fatal_not_retried(run):
    _, result = _drive(run, [{"status": 400, "body": {"error": {
        "message": "Invalid max_tokens value"}}}, {"sse": TEXT}])
    assert result["error_kind"] == "fatal" and len(run.requests) == 1


# ── 5. a transient failure resumes the same transcript ──────────────────────────


@pytest.mark.parametrize("failure", [
    {"status": 429, "body": {"error": {"message": "rate limited"}}},
    {"status": 503, "body": {"error": {"message": "Service Unavailable"}}},
    {"connect_error": True},
    {"sse": TEXT[:2], "drop": True},
    # OpenRouter reports an upstream failure as a line of a 200 stream.
    {"sse": TEXT[:1] + [{"error": {"code": 502, "message": "upstream error"}}]},
], ids=["429", "503", "connect", "dropped", "error-line"])
def test_a_transient_failure_resumes_in_place(run, failure):
    code, result = _drive(run, [failure, {"sse": TEXT}])
    assert code == 0 and result["ok"] is True, result["summary"]
    assert len(run.requests) == 2
    assert run.requests[1]["messages"] == run.requests[0]["messages"]


# ── 2. the governor ─────────────────────────────────────────────────────────────


def test_near_the_cap_the_output_is_clamped_to_what_it_can_pay_for(run):
    run.mp.setattr(run.base, "MAX_USD", 0.001)
    _drive(run, [{"sse": TEXT}])
    # $0.001 at $0.42 per million output tokens is 2,380 tokens.
    assert run.requests[0]["max_tokens"] == 2380


def test_nothing_starts_once_the_cap_is_spent(run):
    # The first call costs $0.0000994: past this cap, and only knowable once
    # it is over — this shape states usage on its last line.
    run.mp.setattr(run.base, "MAX_USD", 0.00005)
    write = {"id": "call_1", "name": "Write", "input": {"file_path": "a.txt", "content": "a"}}
    code, result = _drive(run, [{"sse": _chunks(calls=[write], finish="tool_calls")},
                                {"sse": TEXT}])
    assert code == 1 and "per-run cap reached" in result["summary"]
    assert len(run.requests) == 1
    assert result["cost_usd"] == pytest.approx(_priced())


def test_a_provider_reporting_cost_is_capped_between_calls(stage):
    r = stage(spec=OPENROUTER, model="openrouter/qwen/qwen3-coder")
    r.mp.setattr(r.base, "PRICE", None)
    r.mp.setattr(r.base, "MAX_USD", 0.01)
    write = {"id": "call_1", "name": "Write", "input": {"file_path": "a.txt", "content": "a"}}
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.02}
    code, result = _drive(r, [{"sse": _chunks(calls=[write], finish="tool_calls",
                                              usage=usage)}, {"sse": TEXT}])
    assert code == 1 and "per-run cap reached" in result["summary"]
    assert len(r.requests) == 1 and "max_tokens" not in r.requests[0]


# ── what the model is offered ───────────────────────────────────────────────────


@pytest.mark.parametrize(("perms", "builtins"), [
    ({}, {"Read", "Write", "Edit", "Glob", "Grep"}),
    ({"files": False, "bash": True}, {"Bash"}),
])
def test_the_tools_offered_follow_the_grants_and_nothing_searches(stage, perms, builtins):
    r = stage(perms=perms)
    _drive(r, [{"sse": TEXT}])
    names = {t["function"]["name"] for t in r.requests[0]["tools"]}
    assert {n for n in names if not n.startswith("mcp__")} == builtins
    assert all(t["type"] == "function" for t in r.requests[0]["tools"])


def test_the_shell_never_sees_this_providers_key(run):
    result = asyncio.run(run.loop.BUILTINS["Bash"].handler(
        {"command": "echo key=${DEEPSEEK_API_KEY:-none}"}))
    assert result["content"][0]["text"] == "key=none\n"
