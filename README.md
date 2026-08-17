# yaaf — Yet Another Agent Framework

**Run a small AI company on your own hardware — with rules the agents can't
talk their way out of.**

yaaf runs a company of AI agents: a **CEO** that plans and prioritizes, teams
of **role-based agents** that do the work (engineer, researcher, writer,
marketer…), and **you as chairman** — the one human, with a small set of real
powers. It keeps working while you're away: agents wake on schedule or when
something needs them, do one unit of work, and go back to sleep. Everything
runs on your own machine with Docker Compose, and you watch and steer from a
web dashboard.

<img src="docs/screenshots/cockpit-overview.png" alt="The chairman's cockpit: open tasks vs queue ceiling, runs, verified external evidence, pending approvals, the CEO-judgment metric, budget meter, and the org roster">

## The problem

Anyone who has left an autonomous agent running for more than an afternoon has
met the failure modes:

- It marks its own work "done" without anything actually being done.
- It invents busywork and reports it as progress.
- Two agents thank each other in a loop, at full API price, for hours.
- It spends your budget while you sleep.
- You come back and can't reconstruct what happened or why.

The usual fix is better prompts. But a prompt is a *suggestion* — a
sufficiently confused (or sufficiently clever) model can always talk itself
around one.

**yaaf's core idea: the rules live in the server, not in the prompt.** Agents
can only act through an authenticated API using short-lived, per-run tokens,
and every rule is enforced as an API refusal or an automatic state change. An
agent cannot be talked out of rules it never held. When an agent tries to
break a rule, it doesn't get a stern reminder — it gets a 403.

## What that looks like in practice

- **Work is pulled, never invented.** Only the CEO creates tasks, every task
  needs a success test that can actually fail (a command the *platform* runs,
  or a checkable observable), and there's a hard ceiling on open work.
- **"Done" means verified.** Completions need evidence; claims of real-world
  impact ("someone starred the repo", "we got a signup") only count after the
  platform fetches, checks, and snapshots them — or when they arrive through
  connectors agents can't forge (signed webhooks from GitHub, Slack, or
  anything you wire up).
- **Money can't run away.** Every run is metered (even killed ones). At 80% of
  budget the CEO gets warned; at 100% the project pauses itself and you get an
  email. Circuit breakers kill message loops and refusal storms before they
  cost anything.
- **Agents are contained, not trusted.** Each run happens in a fresh Docker
  container whose only network route is an allowlist proxy — per role, so your
  writer can reach WordPress and nothing else. Secrets are encrypted, scoped
  to roles, and injected per-run; agents never see the values in chat.
- **Everything is on the record.** Every message, task transition, run, dollar,
  and rule enforcement is in Postgres and visible in the dashboard. The
  dashboard also deals you random samples of recent "completed" work and
  evidence to spot-check — because AI reviewers approve nearly everything, and
  you don't.
- **The rules are yours, and only yours.** Each project runs under a rulebook
  (the "constitution") the agents cannot edit, argue with, or talk their way
  around — it is enforced server-side as refusals. You change any of it
  whenever you like and it binds from the next agent call; every change is
  recorded with the reason you gave and disclosed to the CEO, so a limit never
  moves without a note of who moved it and why.

<img src="docs/screenshots/evidence-spot-audit.png" alt="The Work tab: the CEO's bets with kill dates and the proof each one has earned, the tasks serving them, and the human spot-audit sample">

## The feature map

Everything yaaf does, and where it's documented. Full reference:
**[docs/FEATURES.md](docs/FEATURES.md)**.

| Area | What you get |
|---|---|
| **The org** | Versioned role library, teams with leads, CEO-only planning, solo mode. Editing a role never silently changes a running company — you repin. → [DASHBOARD](docs/DASHBOARD.md) |
| **Work discipline** | Queue and subtask ceilings, one-item-at-a-time claims, falsifiable success tests, suggestions instead of self-assigned work, bets with kill dates. → [GOVERNANCE](docs/GOVERNANCE.md) |
| **Verification** | Platform-run success tests, server-side evidence fetching + hashing + snapshotting, unforgeable webhook evidence, human spot-audit, a CEO-judgment metric. |
| **Money** | Per-run metering (killed runs and cache traffic included), USD or token budgets, warn-then-auto-pause, approval thresholds, `yaaf spend-report`. |
| **Loop brakes** | Wake and run ceilings, pair circuit breaker that freezes ping-pong threads, refusal caps, an enforced reporting cadence, suppressed idle wakes. |
| **Containment** | Fresh container per run, internal-only network behind a per-role allowlist proxy, encrypted role-scoped secrets, an idempotency ledger so retries never double-fire. |
| **Memory** | Project and team scopes with a curator, caps as refusals rather than silent eviction, explicit supersede/compact/forget. |
| **Audit** | Every message, task transition, run, dollar, decision, amendment, and refusal — queryable and on screen. |
| **Chat** | A resident CEO session per project: the Boardroom answers in seconds, and so does Slack. Chatting never consumes the work queue's caps. |
| **Integrations** | Slack (Socket Mode — no public URL), GitHub repo-per-project, external MCP servers as one-form data sources, signed webhooks, S3-compatible artifacts, email/webhook alerts. → [INTEGRATIONS](docs/INTEGRATIONS.md) |
| **Setup safety** | A health checklist that names anything misconfigured with a fix link, data sources that prove themselves by speaking MCP, project export and clone. |
| **Proof it works** | 194 offline tests, chaos tests, golden-transcript evals, a $0 mock runtime, and `yaaf experiment`. → [M2_PROTOCOL](docs/M2_PROTOCOL.md) |

## How is this different from a standalone agent?

A standalone agent (a chat assistant, a CLI coding agent) is a brilliant
*tool*: you aim it, it works, you check the result. The discipline comes from
you being there.

yaaf is for the moment you stop being there. It supplies the things a lone
agent doesn't have: something that decides what to work on next (and what to
kill), a definition of "done" that isn't self-graded, budgets that actually
stop spending, memory with a curator instead of an ever-growing scratchpad,
and a complete record you can audit later. You go from *operating a tool* to
*chairing an organization* — you speak only to the CEO, decide approvals, and
spot-check the work.

## How is this different from CrewAI / AutoGen / LangGraph?

Those are **libraries**: you write a program that wires agents into a
workflow, and the workflow lives while your program runs. They're excellent
for building agent-powered *applications*, and yaaf is not trying to be one.

yaaf is a **platform** you deploy — closer to "self-hosted GitLab for an AI
workforce" than to an orchestration SDK:

| | Agent libraries | yaaf |
|---|---|---|
| Lifespan | While your script runs | Weeks — event-driven wakes, schedules, heartbeats |
| Where rules live | In prompts and your code | In the server, as refusals it's not possible to bypass |
| "Task complete" | The model says so | Platform-run success tests, verified evidence, human spot-audit |
| Cost control | You watch the meter | Metered runs, hard budget pauses, loop circuit breakers |
| Isolation | Your process, your network | Container per run, egress allowlist per role, scoped secrets |
| After a crash | Re-run the script | Leases reaped, work released, side-effects ledger prevents double-firing (no duplicate tweet, PR, or payment) |
| Paper trail | Your logs | Full audit: every message, decision, dollar, and enforcement |

If you want to *build an app that uses agents*, use a library. If you want to
*run agents unattended on real goals with real budgets*, that's yaaf.

## The honest part

Multi-agent hierarchies are mostly vibes right now — nobody has shown that a
CEO + team beats one good agent with the same budget and rules. yaaf doesn't
assume it either: the repo ships a controlled experiment
([docs/M2_PROTOCOL.md](docs/M2_PROTOCOL.md), `yaaf experiment`) that runs an
org arm against a single-agent arm under identical governance, blind-judges
the results, and applies a pre-registered decision rule. If the org loses,
the org part of this framework gets shelved — and the governance layer
(queues, verification, budgets, audit, sandbox) still stands on its own for
single agents. Either way, you get the answer instead of the vibes.

## Quickstart

**Dev mode — five minutes, no Docker, $0** (agents are scripted stand-ins):

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/            # the whole suite runs offline

export YAAF_DATABASE_URL=sqlite:////tmp/yaaf.db YAAF_ADMIN_TOKEN=dev-admin-token \
       YAAF_SANDBOX_MODE=subprocess
.venv/bin/python -c "from yaaf.core.db import engine; \
  from yaaf.core.models import Base; Base.metadata.create_all(engine())"
.venv/bin/yaaf-server &                      # API + dispatcher + janitor
export YAAF_API=http://localhost:8080
.venv/bin/yaaf seed-roles roles/
```

**The real thing — Docker Compose** (real Claude agents, sandboxed):

```bash
sudo mkdir -p /var/lib/yaaf/workspaces /var/lib/yaaf/artifacts

# Two secrets, generated once and kept — compose reads deploy/.env every boot.
# The admin token logs you into the dashboard; the master key encrypts every
# stored secret, so losing or changing it loses all of them. Details and the
# rules each one must satisfy: deploy/.env.example.
{ echo "YAAF_ADMIN_TOKEN=$(openssl rand -base64 32)"
  echo "YAAF_MASTER_KEY=$(openssl rand -base64 32)"; } > deploy/.env

docker build -f deploy/sandbox/Dockerfile -t yaaf-sandbox:latest .
cd deploy && docker compose up --build      # reads ./.env automatically
```

That starts Postgres, the API + web dashboard, the runner, and the egress
proxy. Migrations apply themselves; `--profile s3` adds a MinIO artifact store.
Full options in **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

Installing on a box you SSH into rather than your own machine? The dashboard
needs binding to loopback and the role library needs loading over HTTP — see
**[Installing on a server you SSH into](docs/DEPLOYMENT.md#installing-on-a-server-you-ssh-into)**.

Either way, open **http://localhost:8080/app**, log in with the admin token,
and create a project from the wizard.
**[docs/CREATING_A_PROJECT.md](docs/CREATING_A_PROJECT.md) walks through your
first project in plain English** — from writing a goal the CEO can actually
plan from to connecting billing, credentials, and data sources. For worked
setups — an open-source launch, a client deliverable you can clone per
customer, a support desk, an overnight maintenance crew — see
**[examples/](examples/)**, ten of them with goal text, teams, constitution
numbers, and what counts as done.

> **Billing note:** agents pay with an API key you attach per project.
> Anthropic's consumer terms don't permit running fleets on a personal Claude
> subscription — subscription mode exists for single-user tinkering only.

## Documentation

| | |
|---|---|
| [Creating a project](docs/CREATING_A_PROJECT.md) | Your first project, in plain English |
| [Examples](examples/) | Ten worked setups you can copy |
| [Features](docs/FEATURES.md) | The complete feature reference |
| [Dashboard](docs/DASHBOARD.md) | Every screen and what you do there |
| [Governance](docs/GOVERNANCE.md) | All 24 rules, their defaults, and how to change one |
| [Integrations](docs/INTEGRATIONS.md) | Slack, GitHub, MCP, webhooks, billing, storage |
| [Deployment](docs/DEPLOYMENT.md) | Install, configure, back up, upgrade, scale |
| [CLI](docs/CLI.md) | Every `yaaf` command |
| [Agent API](docs/AGENT_API.md) | What agents can do, and how to write a role |
| [Plan](docs/PLAN.md) | Where this is going, and what would kill it |

Index: **[docs/](docs/)**.

## What's in the box

```
yaaf/server     the platform: API, policy engine (the refusals), dispatcher,
                janitor, evidence verifier, Slack bridge, egress generator
yaaf/runner     leases runs, launches sandbox containers, meters everything
yaaf/cli        `yaaf` — admin/observe CLI, incl. `yaaf experiment`
web/            the chairman's dashboard (React, served at /app)
roles/          starter role library (CEO, engineer, researcher, writer…)
evals/          golden-transcript evals — regression-gate for prompt edits
tests/          invariant suite that attacks every rule over HTTP, chaos
                tests, and the eval checks; CI runs it all on SQLite + Postgres
```

## Project status

Actively developed, pre-1.0, APIs still moving. The platform (governance,
sandbox, integrations, dashboard, eval + chaos suites, CI) is built and
tested; the org-vs-single-agent experiment is the current milestone. One
production project runs on it daily. Expect sharp edges; read
[docs/PLAN.md](docs/PLAN.md) for where this is going and what would kill it.

## License

[Apache-2.0](LICENSE).
