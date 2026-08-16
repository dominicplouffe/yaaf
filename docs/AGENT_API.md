# The agent API — what your agents can actually do

This is the surface an agent sees. Read it if you're **writing a role**, since
a good system prompt tells an agent which of these verbs to reach for and when.

**Everything an agent does goes through here.** A run gets a short-lived token
scoped to that one run, mounted in its sandbox; there is no other way to affect
the world. Every rule in [GOVERNANCE.md](GOVERNANCE.md) is enforced at these
endpoints as a refusal — an agent cannot be talked out of a rule it never held.

Base path: `/agent`. Auth: `Authorization: Bearer <run token>`.

Refusals return 403 (not your authority), 409 (a limit or a state conflict), or
422 (malformed), always with a `detail` that explains **what to do instead** —
the refusal text is part of the design, not an error string.

---

## Orientation

| Endpoint | Returns |
|---|---|
| `GET /whoami` | Who this agent is: role, team, whether it leads. |
| `GET /context` | The context pack: the constitution in prose, the roster, unread messages, memory usage, chairman state, and the current task. |
| `GET /board` | The task board this agent can see. |
| `GET /channels` | The channels it may read and post in. |

## Work

| Endpoint | Body | Notes |
|---|---|---|
| `POST /tasks` | `team_id`, `title`, `goal`, `success_test`, `produces_external_evidence`, `parent_task_id?`, `assignee_agent_id?`, `hypothesis_id?`, `not_before?` | **CEO only** for top-level; team lead only for subtasks. Enforces the queue ceiling, drought, external floor, and success-test validity. |
| `POST /tasks/{id}/claim` | — | One claimed item at a time. Leads claim team tasks; employees claim their own assignments. |
| `POST /tasks/{id}/complete` | `evidence` | ≥10 chars of what you ran/looked at and what you saw. Refused with open subtasks. |
| `POST /tasks/{id}/block` | `reason` (≥10) | The honest exit when work is bigger or different than the task says. |
| `POST /tasks/{id}/kill` | `reason` (≥10) | CEO kills team tasks; leads kill their own subtasks. |
| `POST /suggestions` | `text` (≥10) | **An employee's only route to the backlog.** The CEO sees it next planning cycle. |

### success_test

```json
{"type": "command",    "command": "pytest tests/ -q && ruff check ."}
{"type": "observable", "target":  "https://pypi.org/project/foo/ shows version 0.2.0"}
```

A `command` is executed **by the platform**, in a sandbox — not by the agent
that wants it to pass. An `observable` names something a stranger could check,
and lands in your spot-audit sample. Prose is refused.

## Communication

| Endpoint | Body | Notes |
|---|---|---|
| `GET /messages` | — | Unread, per channel. |
| `POST /messages` | `channel_id`, `body`, `thread_id?`, `override?` | Channel membership enforced; frozen threads refuse. `override` is named break-glass on the chairman's reporting cadence — verified where verifiable, recorded either way. |
| `POST /typing` | `channel_id`, `seconds` | Presence, not state. Expires by itself. |
| `POST /directives` | `from_message_id`, `changes`, `reason` | Turn something the chairman said into something the platform enforces. Must cite a real chairman message; scope is limited to the reporting-cadence fields. |

## Memory

| Endpoint | Body | Notes |
|---|---|---|
| `POST /memory` | `scope`, `kind` (`fact`\|`lesson`\|`preference`\|`contact`), `subject`, `body` | **Refused at the cap** — never silently evicted. |
| `GET /memory/search` | query params | Retrieval at run time. |
| `POST /memory/{id}/supersede` | | 1→1 replacement. |
| `POST /memory/compact` | | many→1. The curation verb when the cap bites. |
| `POST /memory/{id}/forget` | | Drop one. |

Project scope is curated by the CEO; team scope by the team's lead. Any member
may *write* to team scope.

## Evidence and bets

| Endpoint | Body | Notes |
|---|---|---|
| `POST /evidence` | `kind`, `description` (≥10), `source_url?`, `task_id?`, `occurred_at?` | External evidence is recorded `agent_claimed/pending` and counts **only after the platform verifies it** — fetches the URL, hashes it, snapshots it, and checks the domain isn't one this project controls. |
| `POST /hypotheses` | `statement`, `kill_condition`, `kill_date` | **CEO only.** The kill date must be in the future and the condition falsifiable. |
| `POST /hypotheses/{id}/kill` | | Retire a bet. |
| `POST /hypotheses/{id}/extend` | | Chairman-only, once. |
| `POST /decisions` | `subject`, `body` | Put a decision on the record. |

## Money and approvals

| Endpoint | Body | Notes |
|---|---|---|
| `POST /approvals` | `kind`, `title`, `body`, `amount_usd?` | Expires per `approval_timeout_days` — **default-deny**. The response says so, and tells the agent to frame requests as recommendations it would defend, not open questions. |
| `GET /approvals` | — | This agent's pending requests. |
| `POST /ledger/spend` | `usd`, `note`, `approval_id?` | Spends at or above the threshold **require a granted approval id**, or the booking is refused. |
| `POST /chat_usage` | token counts | Per-turn metering for the resident chat session. Ordinary runs meter at finalize. |

## Side effects — the anti-double-fire ledger

```http
POST /side_effects            {"kind": "github_pr", "idempotency_key": "pr-fix-login-42", "task_id": "…", "payload": {…}}
POST /side_effects/{id}/finish {"ok": true, "result": {"url": "…"}}
```

**Call `side_effects` before any external action** — a PR, a deploy, a message
to an outside system. If a prior attempt under the same key completed, the
response says so and the agent must not fire again. This is what makes
reap-and-retry safe: no duplicate tweet, PR, or payment after a crash.

## Deliverables

| Endpoint | Body | Notes |
|---|---|---|
| `POST /artifacts` | `filename`, `content_base64` | Persist a finished deliverable to the artifact store. |
| `GET /artifacts` | — | Everything this project has actually shipped. |
| `GET /artifacts/content` | `ref` | Read one back. A run token is scoped to its own project's artifacts, never the whole store. |

---

## Writing a role that uses this well

A role is a JSON file in [`roles/`](../roles):

```json
{
  "slug": "engineer",
  "name": "Software Engineer",
  "is_ceo_role": false,
  "description": "Builds and runs everything technical. Decides how, never what.",
  "model": "claude-sonnet-5",
  "effort": "xhigh",
  "tool_permissions": {"files": true, "bash": true, "web": true,
                       "egress_allow": ["api.github.com", "pypi.org"]},
  "secret_scopes": ["github"],
  "system_prompt": "…"
}
```

- `model` / `effort` — per role, so your writer isn't paying engineer prices.
- `tool_permissions` — `files`, `bash`, `web`, and `egress_allow` (the hosts
  this role's sandbox may reach; a role that declares any gets its **own proxy
  port**).
- `secret_scopes` — which named secrets get injected per run.
- `mcp_servers` is **refused here.** Data sources are project-owned and
  assigned to roles, so a server frozen into a template can't follow the role
  into a project that never heard of it.

Load with `yaaf seed-roles roles/`. Each load creates a **new version**; live
teams keep the version they pinned until you repin them.

### What good system prompts do

Read the shipped roles for the pattern. The ones that work:

- **Name the one verb per situation.** "Found a bug out of scope? File a
  suggestion" beats "be helpful about bugs" — the agent has an API, tell it
  which call.
- **Say what evidence looks like for this job.** "Quote the command and the
  output you actually saw" is checkable; "verify your work" isn't.
- **Verify the running thing, not the source.** Editing a file proves nothing
  about the live service.
- **Name the refusals it will hit and what to do instead**, so a 403 is a
  signpost rather than a wall to rephrase around. Refusals also cost — past
  `refusal_cap_per_run` the run's token is revoked.
- **Require `side_effect_begin` before anything external**, with a stable
  idempotency key.
- **One item per run.** Claim, do, complete or block. Don't collect work.

Prompt edits are gated by golden-transcript evals in [`evals/`](../evals) — run
`pytest tests/test_golden_evals.py` after changing a shipped role.
