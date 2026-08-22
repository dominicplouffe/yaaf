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
| **Proof it works** | 526 offline tests, chaos tests, golden-transcript evals, a $0 mock runtime, and `yaaf experiment`. → [M2_PROTOCOL](docs/M2_PROTOCOL.md) |

## How is this different from a standalone agent?

A standalone agent (a chat assistant, a CLI coding agent) is a brilliant *tool*:
you aim it, it works, you check the result. The discipline comes from you being
there.

yaaf is for the moment you stop being there. It supplies what a lone agent
doesn't have: something that decides what to work on next (and what to kill), a
definition of "done" that isn't self-graded, a scope you agreed to and a date
computed from measured pace, budgets that actually stop spending, memory with a
curator instead of an ever-growing scratchpad, and a complete record you can
audit later. You go from *operating a tool* to *chairing an organization* — you
speak only to the CEO, accept the plan, decide approvals, and spot-check the
work.

## How is this different from CrewAI / AutoGen / LangGraph?

Those are **libraries**: you write a program that wires agents into a workflow,
and the workflow lives while your program runs. They're excellent for building
agent-powered *applications*, and yaaf is not trying to be one.

yaaf is a **platform** you deploy — closer to "self-hosted GitLab for an AI
workforce" than to an orchestration SDK:

| | Agent libraries | yaaf |
|---|---|---|
| Lifespan | While your script runs | Weeks — event-driven wakes, schedules, heartbeats, cycles |
| Where rules live | In prompts and your code | In the server, as refusals it's not possible to bypass |
| "Task complete" | The model says so | Platform-run tests, verified evidence, human spot-audit |
| A delivery date | You estimate it | Computed from measured pace, re-projected, freezes scope when it slips |
| Cost control | You watch the meter | Metered runs, hard budget pauses, a separate real-world envelope, loop breakers |
| Isolation | Your process, your network | Container per run, scoped secrets |
| After a crash | Re-run the script | Leases reaped, work released, side-effect ledger prevents double-firing (no duplicate tweet, PR, or payment) |
| Paper trail | Your logs | Full audit: every message, decision, dollar, and enforcement |

If you want to *build an app that uses agents*, use a library. If you want to
*run agents unattended on real goals with real budgets*, that's yaaf.

## The honest part

Multi-agent hierarchies are mostly vibes right now — nobody has shown that a
CEO + team beats one good agent with the same budget and rules. yaaf doesn't
assume it either: the repo ships a controlled experiment
([docs/M2_PROTOCOL.md](docs/M2_PROTOCOL.md), `yaaf experiment`) that runs an org
arm against a single-agent arm under identical governance, blind-judges the
results, and applies a pre-registered decision rule. If the org loses, the org
part of this framework gets shelved — and the governance layer (requirements,
verification, budgets, audit, sandbox) still stands on its own for single
agents. Either way, you get the answer instead of the vibes.

## Quickstart

**Dev mode — five minutes, no Docker, $0** (agents are scripted stand-ins):

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # needs Python 3.12+
.venv/bin/python -m pytest tests/            # the whole suite runs offline

export YAAF_DATABASE_URL=sqlite:////tmp/yaaf.db YAAF_ADMIN_TOKEN=dev-admin-token \
       YAAF_SANDBOX_MODE=subprocess
.venv/bin/python -c "from yaaf.core.db import engine; \
  from yaaf.core.models import Base; Base.metadata.create_all(engine())"
.venv/bin/yaaf-server &                      # API + dispatcher + janitor
export YAAF_API=http://localhost:8080
.venv/bin/yaaf seed-roles roles/
```

**The real thing — your own server** (real Claude agents, sandboxed). One script
takes a bare box to a running company:

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

## Your first project

Open the dashboard, log in with the admin token, and create a project.
**[docs/CREATING_A_PROJECT.md](docs/CREATING_A_PROJECT.md) walks through it in
plain English** — picking a shape, writing a goal the CEO can plan from,
accepting the plan, connecting billing, credentials, and data sources.

Or don't write one: **[examples/](examples/)** has ten setups you import as a
file and run — a coverage desk, a pre-open market brief, an honest backtest, a
point-in-time data pipeline, a portfolio review, a solo idea hunt, a macro
watch, a newsletter that has to find real readers, a fund screen, and a **$0 dry
run** on the mock runtime that is the best place to start.

> **Billing note:** agents pay with an API key you attach per project.
> Anthropic's consumer terms don't permit running fleets on a personal Claude
> subscription — subscription mode exists for single-user tinkering only.

## Documentation

| | |
|---|---|
| [Creating a project](docs/CREATING_A_PROJECT.md) | Your first project, in plain English |
| [Examples](examples/) | Ten setups you can import as-is |
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
yaaf/cli        `yaaf` — admin/observe CLI, incl. `yaaf experiment`
web/            the chairman's dashboard (React, served at /app)
roles/          starter role library: general-purpose, a markets desk, a
                mobile-game studio
examples/       ten importable project setups (.md + .json)
evals/          golden-transcript evals — regression-gate for prompt edits
tests/          invariant suite that attacks every rule over HTTP, chaos
                tests, and the eval checks; CI runs it all on SQLite + Postgres
```

## Project status

Actively developed, pre-1.0, APIs still moving. The platform (requirements and
commitments, governance, autonomy, sandbox, integrations, dashboard, eval +
chaos suites, CI) is built and tested; the org-vs-single-agent experiment is the
current milestone. Expect sharp edges; read [docs/PLAN.md](docs/PLAN.md) for
where this is going and what would kill it.

## License

[Apache-2.0](LICENSE).
