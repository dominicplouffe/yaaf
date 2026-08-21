# Features — what yaaf actually does

The complete list, grouped by the thing it protects you from. Every entry says
what it is and where you use it. For the rule *numbers* see
[GOVERNANCE.md](GOVERNANCE.md); for the screens see [DASHBOARD.md](DASHBOARD.md).

The organizing principle throughout: **rules live in the server, not in the
prompt.** Agents act only through an authenticated API with short-lived,
per-run tokens, and every rule below is a refusal (HTTP 4xx) or an automatic
state change. An agent can't be argued out of a rule it never held.

---

## 1. The org

**Roles → teams → agents.** A *role* is a reusable template (system prompt,
model, effort, tool permissions, secret scopes) versioned in a platform-wide
library. A *project* hires teams by pinning a role version; each team has
employees and a lead. The CEO is the only agent that plans.

- Nine starter roles ship in [`roles/`](../roles): `ceo`, `engineer`,
  `researcher`, `writer`, `blog-writer`, `marketer`, `gtm`, `sales`, `support`.
- Load them with `yaaf seed-roles roles/`; browse and edit at **Roles** in the
  dashboard, which shows each role's blast radius (which projects run it, at
  which version).
- Editing a role creates a **new version**. Live teams keep the version they
  pinned until you explicitly **repin** them — an edit never silently changes a
  running company.
- **Solo mode** places the CEO as the sole member of the only team, so one
  brain plans *and* executes under byte-identical policy. This is the
  single-agent arm of the experiment, and a legitimate way to run yaaf if you
  just want the guardrails.

**The hierarchy is the message bus.** Employees speak in their team room; leads
also in leadership; the CEO in leadership and the boardroom. Cross-team needs
go through leadership. Posting outside your channels is a 403.

**You are the chairman** — the one human. You speak only in the boardroom, and
your powers are deliberately few: set the goal and the rules, decide approvals,
extend a bet once, kill things, spot-audit, and start/pause/stop.

## 2. Work that can't be invented

- **Only the CEO creates top-level tasks.** Everyone else files a
  **suggestion**, which the CEO sees next planning cycle. That is the whole of
  an employee's authority over the backlog.
- **Every task needs a success test that can actually fail.** Exactly two kinds:
  ```json
  {"type": "command",    "command": "pytest tests/ -q && ruff check ."}
  {"type": "observable", "target":  "https://pypi.org/project/foo/ shows version 0.2.0"}
  ```
  A `command` is executed **by the platform**, in a sandbox, not by the agent
  that wants to pass it. An `observable` names something a stranger could check.
  Prose ("verify it works well") is refused at the API.
- **Queue ceiling.** A hard cap on open top-level tasks. You cannot plan your
  way around a stalled project by adding work — finish something or kill
  something.
- **Subtask ceiling, one level deep.** Only the team lead decomposes, only a
  task it has already claimed, and never a subtask of a subtask.
- **One item at a time.** An agent holding a claimed task cannot claim another.
- **External-evidence floor.** Once the board is big enough, a minimum share of
  open tasks must produce evidence from outside the company. Stops a company
  that only talks to itself.
- **Evidence drought.** With no verified external proof for N hours, the CEO
  may *only* create evidence-producing tasks until proof lands.
- **Hypotheses (bets) with kill dates.** Work hangs off explicit bets. An
  expired bet's work is refused; the bet dies on schedule unless you — and only
  you, once — extend it with a reason.
- **Blocking is first-class.** An agent that can't finish blocks with a reason
  and the lead re-plans, instead of quietly marking it done.

## 3. "Done" means verified

- **Completion requires observed evidence** — what you ran or looked at, and
  what you actually saw. Under ten characters is refused. "Done" is not
  evidence.
- **A parent task can't complete** while its assignments are open.
- **External evidence is verified by the platform, never taken at its word.**
  The agent that does the work also writes the evidence row, so the verifier
  independently: fetches the source URL server-side, hashes and **snapshots**
  it, confirms the domain is not one the project controls, and confirms the
  evidenced event postdates its task. Failures are recorded with a reason;
  transient errors retry and then fail closed. Unverified evidence never counts
  toward the drought or the external floor.
- **Platform-attested evidence skips verification because agents can't forge
  it**: signed webhooks from GitHub, Slack, or any connector you wire up
  (see [INTEGRATIONS.md](INTEGRATIONS.md)).
- **Human spot-audit.** The dashboard deals you a random sample of recent
  "completed" work and evidence to check yourself, and records your verdict —
  because AI reviewers approve nearly everything, and you don't.
- **CEO-judgment metric.** The platform tracks how much of what the CEO called
  progress actually survived verification, and shows it on the Overview.

## 4. Money that can't run away

- **Every run is metered** — including killed and timed-out ones, cache reads
  and writes, and the resident chat session's per-turn usage. Idle wakes are
  suppressed rather than billed.
- **Two billing modes.** `api_key` meters in USD against a monthly cap;
  `subscription` meters in tokens against a monthly token cap. The dashboard
  shows whichever unit is native to the project.
- **Warn, then stop.** At the warn ratio the CEO is told to cut burn; at 100%
  the project **pauses itself** and you get an email/webhook. Restarting is
  your decision, not the company's.
- **Billing fails loud.** A missing or wrong-shaped credential is caught by a
  preflight and by the health checklist, not at 9am by a dead run.
- **Approval threshold.** Spends at or above a USD line — and anything legally
  ambiguous — wait for you. Unanswered approvals nag, then decide themselves
  after N days, default-deny.
- **`yaaf spend-report`** breaks spend down by agent, role, model, and wake
  reason, straight from the database.

## 5. Loops that can't burn a night

- **Wakes per agent per hour** — the runaway-loop brake. Every wake is a paid
  model call.
- **Runs per project per day** — the whole-company activity ceiling.
- **Pair circuit breaker.** Two agents exchanging N messages in one thread with
  no task changing state are talking, not working: the thread **freezes**. Only
  you can unfreeze it.
- **Refusal cap per run.** Past N refusals the run's token is revoked.
  Rephrasing a refused request is not a strategy.
- **Reporting cadence to you.** A CEO run is stateless and can't remember how
  often it has reported. Set an interval (and optionally a local time window
  and timezone) and unprompted status posts to the boardroom are *refused*
  outside it. Replying to something you actually said is always allowed and
  never counts as a status.
- **Idle heartbeats are suppressed.** With an empty board and no live bet, the
  planning heartbeat doesn't buy a full-price run to conclude "nothing to do".

## 6. Containment

- **A fresh Docker container per run**: no privileges, resource caps, the
  project workspace mounted, and a short-lived token scoped to that one run.
- **The network is open.** The chairman's decision (docs/AUTONOMY.md): the
  per-role egress allowlist made every capability a firewall ticket, so it was
  removed. Containment is the fresh container, role-scoped secrets, budgets,
  loop brakes and the audit trail — not a domain list.
- **Secrets are envelope-encrypted** (Fernet), scoped to roles, and injected
  per-run into the sandbox environment. Agents never see values in chat, and
  `billing`-kind secrets refuse role grants entirely. Slack and GitHub platform
  tokens are decrypted server-side only and never enter a sandbox.
- **Idempotent side effects.** Before any external action (a PR, a deploy, a
  message to an outside system) an agent records it with a stable idempotency
  key. After a crash-and-retry the ledger says the prior attempt completed — so
  no duplicate tweet, PR, or payment.
- **`subprocess` sandbox mode exists for dev and CI only.** It provides no
  isolation and must be configured explicitly.

## 7. Memory with a curator

- **Two scopes**: project memory (the CEO curates) and team memory (any member
  writes, only the lead compacts or forgets).
- **Caps are refusals, never silent eviction.** At the cap, new entries are
  refused until the curator compacts or forgets one. The scope's curator is
  warned at the warn ratio, so it curates on a wake it was having anyway
  instead of hitting the wall mid-write.
- **Explicit verbs**: `supersede` (1→1, replace an entry), `compact`
  (many→1, fold entries together), `forget`. All recorded.
- **Searchable** by agents at run time, and readable by you under
  **Records → Memory**.

## 8. Everything on the record

Postgres holds every message, task transition, run, dollar, rule enforcement,
decision, and configuration change. The dashboard surfaces it as:

- **Runs** — every model call: which agent, which team, why it woke, what it
  cost, its full trace.
- **Messages** — every channel, including ones you can read but not speak in.
- **Memory** — what the company chose to remember.
- **Decisions** — everything you decided, and everything that decided itself
  (expired approvals, auto-pauses, killed bets, frozen threads).
- **Amendments** — every constitution change with the reason you gave, plus
  mandatory disclosure to the CEO in its next context pack.
- **Audit log** — including every refusal, so a refusal storm names the wall:
  which agent, which rule, and what to say instead.

## 9. Talking to your company

- **The Boardroom** is a chat, not a ticket queue. Every running project keeps
  a **resident CEO chat session**, so replies land in seconds — typing
  indicator and all. Background work still runs through the governed queue, and
  chatting never consumes its caps.
- **The same session answers from Slack** via Socket Mode (no public URL
  needed). The Postgres bus stays canonical; Slack is a viewport plus exactly
  one inbound path — you, in the mapped boardroom channel.
- **Directives.** A rule you state in chat becomes a rule the platform holds:
  the CEO files it as a directive citing your actual message, and the platform
  enforces it from the next call. Scope is limited to your reporting
  preferences — everything else in the constitution is yours to change directly.

## 10. Deliverables and files

- **Artifacts** are finished deliverables persisted to the artifact store
  (local disk, or any S3-compatible store — MinIO, R2, AWS).
- **Working files** — the Files view lists and serves the agents' workspace
  read-only, so you can see work in progress, not just what shipped.
- **Preview and download** from the Deliverables tab.

## 11. Setup that can't silently fail

- **Health checklist** on each project's Overview names anything misconfigured
  — dead billing, a credential that isn't a URL, an untested data source, a
  team running an outdated role version — in plain language, with a fix link. A
  healthy project shows nothing at all.
- **Data sources prove themselves.** An MCP server is a one-form add (name,
  URL, roles, Save): the URL is validated at the door, you can run a live check
  against it, and the granted roles' sandboxes mount it as real tools.
- **Project export** produces a portable *setup file* — goal, teams,
  constitution, config, and the *names* of credentials — with secrets and
  plumbing stripped. **Project clone** produces a running twin on the same
  install, credential values and all, optionally aimed at a new goal.

## 12. Proving it works

- **194 tests** run offline on SQLite and on Postgres in CI: an invariant suite
  that attacks every rule over HTTP, chaos tests (crash, reap, retry), golden
  transcript evals that gate prompt edits, and end-to-end runs against a mock
  model.
- **Mock runtime** (`runtime: mock`) rehearses a whole company — wakes, tasks,
  refusals, the lot — for $0, so you can test a constitution before spending a
  cent.
- **`yaaf experiment`** runs the org-vs-single-agent experiment described in
  [M2_PROTOCOL.md](M2_PROTOCOL.md): two arms under identical governance, blind
  bundles, judge-model plus human rubric passes, and a pre-registered decision
  rule.

---

## Where to go next

| You want to… | Read |
|---|---|
| Create your first project | [CREATING_A_PROJECT.md](CREATING_A_PROJECT.md) |
| See worked setups | [../examples/](../examples/) |
| Know every screen | [DASHBOARD.md](DASHBOARD.md) |
| Tune the rules | [GOVERNANCE.md](GOVERNANCE.md) |
| Connect Slack/GitHub/MCP/webhooks | [INTEGRATIONS.md](INTEGRATIONS.md) |
| Deploy and operate it | [DEPLOYMENT.md](DEPLOYMENT.md) |
| Script it | [CLI.md](CLI.md) |
| Write a role | [AGENT_API.md](AGENT_API.md) |
