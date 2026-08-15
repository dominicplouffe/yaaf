"""Runs INSIDE the sandbox container: the resident CEO chat session.

The talk lane. Where ``sandbox_entry.py`` drives one stateless unit of work,
this process stays up and holds a conversation: it polls the boardroom as the
CEO, and answers the chairman within seconds — no run queue, no wake caps, no
waiting a turn. Reading through the agent API advances the CEO's read cursor,
which is exactly what keeps the dispatcher from also waking a work run for the
same message.

Same containment as any run: the per-run token, the egress proxy, the agent
HTTP API — and the same platform toolset, so "CEO, do X" can happen right in
the conversation, under the same server-side refusals.

Imports sandbox_entry (staged next to this file in the image) for the tool
definitions and HTTP plumbing; do not import from the yaaf package here.
"""

from __future__ import annotations

import asyncio
import sys

import sandbox_entry as base
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
)

POLL_S = 2.0
MAX_REPLY_CHARS = 19_000
HISTORY_BOOTSTRAP = 30

# Consecutive auth failures before concluding the token was revoked (project
# paused, kill switch) and exiting for the supervisor to see.
AUTH_FAILURES_TO_EXIT = 3


def _log(text: str) -> None:
    print(f"[chat_entry] {text}", file=sys.stderr, flush=True)


def _boardroom_channel() -> str | None:
    result = base._call("GET", "/agent/channels")
    if result["status"] != 200:
        return None
    for channel in result["body"]:
        if channel.get("kind") == "boardroom":
            return str(channel["id"])
    return None


def _read_messages(channel_id: str, limit: int = 50) -> list[dict] | None:
    result = base._call("GET", f"/agent/channels/{channel_id}/messages",
                        params={"limit": limit})
    if result["status"] != 200:
        return None
    return list(result["body"])


def _set_typing(channel_id: str, seconds: float) -> None:
    base._call("POST", "/agent/typing", {"channel_id": channel_id, "seconds": seconds})


def _post_reply(channel_id: str, text: str) -> bool:
    body = text.strip()[:MAX_REPLY_CHARS] or "(no reply)"
    result = base._call("POST", "/agent/messages", {"channel_id": channel_id, "body": body})
    return result["status"] == 200


def _book_usage(tokens_in: int, tokens_out: int, cost_usd) -> None:
    base._call("POST", "/agent/chat_usage", {"tokens_in": tokens_in,
                                             "tokens_out": tokens_out,
                                             "cost_usd": cost_usd})


def _usage_tokens(message: ResultMessage) -> tuple[int, int]:
    usage = message.usage
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
    return (int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0))


def _transcript_block(messages: list[dict]) -> str:
    lines = []
    for m in messages[-HISTORY_BOOTSTRAP:]:
        who = "Chairman" if m.get("from_user") else "You (CEO)"
        lines.append(f"{who}: {m.get('body', '')}")
    return "\n".join(lines)


# total_cost_usd is cumulative across the SDK session; tokens in usage are per
# response. Book the cost delta so a long-lived session never double-bills.
_cost_booked = 0.0


async def _run_turn(client: ClaudeSDKClient, channel_id: str, prompt: str) -> str:
    """One conversational turn: stream the model, keep the typing flag warm,
    meter usage, return the reply text."""
    global _cost_booked
    reply_parts: list[str] = []
    await client.query(prompt)
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            _set_typing(channel_id, 60)
            for block in message.content:
                if isinstance(block, TextBlock):
                    reply_parts.append(block.text)
        elif isinstance(message, ResultMessage):
            tokens_in, tokens_out = _usage_tokens(message)
            cost_delta = None
            if message.total_cost_usd is not None:
                cost_delta = max(0.0, message.total_cost_usd - _cost_booked)
                _cost_booked = max(_cost_booked, message.total_cost_usd)
            _book_usage(tokens_in, tokens_out, cost_delta)
    return "\n".join(part for part in reply_parts if part.strip())


async def main() -> int:
    server = create_sdk_mcp_server(name="yaaf", version="1.0.0", tools=base.ALL_TOOLS)
    options = ClaudeAgentOptions(
        system_prompt=(base.STAGING / "system_prompt.txt").read_text(),
        model=base.MODEL,
        effort=base.EFFORT,
        mcp_servers={"yaaf": server, **base._external_mcp_servers()},
        allowed_tools=base._allowed_tools(),
        permission_mode="bypassPermissions",  # containment is the sandbox + proxy + API
        cwd="/workspace",
        setting_sources=[],
        max_turns=30,
        # Same live lesson as sandbox_entry: a real data pull can exceed the
        # SDK's default 1MB transport buffer.
        max_buffer_size=16 * 1024 * 1024,
    )

    channel_id = None
    while channel_id is None:
        channel_id = _boardroom_channel()
        if channel_id is None:
            _log("boardroom not resolvable yet; retrying")
            await asyncio.sleep(POLL_S)

    # Baseline read: everything before boot is history, not a backlog to answer
    # (a work run may already have answered it). It also advances the cursor.
    history = _read_messages(channel_id) or []
    last_seen = history[-1]["id"] if history else None
    first_turn = True
    auth_failures = 0

    _log(f"chat session live on {channel_id}; {len(history)} message(s) of history")

    async with ClaudeSDKClient(options) as client:
        while True:
            messages = _read_messages(channel_id)
            if messages is None:
                auth_failures += 1
                if auth_failures >= AUTH_FAILURES_TO_EXIT:
                    _log("token appears revoked; exiting for the supervisor")
                    return 0
                await asyncio.sleep(POLL_S)
                continue
            auth_failures = 0

            seen_ids = [m["id"] for m in messages]
            start = seen_ids.index(last_seen) + 1 if last_seen in seen_ids else 0
            fresh = [m for m in messages[start:] if m.get("from_user")]
            if messages:
                last_seen = messages[-1]["id"]
            if not fresh:
                await asyncio.sleep(POLL_S)
                continue

            _set_typing(channel_id, 120)
            prompt_lines = [f"Chairman: {m['body']}" for m in fresh]
            prompt = "\n".join(prompt_lines)
            if first_turn:
                preamble = ""
                if history:
                    preamble += ("Boardroom conversation so far (you may have replied via "
                                 "earlier runs — do not re-answer it):\n"
                                 f"{_transcript_block(history)}\n\n")
                if base.MOUNT_PROBLEMS:
                    preamble += "Data source problems this session:\n" + \
                        "\n".join(base.MOUNT_PROBLEMS) + "\n\n"
                prompt = preamble + "The chairman just said:\n" + prompt
                first_turn = False

            try:
                reply = await _run_turn(client, channel_id, prompt)
            except Exception as exc:
                _log(f"turn failed: {exc!r}")
                _set_typing(channel_id, 0)
                _post_reply(channel_id,
                            "I hit an internal error composing that reply — the platform "
                            f"will surface it in my session logs. ({type(exc).__name__})")
                # A broken SDK session cannot be trusted for the next turn;
                # exit and let the supervisor start a clean one.
                return 1

            _set_typing(channel_id, 0)
            if reply and not _post_reply(channel_id, reply):
                _log("reply post refused or failed; leaving it in the logs:\n" + reply[:500])
            # Anything the chairman sent while we were composing is picked up
            # on the next poll — immediately, not next tick.


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
