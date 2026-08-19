"""Runs INSIDE the sandbox container. Do not import from the yaaf package here —
the container only has this file, the Claude Agent SDK, and httpx.

It wires the yaaf platform tools (board, tasks, messages, memory, evidence,
approvals, side effects) as SDK MCP tools that call the agent HTTP API with the
per-run token, then drives one stateless run: context in, one unit of work,
result.json out.

Refusals come back to the model as tool output — the refusal text *is* the
interface. The platform separately counts them and revokes the token at the cap.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

API = os.environ["YAAF_API_BASE_URL"]
TOKEN = os.environ["YAAF_API_TOKEN"]
RUN_ID = os.environ["YAAF_RUN_ID"]
MODEL = os.environ.get("YAAF_MODEL", "claude-sonnet-5")
EFFORT = os.environ.get("YAAF_EFFORT", "high")
# A run that spends this much has stopped working and started looping. The SDK
# tells the model as it approaches and stops it at the line, so the run ends
# having said something rather than being cut off silently. 0 = unbounded.
MAX_USD = float(os.environ.get("YAAF_MAX_USD", "0") or 0)
PERMS = json.loads(os.environ.get("YAAF_TOOL_PERMISSIONS", "{}"))

STAGING = Path("/workspace/.yaaf")

_client = httpx.Client(
    base_url=API,
    timeout=30,
    headers={"Authorization": f"Bearer {TOKEN}"},
    # The platform API is reached directly, never through the egress proxy.
    trust_env=False,
)


def _call(method: str, path: str, payload: dict | None = None,
          params: dict | None = None) -> dict[str, Any]:
    try:
        response = _client.request(method, path, json=payload, params=params)
        try:
            body = response.json()
        except Exception:
            body = {"raw": response.text[:2000]}
        return {"status": response.status_code, "body": body}
    except Exception as exc:
        return {"status": 0, "body": {"error": f"platform unreachable: {exc!r}"}}


def _text(result: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]}


# ── platform tools ──────────────────────────────────────────────────────────────


@tool("board", "Read the task board visible to you (open + recently closed).", {})
async def t_board(args):
    return _text(_call("GET", "/agent/board"))


@tool(
    "task_create",
    "Create a task. CEO only for team tasks; team leads create subtasks (assignments) "
    "under a team task they claimed. success_test must be "
    '{"type":"command","command":...} (platform-executed) or '
    '{"type":"observable","target":...}. '
    "not_before is an ISO 8601 datetime before which the task cannot be claimed and "
    "wakes nobody — use it to lay work out in time (a weekly rhythm, a step that only "
    "makes sense after another lands) instead of creating it all at once.",
    {
        "team_id": str,
        "title": str,
        "goal": str,
        "success_test": dict,
        "produces_external_evidence": bool,
        "parent_task_id": str,
        "assignee_agent_id": str,
        "hypothesis_id": str,
        "not_before": str,
    },
)
async def t_task_create(args):
    payload = {k: v for k, v in args.items() if v not in (None, "")}
    return _text(_call("POST", "/agent/tasks", payload))


@tool("task_claim", "Claim a ready task that is yours to work.", {"task_id": str})
async def t_task_claim(args):
    return _text(_call("POST", f"/agent/tasks/{args['task_id']}/claim"))


@tool(
    "task_complete",
    "Complete your claimed task with observed evidence (what you ran/saw, quoted). "
    "Command-type success tests are then executed by the platform, not by you.",
    {"task_id": str, "evidence": str},
)
async def t_task_complete(args):
    return _text(
        _call("POST", f"/agent/tasks/{args['task_id']}/complete",
              {"evidence": args["evidence"]})
    )


@tool("task_block", "Block your claimed task with a reason; the CEO will re-plan.",
      {"task_id": str, "reason": str})
async def t_task_block(args):
    return _text(
        _call("POST", f"/agent/tasks/{args['task_id']}/block", {"reason": args["reason"]})
    )


@tool("task_kill", "Kill a task (CEO for team tasks; lead for own subtasks).",
      {"task_id": str, "reason": str})
async def t_task_kill(args):
    return _text(
        _call("POST", f"/agent/tasks/{args['task_id']}/kill", {"reason": args["reason"]})
    )


@tool("suggest", "Leave a suggestion for the CEO's next planning cycle. Creates no work.",
      {"text": str})
async def t_suggest(args):
    return _text(_call("POST", "/agent/suggestions", {"text": args["text"]}))


@tool("channels", "List your channels with unread counts.", {})
async def t_channels(args):
    return _text(_call("GET", "/agent/channels"))


@tool("messages_read", "Read recent messages in one of your channels (advances your cursor).",
      {"channel_id": str, "limit": int})
async def t_messages_read(args):
    return _text(_call("GET", "/agent/messages",
                       params={"channel_id": args["channel_id"],
                               "limit": args.get("limit") or 50}))


@tool(
    "message_post",
    "Post to one of your channels. Boardroom: CEO<->chairman. Leadership: CEO+leads. "
    "Team room: your team. Use thread_id to reply in a thread. An unprompted boardroom "
    "status is refused between the chairman's cadence slots (see the `chairman` block in "
    "your context); set override to 'blocker', 'budget' or 'approval' only when it truly "
    "cannot wait — overrides are recorded and he sees them.",
    {"channel_id": str, "body": str, "thread_id": str, "override": str},
)
async def t_message_post(args):
    payload = {"channel_id": args["channel_id"], "body": args["body"]}
    if args.get("thread_id"):
        payload["thread_id"] = args["thread_id"]
    if args.get("override"):
        payload["override"] = args["override"]
    return _text(_call("POST", "/agent/messages", payload))


@tool(
    "directive_file",
    "CEO: turn a standing instruction the chairman gave you in the boardroom into enforced "
    "policy, instead of a memory you have to remember. Cite the message he wrote it in. Use "
    "this the moment he changes how or how often he wants to be reported to — a rule kept in "
    "memory does not survive into the next run. Settable: "
    "chairman_report_interval_hours (0 disables), chairman_report_window_local ('09:00-20:00'), "
    "timezone (IANA name).",
    {"from_message_id": str, "changes": dict, "reason": str},
)
async def t_directive_file(args):
    return _text(_call("POST", "/agent/directives", {
        "from_message_id": args["from_message_id"],
        "changes": args["changes"],
        "reason": args["reason"],
    }))


@tool("memory_search", "Search durable memory. Query it before repeating work. "
      "include_superseded=true also returns entries folded away by compaction.",
      {"q": str, "scope": str, "include_superseded": bool})
async def t_memory_search(args):
    params = {"q": args.get("q", "")}
    if args.get("scope"):
        params["scope"] = args["scope"]
    if args.get("include_superseded"):
        params["include_superseded"] = "true"
    return _text(_call("GET", "/agent/memory/search", params=params))


@tool(
    "memory_add",
    "Write ONE durable fact/lesson/preference/contact that will still be true next month. "
    "Not a diary. scope: 'project' (CEO only) or 'team'.",
    {"scope": str, "kind": str, "subject": str, "body": str},
)
async def t_memory_add(args):
    return _text(_call("POST", "/agent/memory", dict(args)))


@tool("memory_supersede", "Replace ONE outdated memory with ONE new one (curators only). "
      "Frees no slot — use memory_compact or memory_forget for room.",
      {"memory_id": str, "kind": str, "subject": str, "body": str})
async def t_memory_supersede(args):
    memory_id = args.pop("memory_id")
    return _text(_call("POST", f"/agent/memory/{memory_id}/supersede", dict(args)))


@tool(
    "memory_compact",
    "Fold several overlapping memories into one (curators only) — the way to make room. "
    "Frees all but one of their slots; the originals stay readable via "
    "memory_search include_superseded=true, so folding is safe.",
    {"replaces": list[str], "kind": str, "subject": str, "body": str},
)
async def t_memory_compact(args):
    return _text(_call("POST", "/agent/memory/compact", dict(args)))


@tool("memory_forget", "Forget a wrong/dead memory (curators only).", {"memory_id": str})
async def t_memory_forget(args):
    return _text(_call("POST", f"/agent/memory/{args['memory_id']}/forget"))


@tool(
    "evidence_add",
    "Record evidence. kind='external' needs a source_url on a domain this project does NOT "
    "control; the platform fetches and verifies it before it counts. kind='internal' for "
    "everything we did to our own things.",
    {"kind": str, "description": str, "source_url": str, "task_id": str},
)
async def t_evidence_add(args):
    payload = {k: v for k, v in args.items() if v not in (None, "")}
    return _text(_call("POST", "/agent/evidence", payload))


@tool(
    "hypothesis_add",
    "CEO: state a falsifiable bet with a dated kill condition (ISO datetime), "
    "written BEFORE the work starts.",
    {"statement": str, "kill_condition": str, "kill_date": str},
)
async def t_hypothesis_add(args):
    return _text(_call("POST", "/agent/hypotheses", dict(args)))


@tool("hypothesis_kill", "CEO: kill a bet and say why.", {"hypothesis_id": str, "reason": str})
async def t_hypothesis_kill(args):
    return _text(
        _call("POST", f"/agent/hypotheses/{args['hypothesis_id']}/kill",
              {"reason": args["reason"]})
    )


@tool("decision_add", "Append to the decision record: what was decided and why.",
      {"subject": str, "body": str})
async def t_decision_add(args):
    return _text(_call("POST", "/agent/decisions", dict(args)))


@tool(
    "approval_request",
    "Put a decision to the chairman as a recommendation you would defend — never an open "
    "question. kind: spend|legal|owner_decision|tool_request. Unanswered approvals expire "
    "to DENIED.",
    {"kind": str, "title": str, "body": str, "amount_usd": float},
)
async def t_approval_request(args):
    payload = {k: v for k, v in args.items() if v is not None}
    return _text(_call("POST", "/agent/approvals", payload))


@tool("approvals_list", "List this project's recent approvals and their status.", {})
async def t_approvals_list(args):
    return _text(_call("GET", "/agent/approvals"))


@tool(
    "spend_book",
    "Book an external dollar spend to the ledger. Spends at/above the approval threshold "
    "need an approved approval_id.",
    {"usd": float, "note": str, "approval_id": str},
)
async def t_spend_book(args):
    payload = {k: v for k, v in args.items() if v not in (None, "")}
    return _text(_call("POST", "/agent/ledger/spend", payload))


@tool(
    "side_effect_begin",
    "REQUIRED before any external side effect (posting, emailing, spending, creating a PR): "
    "record it with a stable idempotency_key. If the response says duplicate/completed, DO "
    "NOT fire the effect again — a prior attempt already did.",
    {"kind": str, "idempotency_key": str, "task_id": str, "payload": dict},
)
async def t_side_effect_begin(args):
    payload = {k: v for k, v in args.items() if v not in (None, "")}
    return _text(_call("POST", "/agent/side_effects", payload))


@tool("side_effect_finish", "Mark a recorded side effect completed/failed with its result.",
      {"effect_id": str, "ok": bool, "result": dict})
async def t_side_effect_finish(args):
    effect_id = args.pop("effect_id")
    return _text(_call("POST", f"/agent/side_effects/{effect_id}/finish", dict(args)))


@tool(
    "artifact_put",
    "Persist a finished deliverable (report, dataset, document) to the platform's "
    "artifact store — the durable home for finished work; your workspace is ephemeral "
    "and vanishes after the run. filename is a plain file name (no paths), "
    "content_base64 is the file's bytes base64-encoded, max 5 MB decoded. Returns the "
    "artifact's uri, key, size and sha256 — cite the uri in evidence, task completions "
    "and messages. The uri is NOT a path in your sandbox: read it back with "
    "artifact_get, never with a file tool.",
    {"filename": str, "content_base64": str},
)
async def t_artifact_put(args):
    return _text(
        _call("POST", "/agent/artifacts",
              {"filename": args["filename"], "content_base64": args["content_base64"]})
    )


@tool(
    "artifact_list",
    "List the deliverables this project has shipped to the artifact store: key, name, "
    "run_id, size, modified. Start here when checking anyone's work, including your "
    "own — the store is what shipped, the workspace is only what someone was working "
    "on, and the two can differ.",
    {},
)
async def t_artifact_list(args):
    return _text(_call("GET", "/agent/artifacts"))


@tool(
    "artifact_get",
    "Read a shipped artifact's bytes back. ref is either a key from artifact_list or "
    "the uri cited in a message, evidence row, or task completion. Returns the whole "
    "file's size and sha256 plus a slice of its content (text when the file is text) — "
    "page a large file with offset, default 64 KB per read. An artifact uri is NOT a "
    "path in your sandbox; opening one with a file tool always fails. This is the only "
    "way to read what was actually shipped. exists:false means the uri was cited but "
    "no bytes are stored — that is a finding worth reporting, not an error on your "
    "part, and it costs you nothing to check.",
    {"ref": str, "offset": int, "limit": int},
)
async def t_artifact_get(args):
    params = {"ref": args.get("ref") or ""}
    for name in ("offset", "limit"):
        if args.get(name) is not None:
            params[name] = args[name]
    return _text(_call("GET", "/agent/artifacts/content", params=params))


@tool(
    "env_list",
    "List the environment variable NAMES available in your sandbox (never values). "
    "Answers 'do I have credential X?' definitively — use it instead of guessing.",
    {},
)
async def t_env_list(args):
    return _text({"env_names": sorted(os.environ)})


ALL_TOOLS = [
    t_board, t_task_create, t_task_claim, t_task_complete, t_task_block, t_task_kill,
    t_suggest, t_channels, t_messages_read, t_message_post, t_directive_file,
    t_memory_search, t_memory_add, t_memory_supersede, t_memory_compact, t_memory_forget,
    t_evidence_add, t_hypothesis_add, t_hypothesis_kill, t_decision_add,
    t_approval_request, t_approvals_list, t_spend_book,
    t_side_effect_begin, t_side_effect_finish,
    t_artifact_put, t_artifact_list, t_artifact_get, t_env_list,
]


def _builtin_tools() -> list[str]:
    """The base set of built-in tool declarations, not just permissions: a tool
    absent here never reaches the model at all. Undeclared schemas (Task,
    TodoWrite, a disabled Bash, ...) are otherwise pure input-token cost on
    every single API call."""
    tools: list[str] = []
    if PERMS.get("files", True):
        tools += ["Read", "Write", "Edit", "Glob", "Grep"]
    if PERMS.get("bash", False):
        tools.append("Bash")
    if PERMS.get("web", False):
        tools += ["WebFetch", "WebSearch"]
    return tools


def _allowed_tools() -> list[str]:
    allowed = [f"mcp__yaaf__{t.name}" for t in ALL_TOOLS]
    allowed += _builtin_tools()
    # Server-level grant: every tool the external MCP server offers.
    allowed += [f"mcp__{name}" for name in _external_mcp_servers()]
    return allowed


def _external_mcp_servers(env: dict[str, str] | None = None) -> dict[str, dict]:
    """The project data sources assigned to this role, merged into
    tool_permissions.mcp_servers at lease time — roles never declare servers
    of their own.

    Each entry: {"name": str, "url_env": str, optional "transport": "http"|"sse",
    optional "bearer_env": str}. The URL (and any bearer token) come from role-
    scoped secret env vars — so which roles can even see a data source is decided
    by the chairman's credential grants, never by prompt text. An entry whose
    env vars are missing is skipped: an ungranted role gets no server, silently,
    the same way it gets no secret. Remember the sandbox's only network route is
    the egress proxy — the server's host must be on this role's allowlist.
    """
    env = env if env is not None else dict(os.environ)
    MOUNT_PROBLEMS.clear()
    servers: dict[str, dict] = {}
    for spec in PERMS.get("mcp_servers", []):
        name, url_env = spec.get("name"), spec.get("url_env", "")
        url = env.get(url_env)
        if not name:
            continue
        # A declared source that fails to mount must fail LOUDLY — a silent
        # absence sends agents (and their CEO) on hostname-guessing hunts.
        if not url:
            MOUNT_PROBLEMS.append(
                f"data source '{name}' NOT mounted: env var {url_env or '?'} is absent "
                "(credential missing or not granted to this role)")
            continue
        if not url.startswith(("http://", "https://")):
            MOUNT_PROBLEMS.append(
                f"data source '{name}' NOT mounted: {url_env} is not a URL "
                "(a full http(s)://host:port address is required)")
            continue
        config: dict = {"type": spec.get("transport", "http"), "url": url}
        token = env.get(spec.get("bearer_env", ""))
        if token:
            config["headers"] = {"Authorization": f"Bearer {token}"}
        servers[name] = config
    return servers


MOUNT_PROBLEMS: list[str] = []


def _usage_int(usage, key: str) -> int:
    if isinstance(usage, dict):
        return int(usage.get(key, 0) or 0)
    return int(getattr(usage, key, 0) or 0)


async def main() -> int:
    # Run-keyed: the workspace is shared by every agent on the project, so a
    # fixed name is another run's file half the time.
    system_prompt = (STAGING / f"system_prompt-{RUN_ID}.txt").read_text()
    context = json.loads((STAGING / f"context-{RUN_ID}.json").read_text())
    trace_path = STAGING / f"trace-{RUN_ID}.jsonl"
    result_path = STAGING / f"result-{RUN_ID}.json"

    server = create_sdk_mcp_server(name="yaaf", version="1.0.0", tools=ALL_TOOLS)
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=MODEL,
        effort=EFFORT,
        mcp_servers={"yaaf": server, **_external_mcp_servers()},
        tools=_builtin_tools(),
        allowed_tools=_allowed_tools(),
        permission_mode="bypassPermissions",  # containment is the sandbox + proxy + API
        cwd="/workspace",
        setting_sources=[],
        max_turns=100,
        **({"max_budget_usd": MAX_USD} if MAX_USD > 0 else {}),
        # A big MCP tool result (a real data pull) overflows the SDK's default
        # 1MB transport buffer and kills the run; seen live on audit data.
        max_buffer_size=16 * 1024 * 1024,
    )
    prompt = (
        "Here is your context for this run (trigger, unread messages, notices, board, and "
        "the side-effect ledger for your task, if any):\n\n"
        f"```json\n{json.dumps(context, indent=2, default=str)}\n```\n\n"
        "Do ONE unit of work — answer what needs answering, work your claimed task, or plan "
        "— using the yaaf tools. Record anything durable in memory. Then stop; the platform "
        "will wake you when there is a reason to."
    )

    summary_parts: list[str] = list(MOUNT_PROBLEMS)
    cost_usd = None
    tokens_in = tokens_out = cache_creation = cache_read = 0
    ok = True

    def write_result(partial: bool) -> None:
        # Written after every message (atomically), not just at the end: a
        # container killed at the timeout still leaves its last known token
        # count behind, so hard kills are metered, not free.
        tmp = result_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "ok": ok and not partial,
                    "partial": partial,
                    "summary": ("\n".join(summary_parts))[-4000:],
                    "cost_usd": cost_usd,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cache_creation_tokens": cache_creation,
                    "cache_read_tokens": cache_read,
                    "trace_path": str(trace_path),
                }
            )
        )
        os.replace(tmp, result_path)

    with trace_path.open("a") as trace:
        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            summary_parts.append(block.text)
                            trace.write(json.dumps({"type": "text", "text": block.text}) + "\n")
                        else:
                            trace.write(json.dumps({"type": type(block).__name__,
                                                    "repr": repr(block)[:2000]}) + "\n")
                elif isinstance(message, ResultMessage):
                    cost_usd = message.total_cost_usd
                    if message.usage is not None:
                        usage = message.usage
                        # input_tokens is only the uncached remainder; the cache
                        # fields carry the rest of the real prompt.
                        tokens_in = _usage_int(usage, "input_tokens")
                        tokens_out = _usage_int(usage, "output_tokens")
                        cache_creation = _usage_int(usage, "cache_creation_input_tokens")
                        cache_read = _usage_int(usage, "cache_read_input_tokens")
                    trace.write(json.dumps({"type": "result", "subtype": message.subtype,
                                            "cost_usd": cost_usd}) + "\n")
                else:
                    trace.write(json.dumps({"type": type(message).__name__,
                                            "repr": repr(message)[:2000]}) + "\n")
                trace.flush()
                write_result(partial=True)
        except Exception as exc:
            ok = False
            summary_parts.append(f"[sandbox_entry error: {exc!r}]")
            trace.write(json.dumps({"type": "error", "error": repr(exc)}) + "\n")

    write_result(partial=False)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
