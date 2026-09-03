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

- A starter library ships in [`roles/`](../roles), in four families: the
  general-purpose set (`ceo`, `engineer`, `researcher`, `writer`, `tutor`,
  `adversary`, `operator`, `data-analyst`, `blog-writer`, `marketer`, `gtm`,
  `sales`, `support`), a markets
  desk
  (`cio`, `equity-analyst`, `quant-researcher`, `market-data-engineer`,
  `market-news-analyst`, `macro-analyst`, `technical-analyst`, `risk-manager`,
  `portfolio-manager`, `compliance-officer`, `finance-writer`), a mobile
  game studio (`game-analyst`, `game-developer`, `game-ux-designer`,
  `game-graphic-designer`), and a data-science bench that runs as a chain —
  `data-scientist` finds it, `data-skeptic` tries to kill it, and
  `exec-communicator` turns what survives into a deck an executive finishes.
- Load them with `yaaf seed-roles roles/`; browse and edit at **Roles** in the
  dashboard, which shows each role's blast radius (which projects run it, at
  which version).
- Editing a role creates a **new version**. Live teams keep the version they
  pinned until you explicitly **repin** them — an edit never silently changes a
  running project.
- **Solo mode** places the CEO as the sole member of the only team, so one
  brain plans *and* executes under byte-identical policy — a legitimate way
  to run yaaf if you just want the guardrails.

**The hierarchy is the message bus.** Employees speak in their team room; leads
also in leadership; the CEO in leadership and the boardroom. Cross-team needs
go through leadership. Posting outside your channels is a 403.

**You are the chairman** — the one human. You speak only in the boardroom, and
your powers are deliberately few: set the goal and the rules, accept and cut
scope, sign the date, decide approvals, extend a bet once, kill things,
spot-audit, and start/pause/stop. How many of those you actually exercise is a
setting — see [AUTONOMY.md](AUTONOMY.md).

**Three project shapes**, chosen once at creation because the rules differ by
shape. **Explore** chases a goal with no fixed end (bets and evidence, no
requirements). **Deliver** ships one thing and finishes. **Operate** is a
Deliver that starts again on a rhythm.

## 2. Work that has a terminus

Outside Explore mode the root object is not the task, it is the
**requirement** — a thing that must be true at the end.

- **The CEO proposes; you accept.** A proposed requirement counts as nothing
  until you accept it, which is what keeps the denominator of the forecast out
  of the hands of the party the forecast grades. (An autonomous CEO may hold
  this verb itself — see [AUTONOMY.md](AUTONOMY.md) — and every acceptance is
  still recorded and disclosed.)
- **Nothing but the platform writes `met`.** There is no agent tool that marks
  a requirement true. The strongest form of "done is never self-graded" is that
  the verb does not exist.
- **A task that serves no requirement is refused.** Work that makes nothing
  true is how a project stays busy without finishing.
- **R-00, the plan gate.** A Deliver or Operate project starts owing a plan.
  Until scope exists, is staged, is estimated, and *fits the money*, no work
  may be created. The gate is arithmetic in the server, not a judgement: it
  checks that a plan exists, is ordered, is estimated, and adds up.
- **Estimates are scored, not trusted.** The CEO's `est_runs` is compared with
  what the work actually cost, and future plans are read at that ratio. Writing
  smaller numbers buys one round and is then priced in — and the first estimate
  is kept forever, so lowballing cannot be laundered by revising afterwards.
- **Stages and dependencies.** Requirements sit in ordered stages; work in a
  stage beyond the open one is refused. Dependency edges are append-only and
  kept acyclic by a check on insert, with the critical path computed
  server-side.

## 3. A delivery date the project cannot quietly break

- **The commitment is a row the platform owns.** The CEO proposes; it never
  writes the date — `deliver_by` is stamped from the platform's own projection,
  and you may replace it with your own when you accept.
- **The projection refreshes every janitor tick** from measured pace, remaining
  estimated runs, and the estimate-bias multiplier. No route writes it from
  input.
- **Two freezes at acceptance make drift detectable**: the constitution version
  the promise was made under, and the requirement keys it covered.
- **At risk means scope freezes.** When the projection lands past the promise,
  the status moves itself, new scope is refused for the CEO, and you are told —
  with the arithmetic in front of you and two levers: cut something, or move
  the date with a reason.
- **Re-validation has three triggers**: the rules moved, the scope moved, or
  enough runs died against the plan (`commitment_failure_reforecast`).

## 4. A rhythm, and a standing meeting

- **Operate projects run in cycles** — daily, weekly, monthly or quarterly, on
  a day you choose, read in the constitution's one timezone. Each roll closes
  the cycle, carries unfinished scope forward, reopens the plan gate, and hands
  the CEO the last three cycles' narratives verbatim. Two consecutive cycles
  with nothing accepted pause the project: a rhythm nobody dances to is a bill,
  not a project.
- **The review is opened by the janitor with zero tokens**, on schedule,
  regardless of budget or an empty board — because a review with nothing on the
  board is the one you most need. It carries the platform's frozen numbers;
  the CEO files its variance narrative on a *reserved* governance run.
- **Six levers, each recorded with your reason**: cut scope, add scope, reword,
  move the date, raise the budget, accept the slip. Each is disclosed to the
  CEO and shown at the next review beside what it bought.

## 5. Work that can't be invented

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
  open tasks must produce evidence from outside the project. Stops a project
  that only talks to itself.
- **Evidence drought.** With no verified external proof for N hours, the CEO
  may *only* create evidence-producing tasks until proof lands.
- **Hypotheses (bets) with kill dates.** Work hangs off explicit bets. An
  expired bet's work is refused; the bet dies on schedule unless you — and only
  you, once — extend it with a reason.
- **Blocking is first-class.** An agent that can't finish blocks with a reason
  and the lead re-plans, instead of quietly marking it done.

## 6. "Done" means verified

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

## 7. Money that can't run away

- **Every run is metered** — including killed and timed-out ones, cache reads
  and writes, and the resident chat session's per-turn usage. Idle wakes are
  suppressed rather than billed.
- **Two billing modes.** `api_key` meters in USD against a monthly cap;
  `subscription` meters in tokens against a monthly token cap. The dashboard
  shows whichever unit is native to the project.
- **Warn, then stop.** At the warn ratio the CEO is told to cut burn; at 100%
  the project **pauses itself** and you get an email/webhook. Restarting is
  your decision, not the project's.
- **Billing fails loud.** A missing or wrong-shaped credential is caught by a
  preflight and by the health checklist, not at 9am by a dead run.
- **Approval threshold.** Spends at or above a USD line — and anything legally
  ambiguous — wait for you. Unanswered approvals nag, then decide themselves
  after N days, default-deny.
- **`yaaf spend-report`** breaks spend down by agent, role, model, and wake
  reason, straight from the database.

## 8. Loops that can't burn a night

- **Wakes per agent per hour** — the runaway-loop brake. Every wake is a paid
  model call.
- **Runs per project per day** — the project-wide activity ceiling.
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

## 9. Containment

- **A fresh Docker container per run**: no privileges, resource caps, the
  project workspace mounted, and a short-lived token scoped to that one run.
  The image carries python (pandas, duckdb, playwright) and Chromium — the
  browser lane for services with no API. Captchas, 2FA and human-only steps
  are never defeated: the agent saves its session state and files an assist,
  and the chairman's answer wakes it. Every identity created in the world
  lands on the account registry, so winding down is a reading, not an
  archaeology.
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

## 10. Memory with a curator

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

## 11. Everything on the record

Postgres holds every message, task transition, run, dollar, rule enforcement,
decision, and configuration change. The dashboard surfaces it as:

- **Runs** — every model call: which agent, which team, why it woke, what it
  cost, its full trace.
- **Messages** — every channel, including ones you can read but not speak in.
- **Memory** — what the project chose to remember.
- **Decisions** — everything you decided, and everything that decided itself
  (expired approvals, auto-pauses, killed bets, frozen threads).
- **Amendments** — every constitution change with the reason you gave, plus
  mandatory disclosure to the CEO in its next context pack.
- **Audit log** — including every refusal, so a refusal storm names the wall:
  which agent, which rule, and what to say instead.

## 12. Three tenses, not one

Every screen used to be in the present. The Desk said what needs you *now*, the
Overview said what is true *now*, and reconstructing a weekend meant reading
four tables in Records by hand.

**The briefing — what happened while you were away.** One composed narrative
per project: scope that settled, work that closed, proof the platform verified
or refused, deliverables published, what it cost, what decided itself in your
absence, what the CEO decided on its own authority, what you decided, and what
that leaves waiting on you. It reads on the Desk (every live project), on each
project's Overview, from `yaaf briefing`, and — with `brief_daily_at` set — in
your inbox every morning.

- **Composed from the record at zero tokens.** No run, no wake, no model call.
  Which is why it still arrives on a project the platform paused for an
  exhausted budget, on one with an empty board, and on one that is broke — the
  three mornings you most need to hear something, and the three a
  model-written status could not be afforded. Same reasoning as the janitor
  opening a review with the budget gone.
- **It grades nothing.** Every line counts something the platform already wrote
  down under its own rules: a requirement is met because the platform ran the
  test, evidence counts because the verifier fetched it. R-00 and standing
  requirements are held out of "settled true" on exactly the grounds velocity
  holds them out of pace — a plan gate lifting is not scope landing, and an
  obligation met every cycle by design is not progress.
- **The window is yours and has visible edges.** "Since you were last here" is
  a real mark, moved only when you acknowledge the panel — reading it never
  moves it, so a tab left open overnight does not eat your weekend. A first
  visit summarises the whole project rather than an empty minute.
- **Silence says which kind it is.** A project that did nothing still ships its
  pulse: open tasks, runs in flight, when it last ran. Nothing wrong and
  nothing happening are different screens.
- **Two marks, two meanings.** The daily email's window runs from the last
  email; the panel's runs from the last time you looked. The mail cannot eat
  the story the screen was going to tell.

**The agenda — what it is going to do next.** A fourteen-day strip on each
project's Overview (and `yaaf agenda`): the wall-clock wakes, the standing
review, the cycle roll, bet kill dates, approval and spot-check expiries, the
budget reset, and the promised delivery date with the platform's own projection
beside it. Recurring wakes are drawn as *rhythms* — one fact with a cadence and
a next firing — rather than fourteen copies of one event. Every time is read in
the constitution's one timezone from the same heartbeats the dispatcher reads,
so a wake it draws is a wake that will run. And it explains its own blanks: why
the next review is not scheduled while one sits open, that nobody is on a
clock, that a paused project has no schedule at all.

## 13. A fire drill, before you spend a cent

The riskiest moment in this product is the one before the first real run: you
write a goal, hire a crew off a wizard, hand over an API key and a budget, and
go to bed. (The drill is the second half of that sentence — is the
configuration survivable. The first half, writing the goal and hiring the crew,
is [§18](#18-the-founding-conversation).) Until now the only preflight was a static checklist — is the billing
credential shaped like a credential, is a team on an outdated role version —
and a static check cannot answer the question you are actually asking.

**`yaaf rehearse spec.json`**, and a button on the New-project form's
**Review & create** step, runs your spec as a project for nothing. It is the
same machinery CI uses to drive a whole project for $0 — the mock runtime, the
dispatcher, the janitor, the policy engine, every refusal — pointed at your
configuration instead of a scripted scenario. It needs no deployment and no
key: `yaaf rehearse examples/01-your-first-hour.json` works on a fresh
checkout.

Four things come back:

- **The walk.** Roles resolve, teams hire, the CEO wakes, scope is proposed,
  the plan gate does its arithmetic, the board opens, work is claimed and
  closed with evidence, a requirement settles true — each with the beat it
  happened on, or the reason it did not. A configuration that fails does not
  report "it didn't work"; it reports *where it stopped*, in the platform's own
  words. A role your library cannot resolve, a schedule naming nobody, a
  project with no team in it: each is named, with what to do about it.
- **How often it will stop and wait for you.** The drill plays the chairman and
  counts every time it had to — accept scope, sign the date, judge an
  observable, decide a spend. That number is the autonomy dial made concrete,
  and nothing else in the product could produce it before the money was spent.
  Move one switch and run it again to see the difference.
- **What it costs and what paces it.** The plan in runs against your caps, and
  `capacity.py`'s four limits — because a daily total is not the only wall, and
  a crew whose critical path runs through one agent's hourly wake ceiling looks
  perfectly healthy at rest. On a box that has never priced a run the report
  gives the **break-even**: what a run must cost for the plan to fit. One
  number you can check against a bill you have already seen, instead of a total
  invented from a price nobody measured.
- **What it did not exercise**, in plain sight. Every test the drill's crew
  writes is an `observable`, so no command out of a spec file is ever run;
  evidence carries no URL, so the verifier never reaches the network; no
  credential is read and no socket is opened. It asserts its database is the
  throwaway SQLite file it created and refuses to start otherwise, and the API
  route runs it in a subprocess — the engine is a process-global, and a drill
  inside the server would re-point the server.

**The one thing it changes about your rules.** Seven values are relaxed for the
duration — the per-agent and per-project hourly ceilings, the daily ceiling,
concurrency, the CEO's heartbeat, and the dispatcher's batching windows —
because every one of them measures *wall-clock time*, and a drill compresses a
fortnight into seconds. Left in force they bind on the first beat and report a
stall the compression caused. They are relaxed, the report says which and why,
and their real effect is computed from your own numbers as arithmetic rather
than guessed at by simulation. Every other rule you wrote is in force exactly
as written.

**What it cannot tell you** — and says so, at the bottom of every report — is
whether a real model does the job well. The crew is competent and dull by
construction, so a clean drill is evidence about your *configuration* and no
evidence at all about your *project*.

## 14. Talking to your project

- **The Boardroom** is a chat, not a ticket queue. Every running project keeps
  a **resident CEO chat session**, so replies land in seconds — typing
  indicator and all. Background work still runs through the governed queue, and
  chatting never consumes its caps.
- **The same session answers from Slack** via Socket Mode (no public URL
  needed). The Postgres bus stays canonical; Slack is a viewport plus exactly
  one inbound path — you, in the mapped boardroom channel.
- **One connection, every project.** Slack tokens are stored once, app-wide,
  and every project inherits them — per field, so a project in a different
  workspace can override just the token it needs. Creating a project makes its
  boardroom for you (`{slug}-boardroom`, private, with you invited) and tells
  you the name; **Create missing channels** on the project's Slack screen makes
  the rest (`{slug}-leadership`, one room per team) when you want them, so a new
  project does not cost your workspace six channels nobody has spoken in. The
  bot token still never enters a sandbox, shared or not.
- **Directives.** A rule you state in chat becomes a rule the platform holds:
  the CEO files it as a directive citing your actual message, and the platform
  enforces it from the next call. Scope is limited to your reporting
  preferences — everything else in the constitution is yours to change directly.

## 15. Deliverables and files

- **Artifacts** are finished deliverables persisted to the artifact store
  (local disk, or any S3-compatible store — MinIO, R2, AWS).
- **Working files** — the Files view lists and serves the agents' workspace
  read-only, so you can see work in progress, not just what shipped.
- **Preview and download** from the Deliverables tab.

## 16. Setup that can't silently fail

- **Health checklist** on each project's Overview names anything misconfigured
  — dead billing, a credential that isn't a URL, an untested data source, a
  team running an outdated role version — in plain language, with a fix link. A
  healthy project shows nothing at all.
- **Data sources prove themselves.** An MCP server is a one-form add (name,
  URL, roles, Save): the URL is validated at the door, you can run a live check
  against it, and the granted roles' sandboxes mount it as real tools.
- **Project export** downloads the whole project as one JSON file — goal,
  teams and their schedules, constitution, config, data sources, and every
  credential *value* (billing key, Slack tokens, webhook signing keys
  included), decrypted at the chairman's request so a restore re-enters
  nothing. The API's default export (`include_values=false`, what the seed
  machinery stores) still strips values and plumbing for cross-install
  sharing. **Project clone** produces a running twin on the same install,
  credential values and all, optionally aimed at a new goal.

## 17. Proving it works

- **626 tests** run offline on SQLite and on Postgres in CI: an invariant suite
  that attacks every rule over HTTP, chaos tests (crash, reap, retry), golden
  transcript evals that gate prompt edits, and end-to-end runs against a mock
  model.
- **Mock runtime** (`runtime: mock`) swaps the Agent SDK for scripted
  behaviours, which is how CI drives a whole project — planning, decomposition,
  claims, completions, evidence, every governance trip — for $0. Behaviours are
  registered in code, and a mock project you start yourself still reads its
  context and exits: none is registered for it. The one thing that does ship a
  crew is the [fire drill](#13-a-fire-drill-before-you-spend-a-cent), which
  registers a generic one for the length of a rehearsal and clears it after.
  Still the test harness rather than a demo mode — but now the test harness is
  pointed at your configuration as well as at ours.

---

## 18. The founding conversation

Every other part of this product has agents behind it. A CEO proposes a
plan; teams claim work; an adversary attacks the project's own output; the
platform refuses a success test it cannot check. The founding moment had a
form — and the form asks one person, alone, to hand-author a goal of several
thousand words, an org chart, and a constitution of fifty-odd rules. That is
not a form problem. It is an authoring problem wearing a form.

**+ New project** now opens a fork rather than a form, and its recommended
side — **Describe it, and yaaf drafts it** — puts an agent there instead. You write a paragraph; the
**Founder** replies with a whole project — goal prose, shape, roster, and the
few constitution rules it can defend — and then says what it assumed, what it
added that you never asked for, and what it still needs to know. You argue. It
revises. The draft is the same `project_setup` bundle an export produces, so
**Review & create** fills the ordinary New-project form with it and everything
downstream — the fire drill included — is unchanged.

It proposes; you decide. That is the same division the CEO works under, applied
one step earlier.

Four things make it more than a prompt:

- **A written standard for goals.** [GOAL_GRAMMAR.md](GOAL_GRAMMAR.md) names
  the ten things a goal has to contain — a finish line and the record that
  settles it, who the output has to be right for, the body of practice it is
  judged against, the metrics that explicitly do not count, the bias marked as
  a bias, what you will refuse to do yourself, what is owed if it fails. The
  Founder grades its own draft against that file before showing you anything,
  and the grader reads the same copy. A goal stays prose; the grammar is what
  the critic argues from, never a template to fill in.
- **A roster derived from the goal.** Not a catalogue to browse: what does this
  work actually require, who in the library does that, and what is the smallest
  team that covers it. It will argue for fewer people than you asked for, and
  it tells you up front which credentials the roster will demand — before a run
  fails at 3am asking for one.
- **It runs the drill on its own draft.** §13 costs nothing, so there is no
  reason to hand you a project that cannot get off the ground.
- **The conversation becomes the briefing.** Everything you explained and
  argued through is handed to the new CEO as its day-one context. Being asked
  to say it all again to a fresh agent is exactly the form behaviour this
  replaces.

It runs on a **platform key** — set once, product-wide, never handed to a
project's agents. Every project still pays its own way with its own key, and
the fire drill still costs nothing whether or not a platform key exists.

## Where to go next

| You want to… | Read |
|---|---|
| Create your first project | [CREATING_A_PROJECT.md](CREATING_A_PROJECT.md) |
| See worked setups | [../examples/](../examples/) |
| Know every screen | [DASHBOARD.md](DASHBOARD.md) |
| Tune the rules | [GOVERNANCE.md](GOVERNANCE.md) |
| Decide how much the CEO does alone | [AUTONOMY.md](AUTONOMY.md) |
| Connect Slack/GitHub/MCP/webhooks | [INTEGRATIONS.md](INTEGRATIONS.md) |
| Deploy and operate it | [DEPLOYMENT.md](DEPLOYMENT.md) |
| Script it | [CLI.md](CLI.md) |
| Write a role | [AGENT_API.md](AGENT_API.md) |
