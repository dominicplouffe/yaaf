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
tier, effort, tool permissions, secret scopes) versioned in a platform-wide
library. A role names how hard, never on what: a project runs it on the model
its own provider lists for the role's tier, so one library serves any vendor's
key. A *project* hires teams by pinning a role version; each team has
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
- **The CEO staffs the project.** It hires roles from your library and stands
  down ones whose job is finished, up to `max_teams`, with no approval
  round-trip — it is spending inside a budget it already has, and hiring buys
  no runs (the daily ration and the monthly wall are the project's, not the
  seat's). Every change lands in your boardroom and on the Overview roster,
  where you can stand a role down or recall one yourself. Two seats are not the
  CEO's to remove: its last `challenge` role, and its last reader when the
  constitution asks for an independent read.
- **A seat's end date lives in the plan.** While planning, the CEO sets
  `stand_down_when` on each team — the requirement or the stage after which
  that role is done — and the platform stands the team down itself when the
  condition comes true. A team still holding open work is skipped and stood
  down on the next pass, so a condition can neither orphan a task nor be lost.
  A seat with no declared end that spends runs and closes nothing for
  `staffing_review_days` trips a flag the CEO has to answer.

**The hierarchy is the message bus.** Employees speak in their team room; leads
also in leadership; the CEO in leadership and the boardroom. Cross-team needs
go through leadership. Posting outside your channels is a 403.

**You are the chairman** — the human the project answers to. You speak only in
the boardroom, and your powers are deliberately few: set the goal and the
rules, accept and cut scope, sign the date, decide approvals, extend a bet
once, kill things, spot-audit, and start/pause/stop. How many of those you
actually exercise is a setting — see [AUTONOMY.md](AUTONOMY.md).

**Other people, if you want them.** Co-chairs hold every one of those powers as
your peers and are recorded as themselves; observers read and decide nothing;
only you decide who decides, and you can hand the chair on. Several people
share one box, each project with its own chair, and the instance admin who runs
the box reads everything and decides nothing it does not chair. Every route
declares which of these it needs, and a test refuses one that does not — see
[AUTH.md](AUTH.md).

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
- **LLM credentials are managed, not pasted.** An administrator creates each
  credential once, by name, under app-wide Settings — an Anthropic API key or
  a Claude subscription token — and projects are *assigned* one. A project
  never stores a billing key of its own (`POST /admin/secrets` refuses
  `kind: billing`), rename and rotation happen in one place for every project
  on the credential, and a credential in use cannot be deleted. A credential
  nothing live is paying with can be **hidden** instead: off every menu, off
  the Settings list, unassignable, and reversible in one click — while the
  finished projects that spent through it keep naming what paid for them.
  Hiding is refused while a live project can still spend on it. Every way of
  founding a project offers the same list: the form's picker, the Founder
  (which names the only credential when there is one, is told to ask which
  when there are several, and shows its choice in the draft panel before
  anything is created), and the MCP `catalog` and `create_project`
  tools, whose preview lists the choices when none was named.
- **Two billing modes, decided by the credential.** An API key meters in USD
  against a monthly cap; a subscription meters in tokens against a monthly
  token cap. The mode is a property of the credential, so assigning one sets
  it, and the dashboard shows whichever unit is native to the project.
- **The plan meter.** A subscription credential shows what Anthropic says is
  left on its plan — the five-hour session window and the weekly windows, the
  same numbers as `/usage` in Claude Code — on the credential in Settings and
  on every project paying with it, with a verdict that says what happens next
  ("session at 100%, runs fail until it resets in 22 min"). It is **harvested first, asked on
  request**: the Claude Agent SDK reports these windows to every agent run, on
  the credential doing the spending, so the meter normally costs nothing at all.
  What runs cannot cover — a credential nothing is running on, which is exactly
  what a full window produces — is a **Check now** button beside the meter,
  which asks Anthropic for that one subscription. Nothing asks on a timer: the poller
  this replaced ran sixty times an hour *per credential from every open browser
  tab* and earned a `429` for it. The button is floored at one call per
  subscription every 15 seconds and honours any throttle, so leaning on it
  returns the stored number rather than a rate limit. The number carries the
  time it was reported.
  **Every press says what it did** — the numbers it found, that they had not
  moved, or why it could not ask. A refused token is named as refused and
  offered the way to replace it, rather than going quiet for fifteen minutes; a
  throttle is named with its wait; the fifteen-second floor is waited out rather
  than reported, because somebody pressing twice wants a number, not a reason
  they cannot have one. Those outcomes were one silent answer for as long as the
  button existed, and a button whose every outcome looks the same is a button
  nobody can trust.
  A window whose reset time has passed reports the rollover rather than the
  percentage it held while it was open — the window at 100% is the one that
  stops the runs, and a stopped run reports nothing, so the number would
  otherwise sit at full long after the plan was clear.
  Shown and never enforced: the token cap stays the platform's own rule. API
  keys have no plan meter and say so.
- **Warn, account, then stop.** At the warn ratio the CEO is told to cut burn.
  Past the budget it records an overrun it must account for, and nothing stops:
  the budget is a plan. At the wall — the budget times `hard_stop_multiple`,
  three by default — the project **pauses itself** and you get an
  email/webhook. Restarting is your decision, not the project's.
- **Billing fails loud.** A missing or wrong-shaped credential is caught by a
  preflight and by the health checklist, not at 9am by a dead run.
- **Approval threshold.** Spends at or above a USD line — and anything legally
  ambiguous — wait for you. Unanswered approvals nag, then decide themselves
  after N days, default-deny.
- **Questions with a clock.** When the CEO wants your read on something it
  could decide itself, it asks in the boardroom — and the question becomes a
  row on your desk carrying when it stops waiting and exactly what it will do
  if you say nothing. It never blocks; the project keeps working. Answer, and
  it acts on that. Say nothing, and at the window it does what it wrote down
  and tells you it did. It sits on your Decisions screen beside the pending
  approvals, which is the right neighbour: both are clocked, both are yours to
  answer, and both resolve whether or not you do. Neither half can be silent:
  the question is on your desk while it is open, and the CEO cannot go back to
  ordinary status posts until it has said what it did about the answer it got
  or the one it did not.
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
  per-run into the sandbox environment. Agents never see values in chat. The
  LLM credential a project pays with is not a project secret at all: it lives
  above projects, is never role-grantable, and reaches a sandbox only as the
  model credential the billing path injects. Slack and GitHub platform tokens
  are decrypted server-side only and never enter a sandbox.
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
  **Activity → Memory**.

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

**And one page that adds it up.** `yaaf receipt PROJECT` prints the project's
whole life in two columns: metered spend on the left, including the runs that
died, and on the right only what the platform itself wrote — requirements it
marked met after running their acceptance tests, external claims it fetched and
hashed, closes it drew at random for you to check. The interesting number is
never the total. It is what the total bought that a cheaper run would not have.

The receipt carries no agent-written text — counts and enum values only — which
is what makes it safe to paste somewhere public, and is asserted by a test
rather than by this sentence. Also on the MCP surface as `receipt`, and over
HTTP as `GET /observe/projects/{id}/receipt`.

## 12. Three tenses, not one

Every screen used to be in the present. The Desk said what needs you *now*, the
Overview said what is true *now*, and reconstructing a weekend meant reading
four tables in Records by hand.

**The briefing — what happened while you were away.** One composed narrative
per project: scope that settled, work that closed, proof the platform verified
or refused, deliverables published, what it cost, what decided itself in your
absence, what the CEO decided on its own authority, what you decided, and what
that leaves waiting on you. It reads in the app under **Inbox → Updates** and
**Activity → What happened**, from `yaaf briefing`, over MCP, and — with
`brief_daily_at` set — in your email every morning. It is deliberately not a
panel on the Overview or the Inbox's Needs-you tab: both of those are the
present tense, and a narrative sitting on top of them restated their own counts
back at them. It has a tab of its own beside them instead.

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
  a real mark, moved only when you say you have read it (`yaaf briefing --seen`,
  or `lifecycle(verb="briefing_seen")`) — reading never moves it, so nothing
  eats a window you did not look at. A first read summarises the whole project
  rather than an empty minute.
- **Silence says which kind it is.** A project that did nothing still ships its
  pulse: open tasks, runs in flight, when it last ran. Nothing wrong and
  nothing happening are different screens.
- **Two marks, two meanings.** The daily email's window runs from the last
  email; the read mark runs from the last time you said you had read one. The
  mail cannot eat the story the next read was going to tell.

**The inbox — only what needs you.** Every notice the platform raises names how
much of you it is asking for, and only two of the four kinds interrupt you: an
**alarm** (the project paused, a promise broken, a billing credential dead)
always, and an **action** (an approval to sign, a verdict only you can record,
scope to accept) up to `mail_max_per_day` in a rolling 24 hours — five by
default. A **notice of something already done** — a deliverable published, a
cycle closed, a decision the CEO took under authority you gave it — is never
mailed at all. It is on the webhook, it is in the audit log, and the briefing
above already reports it *batched*: one briefing window in this project's own
record held "20 deliverables published" and "the CEO exercised 26 decisions" —
two lines there, and forty-six separate emails before this change.

This is the autonomy dial finally reaching the one surface that ignored it. The
briefing has filed every line against the dial since it shipped — a chairman
who handed over scope reads "the CEO handled this itself" instead of a
management report — while the mail path shouted every delegated decision at him
anyway. A decision you delegated is not news. Holding an **action** notice
costs you the interrupt and never the news: everything in that class is a
*wait*, and the brief renders every open wait live in its "waiting on you"
section. Both what was sent and what was held are on the record.

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
- **One list, filed by day.** The Deliverables tab is a file browser: published
  deliverables and working files in the same rows, foldered by the day each
  landed, today's folder open. Published-vs-working, file type and a search
  across names and folders are filters on that one list, and the filters live
  in the URL, so a view you found is a link you can send.
- **Preview reads the file as what it is** — a report as a document, a page as
  a page, a dataset as a table, JSON as a tree, an image as a picture, a PDF in
  the browser's viewer, anything else as text with line numbers. The arrow keys
  walk the filtered list without leaving the reader.
- **It stays fast at tens of thousands of files.** The server keeps one index
  per project, rebuilt at most every few seconds however many screens are
  open, and answers the search, filters, sort and day folders itself; the
  browser receives the folder headers and only the rows it is showing. A
  stale index is served at once and refreshed behind the request, with its
  age in the footer, so a slow disk never stalls the tab.
- **The listing says what it dropped.** Past the per-project cap (50,000
  working files) the newest are kept, not the alphabetically first, and the
  screen says how many are not shown.

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
  project service credential *value* (Slack tokens and webhook signing keys
  included), decrypted at the chairman's request. Shared LLM values stay in
  Settings; restores select a managed credential. The API's default export (`include_values=false`, what the seed
  machinery stores) still strips values and plumbing for cross-install
  sharing. **Project clone** produces a running twin on the same install,
  with copied service credentials and the same shared LLM assignment, optionally
  aimed at a new goal.

## 17. Proving it works

- **The test suite** runs offline on SQLite and on Postgres in CI: an invariant
  suite that attacks every rule over HTTP, chaos tests (crash, reap, retry),
  golden transcript evals that gate prompt edits, and end-to-end runs against a
  mock model. One scenario (`losing_ceo`) fails by doing too *little* — a CEO
  that is behind, has capacity and says nothing does not pass.
- **One paid test.** `tests/test_planning_quality.py` calls a model to grade
  whether a CEO's *judgment* was any good — a question golden diffs are
  structurally blind to, since a project can be perfectly compliant and quietly
  losing. It is the only test in the repo that costs money, runs as its own CI
  job, and everything else stays $0 in tokens.
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
