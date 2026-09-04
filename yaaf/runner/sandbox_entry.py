"""Runs INSIDE the sandbox container. Do not import from the yaaf package here —
the container only has this file, the Claude Agent SDK (and its own dependency
`mcp`, which data_fetch uses as a client), and httpx.

It wires the yaaf platform tools (board, tasks, messages, memory, evidence,
approvals, side effects) as SDK MCP tools that call the agent HTTP API with the
per-run token, then drives one stateless run: context in, one unit of work,
result.json out.

Refusals come back to the model as tool output — the refusal text *is* the
interface. The platform separately counts them and revokes the token at the cap.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    SystemMessage,
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
# Constitution run_token_budget: handed to the model as an API-side task
# budget so it paces itself and wraps up before the line. 0 = off.
TASK_BUDGET = int(os.environ.get("YAAF_TASK_BUDGET", "0") or 0)
PERMS = json.loads(os.environ.get("YAAF_TOOL_PERMISSIONS", "{}"))

WORKSPACE = Path("/workspace")
STAGING = Path("/workspace/.yaaf")

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
    {
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
    },
)
async def t_task_create(args):
    payload = {k: v for k, v in args.items() if v not in (None, "")}
    return _text(_call("POST", "/agent/tasks", payload))


@tool(
    "requirement_propose",
    "CEO only. Propose something that must be TRUE at the end — not a task, an outcome. "
    "acceptance_test has the same shape as a success test: "
    '{"type":"command","command":...} the platform runs itself, or '
    '{"type":"observable","target":...} the chairman confirms. Prefer a command: an '
    "observable puts him in the loop every time it closes. "
    "This is a PROPOSAL and creates no scope — the chairman accepts it, and until he "
    "does no work may be created for it. You cannot accept your own, and you cannot "
    "mark one met: the platform runs the test and writes the result. "
    "stage is the order this happens in (1, 2, 3…); est_runs is your honest guess at "
    "how many runs it will take, and it is compared against what it actually costs. "
    "depends_on lists requirement ids that must be settled first.",
    {
        "statement": str,
        "acceptance_test": dict,
        "stage": int,
        "est_runs": int,
        "depends_on": list,
    },
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
    "your context); set override to 'blocker', 'budget', 'approval' or 'question' only "
    "when it truly cannot wait — overrides are recorded and he sees them. 'question' is "
    "the ask verb: when you feel strongly enough that you want his answer before "
    "deciding, ask him plainly. Allowed always, required never — when in doubt, decide "
    "and report.",
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
    {"from_message_id": str, "changes": dict, "action": dict, "reason": str},
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
    {"note": str},
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
    "review_narrative",
    "CEO only, when a review is open (see the `review` block in your context). File your "
    "variance narrative for the standing status meeting: what moved against the plan since "
    "the last review, the SINGLE largest cause named (a requirement key, a dead bet, a "
    "failure storm — something with a handle), and in `recommended`, what you would cut or "
    "move. The platform's numbers are already on the review — explain them, do not restate "
    "them. The chairman's decisions come back to you as notices with his reasons.",
    {"narrative": str, "recommended": str},
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
    "use instead. To want his opinion without blocking, ask in the boardroom with "
    "override='question' and keep going.",
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
    "Book a real-world dollar spend you made with your own tooling. Every spend faces "
    "the same envelope as purchase_begin: the monthly external cap is a hard wall, and "
    "amounts over the per-purchase line or the approval threshold need an approved "
    "approval_id. For a purchase you are ABOUT to make, prefer purchase_begin/"
    "purchase_finish — it checks the envelope before money moves and books itself. "
    "Every real purchase gets booked, one way or the other: an unbooked spend is what "
    "the chairman's spot-audit exists to catch.",
    {"usd": float, "note": str, "approval_id": str},
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
    "task. Unanswered it expires to no; plan the work that does not need it.",
    {"server": str, "tool": str, "arguments": dict, "why": str},
)
async def t_data_write_request(args):
    payload = {"source": (args.get("server") or "").strip(),
               "tool": (args.get("tool") or "").strip(),
               "arguments": args.get("arguments") or {},
               "why": (args.get("why") or "").strip()}
    return _text(_call("POST", "/agent/data_writes", payload))


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
    {"service": str, "username": str, "owner": str, "credential_name": str,
     "url": str, "requirement_id": str, "note": str},
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
    "need_request",
    "CEO only. File a NEED with the chairman: a capability the project lacks — 'I need "
    "AWS access, give me a key', 'we need a Stripe account', 'SMTP credentials for the "
    "writer'. It reaches him where he reads (boardroom, Slack, email) and he fulfills "
    "it in one motion: the credential lands as an env var for the roles you name, and "
    "you are woken with the decision. Unanswered, it expires to no — plan the work "
    "that does not depend on it rather than waiting. Say WHY (what work it unblocks), "
    "suggest a credential name, name the roles, and estimate monthly cost honestly.",
    {"title": str, "why": str, "est_usd_per_month": float,
     "suggested_secret_name": str, "roles": list},
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
    {"name": str, "add_roles": list, "remove_roles": list, "reason": str},
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
    {"vendor": str, "description": str, "usd": float, "idempotency_key": str,
     "requirement_id": str, "approval_id": str},
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
    {"purchase_id": str, "ok": bool, "receipt": str, "external_ref": str},
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
    "artifact store — the shipped, versioned record the platform serves and audits. "
    "Your /workspace is NOT ephemeral: it persists with the project across runs and is "
    "shared by every agent on it, which makes it the right home for working state and "
    "cached data pulls; ship an artifact when the work is FINISHED. "
    "Pass path: a file under /workspace (absolute or "
    "workspace-relative) — the platform reads and uploads the bytes itself. NEVER "
    "base64-encode a file or route its contents through your own output to ship it; "
    "write it to the workspace and hand over the path. filename optionally renames "
    "the artifact (plain name, no path separators; defaults to the file's own name). "
    "content_base64 is a fallback for small payloads you have in hand and nowhere on "
    "disk; filename is required with it. Max 5 MB decoded either way. Returns the "
    "artifact's uri, key, size and sha256 — cite the uri in evidence, task "
    "completions and messages. The uri is NOT a path in your sandbox: read it back "
    "with artifact_get, never with a file tool.",
    {"path": str, "filename": str, "content_base64": str},
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
    return _text(
        _call("POST", "/agent/artifacts",
              {"filename": filename, "content_base64": encoded})
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
    {"command": str, "path": str, "file_text": str, "old_str": str, "new_str": str,
     "new_path": str},
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
    {"server": str, "tool": str, "arguments": dict, "path": str, "approval_id": str},
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
    t_board, t_task_create, t_task_claim, t_task_complete, t_task_block, t_task_kill,
    t_requirement_propose, t_requirement_depends_on,
    t_suggest, t_channels, t_messages_read, t_message_post, t_directive_file,
    t_memory_search, t_memory_add, t_memory_supersede, t_memory_compact, t_memory_forget,
    t_memory_tool,
    t_evidence_add, t_review_narrative, t_commitment_propose, t_commitment_reset,
    t_scope_act,
    t_hypothesis_add, t_hypothesis_kill, t_decision_add,
    t_approval_request, t_approvals_list, t_spend_book,
    t_need_request, t_credentials_list, t_credential_scope,
    t_assist_request, t_account_record, t_accounts_list,
    t_purchase_begin, t_purchase_finish,
    t_side_effect_begin, t_side_effect_finish,
    t_artifact_put, t_artifact_list, t_artifact_get, t_env_list,
]


def run_tools() -> list:
    """ALL_TOOLS plus data_fetch when this role actually has a data source, and
    data_write_request when one of those sources refuses anything.

    Gated for the same reason _builtin_tools() is: an undeclared schema is pure
    input-token cost on every API call, and a fetch tool for a role with nothing
    to fetch from is a schema nobody can use. A role whose every source is
    unrestricted has nothing to ask permission for either."""
    specs = _data_source_specs()
    if not specs:
        return list(ALL_TOOLS)
    tools = [*ALL_TOOLS, t_data_fetch]
    if any(config.get("tools") is not None for config in specs.values()):
        tools.append(t_data_write_request)
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


async def main() -> int:
    # Run-keyed: the workspace is shared by every agent on the project, so a
    # fixed name is another run's file half the time.
    system_prompt = (STAGING / f"system_prompt-{RUN_ID}.txt").read_text()
    context = json.loads((STAGING / f"context-{RUN_ID}.json").read_text())
    trace_path = STAGING / f"trace-{RUN_ID}.jsonl"
    result_path = STAGING / f"result-{RUN_ID}.json"

    server = create_sdk_mcp_server(name="yaaf", version="1.0.0", tools=run_tools())
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
                        trace.write(json.dumps({"type": type(message).__name__,
                                                "repr": repr(message)[:2000]}) + "\n")
                    trace.flush()
                    write_result(partial=True)
                break
            except Exception as exc:
                if attempt >= RESUME_ATTEMPTS or not _looks_transient(exc):
                    ok = False
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
