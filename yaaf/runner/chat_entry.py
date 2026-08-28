"""Runs INSIDE the sandbox container: the resident CEO chat session.

The talk lane. Where ``sandbox_entry.py`` drives one stateless unit of work,
this process stays up and holds a conversation: it polls the boardroom as the
CEO, and answers the chairman within seconds — no run queue, no wake caps, no
waiting a turn. Reading through the agent API advances the CEO's read cursor,
which is exactly what keeps the dispatcher from also waking a work run for the
same message.

Same containment as any run: the per-run token, the sandbox, the agent
HTTP API — and the same platform toolset, so "CEO, do X" can happen right in
the conversation, under the same server-side refusals.

Imports sandbox_entry (staged next to this file in the image) for the tool
definitions and HTTP plumbing; do not import from the yaaf package here.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

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

# Only a genuine 401/403 means the token was revoked (project paused, kill
# switch) — anything else is the API restarting, the network hiccuping, or a
# timeout, and killing a healthy session over six bad seconds of network was
# a top cause of the restart churn. Transients back off and only give up
# after TRANSIENT_EXIT_S of continuous failure.
AUTH_FAILURES_TO_EXIT = 3
BOOT_AUTH_FAILURES_TO_EXIT = 10
TRANSIENT_BACKOFF_START_S = 2.0
TRANSIENT_BACKOFF_MAX_S = 30.0
TRANSIENT_EXIT_S = 300.0
# A failed turn no longer kills the process: the SDK client is rebuilt
# in-process (the CLI under it is a subprocess that can legitimately die,
# e.g. at the max_turns backstop). This caps the rebuilds per session so a
# systematically broken SDK still surfaces to the supervisor.
MAX_CLIENT_REBUILDS = 5


def _log(text: str) -> None:
    print(f"[chat_entry] {text}", file=sys.stderr, flush=True)


def _failure_kind(status: int) -> str:
    """401/403 = the token is genuinely dead; everything else (0 = transport
    exception, 5xx, 429, 404) is transient until proven otherwise."""
    return "auth" if status in (401, 403) else "transient"


def _boardroom_channel() -> tuple[int, tuple[str, int] | None]:
    """(status, (channel_id, unread_count)) for the boardroom — the unread
    count is captured BEFORE the first read advances the cursor, so a session
    that boots onto a waiting chairman message answers it."""
    result = base._call("GET", "/agent/channels")
    if result["status"] != 200:
        return result["status"], None
    for channel in result["body"]:
        if channel.get("kind") == "boardroom":
            return 200, (str(channel["id"]), int(channel.get("unread", 0)))
    return 200, None


def _split_backlog(messages: list[dict], unread: int) -> tuple[list[dict], list[dict]]:
    """(history, backlog): history was read by some earlier run — never
    re-answer it; the unread tail's chairman messages are a backlog this
    session owes an answer to, restart or not."""
    if unread <= 0:
        return messages, []
    history, tail = messages[:-unread], messages[-unread:]
    return history, [m for m in tail if m.get("from_user")]


def _read_messages(channel_id: str, limit: int = 50) -> tuple[int, list[dict] | None]:
    result = base._call("GET", "/agent/messages",
                        params={"channel_id": channel_id, "limit": limit})
    if result["status"] != 200:
        return result["status"], None
    return 200, list(result["body"])


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


def _situation_block() -> str:
    """What is true right now, fetched fresh before every answer.

    A chat session is one container that lives for days, and its system prompt
    is written once at boot and never re-read. So the boardroom CEO held no
    board, no roster, no notices, no budget and no idea whether a cap had
    stopped the project — while being told, in that same frozen prompt, not to
    invent a cause when work had stopped. It guessed, and the guesses reached
    the chairman as fact. This is the fix: a work run gets a context pack on
    every wake, and now so does every turn of the conversation.

    Undelivered notices ride along and are marked delivered by the same call,
    so "your run was reaped" and "budget exhausted" finally reach the one agent
    the chairman actually talks to.
    """
    result = base._call("GET", "/agent/context")
    if result["status"] != 200:
        # Never block a reply on this: an unanswered chairman is worse than an
        # under-briefed one. Say which it is rather than pretending.
        return ("[The platform did not return the current state this turn "
                f"({result['status']}). Read the board with your tools before making "
                "any claim about what is or is not happening.]\n\n")
    pack = result["body"]
    keep = {k: pack[k] for k in
            ("situation", "board", "plan", "chairman", "notices", "roster", "cycles",
             "review", "commitment", "velocity", "scope")
            if pack.get(k)}
    return (
        "CURRENT STATE (fetched just now — this is the platform's own reading, not a "
        "summary. `situation.why` is the exact sentence the chairman can see on his "
        "screen; if he asks why nothing is happening, that is the answer. Do not "
        "reason about the company's state from what you can see in a directory):\n"
        f"{json.dumps(keep, indent=2, default=str)}\n\n"
    )


def _transcript_block(messages: list[dict]) -> str:
    lines = []
    for m in messages[-HISTORY_BOOTSTRAP:]:
        who = "Chairman" if m.get("from_user") else "You (CEO)"
        body = str(m.get("body", ""))
        if len(body) > CHAT_HISTORY_MSG_CHARS:
            body = body[:CHAT_HISTORY_MSG_CHARS] + " …[truncated; messages_read has the rest]"
        lines.append(f"{who}: {body}")
    return "\n".join(lines)


class TurnFailed(Exception):
    """The CLI reported the turn as an error (error_max_turns,
    error_during_execution, ...) — the reply text cannot be trusted."""


def _turn_result_ok(result) -> bool:
    if getattr(result, "is_error", False):
        return False
    subtype = getattr(result, "subtype", None)
    return subtype in (None, "success")


# total_cost_usd is cumulative across the SDK session; tokens in usage are per
# response. Book the cost delta so a long-lived session never double-bills.
# Reset whenever a new SDK client (= new CLI process) starts.
_cost_booked = 0.0


async def _run_turn(client: ClaudeSDKClient, channel_id: str, prompt: str) -> str:
    """One conversational turn: stream the model, keep the typing flag warm,
    meter usage, return the reply text."""
    global _cost_booked
    reply_parts: list[str] = []
    result = None
    await client.query(prompt)
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            _set_typing(channel_id, 60)
            for block in message.content:
                if isinstance(block, TextBlock):
                    reply_parts.append(block.text)
        elif isinstance(message, ResultMessage):
            result = message
            tokens_in, tokens_out, cache_creation, cache_read = _usage_tokens(message)
            cost_delta = None
            if message.total_cost_usd is not None:
                cost_delta = max(0.0, message.total_cost_usd - _cost_booked)
                _cost_booked = max(_cost_booked, message.total_cost_usd)
            _book_usage(tokens_in, tokens_out, cache_creation, cache_read, cost_delta)
    if result is not None and not _turn_result_ok(result):
        raise TurnFailed(getattr(result, "subtype", "unknown"))
    return "\n".join(part for part in reply_parts if part.strip())


async def _resolve_boardroom() -> tuple[str, int] | None:
    auth_failures = 0
    transient_since = None
    delay = TRANSIENT_BACKOFF_START_S
    while True:
        status, resolved = _boardroom_channel()
        if resolved is not None:
            return resolved
        if _failure_kind(status) == "auth":
            auth_failures += 1
            if auth_failures >= BOOT_AUTH_FAILURES_TO_EXIT:
                _log(f"boardroom auth-refused {auth_failures}x (status {status}); "
                     "exiting for the supervisor")
                return None
            _log(f"boardroom auth failure (status {status}); retrying")
            await asyncio.sleep(POLL_S)
        else:
            now = time.monotonic()
            transient_since = transient_since if transient_since is not None else now
            if now - transient_since >= TRANSIENT_EXIT_S:
                _log(f"boardroom unreachable for {TRANSIENT_EXIT_S:.0f}s "
                     f"(last status {status}); exiting for the supervisor")
                return None
            _log(f"boardroom not resolvable yet (status {status}); retrying")
            await asyncio.sleep(delay)
            delay = min(TRANSIENT_BACKOFF_MAX_S, delay * 2)


async def _serve(client: ClaudeSDKClient, channel_id: str, state: dict) -> int | None:
    """The poll/compose loop on one SDK client. Returns an exit code to end
    the process, or None to have the caller rebuild the client and carry on."""
    auth_failures = 0
    transient_since = None
    delay = TRANSIENT_BACKOFF_START_S
    first_turn = True
    while True:
        if state["backlog"]:
            fresh, state["backlog"] = state["backlog"], []
        else:
            status, messages = _read_messages(channel_id)
            if messages is None:
                if _failure_kind(status) == "auth":
                    auth_failures += 1
                    if auth_failures >= AUTH_FAILURES_TO_EXIT:
                        _log(f"token revoked (status {status}); exiting for the supervisor")
                        return 0
                    await asyncio.sleep(POLL_S)
                else:
                    now = time.monotonic()
                    transient_since = transient_since if transient_since is not None else now
                    if now - transient_since >= TRANSIENT_EXIT_S:
                        _log(f"API unreachable for {TRANSIENT_EXIT_S:.0f}s "
                             f"(last status {status}); exiting for the supervisor")
                        return 1
                    await asyncio.sleep(delay)
                    delay = min(TRANSIENT_BACKOFF_MAX_S, delay * 2)
                continue
            auth_failures = 0
            transient_since = None
            delay = TRANSIENT_BACKOFF_START_S

            seen_ids = [m["id"] for m in messages]
            start = seen_ids.index(state["last_seen"]) + 1 \
                if state["last_seen"] in seen_ids else 0
            fresh = [m for m in messages[start:] if m.get("from_user")]
            if messages:
                state["last_seen"] = messages[-1]["id"]
            if not fresh:
                await asyncio.sleep(POLL_S)
                continue

        _set_typing(channel_id, 120)
        # Carry the message id: a standing instruction he gives here has to
        # be citable to `directive_file`, and without the id the CEO can
        # only write it down and hope the next run reads the note.
        prompt_lines = [f"Chairman [{m['id']}]: {m['body']}" for m in fresh]
        prompt = "\n".join(prompt_lines)
        # Every turn, not just the first: this session outlives amendments,
        # budget changes, finished tasks and its own answers.
        preamble = _situation_block()
        if first_turn:
            if state["history"]:
                preamble += ("Boardroom conversation so far (you may have replied via "
                             "earlier runs — do not re-answer it):\n"
                             f"{_transcript_block(state['history'])}\n\n")
            if base.MOUNT_PROBLEMS:
                preamble += "Data source problems this session:\n" + \
                    "\n".join(base.MOUNT_PROBLEMS) + "\n\n"
            first_turn = False
        prompt = preamble + "The chairman just said:\n" + prompt

        try:
            reply = await _run_turn(client, channel_id, prompt)
        except Exception as exc:
            _log(f"turn failed: {exc!r}; rebuilding the SDK client")
            _set_typing(channel_id, 0)
            ids_ = {m["id"] for m in fresh}
            if ids_ and not state["retried"].intersection(ids_):
                # Retry the failed message exactly once on the fresh client;
                # a message that kills two clients is poison — apologize and
                # move on rather than crash-loop on it.
                state["retried"].update(ids_)
                state["backlog"] = fresh + state["backlog"]
            else:
                _post_reply(channel_id,
                            "I hit an internal error composing that reply — the platform "
                            f"will surface it in my session logs. ({type(exc).__name__})")
            return None

        _set_typing(channel_id, 0)
        if reply and not _post_reply(channel_id, reply):
            # Composed and undeliverable — a revoked token, a refused post. The
            # reply itself is lost, but the chairman is not left guessing: the
            # supervisor sees the session end and says so in the boardroom
            # (chat._end_session). It cannot be said from in here, because the
            # thing that stopped the post also stops this.
            _log("reply post refused or failed; leaving it in the logs:\n" + reply[:500])
        # Anything the chairman sent while we were composing is picked up
        # on the next poll — immediately, not next tick.


async def main() -> int:
    global _cost_booked
    server = create_sdk_mcp_server(name="yaaf", version="1.0.0", tools=base.run_tools())
    options = ClaudeAgentOptions(
        system_prompt=(base.STAGING / "system_prompt.txt").read_text(),
        model=base.MODEL,
        effort=base.EFFORT,
        mcp_servers={"yaaf": server, **base._external_mcp_servers()},
        tools=base._builtin_tools(),
        allowed_tools=base._allowed_tools(),
        permission_mode="bypassPermissions",  # containment is the sandbox + API
        cwd="/workspace",
        setting_sources=[],
        # A runaway backstop, not a lifetime budget: the old value (30) was
        # cumulative across the CLI process and quietly killed every busy
        # session — tool calls count as turns.
        max_turns=1000,
        # Same live lesson as sandbox_entry: a real data pull can exceed the
        # SDK's default 1MB transport buffer.
        max_buffer_size=16 * 1024 * 1024,
    )

    resolved = await _resolve_boardroom()
    if resolved is None:
        return 1
    channel_id, unread = resolved

    # Baseline read advances the cursor. What some earlier run already read is
    # history; the unread tail is a backlog this session answers immediately —
    # a chairman must never be silently skipped because a session restarted.
    _, messages = _read_messages(channel_id)
    messages = messages or []
    history, backlog = _split_backlog(messages, unread)
    state = {
        "last_seen": messages[-1]["id"] if messages else None,
        "history": history,
        "backlog": backlog,
        "retried": set(),
    }

    _log(f"chat session live on {channel_id}; {len(history)} message(s) of history, "
         f"{len(backlog)} awaiting an answer")

    rebuilds = 0
    while True:
        _cost_booked = 0.0  # new CLI process: its cumulative total_cost_usd restarts at 0
        try:
            async with ClaudeSDKClient(options) as client:
                code = await _serve(client, channel_id, state)
                if code is not None:
                    return code
        except Exception as exc:
            _log(f"sdk client failed: {exc!r}")
        rebuilds += 1
        if rebuilds > MAX_CLIENT_REBUILDS:
            _log("sdk client rebuilt too many times; exiting for the supervisor")
            return 1
        # The new client has no model context: refresh history so its first
        # turn re-primes with the transcript.
        _, messages = _read_messages(channel_id)
        if messages:
            state["history"] = messages
        await asyncio.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
