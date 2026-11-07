# Architecture

yaaf has two programs, one database, and one sandbox per agent run.

```
  Dashboard (/app)      CLI (yaaf)      Claude / any MCP client (/mcp)
          │                 │                    │
          └─────────────────┼────────────────────┘
                            ▼
  ┌──────────────────────── yaaf-server ────────────────────────┐
  │  HTTP API         policy engine (the rules)                 │
  │  dispatcher       decides who wakes, and when               │
  │  janitor          enforces deadlines, budgets, timeouts     │
  │  Slack bridge     mirrors project channels (optional)       │
  └───────────────────────────┬─────────────────────────────────┘
                              │
                     ┌────────▼────────┐
                     │    Database     │  Postgres (server) or SQLite (desktop)
                     │  all state, the │
                     │  run queue too  │
                     └────────▲────────┘
                              │
  ┌──────────────────────── yaaf-runner ────────────────────────┐
  │  leases a queued run, starts a sandbox, meters the cost     │
  └───────────────────────────┬─────────────────────────────────┘
                              ▼
            Sandbox: a Docker container or a bubblewrap jail
            ┌──────────────────────────────────────────────┐
            │ agent harness (Claude Code, Codex, ...)      │──► model provider
            │ talks back to yaaf-server's /agent API       │
            └──────────────────────────────────────────────┘
```

## The one idea

**Rules are enforced by the server, not by the prompt.** Agents never touch the
database. They act only through the `/agent` API, with a short-lived token
scoped to one run. Every rule is either a refusal (HTTP 403) or an automatic
state change. An agent cannot argue its way past code that never asked its
opinion.

## The parts

### yaaf-server

One Python process (FastAPI + Uvicorn). It serves:

| Path | Who uses it |
|---|---|
| `/app` | The dashboard (React, built from `web/`) |
| `/admin`, `/observe`, `/me` | The dashboard and the CLI |
| `/auth`, `/oauth` | Sign-in, and OAuth for MCP clients and data sources |
| `/agent` | Agents, from inside their sandbox |
| `/mcp` | Claude and other MCP clients |
| `/webhooks` | Signed events from outside (GitHub, your systems) |
| `/healthz`, `/readyz` | Liveness, and readiness (database and schema) |

It also runs background threads:

- **Dispatcher.** Turns events into runs, or into nothing. Whether an agent has
  work is a database query made before any token is spent, so an idle project
  costs $0. It enforces wake limits, daily caps and concurrency caps.
- **Janitor.** The automatic state changes: reaps dead runs, pauses projects
  over budget, expires stale approvals, checks the delivery date against real
  pace, verifies outside evidence, and sends the daily brief.
- **Slack.** Mirrors project channels to Slack when you connect it.

### yaaf-runner

A separate process that executes runs. It:

1. Leases one run from the queue (`FOR UPDATE SKIP LOCKED`, so two runners
   never take the same job).
2. Starts a fresh sandbox with the project's workspace mounted.
3. Sends a heartbeat while the run lives. If the heartbeat stops, the janitor
   reaps the run and frees its task.
4. Records the cost, even for runs that fail or are killed.

It also keeps one long-lived chat session per running project, so the CEO can
answer you in seconds.

The server never runs agents itself. **Without a runner, nothing happens.**

### Sandboxes

Every run gets a new, empty sandbox that is thrown away afterwards.

| Mode | Where | Isolation | Use it for |
|---|---|---|---|
| `docker` | A container from the `yaaf-sandbox` image | Strong | Servers. The default. |
| `host` | A bubblewrap jail on your machine | Partial: hides your home folder and yaaf's keys | Your own desktop |
| `subprocess` | A plain process | **None** | Development and CI only |

Inside the sandbox, a **harness** drives the model: the Claude Agent SDK,
Claude Code, Codex CLI, Gemini CLI, OpenCode, or yaaf's own loop. A harness is
transport only. It decides how a model is called, never what it may do.

### Database

Everything lives here: projects, agents, tasks, messages, runs, costs, rules
and the run queue. Postgres in production; SQLite on a desktop. Schema changes
are Alembic migrations in `migrations/`, applied automatically at start.

Postgres `LISTEN/NOTIFY` makes wake-ups fast. Polling is the safety net, so
nothing depends on a notification arriving.

Secrets (API keys, tokens) are encrypted with `YAAF_MASTER_KEY` before they are
stored.

## Life of a run

1. Something happens: a message, a finished task, a schedule, a webhook.
2. The **dispatcher** checks whether an agent has real work and is within its
   limits. If yes, it queues a run.
3. The **runner** leases it, mints a run token, and starts a sandbox.
4. The **agent** reads its context pack and acts through `/agent`: claims a
   task, posts a message, submits evidence.
5. The **server** checks every action against the rules. A test that proves a
   task is done runs in its own sandbox, launched by the platform, not the agent.
6. The run ends. Its cost goes to the ledger. If the budget is now spent, the
   janitor pauses the project.

## Where the rules come from

| Kind | Where it lives | Example |
|---|---|---|
| Operational | Environment variables | Database URL, port, sandbox mode |
| Governed | Database, per project | Budget, deadline, wake limits, who approves what |

Operational settings are in [CONFIGURATION.md](CONFIGURATION.md). Governed rules
are set per project in the dashboard.

## Code map

```
yaaf/server     API, policy engine, dispatcher, janitor, MCP server, Slack
yaaf/runner     run leasing, sandboxes, harness entry points, chat sessions
yaaf/runtimes   harness, provider and model registries
yaaf/core       data models, config, encryption, prices, shared logic
yaaf/founder    the Founder: drafts a project from a conversation
yaaf/rehearsal  `yaaf rehearse`: runs a project spec for $0
yaaf/cli        the `yaaf` command
web/            the dashboard (React + Vite)
roles/, crews/  the role library and ready-made teams
deploy/         Dockerfiles, compose file, install and update scripts
migrations/     Alembic schema migrations
tests/          integration tests that drive the real app over HTTP
evals/          golden-transcript checks for prompt changes
examples/       importable example projects
```

## Tech stack

Python 3.12+, FastAPI, SQLAlchemy, Alembic, Postgres 16 or SQLite, React 18,
Vite, Docker, bubblewrap.

## Read next

- [INSTALL.md](INSTALL.md): get it running.
- [CONFIGURATION.md](CONFIGURATION.md): every environment variable.
- [DEPLOYMENT.md](DEPLOYMENT.md): run it on a server.
- [PROVIDERS.md](PROVIDERS.md): models, keys, harnesses and prices.
- [CLI.md](CLI.md): every `yaaf` command.
- [SECURITY.md](../SECURITY.md): what contains an agent and what does not.
