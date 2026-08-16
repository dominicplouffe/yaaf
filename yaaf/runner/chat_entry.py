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
# Bootstrap is context, not archive: message bodies run to 20k chars, and 30
# uncut ones would put ~150k tokens into the first turn of every session.
CHAT_HISTORY_MSG_CHARS = 1_000

# Consecutive auth failures before concluding the token was revoked (project
# paused, kill switch) and exiting for the supervisor to see.
AUTH_FAILURES_TO_EXIT = 3


def _log(text: str) -> None:
    print(f"[chat_entry] {text}", file=sys.stderr, flush=True)


def _boardroom_channel() -> tuple[str, int] | None:
    """(channel_id, unread_count) for the boardroom, or None on any failure —
    the unread count is captured BEFORE the first read advances the cursor,
    so a session that boots onto a waiting chairman message answers it."""
    result = base._call("GET", "/agent/channels")
    if result["status"] != 200:
        return None
    for channel in result["body"]:
        if channel.get("kind") == "boardroom":
            return str(channel["id"]), int(channel.get("unread", 0))
    return None


def _split_backlog(messages: list[dict], unread: int) -> tuple[list[dict], list[dict]]:
    """(history, backlog): history was read by some earlier run — never
    re-answer it; the unread tail's chairman messages are a backlog this
    session owes an answer to, restart or not."""
    if unread <= 0:
        return messages, []
    history, tail = messages[:-unread], messages[-unread:]
    return history, [m for m in tail if m.get("from_user")]


def _read_messages(channel_id: str, limit: int = 50) -> list[dict] | None:
    result = base._call("GET", "/agent/messages",
                        params={"channel_id": channel_id, "limit": limit})
    if result["status"] != 200:
        return None
    return list(result["body"])


def _set_typing(channel_id: str, seconds: float) -> None:
    base._call("POST", "/agent/typing", {"channel_id": channel_id, "seconds": seconds})


def _post_reply(channel_id: str, text: str) -> bool:
    body = text.strip()[:MAX_REPLY_CHARS] or "(no reply)"
    result = base._call("POST", "/agent/messages", {"channel_id": channel_id, "body": body})
    return result["status"] == 200


def _book_usage(tokens_in: int, tokens_out: int, cache_creation: int, cache_read: int,
                cost_usd) -> None:
    base._call("POST", "/agent/chat_usage", {"tokens_in": tokens_in,
                                             "tokens_out": tokens_out,
                                             "cache_creation_tokens": cache_creation,
                                             "cache_read_tokens": cache_read,
                                             "cost_usd": cost_usd})


def _usage_tokens(message: ResultMessage) -> tuple[int, int, int, int]:
    usage = message.usage
    if usage is None:
        return 0, 0, 0, 0
    return (base._usage_int(usage, "input_tokens"),
            base._usage_int(usage, "output_tokens"),
            base._usage_int(usage, "cache_creation_input_tokens"),
            base._usage_int(usage, "cache_read_input_tokens"))


def _transcript_block(messages: list[dict]) -> str:
    lines = []
    for m in messages[-HISTORY_BOOTSTRAP:]:
        who = "Chairman" if m.get("from_user") else "You (CEO)"
        body = str(m.get("body", ""))
        if len(body) > CHAT_HISTORY_MSG_CHARS:
            body = body[:CHAT_HISTORY_MSG_CHARS] + " …[truncated; messages_read has the rest]"
        lines.append(f"{who}: {body}")
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
            tokens_in, tokens_out, cache_creation, cache_read = _usage_tokens(message)
            cost_delta = None
            if message.total_cost_usd is not None:
                cost_delta = max(0.0, message.total_cost_usd - _cost_booked)
                _cost_booked = max(_cost_booked, message.total_cost_usd)
            _book_usage(tokens_in, tokens_out, cache_creation, cache_read, cost_delta)
    return "\n".join(part for part in reply_parts if part.strip())


async def main() -> int:
    server = create_sdk_mcp_server(name="yaaf", version="1.0.0", tools=base.ALL_TOOLS)
    options = ClaudeAgentOptions(
        system_prompt=(base.STAGING / "system_prompt.txt").read_text(),
        model=base.MODEL,
        effort=base.EFFORT,
        mcp_servers={"yaaf": server, **base._external_mcp_servers()},
        tools=base._builtin_tools(),
        allowed_tools=base._allowed_tools(),
        permission_mode="bypassPermissions",  # containment is the sandbox + proxy + API
        cwd="/workspace",
        setting_sources=[],
        max_turns=30,
        # Same live lesson as sandbox_entry: a real data pull can exceed the
        # SDK's default 1MB transport buffer.
        max_buffer_size=16 * 1024 * 1024,
    )

    resolved = None
    boot_failures = 0
    while resolved is None:
        resolved = _boardroom_channel()
        if resolved is None:
            boot_failures += 1
            # A dead token 401s here forever; a booting API recovers in a few
            # ticks. Persistent failure means exit and let the supervisor
            # start a clean session — a zombie that retries forever also
            # silently suppresses the fallback wake path (seen live).
            if boot_failures >= 10:
                _log("boardroom unresolvable after repeated attempts; exiting for the supervisor")
                return 1
            _log("boardroom not resolvable yet; retrying")
            await asyncio.sleep(POLL_S)
    channel_id, unread = resolved

    # Baseline read advances the cursor. What some earlier run already read is
    # history; the unread tail is a backlog this session answers immediately —
    # a chairman must never be silently skipped because a session restarted.
    messages = _read_messages(channel_id) or []
    history, backlog = _split_backlog(messages, unread)
    last_seen = messages[-1]["id"] if messages else None
    first_turn = True
    auth_failures = 0

    _log(f"chat session live on {channel_id}; {len(history)} message(s) of history, "
         f"{len(backlog)} awaiting an answer")

    async with ClaudeSDKClient(options) as client:
        while True:
            if backlog:
                fresh, backlog = backlog, []
            else:
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
            # Carry the message id: a standing instruction he gives here has to
            # be citable to `directive_file`, and without the id the CEO can
            # only write it down and hope the next run reads the note.
            prompt_lines = [f"Chairman [{m['id']}]: {m['body']}" for m in fresh]
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
