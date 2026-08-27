# yaaf — Yet Another Agent Framework

**Run a small AI company on your own hardware — with rules the agents can't
talk their way out of, and a delivery date they can't quietly break.**

yaaf runs a company of AI agents: a **CEO** that plans and prioritizes, teams of
**role-based agents** that do the work (analysts, engineers, writers,
researchers…), and **you as chairman** — the one human, with a small set of real
powers. It keeps working while you're away: agents wake on schedule or when
something needs them, do one unit of work, and go back to sleep. Everything runs
on your own machine with Docker Compose, and you watch and steer from a web
dashboard.

<img src="docs/screenshots/cockpit-overview.png" alt="The chairman's cockpit: is it working, the deliverable, the plan, spend against the cap broken down by team, the setup health line, and the org roster">

## The problem

Anyone who has left an autonomous agent running for more than an afternoon has
met the failure modes:

- It marks its own work "done" without anything actually being done.
- It invents busywork and reports it as progress.
- Two agents thank each other in a loop, at full API price, for hours.
- It spends your budget while you sleep.
- You come back and can't reconstruct what happened or why.
- And the one nobody solves: you cannot get a straight answer about **when it
  will be finished**.

The usual fix is better prompts. But a prompt is a *suggestion* — a
sufficiently confused (or sufficiently clever) model can always talk itself
around one.

**yaaf's core idea: the rules live in the server, not in the prompt.** Agents
can only act through an authenticated API using short-lived, per-run tokens, and
every rule is enforced as an API refusal or an automatic state change. An agent
cannot be talked out of rules it never held. When an agent tries to break a
rule, it doesn't get a stern reminder — it gets a 403.

## Isn't this just a Claude Skill?

Fair question, and the honest answer starts with: **yaaf runs your skills.** A
role's `tool_permissions.skills` is an allowlist handed straight to the Agent
SDK, so a role gets exactly the skills you grant it and nothing it wrote for
itself. They are not alternatives. A skill is a *capability* — how to do a thing
well. yaaf is the *company* that decides which capability to point at what,
checks it actually got done, pays for it out of a capped budget, and keeps the
receipt.

**So don't use yaaf when a skill will do**, and that is most of the time:

- You are there, watching, and will read the result before it matters.
- It finishes in one sitting.
- Nothing irreversible happens — no money moves, no PR opens, nobody is emailed.
- "Did it work?" is a question you can answer by looking.
- Nobody will ask you six weeks from now how you know.

**Reach for something like yaaf when one of these is true:**

- **It has to run while you're away.** Nightly, weekly, or whenever your app
  posts an event — for weeks, not for a session.
- **"Done" must not be self-graded.** Somebody other than the author has to
  check, and "the model said so" is not going to survive contact with reality.
- **Money has to stop by itself.** Not "I'll keep an eye on it."
- **You'll have to reconstruct it later** — for a client, a boss, an auditor, or
  yourself in three months.
- **You want agents to disagree.** One agent checking its own work has the same
  blind spots in both seats.

Concretely, four ways to run agents and what each is actually for:

| | Claude Skill / Code session | Scheduled agent (a cron'd prompt, a task) | Agent library (CrewAI, AutoGen, LangGraph) | **yaaf** |
|---|---|---|---|---|
| **What it is** | A capability you invoke | The same, on a timer | A toolkit for wiring agents into your app | A platform you deploy and chair |
| **Decides what's next** | You do | You did, in advance | Your code does | A CEO agent, from a goal and a plan you accepted |
| **"Task complete"** | The model says so | The model says so | The model says so | The **platform** runs the test, or you spot-audit it |
| **Budget ceiling** | You watch the meter | You watch the meter | You watch the meter | Metered per run; warns, then **pauses the project** |
| **Lifespan** | One session | One run at a time | While your script runs | Weeks — wakes, schedules, cycles, webhooks |
| **Memory across runs** | Whatever the product gives you, plus your own files | Whatever you pass in | Yours to build | Scoped, capped, curated; caps are refusals, never silent eviction |
| **After a crash mid-action** | You notice, and redo it | You notice, and redo it | Yours to build | Lease reaped, side-effect ledger stops the double-fire |
| **The record** | Your scrollback | Your logs | Your logs | Every message, run, dollar, decision and refusal |
| **More than one agent** | — | — | Yes, as you code it | Teams with charters that conflict on purpose, on a governed bus |

None of that is about capability. A good model with a good skill will out-write
yaaf's agents on any single task. **The difference is governance and duration** —
and if neither of those is your problem, you don't have a yaaf-shaped problem.

Note the "yours to build" cells. With a library you *can* reach most of this
column; the work is a queue, a policy layer, a verifier, a ledger, an audit
trail and a UI to watch it from. That is a reasonable thing to build if it is
your product. yaaf is that thing, already built, when it isn't.

The [examples](examples/) are organised around exactly this: two you could do in
a Claude session, so you learn the machinery, and nine that name the specific
thing which breaks without it.

## The second idea: work that has a terminus

A task list is a pipeline with no end. So outside open-ended mode the root
object isn't the task — it's the **requirement**: a thing that must be *true*
at the end, carrying a test the platform runs itself.

- **The CEO proposes; you accept.** Scope is not scope until you say so — which
  keeps the denominator of the forecast out of the hands of the party the
  forecast grades.
- **Nothing but the platform writes "met".** There is no agent tool that marks
  a requirement true. The strongest form of "done is never self-graded" is that
  the verb does not exist.
- **A task that serves no requirement is refused.** Work that makes nothing
  true is how a company stays busy without finishing.
- **The estimate is scored, not trusted.** The CEO's run estimate is compared
  with what the work actually cost, and its next plan is read at that ratio.
  Writing small numbers buys one round and is then priced in.

<img src="docs/screenshots/plan-and-promise.png" alt="The Plan tab: the commitment at risk with its two levers, pace and waste, the burn-up against the committed and projected dates, and requirements in stages with dependencies">

## And a delivery date the company cannot quietly break

The CEO proposes a date; it never **writes** one. The platform stamps it from
its own projection of measured pace, and re-projects it on every tick. When the
projection lands past the promise, the commitment moves itself to *at risk*,
**new scope freezes**, and there are exactly two honest ways out: cut something,
or reset the date — with a reason, and only ever to what the arithmetic now
says. Who pulls those levers is the autonomy setting; that they are the only two
is not.

The guarantee isn't that the date holds. Nobody can promise that. It's that when
it stops holding, the company cannot quietly add work and hope.

## What that looks like in practice

- **Money can't run away.** Every run is metered (even killed ones). At 80% of
  budget the CEO gets warned; at 100% the project pauses itself and you get an
  email. Real-world spending has its own hard envelope, in every billing mode.
  Circuit breakers kill message loops and refusal storms before they cost
  anything.
- **"Done" means verified.** `command` tests are run by the *platform*, in a
  sandbox, not by the agent that wants them to pass. Claims of real-world
  impact only count after the platform fetches, checks and snapshots them — or
  when they arrive through connectors agents can't forge (signed webhooks from
  GitHub, Slack, or anything you wire up).
- **Agents are contained, not trusted.** Each run happens in a fresh Docker
  container with the open internet — the chairman's call: capability should
  never be a firewall ticket. Secrets are encrypted, scoped to roles, and
  injected per run; agents never see the values in chat. Captchas and 2FA are
  handed to a human, never defeated.
- **Everything is on the record.** Every message, task transition, run, dollar,
  and rule enforcement is in Postgres and visible in the dashboard. The
  dashboard also deals you random samples of recent "completed" work to
  spot-check — because AI reviewers approve nearly everything, and you don't.
- **The rules are yours, and only yours.** Each project runs under a rulebook
  (the "constitution") the agents cannot edit, argue with, or talk their way
  around. You change any of it whenever you like and it binds from the next
  agent call; every change is recorded with the reason you gave and disclosed
  to the CEO.

<img src="docs/screenshots/work-and-proof.png" alt="The Work tab: the platform's sampled close waiting on the chairman, the read-only board, and every piece of evidence with its verification status including a failed check">

## How much you actually do is a setting

Two presets on the New-project form, each nothing but five constitution
switches: **Supervised** (you accept scope, sign the date, judge unverifiable
closes) or **Autonomous** (the CEO does all three and tells you afterwards).

What does *not* move at any setting: the platform still verifies everything it
can, still refuses to quietly trust what it can't, still meters and caps every
dollar, still records everything, and still hands security challenges to you.
An autonomous CEO is not a less-audited CEO — it's one with more buttons of its
own. → **[docs/AUTONOMY.md](docs/AUTONOMY.md)**

## Three shapes of company

Chosen once at creation, because the rules differ by shape.

| | What it is | Example goal |
|---|---|---|
| **Explore** | Chase a goal with no fixed end. Bets, proof, and your call on when it has earned its keep. | "Find one defensible idea in 30 days." |
| **Deliver** | Ship one thing, then stop. You accept what done means; the platform proves it. | "An initiation pack on five names." |
| **Operate** | A Deliver that starts again on a rhythm, and gets better each cycle. | "A brief before the open, every trading day." |

An Operate cycle roll closes the cycle, carries unfinished scope forward,
reopens the plan gate, and hands the CEO the last three cycles' narratives
verbatim — so "learn from last time" is a step the platform makes the company
take, not a line in a prompt.

## The standing meeting

Once a week (by default) the janitor opens a **review** — with zero tokens,
regardless of budget or an empty board, because a review with nothing on the
board is the one you most need. It carries the platform's own frozen numbers;
the CEO files its variance narrative on a reserved governance run. Then you pull
levers: cut scope, add scope, reword, move the date, raise the budget, accept
the slip. Each is recorded with your reason and shown at the *next* review
beside what it bought.

<img src="docs/screenshots/review.png" alt="The Review tab: pace, requirements met, commitment state, budget, audit coverage and the worst offender, with the CEO's variance narrative and the chairman's six levers">

## The feature map

Everything yaaf does, and where it's documented. Full reference:
**[docs/FEATURES.md](docs/FEATURES.md)**.

| Area | What you get |
|---|---|
| **The org** | Versioned role library, teams with leads, CEO-only planning, solo mode. Editing a role never silently changes a running company — you repin. → [DASHBOARD](docs/DASHBOARD.md) |
| **The plan** | Requirements you accept, ordered stages, a dependency graph with a critical path, a plan gate that checks the arithmetic, scored estimates. |
| **The promise** | A platform-owned delivery date, re-projected every tick, that freezes new scope when it slips. |
| **The rhythm** | Operate cycles, carried-forward scope, verbatim narratives, and a standing review with six recorded levers. |
| **Verification** | Platform-run tests, server-side evidence fetching + hashing + snapshotting, unforgeable webhook evidence, human spot-audit, a CEO-judgment metric. |
| **Money** | Per-run metering (killed runs and cache traffic included), USD or token budgets, warn-then-auto-pause, a separate real-world spend envelope, idempotent purchases, revenue only by signed webhook. |
| **Autonomy** | Five authority switches and two presets, from "you sign everything" to "tell me afterwards" — with the integrity rules unchanged at both ends. → [AUTONOMY](docs/AUTONOMY.md) |
| **Loop brakes** | Wake and run ceilings, a pair circuit breaker that freezes ping-pong threads, refusal caps, an enforced reporting cadence, suppressed idle wakes. |
| **Containment** | Fresh container per run, encrypted role-scoped secrets, Chromium in the box, an assist protocol for human-only steps, an idempotency ledger so retries never double-fire. |
| **Memory** | Project and team scopes with a curator, caps as refusals rather than silent eviction, explicit supersede/compact/forget. |
| **Audit** | Every message, task transition, run, dollar, decision, amendment, and refusal — queryable and on screen. |
| **Chat** | A resident CEO session per project: the Boardroom answers in seconds, and so does Slack. Chatting never consumes the work queue's caps. |
| **Integrations** | Slack (Socket Mode — no public URL), GitHub repo-per-project, external MCP servers as one-form data sources, signed webhooks, S3-compatible artifacts, email/webhook alerts. → [INTEGRATIONS](docs/INTEGRATIONS.md) |
| **Proof it works** | 524 offline tests, chaos tests, golden-transcript evals that gate prompt edits, and a $0 scripted runtime CI drives a whole company through. |

## What you give up

Nothing here is free, and a skeptic deserves the list:

- **You deploy and run a thing.** Postgres, two containers, a box. A skill is a
  file.
- **Setup is minutes, not seconds.** A goal worth planning from takes longer to
  write than a prompt, on purpose — it is the input everything else derives
  from.
- **You accept a plan.** On the default dial, scope waits for you. That is the
  whole mechanism for keeping the forecast honest, and it is still a thing you
  have to do. (Hand it over with one switch — [AUTONOMY](docs/AUTONOMY.md).)
- **The floor is higher than the ceiling is high.** Governance costs tokens:
  planning runs, review runs, verification. On a task you could have done in one
  sitting, that overhead buys you nothing.
- **It is pre-1.0.** APIs move.

If you read that list and thought "none of this is my problem", believe
yourself. Use a skill.

## The honest part

Multi-agent hierarchies are mostly vibes right now — nobody has shown that a
CEO + team beats one good agent with the same budget and rules, and yaaf does
not claim to have shown it either. What it does give you is a fair way to find
out for yourself: **solo mode** runs a single agent under byte-identical
governance, so you can hand the same goal to a hierarchy and to one brain and
compare the bills. And if the org layer turns out not to be worth it, the
governance layer underneath — requirements, verification, budgets, audit,
sandbox — still stands on its own for a single agent.

## Quickstart

**Install it — one script, and the only path you need.** It takes a bare box to
a running company with real, sandboxed Claude agents:

```bash
curl -fsSL https://get.docker.com | sh          # if docker isn't there yet
sudo usermod -aG docker "$USER"                 # then log out and back in

git clone <your-yaaf-remote> yaaf && cd yaaf
./deploy_single_server.sh
```

It generates both secrets, builds both images, brings up Postgres + the API +
the runner, applies migrations, loads the role library, and finishes by printing
the token you log in with. It is safe to re-run: it never overwrites secrets and
never re-seeds a role library that already exists.

The dashboard binds to `127.0.0.1` by default, so reach it over SSH:

```bash
ssh -N -L 8080:localhost:8080 you@your-server   # then open localhost:8080/app
```

Use `--bind 0.0.0.0` to publish it instead — but the admin token is a static
bearer, so put TLS in front of it first. Living with it afterwards:

```bash
./update_single_server.sh    # pull new code, back up, rebuild, migrate, verify
./kill_single_server.sh      # stop everything, keep the data (--wipe destroys it)
```

Migrations need no step of their own — they apply at container start, and the
update script verifies the schema really reached head rather than assuming. Full
detail, and what to do when something goes sideways, in
**[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

<details>
<summary><b>Running it from a checkout instead</b> — for working <i>on</i> yaaf</summary>

No Docker, SQLite, and the sandbox replaced by a plain subprocess. It provides
**no isolation**, so it is for developing yaaf, not for running work you care
about. Four things the compose deployment does for you have to be done by hand,
and missing any one of them leaves a company that looks alive and never moves:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # Python 3.12+
.venv/bin/python -m pytest tests/            # the whole suite runs offline

cd web && npm install && npm run build && cd ..   # 1. without this there is no dashboard

export YAAF_DATABASE_URL=sqlite:////tmp/yaaf.db \
       YAAF_ADMIN_TOKEN=dev-admin-token \
       YAAF_SANDBOX_MODE=subprocess \
       YAAF_API=http://localhost:8080 \
       YAAF_MASTER_KEY=$(.venv/bin/python -c \
         "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
                                             # 2. without this, saving your API key 500s

.venv/bin/python -c "from yaaf.core.db import engine; \
  from yaaf.core.models import Base; Base.metadata.create_all(engine())"

.venv/bin/yaaf-server &     # API, dashboard, dispatcher, janitor
.venv/bin/yaaf-runner &     # 3. the part that actually executes runs — without
                            #    it every run queues and nothing ever happens
.venv/bin/yaaf seed-roles roles/
```

Then `http://localhost:8080/app`, and the admin token is `dev-admin-token`.
Agents still cost money here: they are real Claude agents paid for with the API
key you attach to the project. (4. is the one you do not have to do — the chat
lane needs a container, so in subprocess mode the boardroom answers through
ordinary queued runs and the supervisor stands down instead of erroring.)

</details>

## Your first project

Open the dashboard, log in with the admin token, and create a project.
**[docs/CREATING_A_PROJECT.md](docs/CREATING_A_PROJECT.md) walks through it in
plain English** — picking a shape, writing a goal the CEO can plan from,
accepting the plan, connecting billing, credentials, and data sources.

Or don't write one. **[examples/](examples/)** has eleven projects you import as a
file, split the way the question above splits:

**Two you could do in a Claude session** — a briefing note, and a sourced answer
to an argument you're stuck on. They are there so you learn the loop on
something you can check yourself in five minutes.

**Eight you couldn't** — a desk your app wakes with a signed webhook, a crew
that opens maintenance PRs at 02:00 and doesn't double-fire after a crash, a
tutor that reads what you got wrong last week, a red team holding a requirement
the builders can't close, a change-watch that won't re-report old news, a game
argued over by three roles with conflicting charters, a thirty-day idea hunt on
evidence the platform verifies, and a company note built to survive "how do you
know?".

**They need nothing but your API key** — no data subscriptions, no vendor
accounts, with one free-GitHub exception that says so. Start with
**[01 — Your first hour](examples/01-your-first-hour.md)**: one writer, four
requirements, a few dollars.

> **Billing note:** agents pay with an API key you attach per project.
> Anthropic's consumer terms don't permit running fleets on a personal Claude
> subscription — subscription mode exists for single-user tinkering only.

## Documentation

| | |
|---|---|
| [Creating a project](docs/CREATING_A_PROJECT.md) | Your first project, in plain English |
| [Examples](examples/) | Eleven projects to import: two fundamentals, nine that need yaaf |
| [Features](docs/FEATURES.md) | The complete feature reference |
| [Dashboard](docs/DASHBOARD.md) | Every screen and what you do there |
| [Governance](docs/GOVERNANCE.md) | All 49 rules, their defaults, and how to change one |
| [Autonomy](docs/AUTONOMY.md) | How much the CEO decides without you |
| [Integrations](docs/INTEGRATIONS.md) | Slack, GitHub, MCP, webhooks, billing, storage |
| [Deployment](docs/DEPLOYMENT.md) | Install, configure, back up, upgrade, scale |
| [CLI](docs/CLI.md) | Every `yaaf` command |
| [Agent API](docs/AGENT_API.md) | What agents can do, and how to write a role |
| [Plan](docs/PLAN.md) | Where this is going, and what would kill it |

Index: **[docs/](docs/)**.

## What's in the box

```
yaaf/server     the platform: API, policy engine (the refusals), dispatcher,
                janitor, evidence verifier, Slack bridge
yaaf/runner     leases runs, launches sandbox containers, meters everything
yaaf/cli        `yaaf` — admin/observe CLI and spend reporting
web/            the chairman's dashboard (React, served at /app)
crews/          pre-staffed teams — a named job with a team already on it
roles/          starter role library: general-purpose, a tutor, an analyst,
                a markets desk, a mobile-game studio, a data-science bench
examples/       eleven importable projects (.md + .json) — two fundamentals,
                nine that need the platform
evals/          golden-transcript evals — regression-gate for prompt edits
tests/          invariant suite that attacks every rule over HTTP, chaos
                tests, and the eval checks; CI runs it all on SQLite + Postgres
```

## Project status

Actively developed, pre-1.0, APIs still moving. The platform (requirements and
commitments, governance, autonomy, sandbox, integrations, dashboard, eval +
chaos suites, CI) is built and tested. Expect sharp edges; read
[docs/PLAN.md](docs/PLAN.md) for where this is going and what would kill it.

## License

[Apache-2.0](LICENSE).
