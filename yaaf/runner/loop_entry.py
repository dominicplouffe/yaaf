"""Runs INSIDE the sandbox container: one stateless run on yaaf's own agent loop,
on whichever wire the seat's provider speaks (docs/PROVIDERS.md §3).

The loop is `loop_harness`; the run is `sandbox_entry.run_harness`; this file
is the client the loop drives — chosen by the provider's protocol, which the
daemon hands in as `YAAF_PROVIDER` (yaaf.runtimes.providers.spec):

- **anthropic** — the official `anthropic` SDK. It is exact about cache
  breakpoints and errors that arrive mid-stream, and it states the prompt's
  size at `message_start`, before a word of output, which is what lets the
  governor abandon a call whose prompt alone crosses the cap. None of that is
  worth re-deriving over raw HTTP.
- **everything else** — `yaaf.runtimes.wire` over httpx: OpenAI's Responses
  API (and Azure's), Gemini (and Vertex), Bedrock's Converse, and the OpenAI
  chat shape that DeepSeek, Mistral, Groq, OpenRouter and every local server
  speak. The same file the control plane talks to providers with, copied into
  the image, so a request yaaf's Founder can make is a request a seat makes.

API keys only (yaaf.runtimes.harnesses): a subscription or a login is a CLI
credential, and R1 refuses the seat before it ever gets here.

Two choices the Anthropic API offers and this declines, on purpose:

- **No server-side model fallback.** A seat runs on what it was hired on,
  priced on what it was hired on; a refusal ends the run and says so.
- **No eager tool-input streaming.** Nothing here reads a tool call before it
  is complete, so streaming inputs early would buy only the failure it brings:
  a truncated input that parses.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import time

import httpx
import loop_harness as loop
import sandbox_entry as base

try:
    import wire  # the sandbox: copied into the image beside this file
except ModuleNotFoundError:  # the control plane's tests
    from yaaf.runtimes import wire

#: The wire's transport — a test's stand-in API, else httpx's own.
TRANSPORT: httpx.AsyncBaseTransport | None = None
#: When the run's minted model token expires (yaaf.runner.daemon._mint_token).
TOKEN_EXPIRY_ENV = "YAAF_MODEL_TOKEN_EXPIRES"
#: A token with less than this left is refreshed before the next call.
TOKEN_MARGIN_S = 300


# ── anthropic: the official SDK ────────────────────────────────────────────────

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
        import anthropic

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
        #: The server tool is the search: the loop adds none of its own.
        self.native_search = web_search

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
        import anthropic
        import httpx2

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


# ── every other wire ───────────────────────────────────────────────────────────


def _bill(usage: wire.Usage) -> base.AttemptResult:
    return base.AttemptResult(cost_usd=usage.cost_usd, tokens_in=usage.input,
                              tokens_out=usage.output, cache_creation=usage.cache_write,
                              cache_read=usage.cache_read, web_searches=usage.web_searches)


class WireClient:
    """A provider reached through `yaaf.runtimes.wire`. What differs from the
    Anthropic client, and why it is fine:

    - **Usage arrives with the reply**, not before the first word. The
      governor still starts nothing once the cap is spent and still clamps
      output; it cannot abandon a call at its prompt, so a call that dies
      mid-reply is not metered — the provider never said what it was.
    - **Search is the wire's where it has a server tool** (OpenAI, Azure);
      Gemini answers a search with a grounded call of its own (`search`); every
      other provider searches through the platform's backend, which the loop
      adds itself."""

    def __init__(self, *, model: str, spec: dict, effort: str | None, web_search: bool,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.spec = spec
        self.provider = spec.get("name") or "custom"
        self.name = spec.get("protocol") or "openai_chat"
        # The name the provider's own API knows: without the provider in front.
        self.model = model.removeprefix(f"{self.provider}/")
        self.key_env = spec.get("key_env") or ""
        self.endpoint = wire.Endpoint.from_spec(spec, os.environ.get(self.key_env, ""))
        self.effort = effort
        self.reports_cost = bool(spec.get("reports_cost"))
        self.native_search = (web_search and self.name == "openai_responses"
                              and bool(spec.get("search")))
        if not (web_search and self.name == "gemini" and spec.get("search")):
            # Gemini's grounded call is the only provider search that is a
            # call of its own; without it the loop's platform search serves.
            self.search = None
        self.transport = transport

    async def _fresh_key(self) -> None:
        """A minted token (Vertex) refreshed through the agent API before it
        lapses: the key file it is minted from never enters the container."""
        if not self.spec.get("mints_tokens"):
            return
        expires = float(os.environ.get(TOKEN_EXPIRY_ENV) or 0)
        if expires - time.time() > TOKEN_MARGIN_S:
            return
        answer = await asyncio.to_thread(base._call, "POST", "/model-token")
        body = answer.get("body") or {}
        if answer.get("status") != 200 or not body.get("token"):
            status = int(answer.get("status") or 0)
            raise wire.WireError(status or 503, body,
                                 "transient" if status in (0, 502, 503) else "auth")
        os.environ[self.key_env] = body["token"]
        os.environ[TOKEN_EXPIRY_ENV] = str(body.get("expires_at") or 0)
        self.endpoint = dataclasses.replace(self.endpoint, key=body["token"])

    async def stream(self, system, transcript, tools, max_tokens):
        await self._fresh_key()
        async for reply in wire.call(self.endpoint, self.model, system, transcript, tools,
                                     max_tokens, effort=self.effort,
                                     web_search=self.native_search,
                                     transport=self.transport or TRANSPORT):
            yield loop.Reply(turn=reply.turn, stop=reply.stop, usage=_bill(reply.usage),
                             model=reply.model, detail=reply.detail)

    async def search(self, query: str) -> tuple[str, base.AttemptResult]:
        """A web search as a Gemini call grounded in Google Search, on the
        seat's own model — billed as the call it is, plus the grounding fee."""
        await self._fresh_key()
        text, sources, usage = await wire.grounded_search(
            self.endpoint, self.model, query, transport=self.transport or TRANSPORT)
        listed = "\n".join(f"- {title}: {url}" for title, url in sources)
        return (f"{text}\n\nSources:\n{listed}" if listed else text), _bill(usage)

    def error_kind(self, exc: BaseException) -> str | None:
        if isinstance(exc, wire.WireError):
            if exc.kind == "fatal" and base._error_kind(exc) == "auth":
                return "auth"
            return exc.kind
        # A stream that dropped, or a line it cut in half.
        if isinstance(exc, httpx.TransportError | json.JSONDecodeError):
            return "transient"
        return None


def client_for(spec: dict) -> AnthropicClient | WireClient:
    """The client the seat's provider speaks. A run handed no provider at all
    is on Anthropic's wire — what the loop ran on before it had a second."""
    protocol = spec.get("protocol") or "anthropic"
    wants_web = "WebSearch" in base._builtin_tools()
    if protocol == "anthropic":
        return AnthropicClient(model=base.MODEL, effort=base.EFFORT, flags=base.MODEL_FLAGS,
                               web_search=wants_web and bool(spec.get("search", True)))
    effort = base.EFFORT if base.MODEL_FLAGS.get("effort", False) else None
    return WireClient(model=base.MODEL, spec=spec, effort=effort, web_search=wants_web)


def make_driver(system_prompt: str, system_prompt_path) -> loop.LoopDriver:
    return loop.LoopDriver(client_for(base.PROVIDER or {}), system_prompt)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(base.run_harness(make_driver)))
