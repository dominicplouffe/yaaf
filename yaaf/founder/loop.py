"""The Founder and the grader on any provider with a wire (docs/PROVIDERS.md §7).

On an Anthropic credential both run on the Claude Code CLI, through the Agent
SDK, in this process (`yaaf.founder.session`) — a Claude subscription token is
a CLI credential. Every other provider runs the same conversation on its own
wire (`yaaf.runtimes.wire`): the system prompt, the turn, the Founder's own
tools as functions, the loop that calls them until the model answers. Gemini
natively, OpenAI on its Responses API, Bedrock, Azure, Vertex and every
OpenAI-shaped provider — the same file a seat's run speaks through, so a
request the Founder can make is one a seat can make.

Still the platform credential and never a project's. Still no built-in tool of
any kind — the Founder's seven are the whole surface, exactly as the SDK path
loads none of the CLI's. Still bounded the way the SDK path is: by silence
(`progress.STALL_SECONDS` between replies) and by turns (`MAX_TURNS`), never
by a deadline on the work.

Not metered, as the SDK path is not: the platform credential pays for
product-wide conversations, which belong to no project's budget.
"""

from __future__ import annotations

import logging

import httpx

from yaaf.founder.session import MAX_TURNS, FounderModelError
from yaaf.runtimes import catalog, wire
from yaaf.runtimes.providers import provider, spec
from yaaf.runtimes.seats import unmodelled

log = logging.getLogger("yaaf.founder")

#: Where a credential whose address is its own (a custom server, an Azure
#: resource, a Bedrock region) puts it, in the environment
#: `yaaf.core.platform.founder_env` builds.
BASE_URL_ENV = "CUSTOM_LLM_BASE_URL"

#: Enough for a whole draft in one reply, and within what every wire accepts
#: as an output ceiling.
MAX_OUTPUT_TOKENS = 16_384


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


def endpoint(env: dict[str, str], model: str) -> tuple[wire.Endpoint, str]:
    """(where to reach the model's provider, the model's own name), from the
    credential's environment."""
    if catalog.is_class(model):
        raise FounderModelError("unknown", unmodelled(model))
    seller = provider(catalog.provider_of(model))
    found = spec(seller, "api_key", env.get(BASE_URL_ENV))
    key = env.get(found["key_env"], "")
    if seller.protocol is None or not found["base_url"] or (not key and not seller.local):
        raise FounderModelError(
            "auth", f"the platform credential does not pay for {model}: the Founder's model "
                    "and its credential must be the same provider's — set them together in "
                    "Settings")
    return wire.Endpoint.from_spec(found, key), model.removeprefix(f"{seller.name}/")


def _refused(exc: wire.WireError) -> FounderModelError:
    if exc.kind == "auth":
        return FounderModelError("auth", f"the model rejected this credential. Check "
                                         f"Settings. ({exc})")
    if exc.kind == "transient":
        return FounderModelError("busy", f"the model is rate limited or overloaded — try "
                                         f"again shortly. ({exc})")
    return FounderModelError("unknown", str(exc))


async def run(prompt: str, *, env: dict[str, str], model: str, system: str,
              tools: list | None = None, thread_id: str = "",
              transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Drive one turn to completion and return what the model said — the
    contract `session.run` keeps for the SDK. `tools` are `(name, description,
    schema, handler)`, the handler taking the arguments and returning the MCP
    content the SDK's tools return."""
    from yaaf.founder import progress

    where, name = endpoint(env, model)
    offered = {t[0]: t for t in tools or ()}
    specs = [wire.ToolSpec(n, d, s) for n, d, s, _ in offered.values()]
    transcript: list[wire.Turn] = [wire.Turn("user", text=prompt)]
    said: list[str] = []
    for _ in range(MAX_TURNS):
        try:
            replies = [reply async for reply in wire.call(
                where, name, system, transcript, specs, MAX_OUTPUT_TOKENS,
                transport=transport, read_timeout=progress.STALL_SECONDS)]
            reply = replies[-1] if replies else None
        except httpx.TimeoutException as exc:
            raise FounderModelError(
                "stalled", f"the model said nothing for {int(progress.STALL_SECONDS)}s. "
                           "That is a hang rather than slow work — the conversation "
                           "is saved, so try again.") from exc
        except httpx.TransportError as exc:
            raise FounderModelError("busy", f"could not reach {where.base_url}: "
                                            f"{exc!r}"[:300]) from exc
        except wire.WireError as exc:
            raise _refused(exc) from exc
        if reply is None:
            raise FounderModelError("unknown", "the model's reply was empty")
        if reply.stop == "refusal":
            raise FounderModelError("unknown", f"the model refused: {reply.detail or '-'}")
        text = reply.turn.text.strip()
        if text:
            said.append(text)
            if thread_id:
                progress.note(thread_id, text)
        elif thread_id:
            progress.touch(thread_id)
        if not reply.turn.calls:
            return "\n\n".join(said).strip()
        # The reply goes back as its wire wrote it — with the reasoning a
        # provider requires replayed, and without what one refuses to see
        # again (DeepSeek's reasoning_content never leaves the wire).
        transcript.append(reply.turn)
        outputs = [wire.ToolOutput(c.id, await _call(offered, c.name, c.input))
                   for c in reply.turn.calls]
        transcript.append(wire.Turn("user", outputs=tuple(outputs)))
    raise FounderModelError(
        "looping", f"the Founder used all {MAX_TURNS} of its turns without settling on a "
                   "draft. That is a loop rather than slow work — something refused the "
                   "same draft over and over. The conversation is saved.")


async def _call(offered: dict, name: str, arguments: dict) -> str:
    tool = offered.get(name)
    if tool is None:
        return f"ERROR: no tool named {name!r}"
    if "_unparsed_arguments" in arguments:
        return "ERROR: the arguments were not valid JSON — send them again"
    result = await tool[3](arguments)
    return "\n".join(block.get("text", "") for block in (result or {}).get("content") or []
                     if isinstance(block, dict))
