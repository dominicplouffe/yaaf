"""Runs INSIDE the sandbox container. Do not import from the yaaf package here —
the container only has this file, the Claude Agent SDK (and its own dependency
`mcp`, which data_fetch uses as a client), and httpx.

It declares the yaaf platform tools (board, tasks, messages, memory, evidence,
approvals, side effects) — each one a name, a description, a schema and a
handler that calls the agent HTTP API with the per-run token — then drives one
stateless run on the Agent SDK: context in, one unit of work, result.json out.

The tools are declared as plain data, not as SDK objects, because the SDK is
one harness among several that will need them (docs/RUNTIMES.md). The SDK sees
them through `render_sdk` and nowhere else.

Refusals come back to the model as tool output — the refusal text *is* the
interface. The platform separately counts them and revokes the token at the cap.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import tempfile
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    SdkMcpTool,
    SystemMessage,
    TextBlock,
    create_sdk_mcp_server,
    query,
)

API = os.environ["YAAF_API_BASE_URL"]
TOKEN = os.environ["YAAF_API_TOKEN"]
RUN_ID = os.environ["YAAF_RUN_ID"]
MODEL = os.environ.get("YAAF_MODEL", "claude-sonnet-5-5")
EFFORT = os.environ.get("YAAF_EFFORT", "high")
# A run that spends this much has stopped working and started looping. The SDK
# tells the model as it approaches and stops it at the line, so the run ends
# having said something rather than being cut off silently. 0 = unbounded.
MAX_USD = float(os.environ.get("YAAF_MAX_USD", "0") or 0)
# Constitution run_token_budget: handed to the model as an API-side task
# budget so it paces itself and wraps up before the line. 0 = off.
TASK_BUDGET = int(os.environ.get("YAAF_TASK_BUDGET", "0") or 0)
PERMS = json.loads(os.environ.get("YAAF_TOOL_PERMISSIONS", "{}"))

WORKSPACE = Path("/workspace")
STAGING = Path("/workspace/.yaaf")

# ── file-shaped credentials ────────────────────────────────────────────────────
#
# A service-account JSON, an SSH key, a kubeconfig is only usable as a FILE, and
# the platform will not hand an agent a private key and ask it to save the key
# itself: the one writable place an agent thinks of is /workspace, /workspace is
# a git clone it commits from, and that is how a key gets pushed to a remote.
#
# So the runner sends the bytes under a carrier variable and this writes them,
# here, before the model exists: mode 0600 (ssh refuses a key anyone else can
# read), outside /workspace so no `git add -A` can reach it, and on a tmpfs that
# dies with the container. The carrier is then deleted, which is what makes the
# credential's own variable a path and nothing else — os.environ, `env`, and
# every subprocess the agent spawns inherit the rewritten environment.
#
# What that is NOT: a wall between the agent and its own credential. unsetenv
# does not rewrite the block the kernel exposes at /proc/self/environ, so the
# original bytes stay readable there for the life of the container — and the
# agent could read the 0600 file anyway, since it owns it. That is the design,
# not a hole in it: the role was granted this credential. What the deletion buys
# is that the credential's variable means one thing (a path), everywhere, from
# the first instruction — so nothing downstream has to guess, and nothing that
# reads $GOOGLE_SA expecting a path gets bytes.
#
# Mirrors yaaf/core/naming.py FILE_CRED_PREFIX. This file is copied into the
# image alone and cannot import the yaaf package; a test asserts the two agree.
FILE_CRED_PREFIX = "YAAF_FILE_CRED__"
CREDENTIAL_DIR = Path("/run/yaaf/credentials")

# A credential that could not be written is a capability the role does not have,
# and the agent must be told in the place it will look — its own run summary —
# rather than left to discover a path that isn't there. Kept SEPARATE from
# MOUNT_PROBLEMS because `_data_source_specs` clears that list, and it runs
# after this.
CREDENTIAL_PROBLEMS: list[str] = []


def _credential_dir() -> Path:
    """Where a materialized credential lives. `/run/yaaf` is a tmpfs the runner
    mounts (see sandbox.CREDENTIALS_TMPFS), so the bytes never reach a disk and
    the whole directory dies with the container. Falls back to a private temp
    dir when there is no such mount — a dev box without Docker. Either way it is
    outside /workspace, which is the point: the workspace is a git clone."""
    try:
        CREDENTIAL_DIR.mkdir(parents=True, exist_ok=True)
        CREDENTIAL_DIR.chmod(0o700)
        return CREDENTIAL_DIR
    except OSError:
        return Path(tempfile.mkdtemp(prefix="yaaf-cred-"))


def materialize_file_credentials() -> dict[str, str]:
    """Write each file credential to disk, repoint its variable at the path and
    drop the carrier. Returns {env name: path} — never the contents.

    Runs at import, before the SDK is built, so every subprocess the agent
    spawns inherits an environment in which the credential's variable holds a
    path and the bytes are simply not present. Safe to call twice; the second
    call finds no carriers."""
    carriers = [key for key in os.environ if key.startswith(FILE_CRED_PREFIX)]
    if not carriers:
        return {}
    directory = _credential_dir()
    written: dict[str, str] = {}
    for carrier in sorted(carriers):
        name = carrier[len(FILE_CRED_PREFIX):]
        raw = os.environ.pop(carrier)
        try:
            spec = json.loads(raw)
            body = base64.b64decode(spec["b64"], validate=True)
            # The name was validated at the door, but it is about to become a
            # path and the door is a different process on a different machine:
            # take the basename again so a hand-edited row cannot aim a private
            # key at /workspace.
            filename = os.path.basename(str(spec["filename"]).strip())
        except Exception:
            CREDENTIAL_PROBLEMS.append(
                f"credential {name} NOT available: its file could not be read from "
                "the run's environment")
            continue
        if not filename or filename in (".", ".."):
            CREDENTIAL_PROBLEMS.append(f"credential {name} NOT available: unusable file name")
            continue
        # One directory per credential, named for its variable. Two credentials
        # may legitimately carry the same file name — a chairman with a
        # production and a staging service account calls both key.json — and
        # writing them into one directory made the second silently overwrite the
        # first, so $GOOGLE_SA served the staging key. The variable name is
        # unique by construction (the runner refuses a second credential on a
        # name it already claimed), so this cannot collide.
        holder = directory / name.lower()
        try:
            holder.mkdir(parents=True, exist_ok=True)
            holder.chmod(0o700)
        except OSError as exc:
            CREDENTIAL_PROBLEMS.append(
                f"credential {name} NOT available: {type(exc).__name__} making its directory")
            continue
        path = holder / filename
        try:
            # 0600 before the bytes, never after: ssh refuses a key that was
            # ever readable by anyone else, and so should we.
            handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(handle, "wb") as out:
                out.write(body)
        except OSError as exc:
            CREDENTIAL_PROBLEMS.append(
                f"credential {name} NOT available: {type(exc).__name__} writing its file")
            continue
        os.environ[name] = str(path)
        written[name] = str(path)
    return written


try:
    FILE_CREDENTIALS = materialize_file_credentials()
except Exception as _exc:  # pragma: no cover — the entrypoint must still start
    # Nothing here is worth taking the run down for. A run that dies at import
    # produces no result.json and no summary, so the chairman sees a container
    # that exited and no reason; a run that starts without a credential says so
    # in its summary and does the rest of its work.
    FILE_CREDENTIALS = {}
    CREDENTIAL_PROBLEMS.append(
        f"file credentials NOT available: {type(_exc).__name__} preparing them")

_client = httpx.Client(
    base_url=API,
    timeout=30,
    headers={"Authorization": f"Bearer {TOKEN}"},
    # The platform API is reached directly, ignoring any ambient proxy env.
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


_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean",
               dict: "object", list: "array"}


def _schema(fields: dict, *, optional: tuple[str, ...]) -> dict:
    """A tool schema whose `optional` fields the model may leave out.

    The SDK turns a bare `{name: type}` dict into a schema that REQUIRES every
    field, and a string field cannot carry null. So a tool that needs a field
    left blank forces the model to invent a placeholder — and it sends "null"
    or a literal `""`, which the API then resolves as an id. task_create could
    not open a team task that way for a whole night of a live project.
    """
    unknown = set(optional) - set(fields)
    assert not unknown, f"optional names fields the tool lacks: {sorted(unknown)}"
    properties = {}
    for name, py_type in fields.items():
        if py_type == list[str]:
            properties[name] = {"type": "array", "items": {"type": "string"}}
        else:
            properties[name] = {"type": _JSON_TYPES[py_type]}
    return {"type": "object", "properties": properties,
            "required": [name for name in fields if name not in optional]}


# ── the tool catalog, as data ───────────────────────────────────────────────────
#
# A tool is declared once, here, and rendered for whichever harness runs the
# model. The attribute names are the SDK's own, so `render_sdk` is a copy and
# every test that reads `.name`, `.input_schema` or `.handler` reads it
# unchanged. Nothing about authority lives in this shape: the server's 403s are
# the only thing that decides who may do what, on every harness.


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Awaitable[dict]]


def tool(name: str, description: str, input_schema: dict):
    """The SDK decorator's signature, without the SDK."""

    def decorator(handler: Callable[[dict], Awaitable[dict]]) -> Tool:
        return Tool(name=name, description=description, input_schema=input_schema,
                    handler=handler)

    return decorator


def _wire_type(py_type) -> dict:
    if py_type in _JSON_TYPES:
        return {"type": _JSON_TYPES[py_type]}
    if getattr(py_type, "__origin__", None) is list:
        args = getattr(py_type, "__args__", None)
        return {"type": "array", "items": _wire_type(args[0])} if args else {"type": "array"}
    # The SDK's own fallback for a type it does not recognise.
    return {"type": "string"}


def wire_schema(t: Tool) -> dict:
    """The JSON schema the model receives for `t`, by the SDK's rule: a dict
    that is already a schema passes through; a bare `{name: type}` dict becomes
    one that requires every name. Owned here rather than borrowed from the SDK
    — whose helper is private — because every other harness renders from it;
    tests/fixtures/tool_catalog.json holds it to what the SDK sent."""
    schema = t.input_schema
    if isinstance(schema.get("type"), str) and "properties" in schema:
        return schema
    properties = {name: _wire_type(py_type) for name, py_type in schema.items()}
    return {"type": "object", "properties": properties, "required": list(properties)}


def render_sdk(tools: list[Tool]) -> list:
    """The catalog as the Agent SDK's in-process MCP tools — today's behaviour,
    handed the very schema objects it was always handed."""
    return [SdkMcpTool(name=t.name, description=t.description,
                       input_schema=t.input_schema, handler=t.handler) for t in tools]


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
    "assignee_agent_id IS ONLY FOR SUBTASKS. A team task names a TEAM, never a person: "
    "the team's lead claims it and decides who does what. Setting assignee_agent_id "
    "without parent_task_id is refused every time — it is the single most common "
    "refusal on this platform, so check yourself before you send it. "
    "A team task OMITS parent_task_id, assignee_agent_id and hypothesis_id entirely — "
    "never send 'null' or '\"\"' as a placeholder, it is read as an id and refused. "
    "not_before is an ISO 8601 datetime before which the task cannot be claimed. It "
    "wakes nobody and shows on no screen as work in progress, so a project gated on one "
    "looks stopped: use it only for a genuine calendar rhythm, never to express 'after "
    "the other task finishes'. For that, create this task WHEN that one lands — you are "
    "woken for it. Gates beyond the constitution's limit are refused. "
    "requirement_id names what this work is FOR and is required outside Explore "
    "projects: a task that serves no requirement is refused, because work that makes "
    "nothing true is how a project stays busy without finishing. Your context pack's "
    "`board` and `roster` carry the team ids, agent ids and requirement ids you need — "
    "read them rather than guessing at an id.",
    _schema({
        "team_id": str,
        "title": str,
        "goal": str,
        "success_test": dict,
        "produces_external_evidence": bool,
        "parent_task_id": str,
        "assignee_agent_id": str,
        "hypothesis_id": str,
        "requirement_id": str,
        "not_before": str,
    }, optional=("produces_external_evidence", "parent_task_id", "assignee_agent_id",
                 "hypothesis_id", "requirement_id", "not_before")),
)
async def t_task_create(args):
    payload = {k: v for k, v in args.items() if v not in (None, "")}
    return _text(_call("POST", "/agent/tasks", payload))


@tool(
    "requirement_propose",
    "CEO only. Propose something that must be TRUE at the end — not a task, an outcome. "
    "acceptance_test has the same shape as a success test: "
    '{"type":"command","command":...} the platform runs itself, '
    '{"type":"observable","target":...} the chairman confirms, or '
    '{"type":"metric","source":{"kind":"http_json","url":...,"path":"$.a.b[-1].c"},'
    '"direction":"decreasing","target":500000,"measure_every_hours":24} '
    "the platform MEASURES on a schedule. "
    "If this requirement is the objective itself — the rank, the revenue, the number "
    "the project exists to move — it is a metric, and nothing else will do: a command "
    "greps a file you wrote, so a finish line you can write into your own status "
    "report is not a finish line. Use a command for work that must be verifiably "
    "done, and an observable only when neither fits — it puts the chairman in the "
    "loop every time it closes, and a metric never does, because the platform reads "
    "the number itself. "
    "This is a PROPOSAL and creates no scope — the chairman accepts it, and until he "
    "does no work may be created for it. You cannot accept your own, and you cannot "
    "mark one met: the platform runs the test and writes the result. "
    "stage is the order this happens in (1, 2, 3…); est_runs is your honest guess at "
    "how many runs it will take, and it is compared against what it actually costs. "
    "depends_on lists requirement ids that must be settled first. "
    "recurrence is 'terminal' (met once) or 'standing' (owed again every cycle; "
    "Operate only). ALWAYS fill meta — it is what the project's OWNER sees: "
    "{title: a noun they want ('Wasted-spend list'), description: one line of what "
    "they get in spreadsheet terms, cadence: daily|weekly|null, due: an ISO date or "
    "null, lands: report|memo|note|table}. The statement is for the platform; meta "
    "is for the person who hired you.",
    _schema({
        "statement": str,
        "acceptance_test": dict,
        "stage": int,
        "est_runs": int,
        "depends_on": list,
        "recurrence": str,
        "meta": dict,
    }, optional=("stage", "est_runs", "depends_on", "recurrence")),
)
async def t_requirement_propose(args):
    payload = {k: v for k, v in args.items() if v not in (None, "", [])}
    return _text(_call("POST", "/agent/requirements", payload))


@tool(
    "requirement_depends_on",
    "CEO only. Record that one requirement cannot be worked until another is settled. "
    "This is the sequencing the chairman looks at, and it is enforced: work is refused "
    "for a requirement whose dependencies are still open. An edge that would close a "
    "loop is refused.",
    {"requirement_id": str, "depends_on_requirement_id": str},
)
async def t_requirement_depends_on(args):
    return _text(_call("POST", "/agent/requirement_edges", dict(args)))


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


@tool(
    "task_review",
    "Sign off, or send back, work somebody else finished and left waiting for a "
    "read. `ok` true closes it; false returns it to whoever wrote it with your "
    "notes. Say what is ABSENT and which standard it fails — returning work is "
    "ordinary here, not an escalation, and 'looks fine' teaches the writer "
    "nothing. You cannot review your own work.",
    {"task_id": str, "ok": bool, "notes": str},
)
async def t_task_review(args):
    return _text(
        _call("POST", f"/agent/tasks/{args['task_id']}/review",
              {"ok": bool(args["ok"]), "notes": args["notes"]})
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
      _schema({"channel_id": str, "limit": int}, optional=("limit",)))
async def t_messages_read(args):
    return _text(_call("GET", "/agent/messages",
                       params={"channel_id": args["channel_id"],
                               "limit": args.get("limit") or 50}))


@tool(
    "message_post",
    "Post to one of your channels. THREE SENTENCES. Lead with the answer or the ask, "
    "add the number that carries it, stop — no narration of what you did or decided, "
    "which the board and your memory already hold. Anything that genuinely needs more "
    "room goes in a file or a task, and you post the line that points at it: this body "
    "is re-read by every recipient on every wake, a file is read once by whoever needs "
    "it. Boardroom: CEO<->chairman. Leadership: CEO+leads. "
    "Team room: your team. Use thread_id to reply in a thread. An unprompted boardroom "
    "status is refused between the chairman's cadence slots (see the `chairman` block in "
    "your context); set override to 'blocker', 'budget' or 'approval' only when it truly "
    "cannot wait — overrides are recorded and he sees them. Asking him something is NOT "
    "a status and is not an override: that is ask_chairman, which this gate has never "
    "held.",
    _schema({"channel_id": str, "body": str, "thread_id": str, "override": str},
            optional=("thread_id", "override")),
)
async def t_message_post(args):
    payload = {"channel_id": args["channel_id"], "body": args["body"]}
    if args.get("thread_id"):
        payload["thread_id"] = args["thread_id"]
    if args.get("override"):
        payload["override"] = args["override"]
    return _text(_call("POST", "/agent/messages", payload))


@tool(
    "ask_chairman",
    "Put a question to the chairman that your next move depends on. The reporting "
    "cadence does not hold this — a question is not a status. It does NOT block: keep "
    "working while you wait. answer_by_hours is when you stop waiting; default_action is "
    "exactly what you will do if he has not answered by then, and you are held to it, so "
    "write the real thing rather than 'decide later'. If the window passes you are woken "
    "to do it and to tell him you did. Use this instead of sitting on something he needs "
    "to see — a question nobody knows is waiting is how a project spends a day idle.",
    {"title": str, "body": str, "answer_by_hours": float, "default_action": str},
)
async def t_ask_chairman(args):
    return _text(_call("POST", "/agent/asks", {
        "title": args["title"], "body": args["body"],
        "answer_by_hours": args["answer_by_hours"],
        "default_action": args["default_action"],
    }))


@tool(
    "ask_close",
    "Close one of your questions by having acted on it and said so. message_id is the "
    "boardroom message where you told him what you did — with his answer, or with the "
    "default you named when its window passed. An ask does not close by going quiet.",
    {"ask_id": str, "message_id": str},
)
async def t_ask_close(args):
    return _text(_call("POST", "/agent/asks/close", {
        "ask_id": args["ask_id"], "message_id": args["message_id"],
    }))


@tool(
    "directive_file",
    "CEO: turn something the chairman said in the boardroom into something the platform "
    "does, citing the message he wrote it in. Two shapes, pass exactly one. "
    "`changes` files reporting policy the moment he changes how or how often he wants to "
    "be reported to — a rule kept in memory does not survive into the next run. Settable: "
    "chairman_report_interval_hours (0 disables), chairman_report_window_local "
    "('09:00-20:00'), timezone (IANA name). "
    "`action` EXECUTES a plan order he gave — 'cut R-07', 'reopen R-04', 'move R-02 to "
    "stage 1', 'accept R-09', 'that estimate should be 12 runs', 'reword R-03 to …', "
    "'R-05 should not wait on R-02' — as "
    '{"kind": "accept|cut|reopen|restage|reestimate|reword|unsequence", '
    '"requirement_key": "R-07", and for restage "stage", for reestimate "est_runs", '
    'for reword "statement", for unsequence "depends_on_key"}. '
    "It runs on HIS cited authority through the same machinery as his own buttons, and is "
    "echoed back to him threaded to his message — use it the moment he gives an order "
    "instead of asking him to click it himself.",
    _schema({"from_message_id": str, "changes": dict, "action": dict, "reason": str},
            optional=("changes", "action")),
)
async def t_directive_file(args):
    payload = {"from_message_id": args["from_message_id"], "reason": args["reason"]}
    if args.get("changes"):
        payload["changes"] = args["changes"]
    if args.get("action"):
        payload["action"] = args["action"]
    return _text(_call("POST", "/agent/directives", payload))


@tool(
    "scope_act",
    "CEO only, and only where the constitution's authority dial grants it (your "
    "constitution prose says which verbs you hold; everything you don't hold is a 403 "
    "with the legitimate path named). Exercise a scope verb as YOUR OWN authority: "
    '{"kind": "accept|cut|reopen|restage|reestimate|reword|unsequence|verdict", '
    '"requirement_key": "R-07", plus for restage "stage", for reestimate "est_runs", '
    'for reword "statement", for unsequence "depends_on_key", for verdict "ok" (bool)}. '
    "verdict is for an UNVERIFIABLE observable in checking — judge it honestly and keep "
    "going; every self-judgment is disclosed to the chairman in full. Every use is "
    "audited and sent to the chairman; all of it is reversible. Spend this authority "
    "the way you would want it audited. This is not the tool for executing a chairman "
    "boardroom order — that is directive_file, citing his message, and works in every "
    "mode.",
    {"action": dict, "reason": str},
)
async def t_scope_act(args):
    return _text(_call("POST", "/agent/scope",
                       {"action": args["action"], "reason": args["reason"]}))


@tool(
    "commitment_propose",
    "CEO only. Ask to commit to a delivery date. You do not and cannot write the date: "
    "the platform stamps its own projection from pace and scope, and the chairman "
    "accepts it (with that date or his own) or declines. Nothing binds until he does. "
    "Optional note travels with the proposal.",
    _schema({"note": str}, optional=("note",)),
)
async def t_commitment_propose(args):
    payload = {}
    if args.get("note"):
        payload["note"] = args["note"]
    return _text(_call("POST", "/agent/commitments", payload))


@tool(
    "commitment_reset",
    "CEO only, and only where the constitution grants you commitment_authority. Reset a "
    "promise that has stopped holding to the platform's CURRENT projection. You still "
    "cannot write a date — the only date you can reset to is the arithmetic's, so this "
    "buys no slack the pace has not already spent. Refused while the promise still "
    "holds. Every reset is audited, disclosed to the chairman with a running count of "
    "how many times you have done it, and reversible by him. Reach for it when the "
    "honest answer is that the date cannot hold; reach for cutting scope first when the "
    "honest answer is that the scope is too big.",
    {"reason": str},
)
async def t_commitment_reset(args):
    return _text(_call("POST", "/agent/commitments/reset", {"reason": args["reason"]}))


@tool("memory_search", "Search durable memory. Query it before repeating work. "
      "include_superseded=true also returns entries folded away by compaction.",
      _schema({"q": str, "scope": str, "include_superseded": bool},
              optional=("q", "scope", "include_superseded")))
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
    "Not a diary, and not a write-up: two or three sentences, because every memory you "
    "add is re-read by everyone who searches this scope for as long as it stands. If it "
    "needs more room than that it is a document — put it in a file and record the fact "
    "that the file exists. scope: 'project' (CEO only) or 'team'.",
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
    _schema({"kind": str, "description": str, "source_url": str, "task_id": str},
            optional=("source_url", "task_id")),
)
async def t_evidence_add(args):
    payload = {k: v for k, v in args.items() if v not in (None, "")}
    return _text(_call("POST", "/agent/evidence", payload))


@tool(
    "review_narrative",
    "CEO only, when a review is open (see the `review` block in your context). File your "
    "variance narrative for the standing status meeting: what moved against the plan since "
    "the last review, the SINGLE largest cause named (a requirement key, a dead bet, a "
    "failure storm — something with a handle), and in `recommended`, what you would cut or "
    "move. The platform's numbers are already on the review — explain them, do not restate "
    "them. The chairman's decisions come back to you as notices with his reasons.",
    _schema({"narrative": str, "recommended": str}, optional=("recommended",)),
)
async def t_review_narrative(args):
    payload = {"narrative": args.get("narrative", "")}
    if args.get("recommended"):
        payload["recommended"] = args["recommended"]
    return _text(_call("POST", "/agent/review_narrative", payload))


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


@tool(
    "flag_raise",
    "Close a flag by having TOLD the chairman, with what you propose doing about it. "
    "Two steps, in this order: message_post the news in the boardroom, then flag_raise "
    "with that message_id — the message is the proof he was told, which is why saying "
    "it in the boardroom does not close the flag on its own and why nothing else can. "
    "flag_id comes from the flags in your context pack. The proposal is required: a "
    "flag without one is anxiety forwarded to his desk, and what you add over a "
    "monitoring alert is the sentence AFTER the bad news — what broke, the arithmetic, "
    "the ways out, the one you would pick, and what you need from him. Closing it "
    "dismissively is legitimate and expected: 'the cycle boundary caused it, no action "
    "needed' is this verb working exactly as intended. What is not available is leaving "
    "it open while telling him everything is fine.",
    {"flag_id": str, "message_id": str, "proposal": str},
)
async def t_flag_raise(args):
    return _text(_call("POST", "/agent/flags/raise", dict(args)))


@tool(
    "roles_list",
    "CEO only. The role library you may hire from — slug, name, category and what the "
    "role is for, plus which roles are already on your roster and how many teams you "
    "may run. Read it before hiring: the seat you want may already be here under a "
    "different team name.",
    {},
)
async def t_roles_list(args):
    return _text(_call("GET", "/agent/roles"))


@tool(
    "hire",
    "CEO only. Put a role on this project — yours to decide, no approval needed. "
    "`role_slug` comes from roles_list, `employees` are the names these people will "
    "work under, `reason` says what work this seat is for. Optionally set "
    "stand_down_when now, in the same shape stand_down_when takes. "
    "Hiring does not buy you runs: the daily ration and the monthly wall belong to the "
    "PROJECT, so another seat spreads the same allowance thinner and adds wake-ups of "
    "its own. Hire because work needs doing that nobody here can do, never because a "
    "seat would be nice to have.",
    _schema({"role_slug": str, "name": str, "employees": list, "lead": str, "reason": str,
             "stand_down_when": dict}, optional=("stand_down_when",)),
)
async def t_hire(args):
    payload = {k: v for k, v in args.items() if v not in (None, "", [], {})}
    return _text(_call("POST", "/agent/teams", payload))


@tool(
    "stand_down",
    "CEO only. Stop a role whose job is finished — yours to decide, no approval "
    "needed. Its seats stop waking, stop reading, and stop costing; the team's "
    "channel, memory and history all stay. THIS IS NOT A PAUSE and it is not a "
    "punishment: it is the normal end of a role that was needed for the front half of "
    "a project. An idle seat is not free — it wakes to read its channels and it counts "
    "as a mover in your capacity forecast. Refused if the team is holding open work "
    "(finish it, kill it or move it first) and refused for your last challenge seat, "
    "which is not yours to remove.",
    {"team_id": str, "reason": str},
)
async def t_stand_down(args):
    return _text(_call("POST", f"/agent/teams/{args['team_id']}/stand_down",
                       {"reason": args["reason"]}))


@tool(
    "recall",
    "CEO only. Bring a stood-down role back, with its channel, memory and history "
    "intact — cheaper and better than hiring a replacement. A seat the CHAIRMAN paused "
    "is not yours to recall.",
    {"team_id": str, "reason": str},
)
async def t_recall(args):
    return _text(_call("POST", f"/agent/teams/{args['team_id']}/recall",
                       {"reason": args["reason"]}))


@tool(
    "stand_down_when",
    "CEO only. Say NOW when a team's job will end, and the platform will stand it down "
    "itself when that comes true. Two shapes: "
    "{\"kind\": \"requirement_met\", \"keys\": [\"R-03\"]} stops the seat once "
    "those requirements are all settled, and "
    "{\"kind\": \"stage_cleared\", \"stage\": 2} stops it once every requirement "
    "at or below that stage has settled. "
    "SET THIS FOR EVERY TEAM WHILE YOU ARE WRITING THE PLAN. Writing the plan is the "
    "one moment you know which roles are for the front half of this project — the "
    "requirements gathering, the build, the validation — and it is the moment nobody "
    "has ever asked you. Afterwards nothing raises the question and an idle seat costs "
    "quietly. Pass when: null to clear one.",
    _schema({"team_id": str, "when": dict, "reason": str}, optional=("when",)),
)
async def t_stand_down_when(args):
    return _text(_call("POST", f"/agent/teams/{args['team_id']}/stand_down_when",
                       {"when": args.get("when"), "reason": args["reason"]}))


@tool(
    "roster_activity",
    "Who spent the runs, and on what. `since` takes 'today' (the default), "
    "'yesterday', '7d', '12h', a date like 2026-09-12, or an ISO timestamp; "
    "'today' means today in the project's timezone, not a rolling 24 hours. "
    "Per seat you get runs split by kind and by outcome, what woke each one, "
    "tokens, money, tasks claimed and completed, and what they are holding now. "
    "READ THIS BEFORE YOU ANSWER ANY QUESTION ABOUT WHO SPENT WHAT. Do not "
    "estimate from what you remember doing: the last CEO asked 'who did today's "
    "15 runs' guessed six of them were its own and had spent twelve, while "
    "asking for a bigger ration on the strength of the guess. "
    "`died_of` is why the dead runs died — the cause in words, the tokens it "
    "cost, and `produced_work_first`, the number of them that had already "
    "claimed a task or filed work before they were cut. NEVER read a dead run "
    "as an idle one without checking that number: a CEO reported eleven runs "
    "that 'died without finishing anything' and moved to stand the seat down; "
    "seven of the eleven had filed work, every task that seat touched "
    "completed on a retry, and the real fault was a ceiling cutting it off "
    "mid-task. Fix the cause before you blame the seat.",
    _schema({"since": str, "until": str}, optional=("since", "until")),
)
async def t_roster_activity(args):
    params = {k: v for k, v in args.items() if v}
    return _text(_call("GET", "/agent/activity", params=params))


@tool("flags_list", "The flags open on this project and whether each has been raised.", {})
async def t_flags_list(args):
    return _text(_call("GET", "/agent/flags"))


@tool("decision_add", "Append to the decision record: what was decided and why.",
      {"subject": str, "body": str})
async def t_decision_add(args):
    return _text(_call("POST", "/agent/decisions", dict(args)))


@tool(
    "approval_request",
    "Put a decision to the chairman as a recommendation you would defend — never an open "
    "question. kind: spend|legal|owner_decision|tool_request. Unanswered approvals expire "
    "to DENIED. An approval BLOCKS on a human, so it is the wrong tool for anything the "
    "constitution already put in your hands: where the authority dial grants you the "
    "plan, the scope and the date, an owner_decision is refused and names the verb to "
    "use instead. To want his opinion without blocking, use ask_chairman and keep going.",
    _schema({"kind": str, "title": str, "body": str, "amount_usd": float},
            optional=("amount_usd",)),
)
async def t_approval_request(args):
    payload = {k: v for k, v in args.items() if v is not None}
    return _text(_call("POST", "/agent/approvals", payload))


@tool("approvals_list", "List this project's recent approvals and their status.", {})
async def t_approvals_list(args):
    return _text(_call("GET", "/agent/approvals"))


@tool(
    "spend_book",
    "Book a real-world dollar spend you made with your own tooling. Every spend faces "
    "the same envelope as purchase_begin: the monthly external cap is a hard wall, and "
    "amounts over the per-purchase line or the approval threshold need an approved "
    "approval_id. For a purchase you are ABOUT to make, prefer purchase_begin/"
    "purchase_finish — it checks the envelope before money moves and books itself. "
    "Every real purchase gets booked, one way or the other: an unbooked spend is what "
    "the chairman's spot-audit exists to catch.",
    _schema({"usd": float, "note": str, "approval_id": str}, optional=("approval_id",)),
)
async def t_spend_book(args):
    payload = {k: v for k, v in args.items() if v not in (None, "")}
    return _text(_call("POST", "/agent/ledger/spend", payload))


@tool(
    "data_write_request",
    "Ask the chairman to approve ONE call your data source's allowlist refuses "
    "— a change to his live account, not a read. Pass the arguments you intend "
    "to send, exactly: he decides on those, the platform records their "
    "fingerprint, and the approval is spendable on nothing else. Approved, it "
    "buys exactly one call — a second change needs a second approval, so batch "
    "with the tool's own bulk arguments rather than filing twenty requests. "
    "Say WHY in terms of what it does to his numbers, not what it does to your "
    "task. Unanswered it expires to no; plan the work that does not need it. "
    "ALWAYS fill `disclosure` — it is what he decides on: `change_set` as "
    "[{entity, field, before, after}] for every thing that changes; "
    "`scope_counts` like {campaigns: 3, keywords: 14}; `evidence` as {columns, "
    "rows (the ones that matter, 200 at most), period_start, period_end, "
    "currency, as_of}; `estimate` as {label, value, basis} labelled as an "
    "estimate; `reversible` true/false; `counter_case` — the honest reason he "
    "might say no; `spend_affected_usd_month`; and `category` (one of: bids, "
    "budgets, targeting, negative_keywords, campaign_state, placements, other). "
    "A request without a disclosure is a paragraph with buttons.",
    {"server": str, "tool": str, "arguments": dict, "why": str, "disclosure": dict},
)
async def t_data_write_request(args):
    payload = {"source": (args.get("server") or "").strip(),
               "tool": (args.get("tool") or "").strip(),
               "arguments": args.get("arguments") or {},
               "why": (args.get("why") or "").strip()}
    if isinstance(args.get("disclosure"), dict) and args["disclosure"]:
        payload["disclosure"] = args["disclosure"]
    return _text(_call("POST", "/agent/data_writes", payload))


@tool(
    "data_write_outcome",
    "After a data_fetch spent an approval, file what the call DID — on the run that "
    "made it, or the next one once the effect can be read. ok: whether the call "
    "succeeded; observed: {label, value, basis, as_of} — the effect in the owner's "
    "numbers ('ad spend on the 14 terms', '$0 in the 7 days since', the window it "
    "covers); note: anything else he should know. Without this the owner sees "
    "'approved · applied' and never learns whether it worked.",
    _schema({"approval_id": str, "ok": bool, "observed": dict, "note": str},
            optional=("observed", "note")),
)
async def t_data_write_outcome(args):
    payload = {"ok": bool(args.get("ok", True)), "note": (args.get("note") or "").strip()}
    if isinstance(args.get("observed"), dict) and args["observed"]:
        payload["observed"] = args["observed"]
    approval_id = (args.get("approval_id") or "").strip()
    return _text(_call("POST", f"/agent/data_writes/{approval_id}/outcome", payload))


@tool(
    "assist_request",
    "Any agent. A step only a HUMAN can do is blocking you — a captcha, a 2FA code, a "
    "KYC form, a signature, an account only the chairman can touch. Never try to defeat "
    "a security challenge; hand it over. Before calling: save your session state to the "
    "workspace (playwright storage_state / cookies, the URL you were on, what remains "
    "to do) — the workspace persists across runs, so the next run resumes where you "
    "stopped. Say exactly WHAT you need ('the 6-digit code SMS'd to your phone', "
    "'solve the captcha at this URL and tell me done') and WHERE. His answer arrives "
    "verbatim in the decision that wakes you; unanswered it expires to no, so also "
    "plan the work that does not depend on it.",
    {"what": str, "where": str},
)
async def t_assist_request(args):
    payload = {k: v for k, v in args.items() if v not in (None, "")}
    return _text(_call("POST", "/agent/assists", payload))


@tool(
    "account_record",
    "REQUIRED whenever you create an account/identity anywhere in the world — a signup, "
    "a registration, a service login. Record: service, username, owner ('project' when "
    "it hangs off an identity the project owns like its own email/domain; 'chairman' "
    "when a responsible human is the name behind it — when unsure, ask in the "
    "boardroom), the NAME of the stored credential (never a value — store values as "
    "secrets via a need, or note where they live), url, and a note with what matters "
    "at wind-down (plan tier, renewal date, how to cancel). An account nobody wrote "
    "down is an orphan the owner discovers on a bill.",
    _schema({"service": str, "username": str, "owner": str, "credential_name": str,
             "url": str, "requirement_id": str, "note": str},
            optional=("credential_name", "url", "requirement_id", "note")),
)
async def t_account_record(args):
    payload = {k: v for k, v in args.items() if v not in (None, "")}
    return _text(_call("POST", "/agent/accounts", payload))


@tool(
    "accounts_list",
    "The account registry: every identity the project has created in the world. Read "
    "it before signing up anywhere (it may already exist) and when winding anything "
    "down.",
    {},
)
async def t_accounts_list(args):
    return _text(_call("GET", "/agent/accounts"))


@tool(
    "capacity_request",
    "CEO only. Put a case to the chairman for MORE of a capacity dial — runs a day, "
    "runs an hour, concurrency, queue depth, the month's money. This is the verb for "
    "the constraint that is actually binding: if you have worked out that the run "
    "ration is what paces you, say so here with the arithmetic rather than describing "
    "it in the boardroom, where it is a remark nobody can act on. "
    "rule is the constitution field; `to` is the number you want. All three of buys / "
    "costs / without are required and are the point — what more room BUYS in outcomes, "
    "what it COSTS in money and runs, and what happens WITHOUT it. A request without "
    "them is a wish. "
    "It does not block and it does not move the dial: keep working at the cap you have, "
    "plan for it, and treat a yes as upside. Unanswered it expires to no, which is the "
    "same as today. You may not request a lower bar, a change to who decides, or less "
    "auditing — those are his judgement about the project, not its throughput.",
    {"rule": str, "to": float, "buys": str, "costs": str, "without": str},
)
async def t_capacity_request(args):
    return _text(_call("POST", "/agent/capacity", dict(args)))


@tool(
    "need_request",
    "CEO only. File a NEED with the chairman: a capability the project lacks — 'I need "
    "AWS access, give me a key', 'we need a Stripe account', 'SMTP credentials for the "
    "writer'. It reaches him where he reads (boardroom, Slack, email) and he fulfills "
    "it in one motion: the credential lands as an env var for the roles you name, and "
    "you are woken with the decision. Unanswered, it expires to no — plan the work "
    "that does not depend on it rather than waiting. Say WHY (what work it unblocks), "
    "suggest a credential name, name the roles, and estimate monthly cost honestly.",
    _schema({"title": str, "why": str, "est_usd_per_month": float,
             "suggested_secret_name": str, "roles": list},
            optional=("suggested_secret_name",)),
)
async def t_need_request(args):
    payload = {k: v for k, v in args.items() if v not in (None, "", [])}
    return _text(_call("POST", "/agent/needs", payload))


@tool(
    "credentials_list",
    "CEO only. The credential roster: every service credential the project holds and "
    "which roles receive it — names and scopes only, never values. Read it before "
    "filing a need (the capability may already exist) or delegating one.",
    {},
)
async def t_credentials_list(args):
    return _text(_call("GET", "/agent/credentials"))


@tool(
    "credential_scope",
    "CEO only, and only where the constitution grants capability_authority: ceo — "
    "otherwise a 403 naming the paths (file a need, or the chairman's boardroom order "
    "as a directive). Delegate an existing credential across your team: add_roles / "
    "remove_roles by role slug, with a reason. The value never moves — only who "
    "receives it at run time. Audited and sent to the chairman; effective on each "
    "role's next run.",
    _schema({"name": str, "add_roles": list, "remove_roles": list, "reason": str},
            optional=("add_roles", "remove_roles")),
)
async def t_credential_scope(args):
    payload = {k: v for k, v in dict(args).items()
               if k != "name" and v not in (None, "", [])}
    return _text(_call("POST", f"/agent/credentials/{args['name']}/scope", payload))


@tool(
    "purchase_begin",
    "Clear a real-world purchase against the spend envelope BEFORE paying — a refusal "
    "here costs nothing; the same refusal after paying costs the money. Records the "
    "purchase with a stable idempotency_key so a crash or retry can never buy twice: "
    "if the response says duplicate/completed, the money already moved — do NOT pay "
    "again. Then pay with whatever tool fits (the platform never holds the card for "
    "you), and call purchase_finish with the receipt. usd over the per-purchase line "
    "or the approval threshold needs an approved approval_id; the monthly external "
    "cap is a wall no approval overrides — recommend an amendment instead.",
    _schema({"vendor": str, "description": str, "usd": float, "idempotency_key": str,
             "requirement_id": str, "approval_id": str},
            optional=("requirement_id", "approval_id")),
)
async def t_purchase_begin(args):
    payload = {k: v for k, v in args.items() if v not in (None, "")}
    return _text(_call("POST", "/agent/purchases", payload))


@tool(
    "purchase_finish",
    "Close a purchase opened with purchase_begin. ok=true books the ledger entry and "
    "keeps the receipt (quote the vendor's own confirmation — what was bought, from "
    "whom, for how much); ok=false records the failure and books nothing. Finishing "
    "twice never double-books.",
    _schema({"purchase_id": str, "ok": bool, "receipt": str, "external_ref": str},
            optional=("receipt", "external_ref")),
)
async def t_purchase_finish(args):
    payload = {k: v for k, v in dict(args).items()
               if k != "purchase_id" and v not in (None, "")}
    payload["ok"] = bool(args.get("ok"))
    return _text(_call("POST", f"/agent/purchases/{args['purchase_id']}/finish", payload))


@tool(
    "side_effect_begin",
    "REQUIRED before any external side effect (posting, emailing, spending, creating a PR): "
    "record it with a stable idempotency_key. If the response says duplicate/completed, DO "
    "NOT fire the effect again — a prior attempt already did.",
    _schema({"kind": str, "idempotency_key": str, "task_id": str, "payload": dict},
            optional=("task_id", "payload")),
)
async def t_side_effect_begin(args):
    payload = {k: v for k, v in args.items() if v not in (None, "")}
    return _text(_call("POST", "/agent/side_effects", payload))


@tool("side_effect_finish", "Mark a recorded side effect completed/failed with its result.",
      _schema({"effect_id": str, "ok": bool, "result": dict}, optional=("result",)))
async def t_side_effect_finish(args):
    effect_id = args.pop("effect_id")
    return _text(_call("POST", f"/agent/side_effects/{effect_id}/finish", dict(args)))


@tool(
    "artifact_put",
    "Persist a finished deliverable (report, dataset, document) to the platform's "
    "artifact store — the shipped, versioned record the platform serves and audits. "
    "A published file is how the chairman gets what he is paying for: no message or "
    "chat ceiling applies to it, so give him everything he needs to act, to check you "
    "and to decide. Ship it here and CITE it — never paste its contents into a "
    "channel or the boardroom. "
    "Your /workspace is NOT ephemeral: it persists with the project across runs and is "
    "shared by every agent on it, which makes it the right home for working state and "
    "cached data pulls; ship an artifact when the work is FINISHED. "
    "Pass path: a file under /workspace (absolute or "
    "workspace-relative) — the platform reads and uploads the bytes itself. NEVER "
    "base64-encode a file or route its contents through your own output to ship it; "
    "write it to the workspace and hand over the path. filename optionally renames "
    "the artifact (plain name, no path separators; defaults to the file's own name). "
    "content_base64 is a fallback for small payloads you have in hand and nowhere on "
    "disk; filename is required with it. Max 5 MB decoded either way. satisfies "
    "lists the requirement keys (R-03) this file delivers on. Returns the "
    "artifact's uri, key, size and sha256 — cite the uri in evidence, task "
    "completions and messages. The uri is NOT a path in your sandbox: read it back "
    "with artifact_get, never with a file tool.",
    _schema({"path": str, "filename": str, "content_base64": str, "satisfies": list},
            optional=("path", "filename", "content_base64", "satisfies")),
)
async def t_artifact_put(args):
    path = (args.get("path") or "").strip()
    filename = (args.get("filename") or "").strip()
    encoded = args.get("content_base64") or ""
    if path:
        candidate = Path(path)
        resolved = (candidate if candidate.is_absolute() else WORKSPACE / candidate).resolve()
        if not resolved.is_relative_to(WORKSPACE.resolve()):
            return _text({"status": 422, "body": {
                "error": f"{path!r} is outside /workspace. Artifacts ship from your "
                         "workspace; write the file there and pass that path."}})
        if not resolved.is_file():
            return _text({"status": 404, "body": {
                "error": f"{path!r} is not a file in your workspace. Write the "
                         "deliverable to disk first, then pass its path."}})
        size = resolved.stat().st_size
        if size > 5 * 1024 * 1024:
            return _text({"status": 413, "body": {
                "error": f"{path!r} is {size} bytes; the limit is 5 MB. Store large "
                         "deliverables somewhere addressable and record the URL as "
                         "evidence instead."}})
        encoded = base64.b64encode(resolved.read_bytes()).decode()
        filename = filename or resolved.name
    if not encoded:
        return _text({"status": 422, "body": {
            "error": "pass path (a workspace file, preferred) or content_base64."}})
    if not filename:
        return _text({"status": 422, "body": {
            "error": "filename is required with content_base64."}})
    payload = {"filename": filename, "content_base64": encoded}
    if isinstance(args.get("satisfies"), list) and args["satisfies"]:
        payload["satisfies"] = [str(k) for k in args["satisfies"]]
    return _text(_call("POST", "/agent/artifacts", payload))


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
    _schema({"ref": str, "offset": int, "limit": int}, optional=("offset", "limit")),
)
async def t_artifact_get(args):
    params = {"ref": args.get("ref") or ""}
    for name in ("offset", "limit"):
        if args.get(name) is not None:
            params[name] = args[name]
    return _text(_call("GET", "/agent/artifacts/content", params=params))


def _memory_entry_id(path: str) -> str | None:
    """`/memories/mem_x` → `mem_x`; `/memories` (or bare) → None (a listing)."""
    parts = [part for part in (path or "").split("/") if part]
    if not parts or parts == ["memories"]:
        return None
    return parts[-1]


@tool(
    "memory",
    "Your durable memory, in the standard memory-tool directory shape — over the SAME "
    "governed store as the memory_* tools, with the same rules (caps refuse adds; only "
    "a scope's curator edits, folds or deletes). Commands: "
    "view (path '/memories' lists entries; '/memories/<id>' shows one) · "
    "create (path '/memories/<scope>/<subject>.md' where scope is project|team, "
    "file_text is the content) · "
    "str_replace (path '/memories/<id>', old_str, new_str — edits one entry) · "
    "delete (path '/memories/<id>') · "
    "rename (path '/memories/<id>', new_path names the new subject). "
    "Check your memory directory at the start of a run; record only what will still "
    "be true next month.",
    _schema({"command": str, "path": str, "file_text": str, "old_str": str, "new_str": str,
             "new_path": str},
            optional=("path", "file_text", "old_str", "new_str", "new_path")),
)
async def t_memory_tool(args):
    command = (args.get("command") or "").strip()
    path = (args.get("path") or "").strip()
    entry = _memory_entry_id(path)

    if command == "view":
        if entry is None:
            listing = _call("GET", "/agent/memory/search", params={"q": "", "limit": 50})
            body = listing.get("body")
            if listing["status"] == 200 and isinstance(body, list):
                lines = [f"/memories/{r['id']}  [{r['scope']}/{r['kind']}]  {r['subject']}"
                         for r in body]
                return _text({"status": 200, "body": {
                    "directory": lines,
                    "note": "view /memories/<id> for one entry; memory_search for "
                            "full-text search."}})
            return _text(listing)
        return _text(_call("GET", "/agent/memory/search",
                           params={"memory_id": entry, "limit": 1,
                                   "include_superseded": "true"}))

    if command == "create":
        parts = [p for p in path.split("/") if p]
        scope = parts[1] if len(parts) >= 3 and parts[0] == "memories" \
            and parts[1] in ("project", "team") else "project"
        subject = (parts[-1] if parts else "").removesuffix(".md").replace("-", " ").strip()
        return _text(_call("POST", "/agent/memory", {
            "scope": scope, "kind": "fact",
            "subject": subject or "untitled",
            "body": args.get("file_text") or "",
        }))

    if command == "str_replace":
        if entry is None:
            return _text({"status": 422, "body": {"error": "path must be /memories/<id>"}})
        current = _call("GET", "/agent/memory/search",
                        params={"memory_id": entry, "limit": 1})
        rows = current.get("body") if current["status"] == 200 else None
        if not rows:
            return _text({"status": 404, "body": {"error": f"no memory {entry}"}})
        row = rows[0]
        edited = row["body"].replace(args.get("old_str") or "", args.get("new_str") or "", 1)
        return _text(_call("POST", f"/agent/memory/{entry}/supersede", {
            "kind": row["kind"], "subject": row["subject"], "body": edited}))

    if command == "rename":
        if entry is None:
            return _text({"status": 422, "body": {"error": "path must be /memories/<id>"}})
        current = _call("GET", "/agent/memory/search",
                        params={"memory_id": entry, "limit": 1})
        rows = current.get("body") if current["status"] == 200 else None
        if not rows:
            return _text({"status": 404, "body": {"error": f"no memory {entry}"}})
        row = rows[0]
        new_subject = (args.get("new_path") or "").split("/")[-1] \
            .removesuffix(".md").replace("-", " ").strip()
        return _text(_call("POST", f"/agent/memory/{entry}/supersede", {
            "kind": row["kind"], "subject": new_subject or row["subject"],
            "body": row["body"]}))

    if command == "delete":
        if entry is None:
            return _text({"status": 422, "body": {"error": "path must be /memories/<id>"}})
        return _text(_call("POST", f"/agent/memory/{entry}/forget"))

    return _text({"status": 422, "body": {
        "error": f"unknown command {command!r}; use view, create, str_replace, "
                 "rename or delete"}})


@tool(
    "env_list",
    "List the environment variable NAMES available in your sandbox (never values). "
    "Answers 'do I have credential X?' definitively — use it instead of guessing.",
    {},
)
async def t_env_list(args):
    return _text({"env_names": sorted(os.environ)})


# ── data_fetch: a data source's bytes to disk, never through the context ───────
#
# The expensive default this exists to replace: an agent calls a mounted
# mcp__<source>__* tool, the whole result lands in its context as conversation
# text, and the "analysis" is a model doing arithmetic by reading rows. That is
# paid for by the token on the way in and again on every later turn of the run —
# and a model summing a column is less reliable than three lines of python.
#
# So: this tool makes the call itself and writes the bytes to a workspace file.
# The model gets a path, a size, a digest and a 2 KB preview; the rows never
# enter the conversation at all. The file is then processed with code (the image
# ships pandas and duckdb) and only the aggregate is reported.

DATA_FETCH_MAX_BYTES = 64 * 1024 * 1024
DATA_FETCH_PREVIEW_CHARS = 2048
DATA_FETCH_ERROR_CHARS = 2000
DATA_FETCH_SCHEMA_CHARS = 1024
DATA_FETCH_CONNECT_TIMEOUT_S = 30
DATA_FETCH_READ_TIMEOUT_S = 180
DATA_FETCH_TOTAL_TIMEOUT_S = 300


def _mcp_streams_cm(config: dict):
    """A connected (read, write) stream pair for one data source.

    The transport client is imported by BOTH names it has carried across mcp
    releases: the older ``streamablehttp_client`` (which builds its own httpx
    client) and the newer ``streamable_http_client`` (which takes a client we
    must build). Trying the old name first keeps the default behaviour stable.
    """
    headers = dict(config.get("headers") or {})
    if config.get("type") == "sse":
        from mcp.client.sse import sse_client

        return sse_client(config["url"], headers=headers,
                          timeout=DATA_FETCH_CONNECT_TIMEOUT_S,
                          sse_read_timeout=DATA_FETCH_READ_TIMEOUT_S)
    try:
        from mcp.client.streamable_http import streamablehttp_client

        return streamablehttp_client(config["url"], headers=headers,
                                     timeout=DATA_FETCH_CONNECT_TIMEOUT_S,
                                     sse_read_timeout=DATA_FETCH_READ_TIMEOUT_S)
    except ImportError:
        from mcp.client.streamable_http import streamable_http_client

        return _owned_client_cm(streamable_http_client, config["url"], headers)


@asynccontextmanager
async def _owned_client_cm(factory, url: str, headers: dict):
    """The new-API path: we own the httpx client, so we close it. trust_env
    stays ON (unlike the platform client) — this traffic goes through squid."""
    async with (
        httpx.AsyncClient(
            headers=headers, follow_redirects=True,
            timeout=httpx.Timeout(DATA_FETCH_CONNECT_TIMEOUT_S,
                                  read=DATA_FETCH_READ_TIMEOUT_S),
        ) as client,
        factory(url, http_client=client) as streams,
    ):
        yield streams


@asynccontextmanager
async def _mcp_session(config: dict):
    """An initialized ClientSession for a data source.

    A context manager rather than a generator on purpose: the transport runs on
    anyio task groups, and a generator abandoned by an early `return` gets
    closed later by the garbage collector, on a different task — which raises
    "attempted to exit cancel scope in a different task" and leaves the
    connection's teardown to chance. `async with` unwinds it where it was
    entered, every time.
    """
    from mcp import ClientSession

    # streamable http yields (read, write, get_session_id); sse yields two.
    async with (
        _mcp_streams_cm(config) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        yield session


async def _mcp_call_tool(config: dict, tool_name: str, arguments: dict):
    async with _mcp_session(config) as session:
        return await session.call_tool(tool_name, arguments)


async def _mcp_list_tools(config: dict):
    async with _mcp_session(config) as session:
        return await session.list_tools()


def _block_text(block) -> str | None:
    return getattr(block, "text", None) if getattr(block, "type", None) == "text" else None


def _serialize_result(result) -> tuple[str, bool]:
    """CallToolResult → (bytes to write, whether those bytes are the source's own
    payload rather than a wrapper we built).

    A single text block is written VERBATIM: a source that returns CSV should
    leave a .csv on disk, not CSV wrapped in JSON that every reader must unwrap.
    A multi-block result has no such faithful form, so it is written as an array
    OF BLOCKS — and that array is ours, not data, which is why it comes back
    flagged: counting its entries would report "2 rows" for a result that
    contains a note and a chart.
    """
    structured = getattr(result, "structuredContent", None)
    # A tool that returns something which is not an object has its value wrapped
    # as {"result": value} by the protocol. Unwrap a wrapped STRING: servers
    # built on FastMCP send a CSV export exactly this way, and writing the
    # wrapper would leave JSON-escaped CSV on disk — bigger, unparseable by the
    # tools that would read it, and unwrappable only by the agent we are trying
    # to keep out of the data.
    if isinstance(structured, dict) and set(structured) == {"result"} \
            and isinstance(structured["result"], str):
        return structured["result"], True
    if structured is not None:
        return json.dumps(structured, indent=2, default=str), True
    blocks = list(getattr(result, "content", None) or [])
    if len(blocks) == 1 and (text := _block_text(blocks[0])) is not None:
        return text, True
    payload = []
    for block in blocks:
        text = _block_text(block)
        payload.append({"type": "text", "text": text} if text is not None
                       else {"type": getattr(block, "type", type(block).__name__),
                             "repr": repr(block)[:DATA_FETCH_ERROR_CHARS]})
    return json.dumps(payload, indent=2, default=str), False


def _row_count(body: str, countable: bool) -> int | None:
    """Rows only when the file plainly IS a list of records — a number the agent
    can trust without opening the file, or nothing at all. Never a guess: a
    wrong count is worse than an absent one, because it gets reported."""
    if not countable:
        return None
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return None
    return len(parsed) if isinstance(parsed, list) else None


@tool(
    "data_fetch",
    "Call a tool on one of your data sources and write the FULL result to a file in "
    "your workspace WITHOUT it passing through your context. This is how bulk data is "
    "handled: fetch once to disk, then process the file with code (python3 has pandas "
    "and duckdb) or with Read/Grep slices, and report only the aggregate. "
    "server: the data source name (see DATA SOURCES in your system prompt). "
    "tool: that source's tool name, with or without its mcp__<server>__ prefix — OMIT "
    "tool to list what the source offers (names, descriptions and input schemas), which "
    "is how you discover a source that is not mounted as tools of your own. "
    "arguments: that tool's arguments, verbatim — prefer its own filter, date-range or "
    "pagination arguments over fetching everything and trimming later. "
    "path: optional workspace-relative destination; pass a stable path when you want a "
    "cache you can reuse, else it defaults to data/<server>/<tool>-<timestamp>.json. "
    "Returns {path, bytes, sha256, lines, rows, preview} — the preview is the first 2 KB "
    "ONLY; the answer is in the file, not the preview. Any mcp__* tools you do have are "
    "for small lookups you will read in full; use this for anything larger. Your "
    "workspace persists across runs, so check for an existing file before fetching "
    "again, and leave the script that processed it behind for the next run. "
    "approval_id: only for a tool your source's allowlist refuses — file "
    "data_write_request first, wait to be woken with the decision, then pass "
    "the approved id here with the SAME arguments you had approved.",
    _schema({"server": str, "tool": str, "arguments": dict, "path": str, "approval_id": str},
            optional=("tool", "arguments", "path", "approval_id")),
)
async def t_data_fetch(args):
    specs = _data_source_specs()
    name = (args.get("server") or "").strip()
    if name not in specs:
        known = ", ".join(sorted(specs)) or "none are configured for your role"
        return _text({"status": 422, "body": {
            "error": f"{name!r} is not one of your data sources. Configured: {known}."}})
    config = specs[name]

    tool_name = (args.get("tool") or "").strip()
    if tool_name.startswith(f"mcp__{name}__"):
        tool_name = tool_name[len(f"mcp__{name}__"):]

    if not tool_name:
        # Discovery. Schemas are small and the agent cannot call anything
        # without them — the one thing here that may enter the context.
        try:
            listing = await asyncio.wait_for(_mcp_list_tools(config),
                                             DATA_FETCH_TOTAL_TIMEOUT_S)
        except Exception as exc:
            return _text({"status": 0, "body": {
                "error": f"data source {name!r} could not be reached: {exc!r}"}})
        allowlist = config.get("tools")
        return _text({"status": 200, "body": {
            "server": name,
            # Said here so an agent plans around the rule instead of discovering
            # it one refusal at a time.
            "note": ("Every tool on this source is yours to call." if allowlist is None else
                     "Tools marked needs_approval are refused until the chairman approves "
                     "ONE call with the exact arguments — file data_write_request."),
            "tools": [
                {"name": t.name,
                 "description": (getattr(t, "description", "") or "")[:DATA_FETCH_ERROR_CHARS],
                 "needs_approval": allowlist is not None and t.name not in allowlist,
                 "input_schema": json.dumps(getattr(t, "inputSchema", None) or {},
                                            default=str)[:DATA_FETCH_SCHEMA_CHARS]}
                for t in (getattr(listing, "tools", None) or [])]}})

    # The allowlist is checked before the destination is even resolved: a
    # refused call must not have picked a path, reached the vendor, or written
    # a byte. An approval is the ONLY way past it, it is spent server-side, and
    # it is spent against these exact arguments — so an approval obtained for a
    # $5 bid change cannot be replayed on a $5,000 one.
    allowlist = config.get("tools")
    if allowlist is not None and tool_name not in allowlist:
        arguments = args.get("arguments") or {}
        approval_id = (args.get("approval_id") or "").strip()
        if not approval_id:
            return _text({"status": 403, "body": {
                "error": f"{name}.{tool_name} is not one of the tools this source may be "
                         f"called with. Callable without an approval: "
                         f"{', '.join(sorted(allowlist)) or 'none'}. To make this call, "
                         f"file data_write_request with the exact arguments and wait to be "
                         f"woken with the decision — then call again with approval_id."}})
        spent = _call("POST", f"/agent/data_writes/{approval_id}/spend",
                      {"source": name, "tool": tool_name, "arguments": arguments})
        if spent["status"] >= 400:
            return _text(spent)

    destination = (args.get("path") or "").strip()
    if not destination:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = f"data/{name}/{tool_name}-{stamp}.json"
    candidate = Path(destination)
    resolved = (candidate if candidate.is_absolute() else WORKSPACE / candidate).resolve()
    workspace = WORKSPACE.resolve()
    if not resolved.is_relative_to(workspace):
        return _text({"status": 422, "body": {
            "error": f"{destination!r} is outside /workspace. Fetched data lands in "
                     "your workspace; pass a path inside it."}})
    if resolved == workspace or resolved.is_relative_to(STAGING.resolve()):
        return _text({"status": 422, "body": {
            "error": f"{destination!r} is not a writable destination: "
                     f"{STAGING.name}/ holds this run's inputs. Pick another path."}})

    try:
        result = await asyncio.wait_for(
            _mcp_call_tool(config, tool_name, args.get("arguments") or {}),
            DATA_FETCH_TOTAL_TIMEOUT_S)
    except Exception as exc:
        return _text({"status": 0, "body": {
            "error": f"data source {name!r} call to {tool_name!r} failed: {exc!r}"}})

    if getattr(result, "isError", False):
        detail = next((t for t in (_block_text(b) for b in
                                   (getattr(result, "content", None) or [])) if t), "")
        return _text({"status": 502, "body": {
            "error": f"{name}.{tool_name} returned an error, nothing was written: "
                     f"{detail[:DATA_FETCH_ERROR_CHARS]}"}})

    body, countable = _serialize_result(result)
    data = body.encode()
    if len(data) > DATA_FETCH_MAX_BYTES:
        return _text({"status": 413, "body": {
            "error": f"the result is {len(data)} bytes; the limit is "
                     f"{DATA_FETCH_MAX_BYTES // (1024 * 1024)} MB. Use the tool's own "
                     "filter, date-range or pagination arguments and fetch a slice."}})
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(data)

    preview = body[:DATA_FETCH_PREVIEW_CHARS]
    return _text({"status": 200, "body": {
        "path": str(resolved.relative_to(workspace)),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "lines": body.count("\n") + (1 if body and not body.endswith("\n") else 0),
        "rows": _row_count(body, countable),
        "preview": preview,
        "preview_truncated": len(body) > len(preview),
        "note": "The complete result is on disk and did NOT enter your context. "
                "Process the file — do not answer from the preview.",
    }})


ALL_TOOLS = [
    t_board, t_task_create, t_task_claim, t_task_complete, t_task_review,
    t_task_block, t_task_kill,
    t_requirement_propose, t_requirement_depends_on,
    t_suggest, t_channels, t_messages_read, t_message_post, t_directive_file,
    t_ask_chairman, t_ask_close,
    t_memory_search, t_memory_add, t_memory_supersede, t_memory_compact, t_memory_forget,
    t_memory_tool,
    t_evidence_add, t_review_narrative, t_commitment_propose, t_commitment_reset,
    t_scope_act,
    t_flag_raise, t_flags_list, t_roster_activity,
    t_roles_list, t_hire, t_stand_down, t_recall, t_stand_down_when,
    t_hypothesis_add, t_hypothesis_kill, t_decision_add,
    t_approval_request, t_approvals_list, t_spend_book,
    t_capacity_request, t_need_request, t_credentials_list, t_credential_scope,
    t_assist_request, t_account_record, t_accounts_list,
    t_purchase_begin, t_purchase_finish,
    t_side_effect_begin, t_side_effect_finish,
    t_artifact_put, t_artifact_list, t_artifact_get, t_env_list,
]


#: Tools whose endpoint answers a non-CEO with 403, every time, whatever the
#: arguments. Sending one to an employee costs its schema on every API call of
#: every run and buys a refusal.
#:
#: **This list is not enforcement and must never become it.** The server-side
#: 403s are unchanged and remain the only thing that decides who may do what —
#: an agent that forges a call to a tool it was never offered is refused
#: exactly as before. Withholding the schema is a cost measure sitting on top
#: of a wall that does not move. `test_tool_surface.py` holds both halves: it
#: derives this set by calling every endpoint as an employee over HTTP, fails
#: if the list disagrees in either direction, and separately proves the
#: refusals still fire for a tool the agent was not given.
CEO_ONLY_TOOLS = frozenset({
    "ask_chairman", "ask_close",
    "capacity_request", "need_request",
    "commitment_propose", "commitment_reset",
    "credential_scope", "credentials_list",
    "directive_file",
    "flag_raise",
    "hire", "recall", "roles_list", "stand_down", "stand_down_when",
    "hypothesis_add", "hypothesis_kill",
    "requirement_propose", "requirement_depends_on",
    "review_narrative",
    "scope_act",
})

#: What this run is. Defaults to `ceo` so a caller that forgets to set it gets
#: the full surface: the failure that costs money is withholding a tool an
#: agent needed, not sending one it did not.
AGENT_KIND = os.environ.get("YAAF_AGENT_KIND") or "ceo"


def run_tools() -> list:
    """The tools worth sending THIS agent, and no others.

    Gated for the reason _builtin_tools() is: an undeclared schema is pure
    input-token cost on every API call, and a tool whose endpoint will refuse
    this agent is a schema nobody can use. That was already true of `data_fetch`
    for a role with no data source, and was quietly true of twenty-one CEO-only
    verbs for every employee on the platform — 3,700 tokens a call, half the
    block, on a prefix where the whole context pack was 4,600.

    Nothing about authority changes here. The server refuses what it always
    refused; this only stops paying to describe the refusal in advance.
    """
    tools = [t for t in ALL_TOOLS
             if AGENT_KIND == "ceo" or t.name not in CEO_ONLY_TOOLS]
    specs = _data_source_specs()
    if not specs:
        return tools
    tools.append(t_data_fetch)
    if any(config.get("tools") is not None for config in specs.values()):
        tools.extend([t_data_write_request, t_data_write_outcome])
    return tools


# ── hooks: the platform's ears inside the session, record-only ─────────────────
#
# Both hooks APPEND TO A LOG AND NOTHING ELSE. The Stop hook in particular is
# record-only BY CONSTRUCTION: it never returns a decision key, so it cannot
# block a run from ending — a hook that could would be a lever an agent's
# output might learn to pull.

HOOK_LOG: list[dict] = []


async def hook_precompact(input_data, tool_use_id, context):
    HOOK_LOG.append({
        "type": "hook", "hook": "PreCompact",
        "trigger": (input_data or {}).get("trigger"),
        "note": "context was compacted mid-run; durable facts belong in memory, "
                "not in conversation history",
    })
    return {}


async def hook_stop(input_data, tool_use_id, context):
    HOOK_LOG.append({"type": "hook", "hook": "Stop"})
    return {}


def build_hooks() -> dict:
    return {
        "PreCompact": [HookMatcher(hooks=[hook_precompact])],
        "Stop": [HookMatcher(hooks=[hook_stop])],
    }


def skill_names() -> list[str]:
    """The explicit allowlist for options.skills. ALWAYS a list — never None —
    because None means "CLI defaults", and the workspace is agent-writable: a
    skill an agent authored into .claude/skills must stay dark unless the
    chairman's role definition names it."""
    names = []
    for spec in PERMS.get("skills", []):
        slug = _skill_slug(str(spec.get("name", "")))
        if slug:
            names.append(slug)
    return names


def _skill_slug(name: str) -> str:
    return "".join(
        ch for ch in name.lower().strip().replace(" ", "-")
        if ch.isalnum() or ch == "-"
    )[:64]


def _builtin_tools() -> list[str]:
    """The base set of built-in tool declarations, not just permissions: a tool
    absent here never reaches the model at all. Undeclared schemas (Task,
    TodoWrite, a disabled Bash, ...) are otherwise pure input-token cost on
    every single API call."""
    tools: list[str] = []
    if PERMS.get("files", True):
        tools += ["Read", "Write", "Edit", "Glob", "Grep"]
    # data_processing is the same Bash tool under a narrower charter: it is
    # granted so a role can aggregate bulk data with CODE instead of by reading
    # every row into its own context, which costs tokens and gets arithmetic
    # wrong. A python-only grant would be theatre (python shells out); the real
    # containment is unchanged — the sandbox, the scoped secrets and the audit.
    if PERMS.get("bash", False) or PERMS.get("data_processing", False):
        tools.append("Bash")
    if PERMS.get("web", False):
        tools += ["WebFetch", "WebSearch"]
    return tools


def _allowed_tools() -> list[str]:
    allowed = [f"mcp__yaaf__{t.name}" for t in run_tools()]
    allowed += _builtin_tools()
    # Server-level grant: every tool the external MCP server offers. Bulk
    # sources are deliberately absent — they are reached only through
    # data_fetch, which never routes their results through the context.
    #
    # A source carrying an allowlist is granted tool by tool instead, or the
    # server-level grant would hand an inline source exactly the tools its
    # allowlist exists to refuse.
    specs = _data_source_specs()
    for name in _external_mcp_servers():
        allowlist = specs[name].get("tools")
        if allowlist is None:
            allowed.append(f"mcp__{name}")
        else:
            allowed += [f"mcp__{name}__{tool}" for tool in allowlist]
    return allowed


def _data_source_specs(env: dict[str, str] | None = None) -> dict[str, dict]:
    """EVERY project data source assigned to this role, mounted or not, keyed by
    name — the set data_fetch may call.

    Each spec: {"name": str, "url_env": str, optional "transport": "http"|"sse",
    optional "bearer_env": str, optional "access": "inline"|"bulk"}. The URL (and
    any bearer token) come from role-scoped secret env vars — so which roles can
    even see a data source is decided by the chairman's credential grants, never
    by prompt text. An entry whose env vars are missing is skipped: an ungranted
    role gets no server, silently, the same way it gets no secret.
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
        config: dict = {"type": spec.get("transport", "http"), "url": url,
                        "access": spec.get("access", "inline")}
        if spec.get("tools") is not None:
            config["tools"] = list(spec["tools"])
        token = env.get(spec.get("bearer_env", ""))
        if token:
            config["headers"] = {"Authorization": f"Bearer {token}"}
        servers[name] = config
    return servers


def _external_mcp_servers(env: dict[str, str] | None = None) -> dict[str, dict]:
    """The data sources mounted as native MCP tools — the inline ones only.

    A source marked ``bulk`` is NOT mounted: its results would land whole in the
    model's context, and for a source that exists to serve rows that is the
    expensive path we are removing. Leaving it unmounted is enforcement rather
    than advice — the wrong call cannot be made, because the tool is not there.
    Bulk sources stay reachable through data_fetch, which writes to disk.
    """
    return {name: {k: v for k, v in config.items() if k not in ("access", "tools")}
            for name, config in _data_source_specs(env).items()
            if config.get("access") != "bulk"}


MOUNT_PROBLEMS: list[str] = []


def _plan_windows(info) -> dict | None:
    """The plan meter out of a RateLimitEvent, in the shape the platform stores.

    The numbers live in `raw["unifiedWindows"]`, not in the typed
    `utilization` field — that one describes only the single window the event
    fired about and is routinely None. The raw dict carries every window at
    once, which is what a meter needs.
    """
    raw = getattr(info, "raw", None) or {}
    windows = raw.get("unifiedWindows")
    if not isinstance(windows, dict):
        return None
    out = []
    for key, entry in windows.items():
        if not isinstance(entry, dict):
            continue
        share = entry.get("utilization")
        if share is None:
            continue
        try:
            share = float(share)
        except (TypeError, ValueError):
            continue
        out.append({"key": key, "utilization": share, "resets_at": entry.get("resetsAt")})
    if not out:
        return None
    return {"status": raw.get("status") or getattr(info, "status", None), "windows": out}


def _usage_int(usage, key: str) -> int:
    if isinstance(usage, dict):
        return int(usage.get(key, 0) or 0)
    return int(getattr(usage, key, 0) or 0)


# The model API stream dies mid-response sometimes (network blips, proxy
# resets, an overloaded upstream). Failing the run makes that error expensive
# twice over: the turn's tokens are already spent AND the daemon's retry redoes
# the whole turn from scratch in a fresh session. So transient errors are
# retried HERE instead, resuming the same CLI session in place — completed
# tool calls, context, and prompt cache all survive; only the interrupted
# response is redone. Anything not clearly transient still fails the run, and
# credential errors must fail verbatim: the daemon reads them off the summary.
# MAX_USD and TASK_BUDGET are enforced per attempt (each resume is a fresh CLI
# process); the attempt cap bounds the total.
TRANSIENT_API_MARKERS = (
    "connection lost", "connection error", "connection reset", "connection closed",
    "econnreset", "econnrefused", "etimedout", "epipe", "socket hang up",
    "server disconnected", "overloaded", "internal server error",
    "request timed out", "timed out",
)
FATAL_API_MARKERS = (
    "not logged in", "please run /login", "invalid x-api-key",
    "authentication_error", "invalid bearer token", "oauth token has expired",
    "credit balance",
)
RESUME_ATTEMPTS = 3  # total query() calls: the first + up to 2 in-place resumes
RESUME_BACKOFF_S = (5.0, 20.0)  # indexed by attempt - 1
RESUME_PROMPT = (
    "Your previous response was interrupted by a transient network error "
    "mid-stream; the run has been resumed in the same session. Check what your "
    "last tool call actually returned, re-issue any call that never got a "
    "result, and finish the SAME unit of work. Do not start over."
)


def _looks_transient(exc: BaseException) -> bool:
    text = repr(exc).lower()
    if any(marker in text for marker in FATAL_API_MARKERS):
        return False
    return any(marker in text for marker in TRANSIENT_API_MARKERS)


def _error_kind(exc: BaseException) -> str:
    """How the run failed, said once, here, by the harness that saw it — so
    the daemon reads a field instead of re-guessing from one vendor's prose.
    "auth" is the one with consequences: three in a row pause the project."""
    text = repr(exc).lower()
    if any(marker in text for marker in FATAL_API_MARKERS):
        return "auth"
    if any(marker in text for marker in TRANSIENT_API_MARKERS):
        return "transient"
    return "fatal"


async def main() -> int:
    # Run-keyed: the workspace is shared by every agent on the project, so a
    # fixed name is another run's file half the time.
    system_prompt = (STAGING / f"system_prompt-{RUN_ID}.txt").read_text()
    context = json.loads((STAGING / f"context-{RUN_ID}.json").read_text())
    trace_path = STAGING / f"trace-{RUN_ID}.jsonl"
    plan_reading: dict | None = None
    result_path = STAGING / f"result-{RUN_ID}.json"

    server = create_sdk_mcp_server(name="yaaf", version="1.0.0", tools=render_sdk(run_tools()))
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=MODEL,
        effort=EFFORT,
        mcp_servers={"yaaf": server, **_external_mcp_servers()},
        tools=_builtin_tools(),
        allowed_tools=_allowed_tools(),
        # The agent is not asked for consent it cannot give: nothing here is
        # interactive. Containment is the fresh container, the role-scoped
        # credential set, the metered budget and the audited API — NOT the
        # network. There is no egress proxy; the per-role allowlist was removed
        # deliberately (docs/PLAN.md) and SECURITY.md states what follows from
        # that. An earlier version of this comment still named the proxy.
        permission_mode="bypassPermissions",
        cwd="/workspace",
        setting_sources=[],
        max_turns=100,
        hooks=build_hooks(),
        # Explicit allowlist, [] when the role grants none — [] suppresses
        # every discovered skill, which is the point: the workspace is
        # agent-writable and an unlisted skill must stay dark.
        skills=skill_names(),
        **({"task_budget": {"total": TASK_BUDGET}} if TASK_BUDGET > 0 else {}),
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
        "will wake you when there is a reason to.\n\n"
        "WORK QUIETLY. Prose you write between tool calls goes nowhere: it is not a "
        "message, not the deliverable, and no other run ever reads it. Do not announce "
        "what you are about to do, restate what a tool just returned, or narrate your "
        "reasoning as you go. Finish with ONE short paragraph — what you did, and "
        "anything the next run needs to know. That paragraph is the only prose here "
        "worth writing: it is what the chairman sees on the Runs page when something "
        "has gone wrong, and it is the whole of what this run leaves behind in words."
    )

    summary_parts: list[str] = CREDENTIAL_PROBLEMS + list(MOUNT_PROBLEMS)
    cost_usd = None
    tokens_in = tokens_out = cache_creation = cache_read = 0
    ok = True
    error_kind: str | None = None

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
                    "plan": plan_reading,
                    "error_kind": error_kind,
                }
            )
        )
        os.replace(tmp, result_path)

    with trace_path.open("a") as trace:
        def drain_hooks() -> None:
            while HOOK_LOG:
                trace.write(json.dumps(HOOK_LOG.pop(0)) + "\n")

        session_id: str | None = None
        next_prompt = prompt
        for attempt in range(1, RESUME_ATTEMPTS + 1):
            try:
                async for message in query(prompt=next_prompt, options=options):
                    drain_hooks()
                    sid = getattr(message, "session_id", None)
                    if not sid and isinstance(message, SystemMessage):
                        sid = (message.data or {}).get("session_id")
                    if sid:
                        session_id = sid
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                summary_parts.append(block.text)
                                trace.write(json.dumps({"type": "text",
                                                        "text": block.text}) + "\n")
                            else:
                                trace.write(json.dumps({"type": type(block).__name__,
                                                        "repr": repr(block)[:2000]}) + "\n")
                    elif isinstance(message, ResultMessage):
                        # Accumulated, not assigned: a resumed run yields one
                        # ResultMessage per attempt and every attempt's tokens
                        # were billed. The per-attempt figures land in the trace
                        # so the sum stays auditable.
                        if message.total_cost_usd is not None:
                            cost_usd = (cost_usd or 0.0) + message.total_cost_usd
                        if message.usage is not None:
                            usage = message.usage
                            # input_tokens is only the uncached remainder; the cache
                            # fields carry the rest of the real prompt.
                            tokens_in += _usage_int(usage, "input_tokens")
                            tokens_out += _usage_int(usage, "output_tokens")
                            cache_creation += _usage_int(usage, "cache_creation_input_tokens")
                            cache_read += _usage_int(usage, "cache_read_input_tokens")
                        trace.write(json.dumps({
                            "type": "result", "subtype": message.subtype,
                            "attempt": attempt, "cost_usd": message.total_cost_usd,
                            "tokens_out": _usage_int(message.usage, "output_tokens")
                            if message.usage is not None else 0,
                        }) + "\n")
                    else:
                        # The SDK tells us the plan meter without being asked:
                        # a RateLimitEvent carries the same five_hour/seven_day
                        # windows the /usage screen draws, on the very token
                        # doing the spending. Duck-typed rather than isinstance
                        # so an SDK too old to define the class just does not
                        # match, instead of failing the import for every run.
                        info = getattr(message, "rate_limit_info", None)
                        if info is not None:
                            plan = _plan_windows(info)
                            if plan:
                                plan_reading = plan
                        trace.write(json.dumps({"type": type(message).__name__,
                                                "repr": repr(message)[:2000]}) + "\n")
                    trace.flush()
                    write_result(partial=True)
                break
            except Exception as exc:
                if attempt >= RESUME_ATTEMPTS or not _looks_transient(exc):
                    ok = False
                    error_kind = _error_kind(exc)
                    summary_parts.append(f"[sandbox_entry error: {exc!r}]")
                    trace.write(json.dumps({"type": "error", "attempt": attempt,
                                            "error": repr(exc)}) + "\n")
                    break
                summary_parts.append(
                    f"[transient API error on attempt {attempt}; resuming in place]")
                trace.write(json.dumps({"type": "transient_error", "attempt": attempt,
                                        "resume": session_id, "error": repr(exc)}) + "\n")
                trace.flush()
                await asyncio.sleep(
                    RESUME_BACKOFF_S[min(attempt - 1, len(RESUME_BACKOFF_S) - 1)])
                if session_id:
                    options.resume = session_id
                    next_prompt = RESUME_PROMPT
                # No session yet means the stream died before the model did
                # anything; rerunning the original prompt fresh loses nothing.
        drain_hooks()

    write_result(partial=False)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
