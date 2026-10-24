"""Invariant selftests: yaaf's loop on every wire a provider speaks
(docs/PROVIDERS.md §3) — OpenAI's Responses API and Azure's, Gemini and
Vertex, Bedrock's Converse — held to the contract test_loop_harness.py holds
the Messages API to and test_openai_compatible_harness.py the chat shape:

1. usage parses to the bill, cached and reasoning tokens where each provider
   puts them, and the run is priced;
2. the request goes to the provider's own address, with its key presented its
   own way, under the model's own name;
3. a tool call runs and goes back in the provider's shape, with what the model
   wrote replayed verbatim (encrypted reasoning, thought signatures);
4. a dead credential is `auth` and never retried; a transient failure resumes
   the same transcript; a refusal and a cut-off call run nothing;
5. search is the provider's where its wire has it, a grounded call of its own
   on Gemini, and the platform's backend everywhere else — billed into the run
   either way, and never with its key in the agent's shell;
6. a minted token (Vertex) is refreshed through the agent API before it lapses.

Each provider is a fake server speaking its documented shape. Offline, free.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import time
from types import SimpleNamespace

import httpx
import pytest

from yaaf.runtimes import wire

pytest.importorskip("claude_agent_sdk")

PRICE = {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write_5m": 0.0,
         "cache_write_1h": 0.0, "web_search": 0.025, "as_of": "2026-10-24"}

OPENAI = {"name": "openai", "protocol": "openai_responses", "auth": "bearer",
          "base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY",
          "search": True, "failure_markers": ["incorrect api key provided"]}
AZURE = {**OPENAI, "name": "azure_openai", "auth": "api-key",
         "base_url": "https://acme.openai.azure.com/openai/v1", "key_env": "AZURE_OPENAI_API_KEY"}
GEMINI = {"name": "google", "protocol": "gemini", "auth": "x-goog-api-key",
          "base_url": "https://generativelanguage.googleapis.com/v1beta",
          "key_env": "GEMINI_API_KEY", "search": True, "failure_markers": ["api key not valid"]}
VERTEX = {"name": "vertex", "protocol": "gemini", "auth": "bearer", "mints_tokens": True,
          "base_url": "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/"
                      "us-central1/publishers/google",
          "key_env": "VERTEX_ACCESS_TOKEN", "search": False}
BEDROCK = {"name": "bedrock", "protocol": "bedrock", "auth": "bearer",
           "base_url": "https://bedrock-runtime.us-east-1.amazonaws.com",
           "key_env": "AWS_BEARER_TOKEN_BEDROCK", "search": False}
DEEPSEEK = {"name": "deepseek", "protocol": "openai_chat", "auth": "bearer",
            "base_url": "https://api.deepseek.com/v1", "key_env": "DEEPSEEK_API_KEY",
            "search": False}


# ── the sandbox, staged ─────────────────────────────────────────────────────────


def _staged(monkeypatch, tmp_path, spec: dict, model: str, *, perms: dict | None = None,
            flags: dict | None = None, effort: str = "high", env: dict | None = None):
    for name, value in {"YAAF_API_BASE_URL": "http://api:8080", "YAAF_API_TOKEN": "run-token",
                        "YAAF_RUN_ID": "run_test", "YAAF_MODEL": model, "YAAF_EFFORT": effort,
                        "YAAF_TOOL_PERMISSIONS": json.dumps(perms or {}),
                        "YAAF_MODEL_FLAGS": json.dumps(flags or {}),
                        "YAAF_PROVIDER": json.dumps(spec),
                        spec["key_env"]: "provider-key", **(env or {})}.items():
        monkeypatch.setenv(name, value)
    import yaaf.runner.sandbox_entry as entry_module

    base = importlib.reload(entry_module)
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
    monkeypatch.setattr(base, "_external_mcp_servers", lambda: {})
    (staging / "system_prompt-run_test.txt").write_text("You are the analyst.")
    (staging / "context-run_test.json").write_text("{}")
    return SimpleNamespace(base=base, loop=loop, api=api, staging=staging, workspace=workspace,
                           mp=monkeypatch, requests=[])


@pytest.fixture()
def stage(monkeypatch, tmp_path):
    return lambda spec, model, **kw: _staged(monkeypatch, tmp_path, spec, model, **kw)


def _drive(r, script: list) -> tuple[int, dict]:
    """The production entry, its wire pointed at a fake provider: each request
    gets the next step of the script — a response, or a callable making one."""
    def handler(request: httpx.Request) -> httpx.Response:
        r.requests.append({"url": str(request.url), "headers": dict(request.headers),
                           "body": json.loads(request.content) if request.content else None})
        step = script[len(r.requests) - 1]
        if callable(step):
            return step(request)
        if step.get("connect_error"):
            raise httpx.ConnectError("connection refused")
        return httpx.Response(step.get("status", 200), headers=step.get("headers"),
                              content=step.get("content"),
                              json=step.get("json"))

    r.mp.setattr(r.api, "TRANSPORT", httpx.MockTransport(handler))
    code = asyncio.run(r.base.run_harness(r.api.make_driver))
    return code, json.loads((r.staging / "result-run_test.json").read_text())


def _priced(inp: int, out: int, read: int = 0, searches: int = 0) -> float:
    return ((inp * PRICE["input"] + out * PRICE["output"] + read * PRICE["cache_read"]) / 1e6
            + searches * PRICE["web_search"])


# ── OpenAI's Responses API ──────────────────────────────────────────────────────


def _responses(output: list[dict], *, status: str = "completed", usage: dict | None = None,
               reason: str | None = None) -> dict:
    response = {"id": "resp_1", "model": "gpt-6.1-sol-2026-09", "status": status,
                "output": output,
                "usage": usage or {"input_tokens": 1000, "output_tokens": 50,
                                   "input_tokens_details": {"cached_tokens": 800},
                                   "output_tokens_details": {"reasoning_tokens": 30}}}
    if reason:
        response["incomplete_details"] = {"reason": reason}
    events = [{"type": "response.created", "response": {"id": "resp_1"}},
              {"type": "response.output_text.delta", "delta": "Do"},
              {"type": f"response.{status}", "response": response}]
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return {"content": body.encode(), "headers": {"content-type": "text/event-stream"}}


def _message(text: str) -> dict:
    return {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": [],
                         "logprobs": []}]}


REASONING = {"type": "reasoning", "id": "rs_1", "summary": [],
             "encrypted_content": "gAAAA-opaque-reasoning"}


def test_responses_usage_is_metered_with_its_cache_split_and_priced(stage):
    r = stage(OPENAI, "gpt-6.1-sol")
    code, result = _drive(r, [_responses([REASONING, _message("Done for today.")])])
    assert code == 0 and result["ok"] is True, result["summary"]
    # input_tokens counts the cached part; output_tokens counts the reasoning.
    assert (result["tokens_in"], result["cache_read_tokens"], result["tokens_out"]) == (
        200, 800, 50)
    assert result["cost_usd"] == pytest.approx(_priced(200, 50, 800))
    assert "Done for today." in result["summary"]


def test_responses_request_is_openais_own(stage):
    r = stage(OPENAI, "gpt-6.1-sol", flags={"effort": True})
    _drive(r, [_responses([_message("ok")])])
    sent = r.requests[0]
    assert sent["url"] == "https://api.openai.com/v1/responses"
    assert sent["headers"]["authorization"] == "Bearer provider-key"
    body = sent["body"]
    assert body["model"] == "gpt-6.1-sol" and body["instructions"] == "You are the analyst."
    assert body["stream"] is True and body["store"] is False
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["reasoning"] == {"effort": "high"}
    assert "Do ONE unit of work" in body["input"][0]["content"]
    tools = {t["name"] for t in body["tools"] if t["type"] == "function"}
    assert "Write" in tools and "mcp__yaaf__board" in tools


def test_no_dial_means_no_reasoning_field(stage):
    r = stage(OPENAI, "openai/gpt-7-mystery", flags={"effort": False})
    _drive(r, [_responses([_message("ok")])])
    assert "reasoning" not in r.requests[0]["body"]
    assert r.requests[0]["body"]["model"] == "gpt-7-mystery"


def test_responses_tool_call_round_trips_with_reasoning_replayed(stage):
    r = stage(OPENAI, "gpt-6.1-sol")
    call = {"type": "function_call", "id": "fc_1", "call_id": "call_9", "name": "Write",
            "arguments": json.dumps({"file_path": "notes.md", "content": "found it"})}
    code, result = _drive(r, [_responses([REASONING, call]),
                              _responses([_message("Wrote it.")])])
    assert code == 0, result["summary"]
    assert (r.workspace / "notes.md").read_text() == "found it"
    second = r.requests[1]["body"]["input"]
    # What the model wrote goes back exactly as it came — the encrypted
    # reasoning item included — then the tool's answer by call id.
    assert second[1:3] == [REASONING, call]
    assert second[3]["type"] == "function_call_output" and second[3]["call_id"] == "call_9"
    assert "wrote" in second[3]["output"]


def test_responses_search_is_openais_own_tool_and_billed_per_call(stage):
    r = stage(OPENAI, "gpt-6.1-sol", perms={"web": True})
    searched = {"type": "web_search_call", "id": "ws_1", "status": "completed",
                "action": {"type": "search", "query": "yaaf"}}
    code, result = _drive(r, [_responses([searched, _message("Here is what I found.")])])
    assert code == 0
    tools = r.requests[0]["body"]["tools"]
    assert {"type": "web_search"} in tools
    assert not any(t.get("name") == "WebSearch" for t in tools), "no second search beside it"
    assert result["cost_usd"] == pytest.approx(_priced(200, 50, 800, searches=1))


def test_a_cut_off_responses_call_runs_nothing(stage):
    r = stage(OPENAI, "gpt-6.1-sol")
    call = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "Write",
            "arguments": json.dumps({"file_path": "half.md", "content": "x"})}
    _drive(r, [_responses([call], status="incomplete", reason="max_output_tokens"),
               _responses([_message("ok")])])
    assert not (r.workspace / "half.md").exists()
    assert "cut off" in r.requests[1]["body"]["input"][-1]["output"]


def test_a_responses_refusal_ends_the_run(stage):
    r = stage(OPENAI, "gpt-6.1-sol")
    refused = {"type": "message", "id": "m", "role": "assistant",
               "content": [{"type": "refusal", "refusal": "I can't help with that."}]}
    code, result = _drive(r, [_responses([refused])])
    assert code == 1 and result["error_kind"] == "fatal"
    assert "can't help" in result["summary"]


@pytest.mark.parametrize(("step", "kind", "calls"), [
    ({"status": 401, "json": {"error": {"message": "Incorrect API key provided: sk-…"}}},
     "auth", 1),
    ({"status": 429, "json": {"error": {"message": "Rate limit reached"}}}, None, 2),
    ({"connect_error": True}, None, 2),
])
def test_responses_failures_are_classified(stage, step, kind, calls):
    r = stage(OPENAI, "gpt-6.1-sol")
    code, result = _drive(r, [step, _responses([_message("ok")])])
    assert len(r.requests) == calls
    if kind:
        assert code == 1 and result["error_kind"] == kind
    else:
        assert code == 0 and r.requests[1]["body"]["input"] == r.requests[0]["body"]["input"]


def test_a_failed_response_in_a_200_stream_is_transient(stage):
    r = stage(OPENAI, "gpt-6.1-sol")
    failed = {"type": "response.failed", "response": {"error": {
        "code": "server_error", "message": "The server had an error"}}}
    code, _ = _drive(r, [{"content": f"data: {json.dumps(failed)}\n\n".encode(),
                          "headers": {"content-type": "text/event-stream"}},
                         _responses([_message("ok")])])
    assert code == 0 and len(r.requests) == 2


def test_azure_is_the_same_wire_at_the_resources_address_with_its_own_header(stage):
    r = stage(AZURE, "azure_openai/prod-gpt")
    _drive(r, [_responses([_message("ok")])])
    sent = r.requests[0]
    assert sent["url"] == "https://acme.openai.azure.com/openai/v1/responses"
    assert sent["headers"]["api-key"] == "provider-key"
    assert "authorization" not in sent["headers"]
    assert sent["body"]["model"] == "prod-gpt", "an Azure model is its deployment's name"


# ── Gemini, and Vertex ──────────────────────────────────────────────────────────


def _gemini(parts: list[dict], *, finish: str = "STOP", usage: dict | None = None,
            grounding: dict | None = None) -> dict:
    candidate = {"content": {"role": "model", "parts": parts}, "finishReason": finish}
    if grounding:
        candidate["groundingMetadata"] = grounding
    return {"json": {"candidates": [candidate], "modelVersion": "gemini-3.1-pro-preview",
                     "usageMetadata": usage or {"promptTokenCount": 1000,
                                                "cachedContentTokenCount": 600,
                                                "candidatesTokenCount": 40,
                                                "thoughtsTokenCount": 60}}}


def test_gemini_usage_bills_thinking_as_output(stage):
    r = stage(GEMINI, "gemini-3.1-pro-preview")
    code, result = _drive(r, [_gemini([{"text": "Nothing to do."}])])
    assert code == 0, result["summary"]
    assert (result["tokens_in"], result["cache_read_tokens"], result["tokens_out"]) == (
        400, 600, 100)
    assert result["cost_usd"] == pytest.approx(_priced(400, 100, 600))


def test_gemini_request_is_the_native_api(stage):
    r = stage(GEMINI, "gemini-3.1-pro-preview", flags={"effort": True})
    _drive(r, [_gemini([{"text": "ok"}])])
    sent = r.requests[0]
    assert sent["url"] == ("https://generativelanguage.googleapis.com/v1beta/models/"
                           "gemini-3.1-pro-preview:generateContent")
    assert sent["headers"]["x-goog-api-key"] == "provider-key"
    body = sent["body"]
    assert body["systemInstruction"] == {"parts": [{"text": "You are the analyst."}]}
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "HIGH"}
    declarations = body["tools"][0]["functionDeclarations"]
    assert any(d["name"] == "Write" for d in declarations)
    # Gemini refuses some JSON-schema keywords outright; none is sent.
    assert "additionalProperties" not in json.dumps(declarations)


def test_gemini_tool_call_replays_its_thought_signature(stage):
    r = stage(GEMINI, "gemini-3.1-pro-preview")
    part = {"functionCall": {"name": "Write", "args": {"file_path": "g.md", "content": "hi"}},
            "thoughtSignature": "c2lnbmF0dXJl"}
    code, result = _drive(r, [_gemini([part]), _gemini([{"text": "Done."}])])
    assert code == 0, result["summary"]
    assert (r.workspace / "g.md").read_text() == "hi"
    contents = r.requests[1]["body"]["contents"]
    assert contents[1] == {"role": "model", "parts": [part]}, "signature replayed on its part"
    response = contents[2]["parts"][0]["functionResponse"]
    assert response["name"] == "Write" and "wrote" in response["response"]["content"]


def test_gemini_searches_with_a_grounded_call_of_its_own(stage):
    """Gemini does not take Google Search beside function declarations on every
    model, so the loop offers WebSearch and answers it with a grounded call on
    the seat's model — billed as the call it is, plus the grounding fee."""
    r = stage(GEMINI, "gemini-3.1-pro-preview", perms={"web": True})
    search = {"functionCall": {"name": "WebSearch", "args": {"query": "yaaf framework"}}}
    grounded = _gemini([{"text": "yaaf is an agent framework."}], usage={
        "promptTokenCount": 100, "candidatesTokenCount": 20},
        grounding={"webSearchQueries": ["yaaf framework"], "groundingChunks": [
            {"web": {"uri": "https://example.com/yaaf", "title": "yaaf"}}]})
    code, result = _drive(r, [_gemini([search]), grounded, _gemini([{"text": "Done."}])])
    assert code == 0, result["summary"]
    assert any(d["name"] == "WebSearch"
               for d in r.requests[0]["body"]["tools"][0]["functionDeclarations"])
    assert r.requests[1]["body"]["tools"] == [{"googleSearch": {}}]
    answered = r.requests[2]["body"]["contents"][2]["parts"][0]["functionResponse"]
    assert "https://example.com/yaaf" in answered["response"]["content"]
    expected = (_priced(400, 100, 600) * 2 + _priced(100, 20, searches=1))
    assert result["cost_usd"] == pytest.approx(expected)


@pytest.mark.parametrize(("step", "code", "kind"), [
    (_gemini([{"text": ""}], finish="SAFETY"), 1, "fatal"),
    ({"status": 400, "json": {"error": {"code": 400, "message": "API key not valid. Please "
                                        "pass a valid API key.", "status": "INVALID_ARGUMENT"}}},
     1, "auth"),
    ({"status": 503, "json": {"error": {"message": "The model is overloaded."}}}, 0, None),
])
def test_gemini_failures_are_classified(stage, step, code, kind):
    r = stage(GEMINI, "gemini-3.1-pro-preview")
    got, result = _drive(r, [step, _gemini([{"text": "ok"}])])
    assert got == code and result["error_kind"] == kind


def test_a_vertex_token_is_refreshed_through_the_agent_api_before_it_lapses(stage):
    """The service account key never enters the container. The run holds an
    hour-long token, and asks the platform for a fresh one when its own is
    about to lapse — and the next call is made with that one."""
    r = stage(VERTEX, "vertex/gemini-3.1-pro-preview",
              env={"YAAF_MODEL_TOKEN_EXPIRES": str(int(time.time()) + 60)})
    asked = []

    def platform(method, path, payload=None, params=None):
        asked.append((method, path))
        return {"status": 200, "body": {"token": "fresh-token",
                                        "expires_at": int(time.time()) + 3600}}

    r.mp.setattr(r.base, "_call", platform)
    code, result = _drive(r, [_gemini([{"text": "ok"}])])
    assert code == 0, result["summary"]
    assert asked == [("POST", "/model-token")]
    assert r.requests[0]["headers"]["authorization"] == "Bearer fresh-token"
    assert r.requests[0]["url"].endswith(
        "/publishers/google/models/gemini-3.1-pro-preview:generateContent")


def test_a_vertex_token_with_time_left_is_not_refreshed(stage):
    r = stage(VERTEX, "vertex/gemini-3.1-pro-preview",
              env={"YAAF_MODEL_TOKEN_EXPIRES": str(int(time.time()) + 3000)})
    r.mp.setattr(r.base, "_call", lambda *a, **k: pytest.fail("refreshed with time left"))
    code, _ = _drive(r, [_gemini([{"text": "ok"}])])
    assert code == 0 and r.requests[0]["headers"]["authorization"] == "Bearer provider-key"


# ── Bedrock's Converse ──────────────────────────────────────────────────────────


def _converse(content: list[dict], stop: str = "end_turn", usage: dict | None = None) -> dict:
    return {"json": {"output": {"message": {"role": "assistant", "content": content}},
                     "stopReason": stop,
                     "usage": usage or {"inputTokens": 300, "outputTokens": 40,
                                        "cacheReadInputTokens": 700}}}


def test_bedrock_converse_is_metered_and_addressed_by_model_id(stage):
    r = stage(BEDROCK, "bedrock/us.amazon.nova-pro-v1:0")
    code, result = _drive(r, [_converse([{"text": "Nothing today."}])])
    assert code == 0, result["summary"]
    assert (result["tokens_in"], result["cache_read_tokens"], result["tokens_out"]) == (
        300, 700, 40)
    sent = r.requests[0]
    assert sent["url"] == ("https://bedrock-runtime.us-east-1.amazonaws.com/model/"
                           "us.amazon.nova-pro-v1%3A0/converse")
    assert sent["headers"]["authorization"] == "Bearer provider-key"
    assert sent["body"]["system"] == [{"text": "You are the analyst."}]
    assert any(t["toolSpec"]["name"] == "Write" for t in sent["body"]["toolConfig"]["tools"])


def test_bedrock_tool_call_round_trips(stage):
    r = stage(BEDROCK, "bedrock/us.amazon.nova-pro-v1:0")
    use = {"toolUse": {"toolUseId": "t1", "name": "Write",
                       "input": {"file_path": "b.md", "content": "ok"}}}
    code, result = _drive(r, [_converse([use], stop="tool_use"),
                              _converse([{"text": "Done."}])])
    assert code == 0, result["summary"]
    messages = r.requests[1]["body"]["messages"]
    assert messages[1] == {"role": "assistant", "content": [use]}
    result_block = messages[2]["content"][0]["toolResult"]
    assert result_block["toolUseId"] == "t1" and result_block["status"] == "success"


@pytest.mark.parametrize(("step", "code", "kind"), [
    ({"status": 403, "headers": {"x-amzn-errortype": "UnrecognizedClientException"},
      "json": {"message": "The security token included in the request is invalid."}},
     1, "auth"),
    ({"status": 400, "headers": {"x-amzn-errortype": "ThrottlingException"},
      "json": {"message": "Too many requests"}}, 0, None),
    ({"status": 400, "headers": {"x-amzn-errortype": "ValidationException"},
      "json": {"message": "Malformed input request"}}, 1, "fatal"),
    (_converse([{"text": ""}], stop="guardrail_intervened"), 1, "fatal"),
])
def test_bedrock_failures_are_classified(stage, step, code, kind):
    r = stage(BEDROCK, "bedrock/us.amazon.nova-pro-v1:0")
    got, result = _drive(r, [step, _converse([{"text": "ok"}])])
    assert got == code and result["error_kind"] == kind


# ── the platform's search, on a provider without its own ────────────────────────


def _chat_reply(text: str = "", calls: list[dict] | None = None) -> dict:
    lines = [{"choices": [{"index": 0, "delta": {"content": text}}]}] if text else []
    lines += [{"choices": [{"index": 0, "delta": {"tool_calls": [
        {"index": i, "id": c["id"], "type": "function",
         "function": {"name": c["name"], "arguments": json.dumps(c["input"])}}]}}]}
        for i, c in enumerate(calls or [])]
    lines += [{"choices": [{"index": 0, "delta": {},
                            "finish_reason": "tool_calls" if calls else "stop"}]},
              {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}]
    body = "".join(f"data: {json.dumps(line)}\n\n" for line in lines) + "data: [DONE]\n\n"
    return {"content": body.encode(), "headers": {"content-type": "text/event-stream"}}


def test_a_provider_without_search_searches_through_the_platform_backend(stage):
    """DeepSeek's chat API cannot search. With the install's backend set, the
    loop offers WebSearch from it: the query goes to the backend with the
    install's key, the results go back to the model, and the query is billed
    into the run at the install's price — under the cap like any call."""
    r = stage(DEEPSEEK, "deepseek-chat", perms={"web": True}, env={
        "YAAF_SEARCH": json.dumps({"backend": "brave", "price_usd": 0.004,
                                   "base_url": "https://api.search.brave.com/res/v1"}),
        "YAAF_SEARCH_KEY": "brave-install-key"})
    searched = []

    def brave(request: httpx.Request) -> httpx.Response:
        searched.append({"url": str(request.url),
                         "key": request.headers.get("x-subscription-token")})
        return httpx.Response(200, json={"web": {"results": [
            {"title": "yaaf", "url": "https://example.com/yaaf",
             "description": "an agent framework"}]}})

    r.mp.setattr(r.loop, "SEARCH_TRANSPORT", httpx.MockTransport(brave))
    call = {"id": "call_1", "name": "WebSearch", "input": {"query": "yaaf"}}
    code, result = _drive(r, [_chat_reply(calls=[call]), _chat_reply("Found it.")])
    assert code == 0, result["summary"]
    assert searched[0]["key"] == "brave-install-key" and "q=yaaf" in searched[0]["url"]
    answer = r.requests[1]["body"]["messages"][-1]
    assert answer["role"] == "tool" and "https://example.com/yaaf" in answer["content"]
    deepseek_price = {"input": 2.0, "output": 10.0, "cache_read": 0.2}
    calls = 2 * (100 * deepseek_price["input"] + 10 * deepseek_price["output"]) / 1e6
    assert result["cost_usd"] == pytest.approx(calls + 0.004)
    assert result["priced_usd"] == pytest.approx(calls + 0.004)
    shell = asyncio.run(r.loop.BUILTINS["Bash"].handler(
        {"command": "echo key=${YAAF_SEARCH_KEY:-none}"}))
    assert shell["content"][0]["text"] == "key=none\n"


def test_with_no_backend_and_no_provider_search_nothing_is_offered(stage):
    """R2 refuses such a seat at every door; a run that got here anyway is not
    handed a search that does not exist."""
    r = stage(DEEPSEEK, "deepseek-chat", perms={"web": True})
    _drive(r, [_chat_reply("ok")])
    names = {t["function"]["name"] for t in r.requests[0]["body"]["tools"]}
    assert "WebSearch" not in names and "WebFetch" in names


@pytest.mark.parametrize("near", [False, True], ids=["far", "near"])
@pytest.mark.parametrize(("spec", "model", "field"), [
    (OPENAI, "gpt-6.1-sol", lambda b: b.get("max_output_tokens")),
    (GEMINI, "gemini-3.1-pro-preview",
     lambda b: (b.get("generationConfig") or {}).get("maxOutputTokens")),
    (BEDROCK, "bedrock/us.amazon.nova-pro-v1:0",
     lambda b: (b.get("inferenceConfig") or {}).get("maxTokens")),
], ids=["responses", "gemini", "bedrock"])
def test_an_output_limit_is_sent_only_when_the_cap_needs_one(stage, spec, model, field, near):
    """A limit above a model's own ceiling is refused, and every run on that
    model would fail before it started. Far from the cap the model's default
    stands; near it, the governor's clamp is sent."""
    reply = {"openai_responses": _responses([_message("ok")]),
             "gemini": _gemini([{"text": "ok"}]),
             "bedrock": _converse([{"text": "ok"}])}[spec["protocol"]]
    r = stage(spec, model)
    if near:
        r.mp.setattr(r.base, "MAX_USD", 0.001)
    _drive(r, [reply])
    # $0.001 at $10 per million output tokens is 100 tokens.
    assert field(r.requests[0]["body"]) == (100 if near else None)


# ── the wire itself, against the vendors' own definitions ───────────────────────
#
# Each sample below is shaped by the vendor's machine-readable definition —
# OpenAI's OpenAPI spec, the `anthropic` and `google-genai` SDK types, the
# Gemini and Vertex discovery documents, botocore's `bedrock-runtime` model —
# or, for an error, by the wording the vendor documents for it.


def _sse(events: list, *, named: bool = False) -> dict:
    body = "".join((f"event: {e['type']}\n" if named else "") + f"data: {json.dumps(e)}\n\n"
                   for e in events)
    return {"content": (body + ("" if named else "data: [DONE]\n\n")).encode(),
            "headers": {"content-type": "text/event-stream"}}


def _call(endpoint: wire.Endpoint, script: list, *, transcript: list | None = None,
          tools: list | None = None, max_tokens: int = 100_000, **kw):
    """One `wire.call` against a scripted provider: the reply, and what was sent."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        step = script[len(seen) - 1]
        return httpx.Response(step.get("status", 200), headers=step.get("headers"),
                              content=step.get("content"), json=step.get("json"))

    async def go():
        return [r async for r in wire.call(
            endpoint, "m", "You are the analyst.",
            transcript or [wire.Turn("user", text="hi")], tools or [], max_tokens,
            transport=httpx.MockTransport(handler), **kw)]

    replies = asyncio.run(go())
    return replies[0], [json.loads(r.content) if r.content else None for r in seen]


CHAT = wire.Endpoint(protocol="openai_chat", base_url="https://api.example.com/v1")
RESPONSES = wire.Endpoint(protocol="openai_responses", base_url="https://api.openai.com/v1")
GOOGLE = wire.Endpoint(protocol="gemini", auth="x-goog-api-key",
                       base_url="https://generativelanguage.googleapis.com/v1beta")
CONVERSE = wire.Endpoint(protocol="bedrock",
                         base_url="https://bedrock-runtime.us-east-1.amazonaws.com")
MESSAGES = wire.Endpoint(protocol="anthropic", auth="x-api-key",
                         base_url="https://api.anthropic.com/v1")
WRITE = wire.ToolSpec("Write", "Write a file", {
    "type": "object", "properties": {"file_path": {"type": "string"},
                                     "content": {"type": "string"}},
    "required": ["file_path", "content"]})


# ── a prompt longer than the window is "context", by each vendor's own words ────

OVERFLOWS = {
    "openai": (400, {"error": {
        "message": "This model's maximum context length is 128000 tokens. However, your "
                   "messages resulted in 139020 tokens. Please reduce the length of the "
                   "messages.", "type": "invalid_request_error", "param": "messages",
        "code": "context_length_exceeded"}}),
    "openai_responses": (400, {"error": {
        "message": "Your input exceeds the context window of this model. Please adjust "
                   "your input and try again.", "type": "invalid_request_error",
        "param": "input", "code": "context_length_exceeded"}}),
    "anthropic": (400, {"type": "error", "error": {
        "type": "invalid_request_error",
        "message": "prompt is too long: 208310 tokens > 200000 maximum"}}),
    "anthropic_with_max_tokens": (400, {"type": "error", "error": {
        "type": "invalid_request_error",
        "message": "input length and `max_tokens` exceed context limit: 188000 + 21333 > "
                   "200000, decrease input length or `max_tokens` and try again"}}),
    "gemini": (400, {"error": {
        "code": 400, "message": "The input token count (1210551) exceeds the maximum number "
                                "of tokens allowed (1048576).", "status": "INVALID_ARGUMENT"}}),
    "bedrock": (400, {"message": "Input is too long for requested model."}),
    "bedrock_anthropic": (400, {"message": "prompt is too long: 210000 tokens > 200000 maximum"}),
    "deepseek": (400, {"error": {
        "message": "This model's maximum context length is 131072 tokens. However, you "
                   "requested 140000 tokens (132000 in the messages, 8000 in the completion).",
        "type": "invalid_request_error", "param": None, "code": "invalid_request_error"}}),
    "mistral": (400, {"object": "error", "message": "Prompt contains 140000 tokens and 0 "
                      "draft tokens, too large for model with 131072 maximum context length",
                      "type": "invalid_request_message_error", "code": "3051"}),
    "xai": (400, {"code": "Client specified an invalid argument", "error": "This model's "
                  "maximum prompt length is 131072 but the request contains 200000 tokens."}),
    "groq": (400, {"error": {"message": "Please reduce the length of the messages or "
                             "completion.", "type": "invalid_request_error",
                             "code": "context_length_exceeded"}}),
    "openrouter": (400, {"error": {"message": "This endpoint's maximum context length is "
                                   "65536 tokens. However, you requested about 70000 tokens.",
                                   "code": 400}}),
    "llama.cpp": (400, {"error": {"code": 400, "type": "exceed_context_size_error",
                                  "message": "the request exceeds the available context size, "
                                             "try increasing it"}}),
    "moonshot": (400, {"error": {"message": "Invalid request: Your request exceeded model "
                                            "token limit: 131072",
                                 "type": "invalid_request_error"}}),
}


@pytest.mark.parametrize("vendor", sorted(OVERFLOWS))
def test_a_prompt_too_long_for_the_window_is_context(vendor):
    status, body = OVERFLOWS[vendor]
    assert wire.classify(status, body) == "context"


@pytest.mark.parametrize(("status", "body", "markers", "kind"), [
    # Auth and transient keep their precedence over the window's words.
    (401, OVERFLOWS["openai"][1], (), "auth"),
    (429, OVERFLOWS["openai"][1], (), "transient"),
    (400, {"error": {"message": "Incorrect API key provided; maximum context length"}},
     ("incorrect api key provided",), "auth"),
    # A limit on output, or on rate, is not a prompt too long.
    (400, {"error": {"message": "max_tokens is too large: 100000. This model supports at "
                                "most 16384 completion tokens, whereas you provided 100000.",
                     "type": "invalid_request_error", "param": "max_tokens"}}, (), "fatal"),
    (429, {"error": {"message": "Request too large for gpt-6 on tokens per min (TPM): "
                                "Limit 30000, Requested 50000.", "code": "rate_limit_exceeded"}},
     (), "transient"),
    (400, {"error": {"message": "Invalid schema for function 'Write'", "code": None}}, (),
     "fatal"),
    # OpenAI's documented word for an empty balance: as dead as a 402.
    (429, {"error": {"message": "You exceeded your current quota, please check your plan "
                                "and billing details.", "type": "insufficient_quota",
                     "code": "insufficient_quota"}}, (), "auth"),
])
def test_context_classification_is_conservative(status, body, markers, kind):
    assert wire.classify(status, body, markers) == kind


@pytest.mark.parametrize(("endpoint", "status", "headers", "body"), [
    (CHAT, *OVERFLOWS["deepseek"][:1], None, OVERFLOWS["deepseek"][1]),
    (RESPONSES, 400, None, OVERFLOWS["openai_responses"][1]),
    (GOOGLE, 400, None, OVERFLOWS["gemini"][1]),
    (CONVERSE, 400, {"x-amzn-errortype": "ValidationException:http://internal.amazon.com/"
                                          "coral/com.amazon.bedrock/"}, OVERFLOWS["bedrock"][1]),
    (MESSAGES, 400, None, OVERFLOWS["anthropic"][1]),
], ids=["openai_chat", "openai_responses", "gemini", "bedrock", "anthropic"])
def test_every_wire_raises_an_overflow_as_context(endpoint, status, headers, body):
    with pytest.raises(wire.WireError) as refused:
        _call(endpoint, [{"status": status, "headers": headers, "json": body}])
    assert refused.value.kind == "context" and refused.value.status == status


def test_an_overflow_inside_a_stream_is_context_not_a_retry():
    chunk = {"error": {"message": "This model's maximum context length is 65536 tokens.",
                       "code": "context_length_exceeded"}}
    with pytest.raises(wire.WireError) as refused:
        _call(CHAT, [_sse([chunk])])
    assert refused.value.kind == "context"
    event = {"type": "error", "code": "context_length_exceeded", "sequence_number": 1,
             "message": "Your input exceeds the context window of this model.", "param": None}
    with pytest.raises(wire.WireError) as refused:
        _call(RESPONSES, [_sse([event], named=True)])
    assert refused.value.kind == "context"


# ── openai_chat: the stream as the spec writes it, and as hosts stray from it ───


def _chunk(delta: dict | None = None, finish: str | None = None, **extra) -> dict:
    return {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 1,
            "model": "m-2026", "choices": [{"index": 0, "delta": delta or {},
                                            "finish_reason": finish}], **extra}


def test_parallel_tool_calls_assemble_from_interleaved_fragments():
    """The spec's shape: `index` names the call; id, type and name come once on
    its first fragment; `arguments` arrive in pieces, two calls interleaved —
    one chunk may carry fragments of both. Usage is a last chunk with no
    choices, `usage: null` on every other."""
    first, second = json.dumps({"file_path": "a.md", "content": "A"}), json.dumps(
        {"file_path": "b.md", "content": "B"})
    chunks = [
        _chunk({"role": "assistant", "content": None, "tool_calls": [
            {"index": 0, "id": "call_a", "type": "function",
             "function": {"name": "Write", "arguments": ""}}]}, usage=None),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": first[:10]}},
                               {"index": 1, "id": "call_b", "type": "function",
                                "function": {"name": "Write", "arguments": second[:7]}}]}),
        _chunk({"tool_calls": [{"index": 1, "function": {"arguments": second[7:]}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": first[10:]}}]}),
        _chunk(finish="tool_calls"),
        {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 1, "model": "m-2026",
         "choices": [], "usage": {
             "prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500,
             "prompt_tokens_details": {"cached_tokens": 1000, "audio_tokens": 0},
             "completion_tokens_details": {"reasoning_tokens": 200, "audio_tokens": 0}}},
    ]
    reply, _ = _call(CHAT, [_sse(chunks)], tools=[WRITE])
    assert reply.stop == "tool_use" and reply.model == "m-2026"
    assert [(c.id, c.input["file_path"]) for c in reply.turn.calls] == [
        ("call_a", "a.md"), ("call_b", "b.md")]
    # cached_tokens is inside prompt_tokens; reasoning inside completion_tokens.
    assert (reply.usage.input, reply.usage.cache_read, reply.usage.output) == (200, 1000, 300)
    assert reply.turn.native["tool_calls"][1]["function"]["arguments"] == second


@pytest.mark.parametrize("host", ["no_index", "index_reused", "name_repeated"])
def test_tool_call_fragments_from_hosts_that_stray_from_the_spec(host):
    first, second = '{"file_path": "a.md", "content": "A"}', '{"file_path": "b.md", "content": "B"}'
    if host == "no_index":
        parts = [[{"id": "call_a", "type": "function",
                   "function": {"name": "Write", "arguments": first[:5]}}],
                 [{"function": {"arguments": first[5:]}}],
                 [{"id": "call_b", "type": "function",
                   "function": {"name": "Write", "arguments": second}}]]
    elif host == "index_reused":
        parts = [[{"index": 0, "id": "call_a", "type": "function",
                   "function": {"name": "Write", "arguments": first}}],
                 [{"index": 0, "id": "call_b", "type": "function",
                   "function": {"name": "Write", "arguments": second}}]]
    else:
        parts = [[{"index": 0, "id": "call_a", "function": {"name": "Write", "arguments": ""}}],
                 [{"index": 0, "id": "call_a",
                   "function": {"name": "Write", "arguments": first[:9]}}],
                 [{"index": 0, "id": "call_a",
                   "function": {"name": "Write", "arguments": first[9:]}}],
                 [{"index": 1, "id": "call_b",
                   "function": {"name": "Write", "arguments": second}}]]
    chunks = [_chunk({"tool_calls": p}) for p in parts] + [_chunk(finish="tool_calls")]
    reply, _ = _call(CHAT, [_sse(chunks)], tools=[WRITE])
    assert [(c.id, c.name, c.input.get("file_path")) for c in reply.turn.calls] == [
        ("call_a", "Write", "a.md"), ("call_b", "Write", "b.md")]


def test_a_delta_whose_content_is_a_list_of_chunks_is_read_for_its_text():
    """Mistral's reasoning models stream `content` as chunks, thinking among them."""
    chunks = [_chunk({"content": [{"type": "thinking", "thinking": [
        {"type": "text", "text": "hmm"}]}]}),
        _chunk({"content": [{"type": "text", "text": "Done."}]}), _chunk(finish="stop")]
    reply, _ = _call(CHAT, [_sse(chunks)])
    assert reply.turn.text == "Done." and reply.stop == "end"


def test_chat_request_matches_the_spec():
    _, sent = _call(CHAT, [_sse([_chunk({"content": "ok"}, "stop")])], tools=[WRITE],
                    max_tokens=500, effort="high")
    body = sent[0]
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert body["tools"][0] == {"type": "function", "function": {
        "name": "Write", "description": "Write a file", "parameters": WRITE.input_schema}}
    assert body["max_tokens"] == 500 and body["reasoning_effort"] == "high"


# ── openai_responses ────────────────────────────────────────────────────────────


def test_responses_request_matches_the_spec():
    """`strict` is required on a function tool, and a strict schema must close
    every object — so it is stated false. `max_output_tokens` has a floor of
    16. A turn this wire did not write goes back as the spec's easy message."""
    transcript = [wire.Turn("user", text="hi"), wire.Turn("assistant", text="Looking."),
                  wire.Turn("user", text="go on")]
    reply = _responses([_message("ok")])
    _, sent = _call(RESPONSES, [reply], transcript=transcript, tools=[WRITE], max_tokens=5)
    body = sent[0]
    assert body["tools"] == [{"type": "function", "name": "Write",
                              "description": "Write a file",
                              "parameters": WRITE.input_schema, "strict": False}]
    assert body["max_output_tokens"] == 16
    assert body["input"][1] == {"role": "assistant", "content": "Looking."}


def test_responses_usage_counts_reasoning_once_and_searches_per_call():
    usage = {"input_tokens": 5000, "input_tokens_details": {"cached_tokens": 4000,
                                                            "cache_write_tokens": 0},
             "output_tokens": 900, "output_tokens_details": {"reasoning_tokens": 700},
             "total_tokens": 5900}
    searched = {"type": "web_search_call", "id": "ws_1", "status": "completed",
                "action": {"type": "search", "query": "q"}}
    reply, _ = _call(RESPONSES, [_responses([REASONING, searched, searched, _message("x")],
                                            usage=usage)])
    assert (reply.usage.input, reply.usage.cache_read, reply.usage.output) == (1000, 4000, 900)
    assert reply.usage.web_searches == 2


# ── gemini ──────────────────────────────────────────────────────────────────────


def test_gemini_declarations_hold_only_what_its_schema_has():
    """Gemini's `Schema` is a closed set of fields; anything else is an
    unknown name and a refused request. References are inlined, a type list
    becomes a type and `nullable`, `oneOf` the `anyOf` it means — and a
    property that happens to be *named* like a keyword stays a property."""
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "additionalProperties": False,
        "properties": {
            "default": {"type": "string", "description": "a property called default"},
            "mode": {"type": ["string", "null"], "enum": ["a", "w"]},
            "opts": {"$ref": "#/$defs/Opts"},
            "choice": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
            "level": {"type": "integer", "enum": [1, 2]},
            "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        },
        "required": ["default", "gone"],
        "$defs": {"Opts": {"type": "object", "additionalProperties": False,
                           "properties": {"x": {"type": "number", "exclusiveMinimum": 0}}}}}
    tools = [wire.ToolSpec("Shape", "shapes", schema),
             wire.ToolSpec("Ping", "takes nothing", {"type": "object", "properties": {}})]
    body = wire.gemini_request("sys", [wire.Turn("user", text="hi")], tools, 100)
    shape, ping = body["tools"][0]["functionDeclarations"]
    assert shape["parameters"] == {
        "type": "object",
        "properties": {
            "default": {"type": "string", "description": "a property called default"},
            "mode": {"type": "string", "nullable": True, "enum": ["a", "w"]},
            "opts": {"type": "object", "properties": {"x": {"type": "number"}}},
            "choice": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            "level": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}}},
        "required": ["default"]}
    # An OBJECT with no properties is refused: a tool that takes nothing
    # declares no parameters.
    assert ping == {"name": "Ping", "description": "takes nothing"}


def test_gemini_effort_is_its_thinking_level_enum():
    for effort, level in [("low", "LOW"), ("medium", "MEDIUM"), ("high", "HIGH"),
                          ("max", "HIGH")]:
        body = wire.gemini_request("s", [wire.Turn("user", text="q")], [], 100, effort)
        assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": level}


@pytest.mark.parametrize("finish", ["MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL",
                                    "MISSING_THOUGHT_SIGNATURE", "PROHIBITED_CONTENT"])
def test_a_gemini_finish_that_is_no_answer_stops_the_run_by_name(finish):
    reply = wire.gemini_reply({"candidates": [{"finishReason": finish,
                                               "finishMessage": "the call was malformed"}],
                               "usageMetadata": {"promptTokenCount": 10}})
    assert reply.stop == "refusal" and finish in reply.detail


def test_a_gemini_turn_not_written_by_gemini_replays_validly():
    """A rebuilt function call carries the documented stand-in signature, and
    a reply that came back with no parts (cut off while thinking) is not sent
    back as an empty content."""
    transcript = [wire.Turn("user", text="hi"), wire.Turn("assistant", native=[]),
                  wire.Turn("user", text="again"),
                  wire.Turn("assistant", calls=(wire.ToolCall("call_0", "Write", {}),)),
                  wire.Turn("user", outputs=(wire.ToolOutput("call_0", "ok"),))]
    contents = wire.gemini_request("s", transcript, [WRITE], 100)["contents"]
    assert [c["role"] for c in contents] == ["user", "user", "model", "user"]
    assert contents[2]["parts"] == [{"functionCall": {"name": "Write", "args": {}},
                                     "thoughtSignature": "skip_thought_signature_validator"}]
    assert contents[3]["parts"][0]["functionResponse"] == {"name": "Write",
                                                           "response": {"content": "ok"}}


# ── bedrock ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("stop", ["malformed_tool_use", "malformed_model_output"])
def test_a_malformed_converse_reply_stops_the_run_by_name(stop):
    reply = wire.bedrock_reply({"output": {"message": {"role": "assistant", "content": []}},
                                "stopReason": stop, "usage": {"inputTokens": 1,
                                                              "outputTokens": 1,
                                                              "totalTokens": 2}})
    assert reply.stop == "refusal" and stop in reply.detail


def test_converse_sends_no_blank_system_block():
    assert "system" not in wire.bedrock_request("", [wire.Turn("user", text="hi")], [], 100)


# ── anthropic: the server search, when asked for ────────────────────────────────


def test_anthropic_search_is_its_server_tool_kept_for_replay_and_counted():
    content = [
        {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
         "input": {"query": "yaaf"}},
        {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
            {"type": "web_search_result", "url": "https://example.com/yaaf", "title": "yaaf",
             "encrypted_content": "opaque", "page_age": None}]},
        {"type": "text", "text": "yaaf is a framework.", "citations": None}]
    message = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-x",
               "content": content, "stop_reason": "end_turn", "stop_sequence": None,
               "usage": {"input_tokens": 100, "output_tokens": 20,
                         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                         "server_tool_use": {"web_search_requests": 1,
                                             "web_fetch_requests": 0}}}
    reply, sent = _call(MESSAGES, [{"json": message}], tools=[WRITE], web_search=True)
    assert sent[0]["tools"][-1] == {"type": "web_search_20250305", "name": "web_search"}
    assert reply.usage.web_searches == 1 and reply.turn.text == "yaaf is a framework."
    replay = wire.anthropic_request("m", "s", [wire.Turn("user", text="hi"), reply.turn,
                                               wire.Turn("user", text="more")], [], 100)
    assert replay["messages"][1] == {"role": "assistant", "content": content}
    assert "tools" not in replay
    _, sent = _call(MESSAGES, [{"json": message}], tools=[WRITE])
    assert all(t.get("type") != "web_search_20250305" for t in sent[0]["tools"])


# ── what a key can see: every page of it ────────────────────────────────────────


def test_a_paged_model_list_is_followed_to_its_end():
    def listing(endpoint, pages):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=pages[len(seen) - 1])
        names = asyncio.run(wire.list_models(endpoint, transport=httpx.MockTransport(handler)))
        return names, [str(r.url) for r in seen]

    names, urls = listing(MESSAGES, [
        {"data": [{"id": "claude-a", "type": "model"}], "has_more": True,
         "first_id": "claude-a", "last_id": "claude-a"},
        {"data": [{"id": "claude-b", "type": "model"}], "has_more": False,
         "first_id": "claude-b", "last_id": "claude-b"}])
    assert names == ["claude-a", "claude-b"]
    assert urls == ["https://api.anthropic.com/v1/models",
                    "https://api.anthropic.com/v1/models?limit=1000&after_id=claude-a"]
    names, urls = listing(GOOGLE, [{"models": [{"name": "models/gemini-a"}],
                                    "nextPageToken": "t2"},
                                   {"models": [{"name": "models/gemini-b"}]}])
    assert names == ["gemini-a", "gemini-b"] and urls[1].endswith("?pageSize=1000&pageToken=t2")
    names, urls = listing(CONVERSE, [
        {"modelSummaries": [{"modelId": "amazon.nova-pro-v1:0", "modelArn": "arn:x"}]},
        {"inferenceProfileSummaries": [{"inferenceProfileId": "us.amazon.nova-pro-v1:0"}],
         "nextToken": "n2"},
        {"inferenceProfileSummaries": [{"inferenceProfileId": "eu.amazon.nova-pro-v1:0"}]}])
    assert names == ["amazon.nova-pro-v1:0", "eu.amazon.nova-pro-v1:0",
                     "us.amazon.nova-pro-v1:0"]
    assert urls[1] == ("https://bedrock.us-east-1.amazonaws.com/inference-profiles"
                       "?maxResults=1000")
