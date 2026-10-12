"""Runs INSIDE the sandbox container: one stateless run on yaaf's own agent loop
over the OpenAI-compatible chat API (docs/RUNTIMES.md, M3).

One client for every provider that speaks that shape — DeepSeek, OpenRouter's
hundreds of models, a local Ollama or vLLM server — because the shape is the
same and only facts differ: the address, which variable holds the key, whether
the provider states each call's cost. Those facts are the provider registry's
(yaaf.runtimes.providers), handed in by the daemon as `YAAF_PROVIDER`, so a new
provider of this shape is a row there and nothing here.

Plain HTTP, on the httpx the image already carries: the wire format is a
request and a stream of JSON lines, and an SDK would be one more thing to pin
for what twenty lines of parsing do. The loop is `loop_harness`; the run is
`sandbox_entry.run_harness`.

What differs from the Messages API client, and why it is fine:

- **Usage arrives at the end of a call**, not before the first word. The
  governor still stops nothing from starting once the cap is spent and still
  clamps output; it cannot abandon a call at its prompt, so a call that dies
  mid-stream is not metered — the provider never said what it was.
- **The output is clamped only when the cap is close.** Providers disagree on
  the largest `max_tokens` they accept (DeepSeek's chat model refuses more than
  8,192), so the request names one only when the cap can pay for fewer than
  that, which every provider accepts; otherwise the provider's own default
  applies.
- **No search and no effort.** Neither is part of this shape; a role granting
  `web` is refused on a provider that cannot search (R2), and effort is a
  dial this API has no common word for.
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import loop_harness as loop
import sandbox_entry as base

#: The most `max_tokens` every provider of this shape accepts. Below it the
#: governor's clamp is sent; above it the provider's default stands.
SAFE_MAX_TOKENS = 8192
CONNECT_TIMEOUT_S = 30
READ_TIMEOUT_S = 600

_STOPS = {"stop": "end", "tool_calls": "tool_use", "function_call": "tool_use",
          "length": "max_tokens", "content_filter": "refusal"}


class ProviderError(RuntimeError):
    """The provider refused the call, before or during the stream. `status` is
    the HTTP status — or the error's own code, for an error that arrived as a
    line of a stream whose status was already 200."""

    def __init__(self, status: int, body: object):
        message = body
        if isinstance(body, dict):
            error = body.get("error", body)
            message = error.get("message", error) if isinstance(error, dict) else error
        super().__init__(f"HTTP {status}: {str(message)[:1000]}")
        self.status = status
        self.body = body


class OpenAICompatibleClient:
    name = "openai_compatible"

    def __init__(self, *, model: str, spec: dict, api_key: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.spec = spec
        self.provider = spec.get("name") or "custom"
        # The name the provider's own API knows: without the provider in front.
        self.model = model.removeprefix(f"{self.provider}/")
        self.base_url = (spec.get("base_url") or "").rstrip("/")
        self.api_key = (api_key if api_key is not None
                        else os.environ.get(spec.get("key_env") or "", ""))
        self.reports_cost = bool(spec.get("reports_cost"))
        self.transport = transport

    # ── the request ────────────────────────────────────────────────────────

    @staticmethod
    def _messages(system: str, transcript: list[loop.Turn]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for turn in transcript:
            if turn.role == "assistant":
                out.append(turn.native if turn.native is not None else {
                    "role": "assistant", "content": turn.text or None,
                    **({"tool_calls": [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.name, "arguments": json.dumps(c.input)}}
                        for c in turn.calls]} if turn.calls else {})})
            elif turn.outputs:
                # No error flag in this shape: the text says so instead.
                out += [{"role": "tool", "tool_call_id": o.call_id,
                         "content": f"ERROR: {o.text}" if o.is_error else o.text}
                        for o in turn.outputs]
            else:
                out.append({"role": "user", "content": turn.text})
        return out

    def request(self, system: str, transcript: list[loop.Turn], tools: list[loop.ToolSpec],
                max_tokens: int) -> dict:
        body: dict = {"model": self.model, "messages": self._messages(system, transcript),
                      "stream": True, "stream_options": {"include_usage": True}}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.input_schema}}
                for t in tools]
        if max_tokens < SAFE_MAX_TOKENS:
            body["max_tokens"] = max_tokens
        if self.reports_cost:
            # OpenRouter's switch for the cost on the last line. Newer
            # OpenRouter always sends it and ignores this; older needs it.
            body["usage"] = {"include": True}
        return body

    # ── the reply ──────────────────────────────────────────────────────────

    async def stream(self, system, transcript, tools, max_tokens):
        if not self.base_url:
            raise ProviderError(0, {"error": {
                "message": f"no address for {self.provider}: a custom endpoint's credential "
                           "carries its base URL"}})
        text: list[str] = []
        calls: dict[int, dict] = {}
        finish: str | None = None
        usage: dict | None = None
        served: str | None = None
        timeout = httpx.Timeout(CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S)
        async with (
            httpx.AsyncClient(transport=self.transport, timeout=timeout) as http,
            http.stream("POST", f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=self.request(system, transcript, tools, max_tokens)) as response,
        ):
            if response.status_code >= 400:
                raw = await response.aread()
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = raw.decode(errors="replace")
                raise ProviderError(response.status_code, body)
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
                    raise ProviderError(code if isinstance(code, int) else 200, chunk)
                served = chunk.get("model") or served
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        text.append(delta["content"])
                    for part in delta.get("tool_calls") or []:
                        call = calls.setdefault(part.get("index", len(calls)),
                                                {"id": "", "name": "", "arguments": ""})
                        call["id"] = part.get("id") or call["id"]
                        function = part.get("function") or {}
                        call["name"] += function.get("name") or ""
                        call["arguments"] += function.get("arguments") or ""
                    finish = choice.get("finish_reason") or finish
        yield self.reply("".join(text), [calls[i] for i in sorted(calls)], finish, usage,
                         served)

    def reply(self, text: str, calls: list[dict], finish: str | None, usage: dict | None,
              served: str | None) -> loop.Reply:
        parsed = []
        for call in calls:
            try:
                arguments = json.loads(call["arguments"] or "{}")
            except ValueError:
                arguments = None
            parsed.append(loop.ToolCall(
                call["id"], call["name"],
                arguments if isinstance(arguments, dict)
                else {"_unparsed_arguments": call["arguments"][:2000]}))
        stop = _STOPS.get(finish or "", "end")
        if parsed and stop == "end":
            # Some providers say "stop" with tool calls in hand.
            stop = "tool_use"
        native = {"role": "assistant", "content": text or None}
        if calls:
            native["tool_calls"] = [{"id": c["id"], "type": "function",
                                     "function": {"name": c["name"],
                                                  "arguments": c["arguments"]}}
                                    for c in calls]
        return loop.Reply(
            turn=loop.Turn("assistant", text=text, calls=tuple(parsed), native=native),
            stop=stop, usage=self.bill(usage), model=served,
            detail="the provider's content filter stopped the reply"
            if stop == "refusal" else None)

    def bill(self, usage: dict | None) -> base.AttemptResult:
        """The last line's usage as a bill. `prompt_tokens` counts the cached
        part too, which DeepSeek states as `prompt_cache_hit_tokens` and the
        OpenAI shape as `prompt_tokens_details.cached_tokens`; there are no
        cache writes to pay for in this shape."""
        usage = usage or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        details = usage.get("prompt_tokens_details") or {}
        cached = int(usage.get("prompt_cache_hit_tokens")
                     or (details.get("cached_tokens") if isinstance(details, dict) else 0)
                     or 0)
        cost = usage.get("cost") if self.reports_cost else None
        return base.AttemptResult(
            cost_usd=float(cost) if isinstance(cost, int | float) else None,
            tokens_in=max(0, prompt - cached), tokens_out=int(usage.get("completion_tokens")
                                                               or 0),
            cache_creation=0, cache_read=cached)

    def error_kind(self, exc: BaseException) -> str | None:
        if isinstance(exc, ProviderError):
            if exc.status in (401, 402, 403):
                # 402 is an empty balance: as dead as a revoked key until
                # somebody pays, which is what the three-strikes pause is for.
                return "auth"
            if exc.status in (408, 409, 429) or exc.status >= 500:
                return "transient"
            return "auth" if base._error_kind(exc) == "auth" else "fatal"
        # A stream that dropped, or a line it cut in half.
        if isinstance(exc, httpx.TransportError | json.JSONDecodeError):
            return "transient"
        return None


def make_driver(system_prompt: str, system_prompt_path) -> loop.LoopDriver:
    client = OpenAICompatibleClient(model=base.MODEL, spec=base.PROVIDER)
    return loop.LoopDriver(client, system_prompt)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(base.run_harness(make_driver)))
