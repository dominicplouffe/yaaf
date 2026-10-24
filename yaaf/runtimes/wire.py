"""Every provider's wire: how a transcript becomes a request, and a reply a turn.

**Imports nothing from `yaaf`.** The sandbox image carries single files, not
the package, and the same translation is needed on both sides of it: inside
the container, yaaf's agent loop speaks it to run a seat (`yaaf_loop`); in the
control plane, the Founder and the goal grader speak it, the credential check
lists a provider's models with it, model discovery reads them, and `yaaf
smoke` makes its one real call through it. One copy of "how to talk to
Gemini", read from both sides — the `harnesses.py` discipline, held by
`tests/selftest_invariants/test_runtime_registries.py`.

A protocol is a shape, not a vendor. `openai_chat` is DeepSeek, Mistral, Groq,
OpenRouter's hundreds of models and a local Ollama alike; `openai_responses`
is OpenAI and Azure OpenAI; `gemini` is the Gemini API and Vertex AI. Which
protocol a provider speaks, at which address, authenticated how, is the
provider registry's (yaaf.runtimes.providers) — handed to this file as a plain
`Endpoint`, so a new provider of a known shape is a registry row and nothing
here (docs/PROVIDERS.md §3).

The Anthropic protocol is here for the control plane's own calls (the live
check, discovery, smoke). Inside the sandbox the loop keeps the official
`anthropic` SDK for it: the governor abandons a call at `message_start`, and the
SDK's handling of cache breakpoints and mid-stream errors is not worth
re-deriving (yaaf/runner/loop_entry.py).

Plain httpx throughout: each wire is a request and a JSON reply, and an SDK per
vendor would be a pin per vendor for what a page of mapping does.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

CONNECT_TIMEOUT_S = 30
READ_TIMEOUT_S = 600

#: The most output tokens every model of every non-Anthropic wire accepts as a
#: limit. Below it the governor's clamp is sent; above it the model's own
#: default stands — DeepSeek's chat model refuses more than 8,192, Bedrock's
#: Nova and Llama models refuse more than theirs, and a limit a model refuses
#: is a run that never starts. Anthropic's wire always takes one.
SAFE_MAX_TOKENS = 8192

#: The protocols this file speaks. A provider naming another is refused by the
#: registry test, not discovered at 3am.
PROTOCOLS = ("anthropic", "openai_chat", "openai_responses", "gemini", "bedrock")

#: How a key is presented. `none` is a local server that wants nothing.
AUTH_STYLES = ("bearer", "x-api-key", "api-key", "x-goog-api-key", "none")

#: The transport every call here uses when its caller names none — httpx's
#: own, or a test's stand-in provider. One seam, so a test of the live check,
#: of discovery or of the Founder points one variable at a fake.
TRANSPORT: httpx.AsyncBaseTransport | None = None


# ── the shapes every wire speaks ───────────────────────────────────────────────


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class ToolOutput:
    call_id: str
    text: str
    is_error: bool = False


@dataclass(frozen=True)
class Turn:
    """One message of the transcript. A caller reads `text` and `calls`; the
    wire that produced an assistant turn replays `native`, its own form of it,
    verbatim — thinking, signatures and server tool results must go back
    exactly as they came, because a transcript is append-only."""
    role: str  # "user" | "assistant"
    text: str = ""
    calls: tuple[ToolCall, ...] = ()
    outputs: tuple[ToolOutput, ...] = ()
    native: Any = None


@dataclass(frozen=True)
class Usage:
    """One call's bill in tokens. `input` is the uncached remainder of the
    prompt — the convention every Anthropic surface reports and yaaf prices
    by — with the cached rest in `cache_read` and `cache_write`. Reasoning is
    in `output`: every provider bills it at the output rate, wherever it
    counts it. `cost_usd` only where the provider states it (OpenRouter)."""
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    web_searches: int = 0
    cost_usd: float | None = None


@dataclass(frozen=True)
class Reply:
    """One finished model call. `stop` is the loop's word for why it ended:
    "end", "tool_use", "pause" (the provider's own server-side loop paused;
    send the transcript again as it is), "max_tokens", or "refusal"."""
    turn: Turn
    stop: str
    usage: Usage
    model: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class Endpoint:
    """A provider as a wire needs it: where, how to authenticate, and the few
    facts that change what a reply means. Built from the registry by the
    control plane, and from `YAAF_PROVIDER` inside the sandbox."""
    protocol: str
    base_url: str
    key: str = ""
    auth: str = "bearer"
    name: str = ""
    #: Whether each reply states its own cost (OpenRouter's `usage.cost`).
    reports_cost: bool = False
    #: Lower-case fragments of this provider's words for a dead key, for a
    #: provider that refuses one with a status that says nothing (xAI's 400).
    failure_markers: tuple[str, ...] = ()
    headers: dict = field(default_factory=dict)

    @classmethod
    def from_spec(cls, spec: dict, key: str) -> Endpoint:
        return cls(protocol=spec.get("protocol") or "openai_chat",
                   base_url=(spec.get("base_url") or "").rstrip("/"), key=key,
                   auth=spec.get("auth") or "bearer", name=spec.get("name") or "",
                   reports_cost=bool(spec.get("reports_cost")),
                   failure_markers=tuple(spec.get("failure_markers") or ()),
                   headers=dict(spec.get("headers") or {}))


class WireError(RuntimeError):
    """The provider refused the call, before or during the reply. `status` is
    the HTTP status — or the error's own code, for an error that arrived inside
    a stream whose status was already 200. `kind` is what a run does about it:
    "auth" (a dead credential: pause the project on the third), "transient"
    (resume the same transcript), "context" (the prompt is longer than the
    model's window: the same transcript will be refused the same way, a
    shorter one may not) or "fatal"."""

    def __init__(self, status: int, body: object, kind: str):
        message = body
        if isinstance(body, dict):
            error = body.get("error", body)
            message = (error.get("message", error) if isinstance(error, dict)
                       else body.get("message", error))
        super().__init__(f"HTTP {status}: {str(message)[:1000]}")
        self.status = status
        self.body = body
        self.kind = kind


#: Each vendor's words for a prompt longer than the model's window, lower-case.
#: Matched only once a refusal is neither a dead key nor worth a retry, and
#: only phrases that say "too long" of the prompt: a limit on output, on rate
#: or on request bytes is not this, and making room would not fix it.
_CONTEXT_MARKERS = (
    "context_length_exceeded",  # OpenAI, Azure, Groq: the error code
    "maximum context length",  # OpenAI, DeepSeek, Mistral, vLLM, OpenRouter
    "exceeds the context window",  # OpenAI's Responses API
    "prompt is too long",  # Anthropic, Anthropic on Bedrock and Vertex, Fireworks
    "exceed context limit",  # Anthropic: input length and `max_tokens` exceed ...
    "exceeds the maximum number of tokens allowed",  # Gemini, Vertex
    "input is too long",  # Bedrock
    "too many input tokens",  # Bedrock
    "input tokens exceed",  # Bedrock's Nova: "number of input tokens exceeds ..."
    "maximum prompt length",  # xAI
    "exceeded model token limit",  # Moonshot
    "range of input length should be",  # Qwen (DashScope)
    "reduce the length of the messages",  # OpenAI-compatible servers' advice
    "available context size",  # llama.cpp
    "exceed_context_size_error",  # llama.cpp: the error type
)


def _said(body: object) -> str:
    return body.lower() if isinstance(body, str) else json.dumps(body, default=str).lower()


def overflowed(body: object) -> bool:
    """Whether a refusal says the prompt did not fit the model's window."""
    said = _said(body)
    return any(marker in said for marker in _CONTEXT_MARKERS)


def classify(status: int, body: object, markers: tuple[str, ...] = ()) -> str:
    """A refusal by its status, then by the provider's own words. 402 is an
    empty balance: as dead as a revoked key until somebody pays, which is what
    the three-strikes pause is for — and so is OpenAI's 429
    `insufficient_quota`, its documented word for the same thing, which no
    retry outlasts."""
    said = _said(body)
    if status in (401, 402, 403) or (status == 429 and "insufficient_quota" in said):
        return "auth"
    if status in (408, 409, 425, 429) or status >= 500:
        return "transient"
    if any(marker in said for marker in markers):
        return "auth"
    if overflowed(said):
        return "context"
    return "fatal"


def headers(endpoint: Endpoint) -> dict[str, str]:
    out = {"content-type": "application/json", **endpoint.headers}
    if endpoint.auth == "bearer" and endpoint.key:
        out["authorization"] = f"Bearer {endpoint.key}"
    elif endpoint.auth == "x-api-key":
        out["x-api-key"] = endpoint.key
    elif endpoint.auth == "api-key":
        out["api-key"] = endpoint.key
    elif endpoint.auth == "x-goog-api-key":
        out["x-goog-api-key"] = endpoint.key
    if endpoint.protocol == "anthropic":
        out.setdefault("anthropic-version", "2023-06-01")
    return out


def _timeout(read: float | None = None) -> httpx.Timeout:
    return httpx.Timeout(CONNECT_TIMEOUT_S, read=read or READ_TIMEOUT_S)


async def _refused(response: httpx.Response, endpoint: Endpoint) -> WireError:
    raw = await response.aread()
    try:
        body: object = json.loads(raw)
    except ValueError:
        body = raw.decode(errors="replace")
    return WireError(response.status_code, body,
                     classify(response.status_code, body, endpoint.failure_markers))


def _arguments(raw: Any) -> dict:
    """A tool call's arguments as an object. A string that does not parse is
    kept, clipped, where the model can see what it sent."""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        parsed = None
    return parsed if isinstance(parsed, dict) else {"_unparsed_arguments": str(raw)[:2000]}


def _names_by_call(transcript: list[Turn]) -> dict[str, str]:
    """call id → tool name, for a wire whose tool results name the tool."""
    return {c.id: c.name for t in transcript for c in t.calls}


# ── openai_chat: the chat-completions shape ────────────────────────────────────

_CHAT_STOPS = {"stop": "end", "tool_calls": "tool_use", "function_call": "tool_use",
               "length": "max_tokens", "content_filter": "refusal"}


def chat_request(endpoint: Endpoint, model: str, system: str, transcript: list[Turn],
                 tools: list[ToolSpec], max_tokens: int, effort: str | None = None) -> dict:
    messages: list[dict] = [{"role": "system", "content": system}]
    for turn in transcript:
        if turn.role == "assistant":
            messages.append(turn.native if turn.native is not None else {
                "role": "assistant", "content": turn.text or None,
                **({"tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.input)}}
                    for c in turn.calls]} if turn.calls else {})})
        elif turn.outputs:
            # No error flag in this shape: the text says so instead.
            messages += [{"role": "tool", "tool_call_id": o.call_id,
                          "content": f"ERROR: {o.text}" if o.is_error else o.text}
                         for o in turn.outputs]
        else:
            messages.append({"role": "user", "content": turn.text})
    body: dict = {"model": model, "messages": messages, "stream": True,
                  "stream_options": {"include_usage": True}}
    if tools:
        body["tools"] = [{"type": "function", "function": {
            "name": t.name, "description": t.description, "parameters": t.input_schema}}
            for t in tools]
    if max_tokens < SAFE_MAX_TOKENS:
        # OpenAI's spec deprecates this for `max_completion_tokens`, and its
        # reasoning models refuse it — but OpenAI is on the Responses wire,
        # and the hosts on this one (DeepSeek, Mistral, OpenRouter, Ollama...)
        # document `max_tokens`, where the newer name is not universal.
        body["max_tokens"] = max_tokens
    if effort:
        body["reasoning_effort"] = effort
    if endpoint.reports_cost:
        # OpenRouter's switch for the cost on the last line. Newer OpenRouter
        # always sends it and ignores this; older needs it.
        body["usage"] = {"include": True}
    return body


def chat_usage(usage: dict | None, reports_cost: bool) -> Usage:
    """The last line's usage. `prompt_tokens` counts the cached part too, which
    DeepSeek states as `prompt_cache_hit_tokens` and the OpenAI shape as
    `prompt_tokens_details.cached_tokens`. The OpenAI spec now also states
    `cache_write_tokens`; they stay in `input`, priced at the input rate,
    because an install's price row for a model on this wire need not carry a
    cache-write rate, and a missing rate would meter them as free. The
    output is the larger of `completion_tokens` and
    what the total leaves after the prompt: the OpenAI shape counts reasoning
    inside the completion, xAI's beside it, and only the total says which."""
    usage = usage or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or 0)
    details = usage.get("prompt_tokens_details") or {}
    cached = int(usage.get("prompt_cache_hit_tokens")
                 or (details.get("cached_tokens") if isinstance(details, dict) else 0) or 0)
    cost = usage.get("cost") if reports_cost else None
    return Usage(input=max(0, prompt - cached), output=max(completion, total - prompt),
                 cache_read=cached,
                 cost_usd=float(cost) if isinstance(cost, int | float) else None)


def chat_reply(text: str, calls: list[dict], finish: str | None, usage: Usage,
               served: str | None, reasoning: str = "") -> Reply:
    parsed = tuple(ToolCall(c["id"], c["name"], _arguments(c["arguments"])) for c in calls)
    stop = _CHAT_STOPS.get(finish or "", "end")
    if parsed and stop == "end":
        # Some providers say "stop" with tool calls in hand.
        stop = "tool_use"
    native: dict = {"role": "assistant", "content": text or None}
    if calls:
        native["tool_calls"] = [{"id": c["id"], "type": "function",
                                 "function": {"name": c["name"], "arguments": c["arguments"]}}
                                for c in calls]
        # Thinking streamed beside a turn that called tools goes back with
        # it, under the name it came by: DeepSeek's thinking mode (V4) and
        # Kimi refuse a tool loop that drops it, and ignore it on a turn
        # without calls — so a turn that called nothing does not carry it.
        # A host that never sends `reasoning_content` is never sent one.
        if reasoning:
            native["reasoning_content"] = reasoning
    return Reply(turn=Turn("assistant", text=text, calls=parsed, native=native),
                 stop=stop, usage=usage, model=served,
                 detail="the provider's content filter stopped the reply"
                 if stop == "refusal" else None)


def _chat_text(content: Any) -> str:
    """A delta's text. The spec's is a string; Mistral's reasoning models send
    a list of chunks, its thinking among them, of which only `text` is the
    reply."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(c.get("text") or "") for c in content
                       if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _complete(arguments: str) -> bool:
    try:
        return isinstance(json.loads(arguments), dict)
    except ValueError:
        return False


def _merge_call(calls: list[dict], slots: dict, part: dict) -> None:
    """One streamed fragment of a tool call into the call it continues. The
    spec's shape: `index` names the call, its `id` and name come on its first
    fragment, and `arguments` arrive in pieces. Hosts on this wire stray from
    it: no `index` at all, one `index` reused for parallel calls that each
    arrive whole under their own id, the name repeated on every fragment. A
    fragment starts a new call when it says so by an index not yet seen — or
    by a new id and a name after a call whose arguments are already whole."""
    function = part.get("function") or {}
    call_id, name = part.get("id") or "", function.get("name") or ""
    index = part.get("index")
    slot = slots.get(index) if index is not None else (len(calls) - 1 if calls else None)
    if (slot is not None and call_id and name and calls[slot]["id"]
            and call_id != calls[slot]["id"] and _complete(calls[slot]["arguments"])):
        slot = None
    if slot is None:
        calls.append({"id": "", "name": "", "arguments": ""})
        slot = len(calls) - 1
    if index is not None:
        slots[index] = slot
    call = calls[slot]
    call["id"] = call_id or call["id"]
    if name != call["name"]:
        call["name"] += name
    call["arguments"] += function.get("arguments") or ""


async def _chat(http: httpx.AsyncClient, endpoint: Endpoint, body: dict):
    text: list[str] = []
    reasoning: list[str] = []
    calls: list[dict] = []
    slots: dict = {}
    finish: str | None = None
    usage: dict | None = None
    served: str | None = None
    async with http.stream("POST", f"{endpoint.base_url}/chat/completions",
                           headers=headers(endpoint), json=body) as response:
        if response.status_code >= 400:
            raise await _refused(response, endpoint)
        async for line in response.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if chunk.get("error"):
                error = chunk["error"]
                code = error.get("code") if isinstance(error, dict) else None
                status = code if isinstance(code, int) else 200
                raise WireError(status, chunk,
                                ("context" if overflowed(chunk) else "transient")
                                if status == 200 else
                                classify(status, chunk, endpoint.failure_markers))
            served = chunk.get("model") or served
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                # Moonshot's documented place for it: inside the last choice.
                if choice.get("usage") and not chunk.get("usage"):
                    usage = choice["usage"]
                delta = choice.get("delta") or {}
                text.append(_chat_text(delta.get("content")))
                if isinstance(delta.get("reasoning_content"), str):
                    reasoning.append(delta["reasoning_content"])
                for part in delta.get("tool_calls") or []:
                    if isinstance(part, dict):
                        _merge_call(calls, slots, part)
                finish = choice.get("finish_reason") or finish
    yield chat_reply("".join(text), calls, finish, chat_usage(usage, endpoint.reports_cost),
                     served, "".join(reasoning))


# ── openai_responses: OpenAI's own API, and Azure's ────────────────────────────


def responses_request(model: str, system: str, transcript: list[Turn], tools: list[ToolSpec],
                      max_tokens: int, effort: str | None = None,
                      web_search: bool = False) -> dict:
    items: list[dict] = []
    for turn in transcript:
        if turn.role == "assistant":
            # A turn this wire did not write goes back as the spec's easy
            # message: an `output_text` part must carry what only the server
            # writes (`annotations`, `logprobs`).
            items += turn.native if turn.native is not None else [
                *([{"role": "assistant", "content": turn.text}] if turn.text else []),
                *({"type": "function_call", "call_id": c.id, "name": c.name,
                   "arguments": json.dumps(c.input)} for c in turn.calls)]
        elif turn.outputs:
            items += [{"type": "function_call_output", "call_id": o.call_id,
                       "output": f"ERROR: {o.text}" if o.is_error else o.text}
                      for o in turn.outputs]
        else:
            items.append({"role": "user", "content": turn.text})
    body: dict = {
        "model": model, "instructions": system, "input": items,
        # `strict` is required by the spec, and a strict tool's schema must
        # close every object and require every property — which an MCP
        # server's schema need not. Stated, so no default decides it.
        "tools": [{"type": "function", "name": t.name, "description": t.description,
                   "parameters": t.input_schema, "strict": False} for t in tools],
        # Nothing is kept on the provider's side: the transcript is yaaf's,
        # replayed whole, which is what makes a resume the same call again.
        # Reasoning comes back encrypted so it can be replayed without being
        # stored.
        "store": False, "include": ["reasoning.encrypted_content"],
    }
    if max_tokens < SAFE_MAX_TOKENS:
        # The spec's floor: a limit below 16 is refused, not honoured.
        body["max_output_tokens"] = max(16, max_tokens)
    if web_search:
        body["tools"].append({"type": "web_search"})
    if effort:
        body["reasoning"] = {"effort": effort}
    return body


def responses_reply(response: dict) -> Reply:
    output = [item for item in response.get("output") or [] if isinstance(item, dict)]
    text: list[str] = []
    calls: list[ToolCall] = []
    refusal: str | None = None
    searches = 0
    for item in output:
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    text.append(part.get("text") or "")
                elif part.get("type") == "refusal":
                    refusal = part.get("refusal") or "refused"
        elif kind == "function_call":
            calls.append(ToolCall(item.get("call_id") or item.get("id") or "",
                                  item.get("name") or "", _arguments(item.get("arguments"))))
        elif kind == "web_search_call":
            searches += 1
    usage = response.get("usage") or {}
    prompt = int(usage.get("input_tokens") or 0)
    cached = int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    # output_tokens includes reasoning_tokens in this shape.
    bill = Usage(input=max(0, prompt - cached), output=int(usage.get("output_tokens") or 0),
                 cache_read=cached, web_searches=searches)
    status = response.get("status")
    reason = (response.get("incomplete_details") or {}).get("reason")
    if refusal is not None or reason == "content_filter":
        stop = "refusal"
    elif status == "incomplete" and reason == "max_output_tokens":
        stop = "max_tokens"
    elif calls:
        stop = "tool_use"
    else:
        stop = "end"
    return Reply(turn=Turn("assistant", text="".join(text), calls=tuple(calls), native=output),
                 stop=stop, usage=bill, model=response.get("model"),
                 detail=refusal if stop == "refusal" else None)


async def _responses(http: httpx.AsyncClient, endpoint: Endpoint, body: dict):
    final: dict | None = None
    async with http.stream("POST", f"{endpoint.base_url}/responses",
                           headers=headers(endpoint), json={**body, "stream": True}) as response:
        if response.status_code >= 400:
            raise await _refused(response, endpoint)
        async for line in response.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            event = json.loads(data)
            kind = event.get("type")
            if kind in ("response.completed", "response.incomplete"):
                final = event.get("response") or {}
            elif kind == "response.failed":
                failure = (event.get("response") or {}).get("error") or {}
                code = str(failure.get("code") or "")
                raise WireError(200, {"error": failure},
                                "transient" if code in ("server_error", "rate_limit_exceeded")
                                else classify(400, failure, endpoint.failure_markers))
            elif kind == "error":
                # The spec's event is flat (`code`, `message`); a nested
                # `error` object is read too, as the failed response holds it.
                nested = event.get("error") if isinstance(event.get("error"), dict) else {}
                code = str(event.get("code") or nested.get("code") or "")
                raise WireError(200, {"error": {**nested, **event}},
                                "auth" if "auth" in code or "api_key" in code
                                else "context" if overflowed(event)
                                else "transient" if code in ("server_error",
                                                             "rate_limit_exceeded", "")
                                else "fatal")
    if final is None:
        raise WireError(200, {"error": {"message": "the stream ended without a response"}},
                        "transient")
    yield responses_reply(final)


# ── gemini: the Gemini API, and Vertex AI ──────────────────────────────────────

_GEMINI_REFUSALS = ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII",
                    "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION",
                    "LANGUAGE", "ESCALATION", "MODEL_ARMOR", "PUP_LIMITED_DISABLED")
#: Finishes that are no answer at all: the call went wrong on Google's side
#: of the reply. Read as "end" they would close a run as done with nothing
#: done, so they stop it, loudly, by name.
_GEMINI_FAILURES = ("MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL", "TOO_MANY_TOOL_CALLS",
                    "MISSING_THOUGHT_SIGNATURE", "MALFORMED_RESPONSE")
#: yaaf's effort rungs as the API's `thinkingLevel` enum (MINIMAL, LOW, MEDIUM,
#: HIGH): there is nothing above HIGH to send a stronger rung as.
_GEMINI_LEVELS = {"minimal": "MINIMAL", "low": "LOW", "medium": "MEDIUM", "high": "HIGH",
                  "xhigh": "HIGH", "max": "HIGH"}
#: The documented stand-in for a signature on a function call Gemini did not
#: write: a turn rebuilt from text (another wire's, a test's) has none, and
#: Gemini 3 refuses a function call without one.
_GEMINI_UNSIGNED = "skip_thought_signature_validator"


def gemini_request(system: str, transcript: list[Turn], tools: list[ToolSpec],
                   max_tokens: int, effort: str | None = None,
                   grounded: bool = False) -> dict:
    """`grounded` is a search call: Google Search as the only tool, so the
    model answers from the web. Gemini does not take a server search tool
    beside function declarations on every model, so the loop searches with a
    call of its own rather than mixing the two (yaaf/runner/loop_entry.py)."""
    names = _names_by_call(transcript)
    contents: list[dict] = []
    for turn in transcript:
        if turn.role == "assistant":
            parts = turn.native if turn.native is not None else [
                *([{"text": turn.text}] if turn.text else []),
                *({"functionCall": {"name": c.name, "args": c.input},
                   **({"thoughtSignature": _GEMINI_UNSIGNED} if i == 0 else {})}
                  for i, c in enumerate(turn.calls))]
            if parts:
                # A reply cut off while thinking has no parts, and a content
                # without parts is refused.
                contents.append({"role": "model", "parts": parts})
        elif turn.outputs:
            contents.append({"role": "user", "parts": [
                {"functionResponse": {
                    "name": names.get(o.call_id, ""),
                    **({"id": o.call_id} if not o.call_id.startswith("call_") else {}),
                    "response": {"error": o.text} if o.is_error else {"content": o.text}}}
                for o in turn.outputs]})
        else:
            contents.append({"role": "user", "parts": [{"text": turn.text}]})
    config: dict = {"maxOutputTokens": max_tokens} if max_tokens < SAFE_MAX_TOKENS else {}
    if effort:
        # The enum's own spelling, as the Gemini CLI sends it — the one
        # spelling this repo has seen the API accept (gemini_cli_entry.py).
        config["thinkingConfig"] = {
            "thinkingLevel": _GEMINI_LEVELS.get(effort.lower(), effort.upper())}
    body: dict = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
                  "generationConfig": config}
    if grounded:
        body["tools"] = [{"googleSearch": {}}]
    elif tools:
        body["tools"] = [{"functionDeclarations": [_gemini_function(t) for t in tools]}]
    return body


def _gemini_function(tool: ToolSpec) -> dict:
    declared: dict = {"name": tool.name, "description": tool.description}
    parameters = _gemini_schema(tool.input_schema, tool.input_schema)
    # An object with no properties is refused ("should be non-empty for
    # OBJECT type"); a tool that takes nothing declares no parameters.
    if isinstance(parameters, dict) and parameters.get("properties"):
        declared["parameters"] = parameters
    return declared


#: The fields of the Gemini API's `Schema` (the discovery document's; Vertex's
#: adds a few). A field outside it is an unknown name, and an unknown name is
#: a refused request — a run that never starts — so everything else is
#: dropped: `additionalProperties`, `$schema`, `examples`, `allOf`, `not`...
#: `default` and `example` are in it but dropped too: a default that does not
#: match the type is refused, and the model needs neither.
_GEMINI_KEYWORDS = frozenset((
    "anyOf", "description", "enum", "format", "items", "maxItems", "maxLength",
    "maxProperties", "maximum", "minItems", "minLength", "minProperties", "minimum",
    "nullable", "pattern", "properties", "propertyOrdering", "required", "title", "type"))


def _gemini_ref(root: Any, ref: Any) -> Any:
    """A local `$ref` (`#/$defs/X`, `#/definitions/X`) resolved in `root`."""
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node = root
    for key in ref[2:].split("/"):
        node = node.get(key.replace("~1", "/").replace("~0", "~")) if isinstance(
            node, dict) else None
    return node if isinstance(node, dict) else None


def _gemini_schema(schema: Any, root: Any = None, depth: int = 0) -> Any:
    """A JSON schema as Gemini's `Schema`: references inlined (a recursive
    one cut at a depth), `oneOf` as the `anyOf` it means here, `const` as a
    one-value enum, a type list as one type and `nullable`, and keywords only
    where a schema is — a property *named* `default` is a property."""
    if not isinstance(schema, dict) or depth > 12:
        return {}
    if "$ref" in schema:
        target = _gemini_ref(root, schema["$ref"])
        rest = {k: v for k, v in schema.items() if k != "$ref"}
        return _gemini_schema({**target, **rest} if target else rest, root, depth + 1)
    schema = dict(schema)
    if "oneOf" in schema and "anyOf" not in schema:
        schema["anyOf"] = schema["oneOf"]
    if "const" in schema and "enum" not in schema:
        schema["enum"] = [schema["const"]]
    out: dict = {}
    for key, value in schema.items():
        if key not in _GEMINI_KEYWORDS:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {name: _gemini_schema(sub, root, depth + 1) for name, sub in value.items()}
        elif key == "items":
            out[key] = _gemini_schema(value, root, depth + 1)
        elif key == "anyOf" and isinstance(value, list):
            out[key] = [_gemini_schema(sub, root, depth + 1) for sub in value]
        elif key == "type" and isinstance(value, list):
            kinds = [k for k in value if k != "null"]
            if len(kinds) == 1:
                out["type"] = kinds[0]
            elif kinds:
                out["anyOf"] = [{"type": k} for k in kinds]
            if "null" in value:
                out["nullable"] = True
        elif key == "enum":
            # The enum is a string type's: other values are refused.
            if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
                out[key] = value
        else:
            out[key] = value
    if isinstance(out.get("required"), list) and isinstance(out.get("properties"), dict):
        # A required name that is not a property is refused.
        out["required"] = [n for n in out["required"] if n in out["properties"]]
    return out


def gemini_reply(body: dict) -> Reply:
    candidates = body.get("candidates") or []
    feedback = body.get("promptFeedback") or {}
    usage = body.get("usageMetadata") or {}
    prompt = int(usage.get("promptTokenCount") or 0) + int(
        usage.get("toolUsePromptTokenCount") or 0)
    cached = int(usage.get("cachedContentTokenCount") or 0)
    # Thinking is billed as output, and stated beside the answer.
    bill = Usage(input=max(0, prompt - cached),
                 output=int(usage.get("candidatesTokenCount") or 0)
                 + int(usage.get("thoughtsTokenCount") or 0),
                 cache_read=cached)
    if not candidates:
        reason = feedback.get("blockReason") or "no candidate"
        return Reply(turn=Turn("assistant", native=[]), stop="refusal", usage=bill,
                     model=body.get("modelVersion"), detail=f"the prompt was blocked: {reason}")
    candidate = candidates[0]
    parts = [p for p in (candidate.get("content") or {}).get("parts") or []
             if isinstance(p, dict)]
    text = "".join(p.get("text") or "" for p in parts if not p.get("thought"))
    calls = []
    for index, part in enumerate(parts):
        call = part.get("functionCall")
        if isinstance(call, dict):
            calls.append(ToolCall(call.get("id") or f"call_{index}", call.get("name") or "",
                                  _arguments(call.get("args"))))
    finish = candidate.get("finishReason") or "STOP"
    if finish in _GEMINI_REFUSALS or finish in _GEMINI_FAILURES:
        message = candidate.get("finishMessage")
        stop, detail = "refusal", f"the reply was stopped: {finish}" + (
            f" ({str(message)[:300]})" if message else "")
    elif finish in ("MAX_TOKENS", "CONTINUATION"):
        stop, detail = "max_tokens", None
    else:
        stop, detail = ("tool_use" if calls else "end"), None
    if (candidate.get("groundingMetadata") or {}).get("webSearchQueries"):
        bill = Usage(input=bill.input, output=bill.output, cache_read=bill.cache_read,
                     web_searches=len(candidate["groundingMetadata"]["webSearchQueries"]))
    return Reply(turn=Turn("assistant", text=text, calls=tuple(calls), native=parts),
                 stop=stop, usage=bill, model=body.get("modelVersion"), detail=detail)


def grounding_sources(body: dict) -> list[tuple[str, str]]:
    """(title, url) of every page a grounded answer drew on."""
    meta = ((body.get("candidates") or [{}])[0].get("groundingMetadata") or {})
    out = []
    for chunk in meta.get("groundingChunks") or []:
        web = chunk.get("web") or {}
        if web.get("uri"):
            out.append((web.get("title") or web["uri"], web["uri"]))
    return out


async def _gemini(http: httpx.AsyncClient, endpoint: Endpoint, model: str, body: dict):
    """Not streamed: Gemini's thought signatures ride on particular parts, and a
    stream splits parts across chunks — reassembling them is a way to replay a
    signature on the wrong part. Usage arrives at the end either way."""
    response = await http.post(
        f"{endpoint.base_url}/models/{quote(model, safe='')}:generateContent",
        headers=headers(endpoint), json=body)
    if response.status_code >= 400:
        raise await _refused(response, endpoint)
    yield gemini_reply(response.json())


# ── bedrock: the Converse API ──────────────────────────────────────────────────

_BEDROCK_STOPS = {"end_turn": "end", "stop_sequence": "end", "tool_use": "tool_use",
                  "max_tokens": "max_tokens", "model_context_window_exceeded": "max_tokens",
                  "guardrail_intervened": "refusal", "content_filtered": "refusal",
                  # No answer, and no call fit to run: stopped by name rather
                  # than read as a run that finished.
                  "malformed_model_output": "refusal", "malformed_tool_use": "refusal"}
_BEDROCK_AUTH = ("unrecognizedclient", "accessdenied", "expiredtoken", "invalidsignature")
_BEDROCK_TRANSIENT = ("throttling", "servicequota", "modelnotready", "serviceunavailable",
                      "internalserver", "modeltimeout")


def bedrock_request(system: str, transcript: list[Turn], tools: list[ToolSpec],
                    max_tokens: int) -> dict:
    messages: list[dict] = []
    for turn in transcript:
        if turn.role == "assistant":
            content = turn.native if turn.native is not None else [
                *([{"text": turn.text}] if turn.text else []),
                *({"toolUse": {"toolUseId": c.id, "name": c.name, "input": c.input}}
                  for c in turn.calls)]
            messages.append({"role": "assistant", "content": content})
        elif turn.outputs:
            messages.append({"role": "user", "content": [
                {"toolResult": {"toolUseId": o.call_id, "content": [{"text": o.text}],
                                "status": "error" if o.is_error else "success"}}
                for o in turn.outputs]})
        else:
            messages.append({"role": "user", "content": [{"text": turn.text}]})
    # A system block's text must not be empty: none is sent instead.
    body: dict = {**({"system": [{"text": system}]} if system else {}), "messages": messages}
    if max_tokens < SAFE_MAX_TOKENS:
        body["inferenceConfig"] = {"maxTokens": max_tokens}
    if tools:
        body["toolConfig"] = {"tools": [{"toolSpec": {
            "name": t.name, "description": t.description or t.name,
            "inputSchema": {"json": t.input_schema}}} for t in tools]}
    return body


def bedrock_reply(body: dict, model: str | None = None) -> Reply:
    content = [b for b in ((body.get("output") or {}).get("message") or {}).get("content") or []
               if isinstance(b, dict)]
    text = "".join(b.get("text") or "" for b in content)
    calls = tuple(ToolCall(b["toolUse"].get("toolUseId") or "", b["toolUse"].get("name") or "",
                           _arguments(b["toolUse"].get("input")))
                  for b in content if isinstance(b.get("toolUse"), dict))
    usage = body.get("usage") or {}
    bill = Usage(input=int(usage.get("inputTokens") or 0),
                 output=int(usage.get("outputTokens") or 0),
                 cache_read=int(usage.get("cacheReadInputTokens") or 0),
                 cache_write=int(usage.get("cacheWriteInputTokens") or 0))
    stop = _BEDROCK_STOPS.get(body.get("stopReason") or "", "end")
    if calls and stop == "end":
        stop = "tool_use"
    return Reply(turn=Turn("assistant", text=text, calls=calls, native=content), stop=stop,
                 usage=bill, model=model,
                 detail=f"stopped: {body.get('stopReason')}" if stop == "refusal" else None)


async def _bedrock(http: httpx.AsyncClient, endpoint: Endpoint, model: str, body: dict):
    response = await http.post(f"{endpoint.base_url}/model/{quote(model, safe='')}/converse",
                               headers=headers(endpoint), json=body)
    if response.status_code >= 400:
        error_type = (response.headers.get("x-amzn-errortype") or "").lower()
        refused = await _refused(response, endpoint)
        said = f"{error_type} {refused}".lower()
        if any(word in said for word in _BEDROCK_AUTH):
            refused.kind = "auth"
        elif any(word in said for word in _BEDROCK_TRANSIENT):
            refused.kind = "transient"
        raise refused
    yield bedrock_reply(response.json(), model)


# ── anthropic: the Messages API, for the control plane's own calls ─────────────


def anthropic_request(model: str, system: str, transcript: list[Turn], tools: list[ToolSpec],
                      max_tokens: int, effort: str | None = None,
                      web_search: bool = False) -> dict:
    messages: list[dict] = []
    for turn in transcript:
        if turn.role == "assistant":
            messages.append({"role": "assistant", "content": turn.native if turn.native
                             is not None else [
                *([{"type": "text", "text": turn.text}] if turn.text else []),
                *({"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
                  for c in turn.calls)]})
        elif turn.outputs:
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": o.call_id, "content": o.text,
                 "is_error": o.is_error} for o in turn.outputs]})
        else:
            messages.append({"role": "user", "content": turn.text})
    body: dict = {"model": model, "max_tokens": max_tokens, "system": system,
                  "messages": messages}
    body["tools"] = [{"name": t.name, "description": t.description,
                      "input_schema": t.input_schema} for t in tools]
    if web_search:
        # The server search's first version: every model with search takes
        # it, where the newer ones are model by model, and this file knows no
        # models. Its `server_tool_use` and `web_search_tool_result` blocks
        # come back in `content` and go back in `native`, as they must.
        body["tools"].append({"type": "web_search_20250305", "name": "web_search"})
    if not body["tools"]:
        del body["tools"]
    if effort:
        body["output_config"] = {"effort": effort}
    return body


_ANTHROPIC_STOPS = {"end_turn": "end", "stop_sequence": "end", "tool_use": "tool_use",
                    "pause_turn": "pause", "max_tokens": "max_tokens",
                    "model_context_window_exceeded": "max_tokens", "refusal": "refusal"}


def anthropic_reply(body: dict) -> Reply:
    content = [b for b in body.get("content") or [] if isinstance(b, dict)]
    usage = body.get("usage") or {}
    server = usage.get("server_tool_use") or {}
    return Reply(
        turn=Turn("assistant", text="".join(b.get("text") or "" for b in content
                                            if b.get("type") == "text"),
                  calls=tuple(ToolCall(b.get("id") or "", b.get("name") or "",
                                       _arguments(b.get("input")))
                              for b in content if b.get("type") == "tool_use"),
                  native=content),
        stop=_ANTHROPIC_STOPS.get(body.get("stop_reason") or "", "end"),
        usage=Usage(input=int(usage.get("input_tokens") or 0),
                    output=int(usage.get("output_tokens") or 0),
                    cache_read=int(usage.get("cache_read_input_tokens") or 0),
                    cache_write=int(usage.get("cache_creation_input_tokens") or 0),
                    web_searches=int(server.get("web_search_requests") or 0)),
        model=body.get("model"))


async def _anthropic(http: httpx.AsyncClient, endpoint: Endpoint, body: dict):
    response = await http.post(f"{endpoint.base_url}/messages", headers=headers(endpoint),
                               json=body)
    if response.status_code >= 400:
        raise await _refused(response, endpoint)
    yield anthropic_reply(response.json())


# ── one call, whatever the wire ────────────────────────────────────────────────


async def call(endpoint: Endpoint, model: str, system: str, transcript: list[Turn],
               tools: list[ToolSpec], max_tokens: int, *, effort: str | None = None,
               web_search: bool = False,
               transport: httpx.AsyncBaseTransport | None = None,
               read_timeout: float | None = None) -> AsyncIterator[Reply]:
    """One model call on `endpoint`'s wire: yields exactly one `Reply`, or
    raises a `WireError` (or the transport's own error, for a dropped
    connection — transient by its type). `effort` is the provider's own rung,
    or None for a model with no dial; `web_search` asks for the provider's
    server search where its wire has one. `read_timeout` is the longest
    silence tolerated — on a streamed wire between lines, on another for the
    whole reply."""
    if not endpoint.base_url:
        raise WireError(0, {"error": {"message": f"no address for {endpoint.name or 'this '
                                                 'provider'}: its credential carries one"}},
                        "fatal")
    async with httpx.AsyncClient(transport=transport or TRANSPORT,
                                 timeout=_timeout(read_timeout)) as http:
        if endpoint.protocol == "openai_chat":
            stream = _chat(http, endpoint, chat_request(endpoint, model, system, transcript,
                                                        tools, max_tokens, effort))
        elif endpoint.protocol == "openai_responses":
            stream = _responses(http, endpoint, responses_request(
                model, system, transcript, tools, max_tokens, effort, web_search))
        elif endpoint.protocol == "gemini":
            stream = _gemini(http, endpoint, model,
                             gemini_request(system, transcript, tools, max_tokens, effort))
        elif endpoint.protocol == "bedrock":
            stream = _bedrock(http, endpoint, model,
                              bedrock_request(system, transcript, tools, max_tokens))
        elif endpoint.protocol == "anthropic":
            stream = _anthropic(http, endpoint, anthropic_request(
                model, system, transcript, tools, max_tokens, effort, web_search))
        else:
            raise WireError(0, {"error": {"message": f"unknown protocol {endpoint.protocol!r}"}},
                            "fatal")
        async for reply in stream:
            yield reply


async def grounded_search(endpoint: Endpoint, model: str, query: str, *,
                          transport: httpx.AsyncBaseTransport | None = None
                          ) -> tuple[str, list[tuple[str, str]], Usage]:
    """A web search answered by a Gemini model with Google Search as its only
    tool: the answer, the pages it drew on, and the call's bill."""
    async with httpx.AsyncClient(transport=transport or TRANSPORT, timeout=_timeout()) as http:
        response = await http.post(
            f"{endpoint.base_url}/models/{quote(model, safe='')}:generateContent",
            headers=headers(endpoint),
            json=gemini_request("Answer from current web results. Cite what you found.",
                                [Turn("user", text=query)], [], 4096, grounded=True))
        if response.status_code >= 400:
            raise await _refused(response, endpoint)
        body = response.json()
    reply = gemini_reply(body)
    return reply.turn.text, grounding_sources(body), _one_search(reply.usage)


def _one_search(usage: Usage) -> Usage:
    """A grounded call is one search, however many queries Google ran for it:
    the grounding fee is per prompt."""
    return Usage(input=usage.input, output=usage.output, cache_read=usage.cache_read,
                 cache_write=usage.cache_write, web_searches=1, cost_usd=usage.cost_usd)


# ── what a key can see: the live check and model discovery ─────────────────────


def models_url(endpoint: Endpoint) -> str:
    """Where a provider lists the models a key may call. Bedrock lists them on
    its control endpoint, not the runtime one a run calls; Vertex lists Google's
    models per publisher, not per project."""
    if endpoint.protocol == "bedrock":
        return endpoint.base_url.replace("bedrock-runtime.", "bedrock.", 1) + "/foundation-models"
    if endpoint.protocol == "gemini" and "aiplatform.googleapis.com" in endpoint.base_url:
        scheme, _, rest = endpoint.base_url.partition("://")
        return f"{scheme}://{rest.split('/', 1)[0]}/v1beta1/publishers/google/models"
    return f"{endpoint.base_url}/models"


def model_ids(endpoint: Endpoint, body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    if endpoint.protocol == "gemini":
        rows = body.get("models") or body.get("publisherModels") or []
        names = [str(r.get("name") or "") for r in rows if isinstance(r, dict)]
        return sorted({n.rsplit("/", 1)[-1] for n in names if n})
    if endpoint.protocol == "bedrock":
        rows = [*(body.get("modelSummaries") or []),
                *(body.get("inferenceProfileSummaries") or [])]
        ids = [r.get("modelId") or r.get("inferenceProfileId") for r in rows
               if isinstance(r, dict)]
        return sorted({str(i) for i in ids if i})
    rows = body.get("data") or body.get("models") or []
    return sorted({str(r.get("id") or r.get("name")) for r in rows
                   if isinstance(r, dict) and (r.get("id") or r.get("name"))})


async def list_models(endpoint: Endpoint, *,
                      transport: httpx.AsyncBaseTransport | None = None) -> list[str]:
    """Every model the key may call, by the provider's own names. Free on every
    provider that has the endpoint, and answered only for a live key — which
    is why it is also the live check. Raises `WireError` on a refusal."""
    if not endpoint.base_url:
        raise WireError(0, {"error": {"message": "no address to reach this provider at"}},
                        "fatal")
    async with httpx.AsyncClient(transport=transport or TRANSPORT,
                                 timeout=httpx.Timeout(CONNECT_TIMEOUT_S)) as http:
        response = await http.get(models_url(endpoint), headers=headers(endpoint))
        if response.status_code >= 400:
            raise await _refused(response, endpoint)
        body = response.json()
        names = set(model_ids(endpoint, body))
        for _ in range(_MAX_PAGES):
            following = _next_page(endpoint, body)
            if following is None:
                break
            response = await http.get(models_url(endpoint), params=following,
                                      headers=headers(endpoint))
            if response.status_code >= 400:
                raise await _refused(response, endpoint)
            body = response.json()
            names.update(model_ids(endpoint, body))
        if endpoint.protocol == "bedrock":
            names.update(await _inference_profiles(http, endpoint))
        return sorted(names)


#: How many further pages a listing is followed for: a provider that pages
#: forever is not listed forever.
_MAX_PAGES = 20


def _next_page(endpoint: Endpoint, body: Any) -> dict | None:
    """The query for a listing's next page, or None at its end. Anthropic
    pages by `has_more` and `last_id`, 20 to a page unless asked; Google by
    `nextPageToken`, 50 to a page on the Gemini API. The first page is asked
    for plainly, so a provider that pages nothing is asked nothing new."""
    if not isinstance(body, dict):
        return None
    if endpoint.protocol == "anthropic" and body.get("has_more") and body.get("last_id"):
        return {"limit": 1000, "after_id": body["last_id"]}
    if endpoint.protocol == "gemini" and body.get("nextPageToken"):
        return {"pageSize": 1000, "pageToken": body["nextPageToken"]}
    return None


async def _inference_profiles(http: httpx.AsyncClient, endpoint: Endpoint) -> list[str]:
    """Bedrock's inference profiles (`us.anthropic...`): not foundation models,
    so not in that list, yet the only id many models answer on demand to.
    Best effort — a key that may list models but not profiles still lists."""
    url = endpoint.base_url.replace("bedrock-runtime.", "bedrock.", 1) + "/inference-profiles"
    out: list[str] = []
    params: dict = {"maxResults": 1000}
    for _ in range(_MAX_PAGES):
        try:
            response = await http.get(url, params=params, headers=headers(endpoint))
            body = response.json() if response.status_code < 400 else None
        except (httpx.HTTPError, ValueError):
            body = None
        if not isinstance(body, dict):
            break
        out += model_ids(endpoint, {"inferenceProfileSummaries":
                                    body.get("inferenceProfileSummaries") or []})
        if not body.get("nextToken"):
            break
        params = {"maxResults": 1000, "nextToken": body["nextToken"]}
    return out


# ── web search backends: the platform's own search (docs/PROVIDERS.md §6) ──────


@dataclass(frozen=True)
class SearchBackend:
    name: str
    label: str
    #: Where it is reached; a self-hosted one's address is the install's.
    base_url: str | None
    #: What one query costs at the backend's published entry rate, offered as
    #: the price an install starts from. The install's own number is what
    #: meters (Settings → Web search).
    suggested_usd: float
    key: bool = True
    console_url: str | None = None


SEARCH_BACKENDS: dict[str, SearchBackend] = {b.name: b for b in (
    SearchBackend("brave", "Brave Search", "https://api.search.brave.com/res/v1", 0.005,
                  console_url="https://api-dashboard.search.brave.com/app/keys"),
    SearchBackend("tavily", "Tavily", "https://api.tavily.com", 0.008,
                  console_url="https://app.tavily.com/home"),
    SearchBackend("exa", "Exa", "https://api.exa.ai", 0.005,
                  console_url="https://dashboard.exa.ai/api-keys"),
    SearchBackend("serper", "Serper (Google results)", "https://google.serper.dev", 0.001,
                  console_url="https://serper.dev/api-key"),
    SearchBackend("perplexity", "Perplexity Search", "https://api.perplexity.ai", 0.005,
                  console_url="https://www.perplexity.ai/account/api/keys"),
    SearchBackend("searxng", "SearXNG (self-hosted)", None, 0.0, key=False),
)}


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str


def _hits(rows: Any, title: str, url: str, snippet: str) -> list[SearchHit]:
    return [SearchHit(str(r.get(title) or r.get(url) or ""), str(r.get(url) or ""),
                      str(r.get(snippet) or "")[:1000])
            for r in rows or [] if isinstance(r, dict) and r.get(url)]


def _exa_hits(body: dict) -> list[SearchHit]:
    return [SearchHit(str(r.get("title") or r["url"]), str(r["url"]),
                      (" ".join(r.get("highlights") or []) or str(r.get("text") or ""))[:1000])
            for r in body.get("results") or [] if isinstance(r, dict) and r.get("url")]


async def search_web(backend: str, base_url: str, key: str, query: str, count: int = 8, *,
                     transport: httpx.AsyncBaseTransport | None = None) -> list[SearchHit]:
    """One query on the install's search backend: the results, or a
    `WireError`. Every backend answers the same three things — title, address,
    a snippet — which is all a model needs to decide what to fetch."""
    base = (base_url or "").rstrip("/")
    async with httpx.AsyncClient(transport=transport or TRANSPORT,
                                 timeout=httpx.Timeout(CONNECT_TIMEOUT_S, read=60)) as http:
        if backend == "brave":
            response = await http.get(f"{base}/web/search", params={"q": query, "count": count},
                                      headers={"X-Subscription-Token": key,
                                               "accept": "application/json"})
            parse = lambda b: _hits((b.get("web") or {}).get("results"),  # noqa: E731
                                    "title", "url", "description")
        elif backend == "tavily":
            response = await http.post(f"{base}/search", json={"query": query,
                                                               "max_results": count},
                                       headers={"authorization": f"Bearer {key}"})
            parse = lambda b: _hits(b.get("results"), "title", "url", "content")  # noqa: E731
        elif backend == "exa":
            response = await http.post(f"{base}/search", json={
                "query": query, "numResults": count,
                "contents": {"highlights": {"numSentences": 3}}}, headers={"x-api-key": key})
            parse = _exa_hits
        elif backend == "serper":
            response = await http.post(f"{base}/search", json={"q": query, "num": count},
                                       headers={"X-API-KEY": key})
            parse = lambda b: _hits(b.get("organic"), "title", "link", "snippet")  # noqa: E731
        elif backend == "perplexity":
            response = await http.post(f"{base}/search", json={"query": query,
                                                               "max_results": count},
                                       headers={"authorization": f"Bearer {key}"})
            parse = lambda b: _hits(b.get("results"), "title", "url", "snippet")  # noqa: E731
        elif backend == "searxng":
            response = await http.get(f"{base}/search", params={"q": query, "format": "json"})
            parse = lambda b: _hits(b.get("results"), "title", "url", "content")  # noqa: E731
        else:
            raise WireError(0, {"error": {"message": f"unknown search backend {backend!r}"}},
                            "fatal")
        if response.status_code >= 400:
            try:
                body: object = response.json()
            except ValueError:
                body = response.text
            raise WireError(response.status_code, body, classify(response.status_code, body))
        return parse(response.json())[:count]
