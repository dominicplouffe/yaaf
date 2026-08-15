# yaaf — Yet Another Agent Framework

A hardened multi-agent company platform. A project gets an LLM **CEO**, teams of
role-based LLM agents (with a derived **team lead** per team), and a human
**chairman**. The chairman speaks only to the CEO (boardroom); leads speak to the
CEO (leadership); employees speak in their team room. Everything is recorded in
Postgres; Slack, when attached, is a mirror — never the source of truth.

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
| Spends ≥ threshold need chairman approval; approvals expire per the constitution (default **deny**), with the chairman nagged before expiry | 403 refusal + janitor expiry + notifier |
| Budgets: tokens always booked, dollars only when real; 80% warns, 100% auto-pauses and notifies the chairman | janitor |
| Wake-loop prevention: per-agent wake caps, daily run ceiling, ping-pong thread circuit breaker, per-run refusal cap (token revoked) | dispatcher + API |
| Idempotent side effects: recorded **before** firing; retries see the ledger | `side_effects` + context packs |
| Stale runs are reaped; claims released; nothing double-fires | janitor leases |
| The constitution freezes at project start; chairman amendments cool off 72h (cancellable meanwhile) and are disclosed to the CEO | `constitutions` + `amendments` |
| Revoked runs stop burning: kill switches stop the **container**, and killed runs still book their tokens | runner + usage checkpoints |
| Connector evidence agents cannot forge (GitHub events, signed generic webhooks, Slack inbound) | HMAC-verified `/webhooks/*` |
| Per-role egress: a role's containers can only reach that role's allowlist | squid ports generated from the role library |
| Failed chairman spot-audits (evidence **or** completions) mark the stream suspect and wake the CEO | `/admin/*/audit` + notices |

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
  "controlled_domains": ["mytoyproject.example", "github:myaccount"],
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
`ANTHROPIC_API_KEY` into the sandbox. (Subscription mode exists but Anthropic's
consumer terms do **not** permit multi-agent platforms on subscription auth —
API-key billing is the required mode; see docs/PLAN.md.)

`"solo": true` with exactly one employee-less team creates a **solo project**:
the CEO is the sole member and derived lead of its only team — one brain that
plans and executes under byte-identical policy. This is the M2 single-agent arm.

## Full stack (Docker Compose)

```bash
sudo mkdir -p /var/lib/yaaf/workspaces /var/lib/yaaf/artifacts
docker build -f deploy/sandbox/Dockerfile -t yaaf-sandbox:latest .
cd deploy && YAAF_ADMIN_TOKEN=… YAAF_MASTER_KEY=… docker compose up --build
```

Services: Postgres, API (dispatcher+janitor, migrations applied on start),
runner (launches sibling sandbox containers via the Docker socket), and the
**egress proxy** — sandbox containers sit on an internal network whose only way
out is a squid allowlist. `docker compose --profile s3 up` adds a MinIO
artifact store (see Integrations). The web cockpit is served at
**http://localhost:8080/app** — log in with the admin token from `deploy/.env`.

## The chairman's cockpit (web UI)

Login → project list → per-project tabs: **Overview** (budget meter, org
roster, constitution in force, and the **CEO judgment** tile — the share of the
CEO's bets that ever produced verified external evidence), **Boardroom** (talk
to the CEO), **Board**, **Channels** (read-all/write-none), **Runs** (tokens,
refusals, wake reasons, full trace viewer), **Approvals**, **Evidence**
(hypotheses, evidence, and the spot-audit sample with pass/fail verdicts on
both evidence and completions), **Settings** (billing, service credentials with
per-role grants, amendments with cooling-off countdown and cancel). A
WebSocket layers live updates over the polling — a latency hint, never the
source of truth. There's also a project wizard and a role-library editor
(guarded by the golden evals, below).

## The chairman's powers (deliberately few)

`yaaf say` (boardroom), `yaaf decide` (approvals), `yaaf extend` (one
hypothesis extension), `yaaf pause|stop` (kill switches — they stop the
containers, not just the tokens), `yaaf unfreeze` (circuit-broken threads),
`yaaf audit` + `yaaf audit-evidence` and `POST /admin/tasks/{id}/audit`
(spot-audit sampling — the human layer that doesn't approve everything put in
front of it), constitution amendments (72h cooling-off, cancellable, listed),
`yaaf egress-sync` (regenerate per-role proxy config), and webhook-secret
provisioning. Everything else is observation: `dashboard`, `board`, `org`,
`channels`, `tail`, `runs`, `ledger`, `evidence`, `hypotheses`.

The chairman is also **notified outward** (approval nags before expiry, budget
auto-pauses) via SMTP and/or a JSON webhook — set `YAAF_SMTP_HOST` (+
`_PORT/_USERNAME/_PASSWORD/_FROM`) and/or `YAAF_CHAIRMAN_WEBHOOK_URL`; unset
means log-only.

## Integrations (M4)

**Evidence connectors** — evidence agents cannot forge, arriving as
`platform_attested`. Provision per-project secrets once with
`POST /admin/projects/{id}/webhook_secrets` (shown once; regenerate to
recover), then point senders at:

- `POST /webhooks/github/{project_id}` — a GitHub repo webhook (signed
  `X-Hub-Signature-256`). Stars, watches, forks, opened issues/PRs, and issue
  comments by accounts **not** registered as `github:<login>` in
  `controlled_domains` become external evidence. Redeliveries dedupe.
- `POST /webhooks/generic/{project_id}` — `{description, source_url?,
  occurred_at?}` signed with `X-Yaaf-Signature` (HMAC-SHA256 of the raw body).
  For anything you can teach to send a signed POST: mail routes, Stripe
  forwarders, analytics jobs.

**Slack bridge** (optional, per project; the bus stays canonical). Configure it
in **Settings → Slack bridge**: paste the app's bot token + signing secret
(stored as ungranted secrets — agents never hold them), your Slack member ID,
and the channel map (boardroom / leadership / per-team). Under the hood this is
`config.slack = {bot_token_secret, signing_secret, chairman_slack_user,
channel_map}`, settable by PATCH for scripting.

The janitor mirrors mapped channels outward each tick (best-effort; an outage
never fails an enforcement tick). For inbound, point the Slack app's events URL
(shown in the card) at `POST /webhooks/slack/{project_id}` over public HTTPS:
only the chairman's Slack user posting in the mapped boardroom channel becomes
a boardroom message — everything else is ignored and audited.

**GitHub workspaces.** Store a platform-level secret named `github` (a PAT),
then `POST /admin/projects/{id}/github {"repo": "name-or-owner/name"}` creates
(or verifies) a private repo and pins it to the project. The runner clones it
into the workspace at lease time and scrubs the credential from `.git/config`;
agents push with their own role-scoped secret (`github` in `secret_scopes`).

**Artifact store.** Local files under `artifacts_root` by default; set
`YAAF_ARTIFACT_S3_ENDPOINT` (+ `_BUCKET/_ACCESS_KEY/_SECRET_KEY`, and
`pip install 'yaaf[s3]'`) to switch to any S3-compatible store —
`docker compose --profile s3 up` ships MinIO. Holds evidence snapshots, run
traces (`GET /observe/runs/{id}/trace`), and agent deliverables: the
`artifact_put` sandbox tool is the durable home for finished work, because
workspaces are ephemeral.

**Per-role egress.** Give a role `tool_permissions.egress_allow` (in the role
editor or roles/*.json), run `yaaf egress-sync`, and HUP the proxy: that role's
containers get their own squid port whose allowlist is exactly that list,
default-deny beyond it. Roles without one stay on the global allowlist.

## The M2 experiment

The gate the whole org-framework bet stands or falls on: CEO+team vs a single
agent under identical governance, goal, and budget. The protocol
(docs/M2_PROTOCOL.md) is frozen before any run; the harness drives it:

```
yaaf experiment init experiments/m2.spec.json    # provision both arms per goal pair
yaaf experiment status experiments/m2.spec.json  # live E / tokens / time / conversion
yaaf experiment export …                         # sealed random-label blind bundles
yaaf experiment judge …                          # 3 judge-model rubric passes per bundle
yaaf experiment score … <label> f c u co         # the chairman's blind pass
yaaf experiment decide …                         # unseal, composite, verdict → docs/M2_RESULT.md
```

See experiments/README.md for the runbook and arm construction.

## Verification

- **Invariant selftests** attack every rule over HTTP with real scoped tokens,
  on SQLite and (via `YAAF_TEST_DATABASE_URL`) real Postgres.
- **Chaos suite** (tests/chaos): worker races on SKIP LOCKED, worker death →
  reap → retry sees prior side effects, reaped runs can't double-book,
  DB-connection loss between ticks, pause-mid-queue.
- **Golden-transcript evals** (evals/): scripted scenarios replay against the
  real API at $0; transcripts — every refusal verbatim — are normalized and
  diffed against recorded goldens. `python -m evals.run --record` to bless a
  deliberate change; CI fails on any silent drift. This is the gate that makes
  role-prompt editing safe.
- **CI** (.github/workflows/ci.yml): lint, SQLite suite, Postgres suite, web
  build — every push and PR.

## Status

M1 (the experiment rig), the full org mechanics of M3, the M4 integrations
above, and the M5 hardening assets are built — docs/PLAN.md's status appendix
is the authoritative ledger.

The gate is now **M2**: goal selection awaits the chairman; per the kill
condition the experiment is decided by 2026-09-30. If the org arm loses, the
org framework is shelved and the salvage path (a governance harness for single
agents) proceeds.
