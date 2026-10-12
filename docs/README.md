# yaaf documentation

Start here if you're new; skim the table if you're not.

## Getting started

| Doc | What it covers |
|---|---|
| [CREATING_A_PROJECT.md](CREATING_A_PROJECT.md) | **Start here.** Your first project in plain English: picking a shape, writing a goal the CEO can plan from, accepting the plan, connecting billing, credentials, and data sources. |
| [../examples/](../examples/) | **Twelve projects to import and run**, split by the question a skeptic asks: two you could do in a Claude session (so you learn the loop) and ten that name the specific thing which breaks without the platform. Each needs nothing but your API key, bar one free-GitHub exception. |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Install and run it: one script for a real deployment, a checkout for working on yaaf, every environment variable, backups, upgrades, scaling. |

## Using it

| Doc | What it covers |
|---|---|
| [FEATURES.md](FEATURES.md) | The complete feature reference, grouped by the failure it prevents. |
| [DASHBOARD.md](DASHBOARD.md) | Every screen and what you do there — Desk, Projects, Roles, and each project tab. |
| [GOVERNANCE.md](GOVERNANCE.md) | The constitution: all 60 rules, their defaults, what each refuses, and how to change one. |
| [AUTONOMY.md](AUTONOMY.md) | How much the CEO decides without you — the five authority switches, and what never moves at any setting. |
| [INTEGRATIONS.md](INTEGRATIONS.md) | Billing, credentials, MCP data sources, Slack, GitHub, webhooks, notifications, artifact storage. |

## Building on it

| Doc | What it covers |
|---|---|
| [CLI.md](CLI.md) | Every `yaaf` command, the project spec shape, and a full provisioning script. |
| [AGENT_API.md](AGENT_API.md) | The API your agents act through, and how to write a role that uses it well. |
| [RUNTIMES.md](RUNTIMES.md) | **M0–M6 built (the live runs and the Cursor CLI aside).** Running a seat on any harness and provider — the Claude CLI, the Anthropic API under yaaf's own loop, then Codex, Gemini and the rest — under the same rules: the pattern, the refusals it adds, and what adding a provider costs. |
| [MCP_SERVER.md](MCP_SERVER.md) | **Built — M0 through M5.** yaaf as an MCP server: chairing your projects and founding new ones from a model client, every tool and scope, and the security model that makes writes safe. |

## Background

| Doc | What it covers |
|---|---|
| [PLAN.md](PLAN.md) | The thesis, the kill conditions, what is built, and what is deliberately not. |
| [projects/](projects/) | Full worked project definitions from real use, longer than an example. |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | How to work on yaaf itself. |
