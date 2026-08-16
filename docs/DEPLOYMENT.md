# Deployment — installing, running, and operating yaaf

Single-box Docker Compose is the **primary target**, not a starter kit. Agent
runs are I/O-bound on someone else's inference, so rate limits arrive long
before worker capacity does. Extra worker machines are a deploy choice, not an
architecture change.

---

## Dev mode — five minutes, no Docker, $0

Agents are scripted stand-ins (`runtime: mock`), so you can rehearse a whole
company — wakes, tasks, refusals, budget pauses — before spending a cent.

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/            # 194 tests, offline; 1 skips without Postgres

export YAAF_DATABASE_URL=sqlite:////tmp/yaaf.db \
       YAAF_ADMIN_TOKEN=dev-admin-token \
       YAAF_SANDBOX_MODE=subprocess
.venv/bin/python -c "from yaaf.core.db import engine; \
  from yaaf.core.models import Base; Base.metadata.create_all(engine())"
.venv/bin/yaaf-server &                      # API + dispatcher + janitor
export YAAF_API=http://localhost:8080
.venv/bin/yaaf seed-roles roles/
```

Open **http://localhost:8080/app** and log in with the admin token.

> `YAAF_SANDBOX_MODE=subprocess` provides **no isolation**. It exists for dev
> machines and CI, and yaaf refuses it unless you configure it explicitly.

## The real thing — Docker Compose

```bash
# Host prerequisite: sibling-container workspace mounts need identical
# host/container paths.
sudo mkdir -p /var/lib/yaaf/workspaces /var/lib/yaaf/artifacts

# Generate a master key for secret encryption — losing it loses every secret.
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker build -f deploy/sandbox/Dockerfile -t yaaf-sandbox:latest .
cd deploy && YAAF_ADMIN_TOKEN=… YAAF_MASTER_KEY=… docker compose up --build
```

Then open the dashboard at **http://localhost:8080/app**, create a project,
connect billing in Settings, give your teams their credentials, and press
start. See [CREATING_A_PROJECT.md](CREATING_A_PROJECT.md).

Migrations apply themselves on boot (the image entrypoint runs Alembic before
the server).

### What comes up

| Service | What it is |
|---|---|
| `db` | Postgres 16, with a healthcheck the other services wait on |
| `api` | The API, the dispatcher and janitor threads, the Slack mirror and socket threads, and the built web UI at `/app` |
| `runner` | Leases runs, launches sandbox containers, meters everything, keeps the proxy config current |
| `proxy` | Squid — the sandbox network's only route to the internet |
| `minio` | Optional S3-compatible artifact store (`--profile s3`) |

The `sandbox` network is `internal: true`: sandbox containers reach the world
**only** through the proxy.

### Two images

`deploy/Dockerfile` builds the control plane (api + runner + web UI).
`deploy/sandbox/Dockerfile` builds the image agent runs execute inside. **They
are separate — rebuild both** when you change something that affects each.

---

## Configuration

Everything **operational** is environment (`YAAF_`-prefixed, also readable from
a `.env`). Everything **governed** lives in the database, per project — never
in env vars. See [GOVERNANCE.md](GOVERNANCE.md).

### Core

| Variable | Default | Notes |
|---|---|---|
| `YAAF_DATABASE_URL` | `postgresql+psycopg://yaaf:yaaf@localhost:5432/yaaf` | SQLite works for dev |
| `YAAF_ADMIN_TOKEN` | `dev-admin-token` | Bearer token for the admin/chairman API and dashboard login. **Change it.** |
| `YAAF_MASTER_KEY` | — | Fernet key (urlsafe base64, 32 bytes) for envelope-encrypting secrets |
| `YAAF_API_BASE_URL` | `http://localhost:8080` | How sandboxes reach the platform API |
| `YAAF_API_HOST` / `YAAF_API_PORT` | `0.0.0.0` / `8080` | |

### Runner and sandbox

| Variable | Default | Notes |
|---|---|---|
| `YAAF_WORKER_NAME` | `worker-1` | Identifies this runner in leases |
| `YAAF_SANDBOX_MODE` | `docker` | `docker` (isolation) or `subprocess` (dev/CI only — **no isolation**) |
| `YAAF_SANDBOX_IMAGE` | `yaaf-sandbox:latest` | |
| `YAAF_SANDBOX_NETWORK` | `yaaf_sandbox` | Must be the internal network |
| `YAAF_EGRESS_PROXY_URL` | `http://proxy:3128` | |
| `YAAF_WORKSPACES_ROOT` | `/var/lib/yaaf/workspaces` | Host path must equal container path |
| `YAAF_ARTIFACTS_ROOT` | `/var/lib/yaaf/artifacts` | |

### Egress auto-apply

| Variable | Default | Notes |
|---|---|---|
| `YAAF_PROXY_GENERATED_DIR` | *(empty)* | When set, the runner writes the generated per-role config here on change |
| `YAAF_PROXY_CONTAINER` | *(empty)* | …and reloads squid in this container. Empty = manual `yaaf egress-sync` (multi-box) |

Reloading is `docker exec <container> squid -k reconfigure`, **not** a
container HUP — PID 1 is a bash entrypoint that swallows signals.

### Artifact store (optional)

`YAAF_ARTIFACT_S3_ENDPOINT`, `YAAF_ARTIFACT_S3_BUCKET`,
`YAAF_ARTIFACT_S3_ACCESS_KEY`, `YAAF_ARTIFACT_S3_SECRET_KEY`. Setting the
endpoint switches artifacts from local files to S3; requires `pip install
'yaaf[s3]'`. Keep these **in lockstep on both `api` and `runner`**.

### Notifications (optional)

`YAAF_SMTP_HOST`, `YAAF_SMTP_PORT`, `YAAF_SMTP_USERNAME`,
`YAAF_SMTP_PASSWORD`, `YAAF_SMTP_FROM`, `YAAF_CHAIRMAN_WEBHOOK_URL`. Unset =
log only.

### Loop cadences (seconds)

`YAAF_DISPATCH_POLL_S` (2.0), `YAAF_JANITOR_POLL_S` (15.0),
`YAAF_RUNNER_POLL_S` (2.0), `YAAF_LEASE_HEARTBEAT_S` (20.0),
`YAAF_LEASE_TTL_S` (90.0). Postgres LISTEN/NOTIFY is a latency hint on top of
these polling floors, never a replacement for them.

---

## Operating it

### Health

- `GET /healthz` — process liveness.
- `GET /observe/projects/{id}/health` — the per-project setup checklist, the
  same one the Overview renders. All green = the UI hides the card.
- Dispatcher, janitor, and Slack threads log at INFO to the `api` container.

### Backups

Everything that matters is in **Postgres** (`pgdata` volume) plus the
**artifacts** directory. Back up both.

**`YAAF_MASTER_KEY` is not recoverable.** Lose it and every stored secret
becomes unreadable — you'd re-enter all credentials by hand. Keep it wherever
you keep your other irreplaceable keys, not only in `deploy/.env`.

Workspaces (`/var/lib/yaaf/workspaces`) are working state; back them up if
agents keep uncommitted work there, or attach a GitHub repo per project and let
git be the durable copy.

### Upgrades

```bash
git pull
docker build -f deploy/sandbox/Dockerfile -t yaaf-sandbox:latest .   # sandbox image
cd deploy && docker compose up --build -d                             # control plane
```

Migrations run on boot. Pre-1.0: read the commit log before upgrading a
deployment with running projects — APIs are still moving.

### Scaling

Add runners, not API servers. Runs are leased with a TTL heartbeat, so several
`yaaf-runner` processes (or boxes) safely share one database. Each needs
`YAAF_DATABASE_URL`, `YAAF_MASTER_KEY`, a unique `YAAF_WORKER_NAME`, and access
to Docker plus the proxy. On multi-box setups, run `yaaf egress-sync` on the
box hosting the proxy after role or data-source changes.

### When a run dies

Nothing needs you. The janitor reaps runs past `run_timeout_s`, releases their
task claims back to the queue, and the **side-effect ledger** prevents the
retry from double-firing anything external — no duplicate PR, tweet, or
payment.

---

## Repository layout

```
yaaf/server     API, policy engine (the refusals), dispatcher, janitor,
                evidence verifier, Slack bridge, egress generator
yaaf/runner     leases runs, launches sandbox containers, meters everything
yaaf/core       models, constitution schema, crypto, config, artifacts
yaaf/cli        `yaaf` — admin/observe CLI, `yaaf experiment`, `yaaf spend-report`
web/            the chairman's dashboard (React, served at /app)
roles/          starter role library (CEO, engineer, researcher, writer…)
deploy/         Dockerfiles, compose, squid config
evals/          golden-transcript evals — regression-gate for prompt edits
tests/          invariant suite over HTTP, chaos tests, eval checks
migrations/     Alembic
examples/       ten worked project setups
docs/           this documentation
```
