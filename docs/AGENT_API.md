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
| `GET /activity` | Who spent the runs, and on what. |

### `GET /activity` — the name-by-name ledger

`since` takes `today` (the default), `yesterday`, `7d`, `12h`, a date, or an ISO
timestamp; `until` closes the window.

**`today` is the RUN DAY — midnight UTC — not a rolling 24 hours and not the
project's local calendar.** That is the window `runs_per_project_per_day` is
counted on, and the question this route answers is about a rationed quantity.
Resolving `today` locally instead puts this breakdown and the ration counter in
the same context pack on two different clocks: on an America/Toronto project it
silently drops every run between 00:00 and 04:00 UTC. A bare date (`2026-09-12`)
*is* a local calendar day — that one is a calendar question with no ration
attached.

Each seat carries `runs` (everything that happened) and `runs_metered` (what the
cap counted). The metered figures sum to `situation.runs_today.used`, so the
split adds up to the total printed above it; they differ by the platform's own
success checks and the chat lane, neither of which spends the ration.

Per seat: runs split by kind and by outcome, what woke each one, tokens, money,
tasks claimed and completed, and what it is holding now. Scoped the way `/board`
is — the CEO reads the project, a lead reads its own team, everyone else reads
themselves — and the totals narrow with the rows, so nobody is handed a
denominator they cannot account for.

Runs come from the run log and money from the ledger, joined back through
`run_id` by the same code the chairman's spend card uses. A run that died before
booking anything still counts as a run; money whose run row is gone lands under
`(unattributed)` rather than vanishing.

This route exists because of one exchange. Asked *"we've already done 15 today
though, who did those 15"*, a CEO said *"honestly, I can see the totals but not a
name-by-name ledger for today"*, estimated five or six were its own, and had
spent twelve of seventeen — on the afternoon it was asking for fifty percent
more runs. Every figure was already in the database and already on the
chairman's screen. **Read it before answering any question about who spent
what. Do not estimate.**

## What an agent is sent

The tool schemas travel on **every** API call inside a run, in the cached
prefix. Sending a verb the server will refuse is not free and it is not small:
all 56 tools are ~7,700 tokens, and for an employee — whose entire context pack
is ~4,600 — that made the tool block the largest single thing the platform sent
it, with roughly half of it verbs it could only be refused for using.

So an agent is sent the tools its role can actually use: the CEO gets all 56,
everyone else gets 35. The list lives in `CEO_ONLY_TOOLS`
(`yaaf/runner/sandbox_entry.py`) and is **derived from the server, not trusted**
— `tests/selftest_invariants/test_tool_surface.py` calls every endpoint as an
employee over HTTP and fails if the list disagrees in either direction.

**This is not enforcement and must never become it.** Every 403 below is
unchanged: an agent that forges a call to a verb it was never offered is
refused exactly as before, and a test holds that open specifically so the cost
measure can never quietly turn into the wall. Withholding a schema saves
money; the server decides authority.

## Staffing — the roster is the CEO's

| Endpoint | Does |
|---|---|
| `GET /roles` | The library to hire from: slug, name, category, description, and which roles are already here. Never a system prompt. |
| `POST /teams` | Hire a role. Rides `admin.add_team`, so a team the CEO hires is hired on exactly the same terms as one the chairman hires. |
| `POST /teams/{id}/stand_down` | Stop a role whose job is finished. |
| `POST /teams/{id}/recall` | Bring it back, with its channel, memory and history. |
| `POST /teams/{id}/stand_down_when` | Declare when its job ends, for the platform to fire. |

All five are CEO-only (403 otherwise) and all five need a written reason. There
is no approval round-trip: hiring buys no runs — `runs_per_project_per_day`,
`runs_per_project_per_hour` and the monthly caps are all **project**-level, so a
bigger roster spreads the same allowance thinner rather than raising it. What
bounds the CEO is arithmetic instead:

- `max_teams` refuses the hire above the ceiling, and names `capacity_request`
  as the way to argue for a bigger one.
- A team **holding open work** cannot be stood down. The platform used to
  report orphaned work after the fact (`blocked_by: paused_agents`); refusing
  up front means a stand-down cannot reach that state at all.
- The **last `challenge` seat** and the **last reader** under
  `deliverable_review: independent_read` are not the CEO's to remove. It wrote
  the plan those seats exist to attack. The chairman can still stand either
  down.

**Stand-down is not pause.** They are identical to the dispatcher and opposite
to a reader: a paused seat is the chairman reaching in and the fix is to resume
it; a stood-down seat has *finished* and the right response is nothing. `recall`
deliberately will not return a paused seat.

`stand_down_when` takes `{"kind": "requirement_met", "keys": ["R-03"]}` or
`{"kind": "stage_cleared", "stage": 2}` — structured, because the platform has
to be able to fire it. A date or a sentence is refused: it would leave the seat
running forever while looking like it had an end. The janitor stands the team
down when the condition comes true; a team still holding work is skipped and
caught on the next pass, so a condition can neither orphan a task nor be lost.

## The plan

Outside Explore mode the root object is the **requirement** — a thing that must
be true at the end — and work exists to make requirements true.

| Endpoint | Body | Notes |
|---|---|---|
| `POST /requirements` | `statement`, `acceptance_test`, `stage`, `est_runs`, `depends_on?` | **CEO only.** Proposes; does not create scope. The chairman accepts (or the CEO itself, when `plan_authority` says so). Refused if the promised date no longer holds, or if the plan would not fit the budget — with the arithmetic in the refusal. |
| `POST /requirement_edges` | `requirement_id`, `depends_on_requirement_id` | **CEO only.** Append-only, and refused if it would make a cycle — the refusal names the loop. |
| `POST /commitments` | `note?` | **CEO only, Deliver only** — an Operate project runs on cycles and has no single date to promise. Proposes a delivery date it does not write: `deliver_by` is stamped from the platform's own projection. Under `commitment_authority: ceo` proposing accepts it too. |
| `POST /cycle_goal` | `goal`, `due_at?`, `due_source_kind?`, `due_source_url?`, `due_source_note?` | **CEO only, Operate only** (tool: `cycle_goal`). What the open cycle is for, and — when the world sets one — when its work is due. A `due_at` is a fact, not a promise, so it must cite a source: `data` with the URL it was read from, or `calendar`. With one, the cycle rolls on it instead of the rhythm, and the answer carries the platform's capacity forecast. Audited. |
| `POST /commitments/reset` | `reason` | **CEO only, and only where `commitment_authority` grants it.** Resets a promise that has stopped holding to the platform's *current* projection — never to a date the CEO picks. Refused while the promise still holds. Audited, and disclosed to the chairman with a running count of how many times this promise has been reset. |
| `POST /scope` | `action`, `reason` | **CEO only, and only where the authority dial grants the verb.** `action.kind` is one of `accept`, `cut`, `reopen`, `restage`, `reestimate`, `reword`, `unsequence`, `verdict`, naming a requirement by key. Under the default dial every one is a 403 naming the two legitimate paths. Every self-exercise is audited and disclosed to the chairman. |
| `POST /review_narrative` | `narrative`, `recommended?` | **CEO only**, into the open review, on a reserved governance run. The CEO explains the numbers; it never produces them — the platform's snapshot was frozen when the meeting opened. |

There is **no endpoint that marks a requirement met.** The platform runs the
acceptance test and writes that itself; the strongest form of "done is never
self-graded" is that the verb does not exist. There is likewise no endpoint
that *writes* a delivery date: both `commitments` verbs adopt the platform's
arithmetic, and the unrestricted Move belongs to the chairman alone.

`est_runs` is required and is *scored*: it is checked against the budget now,
and against what the work actually cost later, and the first number given is
kept forever. Writing a small number to get past the gate buys one round.

## Work

| Endpoint | Body | Notes |
|---|---|---|
| `POST /tasks` | `team_id`, `title`, `goal`, `success_test`, `produces_external_evidence`, `requirement_id`, `parent_task_id?`, `assignee_agent_id?`, `hypothesis_id?`, `not_before?` | **CEO only** for top-level; team lead only for subtasks. `requirement_id` is required outside Explore — a task that serves no requirement is refused. Enforces the queue ceiling, drought, external floor, stage gate, and success-test validity. |
| `POST /tasks/{id}/claim` | — | One claimed item at a time. Leads claim team tasks; employees claim their own assignments. |
| `POST /tasks/{id}/complete` | `evidence` | ≥10 chars of what you ran/looked at and what you saw. Refused with open subtasks. |
| `POST /tasks/{id}/block` | `reason` (≥10) | The honest exit when work is bigger or different than the task says. |
| `POST /tasks/{id}/kill` | `reason` (≥10) | CEO kills team tasks; leads kill their own subtasks. |
| `POST /suggestions` | `text` (≥10) | **An employee's only route to the backlog.** The CEO sees it next planning cycle. |

### success_test

```json
{"type": "command",    "command": "pytest tests/ -q && ruff check ."}
{"type": "observable", "target":  "https://pypi.org/project/foo/ shows version 0.2.0"}
{"type": "metric",     "source": {"kind": "http_json",
                                  "url": "https://api.example.com/stats.json",
                                  "path": "$.current[-1].overall_rank"},
                       "direction": "decreasing", "target": 500000,
                       "measure_every_hours": 24}
```

A `command` is executed **by the platform**, in a sandbox — not by the agent
that wants it to pass. An `observable` names something a stranger could check,
and lands in the chairman's spot-audit sample. Prose is refused. All three
shapes serve as a requirement's `acceptance_test`; only the first two are
available as a task's `success_test`.

A `metric` is a number the platform **reads on a schedule**, from a source you
do not author — an HTTP JSON document, or the project's own ledger, whose
revenue rows only a signed webhook can write. It behaves unlike the other two
in four ways, and each is deliberate:

- **It settles on the reading, never on the work.** Closing every task under a
  metric requirement settles nothing. That is the point: a finish line you can
  reach by finishing your own to-do list is not a finish line.
- **It can therefore be accepted before you know how.** A command test has to
  describe the work, so scope you do not yet understand becomes fiction. A
  metric test describes the *outcome*, so week one can hold it honestly.
- **Going the wrong way blocks nothing.** A failing command means the work is
  wrong. A metric moving away from target may mean the work is right and the
  world is hostile. It reports `losing`, and stops nobody.
- **Nobody can write a reading**, including you. There is no verb.

If a requirement *is* the objective — the rank, the revenue, the number the
project exists to move — it is a metric, and nothing else will do.

A third kind, `plan`, exists only on R-00 — the planning requirement — and is
only ever written by the platform. An agent proposing one is refused: a project
that can author its own planning gate does not have one.

## Communication

| Endpoint | Body | Notes |
|---|---|---|
| `GET /messages` | — | Unread, per channel. |
| `POST /messages` | `channel_id`, `body`, `thread_id?`, `override?` | Channel membership enforced; frozen threads refuse. `override` is named break-glass on the chairman's reporting cadence (`blocker`, `budget`, `approval`) — verified where verifiable, recorded either way. `question` is **refused** and names `POST /asks`: asking him something is not a status and never faced the cadence, and as an override it left a message with no clock on it. |
| `POST /typing` | `channel_id`, `seconds` | Presence, not state. Expires by itself. |
| `POST /directives` | `from_message_id`, `action?` / `changes?`, `reason` | Turn something the chairman said into something the platform does. Must cite a real boardroom message he actually wrote — or a current co-chair did (their messages carry `from_name`, e.g. "Sam (co-chair)") — within 24 hours. Two shapes: a **rule** directive files reporting-cadence policy; a **scope** directive executes a plan order (`accept`, `cut`, `reopen`, `restage`, `reestimate`, `reword`, `unsequence`) through the same machinery as his own buttons, echoed back threaded to his words. |

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

## Flags: what the platform makes you say

A flag is the platform deciding the chairman needs to hear something. Every kind
is arithmetic or a failed test — never a judgement the CEO makes about whether
the news is worth his time. Raising them **overrides the reporting cadence**: a
run may post for this even when it may not post a status.

| Endpoint | Body | Notes |
|---|---|---|
| `GET /flags` | — | The open flags and whether each has been raised. They also arrive in the context pack every wake, until you say something. |
| `POST /flags/raise` | `flag_id`, `message_id`, `proposal` | Close a flag **by having told him**. Two steps: post the news in the boardroom, then raise with that `message_id` — the message is the proof, which is why saying it in the boardroom does not close the flag on its own. `proposal` is required: a flag without one is anxiety forwarded to his desk. Closing it dismissively is legitimate and expected — *"the cycle boundary caused it, no action needed"* is this verb working. What is not available is leaving it open while reporting that everything is fine. Raising moves it to RAISED, which is where the chairman sees it — what marks it answered is him acknowledging it, and you are told when he does. |

The kinds: `standing_missed`, `deadline_at_risk`, `cycle_missed`, `work_starved`,
`spend_window`, `budget_overrun`, and two that are about the project rather than
its paperwork — `losing_ground` (the measured objective is further from target
than when the window opened) and `self_gated` (nothing is claimable and no cap
is responsible, because every task carries a start time the CEO chose).

> **This section is here because it was missing, and that cost five days.**
> `POST /flags/raise` shipped as a route with no tool exposing it, while the
> context pack told the CEO every wake to *"close each one with flag_raise"*.
> A live CEO carried one unraised flag from 1 September, explained it in the
> boardroom six times, and wrote in its own run: *"the platform has no tool to
> close it, only a post at a real cadence slot."* It was right. An obligation
> the platform can open and an agent cannot discharge is not strict, it is
> broken — and it teaches the agent that the rules are noise.

## Money and approvals

| Endpoint | Body | Notes |
|---|---|---|
| `POST /approvals` | `kind`, `title`, `body`, `if_declined`, `amount_usd?` | `if_declined` is required: what happens if he says no or lets it expire, filed as the case's last line ("What happens without it:") so the Inbox shows it under *if you deny*. Expires per `approval_timeout_days` — **default-deny**. The response says so, and tells the agent to frame requests as recommendations it would defend, not open questions. `kind: owner_decision` is **refused** on a project whose authority dial already gives the CEO the plan, the scope and the date — an approval blocks on a human, and filing one for a decision the chairman delegated is the one way an autonomous project can still stall on him. The refusal names the verb to use instead. `spend`, `legal`, `tool_request` and `assist` are undelegable and unaffected at every setting. |
| `GET /approvals` | — | This agent's pending requests. |
| `POST /asks` | `title`, `body`, `why`, `options?`, `recommendation?`, `answer_by_hours`, `default_action` | **CEO only.** A question to the chairman that the next move depends on. `why` (why it matters now) is required; `options` (up to four one-liners) and `recommendation` are for a choice and left out for a question of fact. The platform files them as one labelled case, posts it with the deadline in the project's timezone, and shows the same case in the Inbox and the email chase. Posts to the boardroom and opens a row with a clock: the cadence never governs it, and it does **not** block — `default_action` is what the CEO does if the window passes, written down before the answer is known. Capped by `ask_window_hours_max` and `max_open_asks`. It sits on his desk beside the pending approvals while it is open — the same shelf, because both are clocked things he answers and neither is gated on the autonomy dial. The janitor marks it answered the moment he says anything in the boardroom after it, chases him halfway through the window, and at `answer_by` lapses it with an **urgent** notice — which is the wake that makes the default happen on a project whose board emptied while it waited. |
| `POST /asks/close` | `ask_id`, `message_id` | Close an ask by having acted on it and said so. `message_id` must be a boardroom message this agent posted — the proof, exactly as with `flags/raise`. An open ask cannot be closed, and no ask closes by going quiet. |
| `GET /asks` | — | This project's live questions and the ones this agent still owes an answer about. |
| `POST /capacity` | `rule`, `to`, `buys`, `costs`, `without` | **CEO only.** A case for MORE of a capacity dial — runs a day or an hour, concurrency, queue depth, the month's money. All three of buys/costs/without are required, because the difference between this and a remark in the boardroom is that this carries the numbers to decide on. Filing it does **not** move the dial and does **not** block: keep working at the cap you have and treat a yes as upside. His yes raises the dial at once (to his number if he changed it), through the ordinary amendment path, and you are told the number in force. Restricted to the rules that govern *how much work the project may do* — the mirror of `directive_file`, which is restricted to rules that limit what you ask of him. Authority, the quality bar, verdict policy and the audit rates are refused: arguing for a lower bar on yourself is not a capacity request. `max_concurrent_runs` is granted differently from the rest and says so — it protects a provider rate limit every project on the box shares. |
| `POST /brief` | `goal`, `standing_orders`, `retired`?, `what_changed` | **CEO only.** Write the chairman's current direction back to him for his Confirm: the whole goal as it stands now, every standing order with its number, and what no longer applies, quoting what he said. Nothing changes until he confirms. Confirming replaces the goal and the standing orders every agent reads first on every run, restarts the chat session onto them, and tells you to rewrite the files, scripts and memories that still say the old thing. A newer draft replaces an unconfirmed one; the brief already in force is 409. `POST /approvals` refuses `kind=brief`. |
| `POST /heads_up` | `title`, `body`, `standing_order` | **CEO only.** Tell the chairman something he *asked* to be told, for him to mark Noted — no Approve, no Deny, no expiry. `standing_order` quotes the order in the brief that asks for it (enough of it to name one order); without one it is refused, because then it is information and belongs in the deliverable or the daily brief. If he must decide, file an approval; if he must do something, an assist. |
| `POST /notes/{id}/answer` | `reply`, `standing` | **CEO only.** Answer a note the chairman wrote on a decision. Every unanswered note is in the context pack's `chairman_notes` on every run until answered. `reply` is the one line he reads under his note. `standing=true` says it applies beyond that one item, and is refused unless a brief written after the note exists — fold it in with `POST /brief` first. |
| `POST /ledger/spend` | `usd`, `note`, `approval_id?` | Spends at or above the threshold **require a granted approval id**, or the booking is refused. Money already spent is always *recorded* — an overshoot books with an audit row and a note to the chairman rather than vanishing. |
| `POST /purchases` | `usd`, `what`, `idempotency_key` | Clears the real-world spend envelope **before** money moves, and reserves the headroom so two overlapping purchases cannot spend it twice. |
| `POST /purchases/{id}/finish` | `ok`, `receipt?` | Books the ledger row. Finishing twice never double-books. |
| `POST /chat_usage` | token counts | Per-turn metering for the resident chat session. Ordinary runs meter at finalize. |

## Capabilities and the human

| Endpoint | Body | Notes |
|---|---|---|
| `GET /credentials` | — | What this role has been granted: names and what each *is*, never values. A row marked `file` holds a **path** in its variable — the platform has already written the file. |
| `POST /credentials/{name}/scope` | `roles` | Re-scope an existing credential across the team — only where `capability_authority` grants it. Never billing, never values. |
| `POST /needs` | `what`, `why` | "I cannot do this without X." Rides the approval machinery **and** lands as a boardroom line, so the chairman fulfils it in one motion: credential stored, roles scoped, need approved. The answer may be a **file** (a service-account key, an SSH key), in which case the variable holds its path. |
| `POST /assists` | `what`, `context` | Hand a captcha, a 2FA prompt, or any human-only step to the chairman. Save session state to the workspace first; his answer rides back verbatim and wakes you. The platform never defeats a security challenge. |
| `POST /accounts` | `service`, `identity`, `owner` | Record an identity the project created in the world. Record-only, so winding down is a reading rather than an archaeology. |
| `GET /accounts` | — | What already exists, before you sign up for it again. |

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
| `POST /artifacts` | `filename`, `content_base64` | Persist a finished deliverable to the artifact store. Agents reach this through the `artifact_put` tool, which takes a workspace **path** and reads the bytes itself — a deliverable must never be base64'd through a model's own output. |
| `GET /artifacts` | — | Everything this project has actually shipped. |
| `GET /artifacts/content` | `ref` | Read one back. A run token is scoped to its own project's artifacts, never the whole store. |

## Bulk data

The sandbox tool `data_fetch(server, tool, arguments, path)` calls a project
data source and writes the **whole** result to a workspace file, returning only
a path, size, digest and a 2 KB preview. Omit `tool` to list what a source
offers. It exists because an MCP tool result otherwise lands in the model's
context in full, billed by the token on that turn and every turn after — and
because counting and summing are things code does exactly and a model does
approximately. The sandbox ships `pandas` and `duckdb` for the processing half.

A data source marked `access: "bulk"` is **not mounted as tools at all**, so its
rows cannot reach a context by accident; `data_fetch` is the only way in. Mark
sources that serve rows as bulk and leave small-lookup sources `inline`.

---

## Writing a role that uses this well

A role is a JSON file in [`roles/`](../roles):

```json
{
  "slug": "engineer",
  "name": "Software Engineer",
  "is_ceo_role": false,
  "description": "Builds and runs everything technical. Decides how, never what.",
  "model": "@balanced",
  "effort": "xhigh",
  "tool_permissions": {"files": true, "bash": true,
                       "data_processing": true, "web": true},
  "secret_scopes": ["github"],
  "system_prompt": "…"
}
```

- `model` / `effort` — per role, so your writer isn't paying engineer prices.
  Write the model as a **class** — `@frontier`, `@balanced` or `@fast` — and
  the role runs on whatever provider pays the project: Claude Sonnet on a
  project an Anthropic key pays, GPT on one an OpenAI key pays, each provider's
  model for the class from its lineup ([PROVIDERS.md](PROVIDERS.md)). Name a
  model (`claude-opus-5-5`, `gpt-6.1-sol`) only when the role needs that one
  model whatever pays — it then needs its provider paid too. A model the
  catalog does not list names its provider first: `openrouter/qwen/qwen3-coder`,
  `custom/llama3.1:8b`; `openai/@fast` is OpenAI's fast model whoever else
  pays. `effort` is yaaf's ladder (`low` … `max`): a class seat runs at its
  model's lowest rung at or above it, a pinned model is held to its own list.
- `harness` (optional) — what drives the model in the sandbox:
  `claude_agent_sdk`, `claude_cli`, `yaaf_loop` — yaaf's own loop on the
  provider's own API, for any API key — `codex_cli`, OpenAI's Codex CLI,
  `gemini_cli`, Google's Gemini CLI, or `opencode`, the OpenCode CLI for any
  provider on the OpenAI-compatible chat API ([RUNTIMES.md](RUNTIMES.md)).
  Absent is the default of whatever credential pays the seat: the Agent SDK
  for Anthropic, yaaf's loop for every other API key, the provider's own CLI
  for a login. A role granting `web` cannot run where nothing can search — no
  search on the harness or the provider, and no web search backend on the
  install (Settings → Web search); a role withholding `files` cannot run on
  Codex, which hands its models a file editor regardless. It changes how a run
  is driven, never what the role may do; a harness that cannot take away a
  grant this role withholds is refused here, and so is a pinned model the
  loop has no price for, because it meters every call by the price.
- `tool_permissions` — `files`, `bash`, `data_processing`, `web`.
  `data_processing` grants the same shell
  `bash` does; it is separate so that a role which has one in order to aggregate
  data with code says so, rather than looking like it builds software. Grant it
  to any role that reads a data source — one that can fetch bulk data but not
  process it is worse off than one that never fetched it.
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
