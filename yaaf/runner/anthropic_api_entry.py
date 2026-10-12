"""Runs INSIDE the sandbox container: one stateless run on yaaf's own agent loop
over the Anthropic Messages API (docs/RUNTIMES.md, M2).

The loop family's reference: the `ModelClient` every provider reached by plain
API is built beside. Everything here is translation — the transcript into a
request, the streamed reply into a `Reply`, the SDK's typed errors into the
three kinds a run can fail by. The loop is `loop_harness`; the run is
`sandbox_entry.run_harness`.

API keys only (yaaf.runtimes.harnesses): a subscription token is a credential
for the Claude Code CLI, not for the Messages API, and R1 refuses the seat
before it ever gets here.

Two choices the API offers and this declines, on purpose:

- **No server-side model fallback.** A refused turn could be retried on another
  model by the API itself, but a seat runs on what it was hired on, priced on
  what it was hired on; a refusal ends the run and says so.
- **No eager tool-input streaming.** Nothing here reads a tool call before it
  is complete, so streaming inputs early would buy nothing and add the one
  failure it brings: a truncated input that parses.
"""

from __future__ import annotations

import asyncio
import os

import anthropic
import httpx2
import loop_harness as loop
import sandbox_entry as base

#: The API's stop reasons in the loop's words. A context window exceeded ends
#: the turn the way the output limit does: whatever call it cut off is not run.
_STOPS = {"end_turn": "end", "stop_sequence": "end", "tool_use": "tool_use",
          "pause_turn": "pause", "max_tokens": "max_tokens",
          "model_context_window_exceeded": "max_tokens", "refusal": "refusal"}

#: The error `type` an API error body carries. Read off the body because an
#: error that arrives mid-stream is raised with the stream's own status — a 200
#: — so its class says nothing and its body says everything.
_AUTH_TYPES = ("authentication_error", "permission_error", "billing_error")
_TRANSIENT_TYPES = ("overloaded_error", "api_error", "rate_limit_error", "timeout_error")


class AnthropicClient:
    name = "anthropic"

    def __init__(self, *, model: str, effort: str, flags: dict, web_search: bool,
                 api_key: str | None = None, http_client=None, max_retries: int = 2):
        key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        # Explicit, and never the SDK's own search for a credential: it would
        # find whatever else the environment holds, and a seat pays with the
        # credential it was refused or allowed on, and no other.
        self.client = anthropic.AsyncAnthropic(api_key=key or "missing",
                                               http_client=http_client,
                                               max_retries=max_retries)
        self.model = model
        self.effort = effort
        self.flags = flags
        self.web_search = web_search

    def request(self, system: str, transcript: list[loop.Turn], tools: list[loop.ToolSpec],
                max_tokens: int) -> dict:
        params: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            # One explicit breakpoint at the end of the static prefix (tools,
            # then the system prompt) — the part every call of every run of
            # this seat shares — and automatic caching for the growing tail.
            "system": [{"type": "text", "text": system,
                        "cache_control": {"type": "ephemeral"}}],
            "cache_control": {"type": "ephemeral"},
            "messages": [self._message(turn) for turn in transcript],
            "tools": [{"name": t.name, "description": t.description,
                       "input_schema": t.input_schema} for t in tools],
        }
        if self.web_search:
            version = "20260209" if self.flags.get("web_tools") == "20260209" else "20250305"
            params["tools"].append({"type": f"web_search_{version}", "name": "web_search"})
        # Thinking is left to the model's default (adaptive where it has one).
        # Effort only where the model takes it: Claude Haiku 4.5 rejects it.
        if self.flags.get("effort", True):
            params["output_config"] = {"effort": self.effort}
        return params

    @staticmethod
    def _message(turn: loop.Turn) -> dict:
        if turn.role == "assistant":
            content = turn.native if turn.native is not None else [
                *([{"type": "text", "text": turn.text}] if turn.text else []),
                *({"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
                  for c in turn.calls)]
            return {"role": "assistant", "content": content}
        if turn.outputs:
            return {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": o.call_id, "content": o.text,
                 "is_error": o.is_error} for o in turn.outputs]}
        return {"role": "user", "content": turn.text}

    async def stream(self, system, transcript, tools, max_tokens):
        async with self.client.messages.stream(
                **self.request(system, transcript, tools, max_tokens)) as stream:
            async for event in stream:
                # The prompt's size, cache split included, before a word of
                # output: what the governor needs to stop a call mid-flight.
                if event.type == "message_start":
                    yield base.usage_bill(event.message.usage, cost_usd=None, subtype=None)
            message = await stream.get_final_message()
        yield self.reply(message)

    @staticmethod
    def reply(message) -> loop.Reply:
        content = list(message.content)
        stop = _STOPS.get(message.stop_reason or "", "end")
        detail = None
        if stop == "refusal" and message.stop_details is not None:
            details = message.stop_details
            detail = " — ".join(str(part) for part in (details.category, details.explanation)
                                if part)
        return loop.Reply(
            turn=loop.Turn(
                "assistant",
                # Citations split one paragraph into many blocks; joined, they
                # are the paragraph again.
                text="".join(b.text for b in content if b.type == "text"),
                calls=tuple(loop.ToolCall(b.id, b.name, b.input if isinstance(b.input, dict)
                                          else {})
                            for b in content if b.type == "tool_use"),
                native=content),
            stop=stop,
            usage=base.usage_bill(message.usage, cost_usd=None, subtype=None),
            model=message.model,
            detail=detail)

    def error_kind(self, exc: BaseException) -> str | None:
        # The SDK has already retried what it retries (429, 5xx, connection
        # errors) by the time one of these reaches here.
        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            return "auth"
        # A stream that drops mid-reply raises the transport's own error, not
        # the SDK's: the SDK wraps what fails before the response, not after.
        if isinstance(exc, (anthropic.RateLimitError, anthropic.InternalServerError,
                            anthropic.OverloadedError, anthropic.ServiceUnavailableError,
                            anthropic.APIConnectionError, httpx2.TransportError)):
            return "transient"
        if isinstance(exc, anthropic.APIStatusError):
            body = exc.body if isinstance(exc.body, dict) else {}
            error = body.get("error") if isinstance(body.get("error"), dict) else body
            kind = error.get("type")
            if kind in _AUTH_TYPES:
                return "auth"
            if kind in _TRANSIENT_TYPES:
                return "transient"
            # "Your credit balance is too low" is a 400 like any bad request;
            # the markers know it for the dead credential it is.
            return "auth" if base._error_kind(exc) == "auth" else "fatal"
        return None


def make_driver(system_prompt: str, system_prompt_path) -> loop.LoopDriver:
    client = AnthropicClient(model=base.MODEL, effort=base.EFFORT, flags=base.MODEL_FLAGS,
                             web_search="WebSearch" in base._builtin_tools())
    return loop.LoopDriver(client, system_prompt)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(base.run_harness(make_driver)))
