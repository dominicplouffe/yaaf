"""The Founder and the grader on any provider but Anthropic (docs/RUNTIMES.md, M6).

On an Anthropic credential both run on the Claude Code CLI, through the Agent
SDK, in this process (`yaaf.founder.session`). Every other provider the
platform credential can name speaks the OpenAI-compatible chat API, so this
is the same conversation on that: the system prompt, the turn, the Founder's
own tools as functions, the loop that calls them until the model answers.

Still the platform credential and never a project's. Still no built-in tool of
any kind — the Founder's seven are the whole surface, exactly as the SDK path
loads none of the CLI's. Still bounded the way the SDK path is: by silence
(`progress.STALL_SECONDS` between replies) and by turns (`MAX_TURNS`), never
by a deadline on the work.

Not metered, as the SDK path is not: the platform credential pays for
product-wide conversations, which belong to no project's budget.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from yaaf.founder.session import MAX_TURNS, FounderModelError
from yaaf.runtimes import catalog
from yaaf.runtimes.providers import provider

log = logging.getLogger("yaaf.founder")

#: Where a credential whose address is its own (a custom server) puts it, in
#: the environment `yaaf.core.platform.founder_env` builds.
BASE_URL_ENV = "CUSTOM_LLM_BASE_URL"

CONNECT_TIMEOUT_S = 30.0


def schema(fields: dict) -> dict:
    """The JSON schema the SDK's `@tool` builds from `{name: type}`, for the
    function-calling shape, which takes the schema itself."""
    if fields.get("type") == "object":
        return fields
    kinds = {str: "string", int: "integer", float: "number", bool: "boolean",
             dict: "object", list: "array"}
    return {"type": "object",
            "properties": {name: {"type": kinds.get(kind, "string")}
                           for name, kind in fields.items()}}


def address(env: dict[str, str], model: str) -> tuple[str, str, str]:
    """(base URL, key, the model's own name) for a model of a provider reached
    over the OpenAI-compatible chat API, from the credential's environment."""
    seller = provider(catalog.provider_of(model))
    base_url = env.get(BASE_URL_ENV) if seller.credential_base_url else seller.base_url
    key = env.get(seller.kind("api_key").env_var)
    if not base_url or not key:
        raise FounderModelError(
            "auth", f"the platform credential does not pay for {model}: the Founder's model "
                    "and its credential must be the same provider's — set them together in "
                    "Settings")
    return base_url.rstrip("/"), key, model.removeprefix(f"{seller.name}/")


def _refused(status: int, body: Any) -> FounderModelError:
    message = body
    if isinstance(body, dict):
        error = body.get("error", body)
        message = error.get("message", error) if isinstance(error, dict) else error
    said = f"HTTP {status}: {str(message)[:300]}"
    if status in (401, 402, 403):
        return FounderModelError("auth", f"the model rejected this credential. Check "
                                         f"Settings. ({said})")
    if status in (408, 409, 429) or status >= 500:
        return FounderModelError("busy", f"the model is rate limited or overloaded — try "
                                         f"again shortly. ({said})")
    return FounderModelError("unknown", said)


async def run(prompt: str, *, env: dict[str, str], model: str, system: str,
              tools: list | None = None, thread_id: str = "",
              transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Drive one turn to completion and return what the model said — the
    contract `session.run` keeps for the SDK. `tools` are `(name, description,
    schema, handler)`, the handler taking the arguments and returning the MCP
    content the SDK's tools return."""
    from yaaf.founder import progress

    base_url, key, name = address(env, model)
    offered = {t[0]: t for t in tools or ()}
    messages: list[dict] = [{"role": "system", "content": system},
                            {"role": "user", "content": prompt}]
    said: list[str] = []
    timeout = httpx.Timeout(CONNECT_TIMEOUT_S, read=progress.STALL_SECONDS)
    async with httpx.AsyncClient(transport=transport, timeout=timeout) as http:
        for _ in range(MAX_TURNS):
            body: dict = {"model": name, "messages": messages}
            if offered:
                body["tools"] = [{"type": "function", "function": {
                    "name": n, "description": d, "parameters": s}}
                    for n, d, s, _ in offered.values()]
            try:
                response = await http.post(f"{base_url}/chat/completions", json=body,
                                           headers={"Authorization": f"Bearer {key}"})
            except httpx.TimeoutException as exc:
                raise FounderModelError(
                    "stalled", f"the model said nothing for {int(progress.STALL_SECONDS)}s. "
                               "That is a hang rather than slow work — the conversation "
                               "is saved, so try again.") from exc
            except httpx.TransportError as exc:
                raise FounderModelError("busy", f"could not reach {base_url}: {exc!r}"[:300]) \
                    from exc
            if response.status_code >= 400:
                try:
                    refused = response.json()
                except ValueError:
                    refused = response.text
                raise _refused(response.status_code, refused)
            message = ((response.json().get("choices") or [{}])[0].get("message") or {})
            text = (message.get("content") or "").strip()
            if text:
                said.append(text)
                if thread_id:
                    progress.note(thread_id, text)
            elif thread_id:
                progress.touch(thread_id)
            calls = message.get("tool_calls") or []
            if not calls:
                return "\n\n".join(said).strip()
            # Reasoning a provider returned beside the reply is never sent
            # back: DeepSeek refuses a request that carries it.
            messages.append({"role": "assistant", "content": message.get("content"),
                             "tool_calls": calls})
            for call in calls:
                function = call.get("function") or {}
                messages.append({"role": "tool", "tool_call_id": call.get("id"),
                                 "content": await _call(offered, function)})
    raise FounderModelError(
        "looping", f"the Founder used all {MAX_TURNS} of its turns without settling on a "
                   "draft. That is a loop rather than slow work — something refused the "
                   "same draft over and over. The conversation is saved.")


async def _call(offered: dict, function: dict) -> str:
    tool = offered.get(function.get("name"))
    if tool is None:
        return f"ERROR: no tool named {function.get('name')!r}"
    try:
        arguments = json.loads(function.get("arguments") or "{}")
    except ValueError:
        return "ERROR: the arguments were not valid JSON — send them again"
    if not isinstance(arguments, dict):
        return "ERROR: the arguments must be an object"
    result = await tool[3](arguments)
    return "\n".join(block.get("text", "") for block in (result or {}).get("content") or []
                     if isinstance(block, dict))
