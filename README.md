# yaaf — Yet Another Agent Framework

A hardened multi-agent company platform. A project gets an LLM **CEO**, teams of
role-based LLM agents (with a derived **team lead** per team), and a human
**chairman**. The chairman speaks only to the CEO (boardroom); leads speak to the
CEO (leadership); employees speak in their team room. Everything is recorded in
Postgres; Slack, when attached later, is a mirror — never the source of truth.

yaaf generalizes the discipline of the lana-project experiment: **rules live in
code, not prompts.** Agents interact with the platform through an authenticated
HTTP API using short-lived per-run tokens, and every forcing function is a
server-side refusal or an automatic state change — an agent cannot be talked out
of rules it never held.

## The forcing functions (enforced, not suggested)

| Rule | Mechanism |
|---|---|
| Work is pulled, never invented; only the CEO creates tasks | 403 refusal |
| Every task carries a falsifiable success test (`command` → platform-executed; `observable` → spot-audited) | 422 refusal; `checking` state |
| Queue ceiling, subtask ceiling, external-evidence floor | 409 refusals |
| External evidence counts only when platform-verified (fetch, hash, controlled-domain check, postdate check) | verifier + drought/floor computations |
| Evidence drought → internal task creation refused | 409 refusal |
| Hypotheses expire **at** their kill date; linked tasks block; only the chairman extends, once | janitor auto-expiry |
| Spends ≥ threshold need chairman approval; approvals expire to **deny** | 403 refusal + janitor expiry |
| Budgets: tokens always booked, dollars only when real; 80% warns, 100% auto-pauses | janitor |
| Wake-loop prevention: per-agent wake caps, daily run ceiling, ping-pong thread circuit breaker, per-run refusal cap (token revoked) | dispatcher + API |
| Idempotent side effects: recorded **before** firing; retries see the ledger | `side_effects` + context packs |
| Stale runs are reaped; claims released; nothing double-fires | janitor leases |
| The constitution freezes at project start; chairman amendments cool off 72h (cancellable meanwhile) and are disclosed to the CEO | `constitutions` + `amendments` |
| Revoked runs stop burning: kill switches stop the **container**, and killed runs still book their tokens | runner + usage checkpoints |
| Connector evidence agents cannot forge (GitHub events, signed generic webhooks, Slack inbound) | HMAC-verified `/webhooks/*` |
| Per-role egress: a role's containers can only reach that role's allowlist | squid ports generated from the role library |

## Layout

```
yaaf/core       models, constitution, ids, tokens, crypto, db, artifact store
yaaf/server     FastAPI (agent/admin/observe/webhooks APIs + WebSocket), policy
                engine, dispatcher, janitor, evidence verifier, context packs,
                Slack bridge, GitHub provisioning, egress generator, notifier
yaaf/runner     lease daemon, MockModel backend, Agent SDK backend,
                Docker sandbox + sandbox_entry (runs inside the container)
yaaf/cli        `yaaf` — the chairman's terminal, incl. `yaaf experiment` (M2)
web/            React SPA — the chairman's cockpit (served under /app)
roles/          seed role library (CEO, engineer, researcher, writer, …)
evals/          golden-transcript evals (gate for role-prompt edits)
experiments/    M2 baseline-experiment spec + runbook
deploy/         docker-compose (db, api, runner, egress proxy, optional minio)
migrations/     alembic
tests/          invariant selftests (attack every rule over HTTP), MockModel
                e2e, chaos suite (tests/chaos), golden-eval checks
```

## Quickstart (dev, no Docker)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/          # the whole suite runs at $0 in tokens

# A live smoke on SQLite:
export YAAF_DATABASE_URL=sqlite:////tmp/yaaf.db YAAF_ADMIN_TOKEN=dev-admin-token \
       YAAF_SANDBOX_MODE=subprocess
.venv/bin/python -c "from yaaf.core.db import engine; \
  from yaaf.core.models import Base; Base.metadata.create_all(engine())"
.venv/bin/yaaf-server &                    # API + dispatcher + janitor
export YAAF_API=http://localhost:8080
.venv/bin/yaaf seed-roles roles/
.venv/bin/yaaf create-user you@example.com "You"
.venv/bin/yaaf create-project project.json   # see spec shape below
.venv/bin/yaaf start <slug>
.venv/bin/yaaf say <slug> "Welcome. Here is the goal…"
.venv/bin/yaaf-runner &                    # leases and executes runs
.venv/bin/yaaf dashboard <slug>
```

Project spec (`project.json`):

```json
{
  "slug": "toy", "name": "Toy", "goal": "…",
  "chairman_email": "you@example.com",
  "ceo_role_slug": "ceo",
  "billing_mode": "api_key",
  "controlled_domains": ["mytoyproject.example"],
  "constitution": {"monthly_usd_cap": 200},
  "config": {"runtime": "agent_sdk", "ceo_heartbeat_hours": 6},
  "teams": [
    {"role_slug": "engineer", "name": "Build", "employees": ["Ada", "Lin"], "lead": "Ada"}
  ]
}
```

`config.runtime` is `agent_sdk` (real model, Docker sandbox) or `mock`
(scripted behaviors; CI). Billing: store the project's credential with
`POST /admin/secrets` and set `billing_secret_id` — `api_key` mode injects
`ANTHROPIC_API_KEY`, `subscription` mode injects `CLAUDE_CODE_OAUTH_TOKEN`
into the sandbox.

## Full stack (Docker Compose)

```bash
sudo mkdir -p /var/lib/yaaf/workspaces /var/lib/yaaf/artifacts
docker build -f deploy/sandbox/Dockerfile -t yaaf-sandbox:latest .
cd deploy && YAAF_ADMIN_TOKEN=… YAAF_MASTER_KEY=… docker compose up --build
```

Services: Postgres, API (dispatcher+janitor), runner (launches sibling sandbox
containers via the Docker socket), and the **egress proxy** — sandbox containers
sit on an internal network whose only way out is a squid allowlist
(`deploy/proxy/allowlist.txt`). Requires the invoking user in the `docker` group.

## The chairman's powers (deliberately few)

`yaaf say` (boardroom), `yaaf decide` (approvals), `yaaf extend` (one hypothesis
extension), `yaaf pause|stop` (kill switches, which revoke live run tokens),
`yaaf unfreeze` (circuit-broken threads), `yaaf audit` + `yaaf audit-evidence`
(spot-audit sampling — the human layer that doesn't approve everything put in
front of it), and constitution amendments via the admin API (72h cooling-off).
Everything else is observation: `dashboard`, `board`, `org`, `channels`, `tail`,
`runs`, `ledger`, `evidence`, `hypotheses`.

## Status

M1 (the experiment rig), the full org mechanics of M3, the M4 integrations
(evidence connectors, Slack bridge, GitHub workspaces, artifact store, per-role
egress), and the M5 hardening assets (chaos suite, CI, golden-transcript evals)
are built — see docs/PLAN.md's status appendix for the authoritative ledger.

The gate is now **M2**: the baseline experiment (CEO+team vs a single agent
under identical governance, goal, and budget) that decides whether the org
structure earns its tokens. Protocol: docs/M2_PROTOCOL.md; harness:
`yaaf experiment` + experiments/README.md. Goal selection awaits the chairman;
per the kill condition the experiment is decided by 2026-09-30.
