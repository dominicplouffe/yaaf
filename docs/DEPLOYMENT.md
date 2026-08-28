# Deployment — installing, running, and operating yaaf

Single-box Docker Compose is the **primary target**, not a starter kit. Agent
runs are I/O-bound on someone else's inference, so rate limits arrive long
before worker capacity does. Extra worker machines are a deploy choice, not an
architecture change.

---

## Dev mode — a checkout, for working on yaaf

No Docker, SQLite instead of Postgres, and the sandbox replaced by a plain
subprocess. This is the **contributor** path: if you want to *run* yaaf, use
[the deploy script](#the-real-thing--your-own-server) below instead.

The compose deployment does four things for you that you have to do by hand
here. Miss any one and you get a company that looks alive and never moves — so
each is called out.

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"      # Python 3.12+
.venv/bin/python -m pytest tests/            # 626 tests, offline; 1 skips without Postgres

# 1. The dashboard is built, not shipped: web/dist is gitignored, and without
#    it the API serves a JSON note instead of /app.
cd web && npm install && npm run build && cd ..

# 2. YAAF_MASTER_KEY encrypts every stored secret. Without it, saving the
#    project's Anthropic key answers 500 and the project can never start.
export YAAF_DATABASE_URL=sqlite:////tmp/yaaf.db \
       YAAF_ADMIN_TOKEN=dev-admin-token \
       YAAF_SANDBOX_MODE=subprocess \
       YAAF_API=http://localhost:8080 \
       YAAF_MASTER_KEY=$(.venv/bin/python -c \
         "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

.venv/bin/python -c "from yaaf.core.db import engine; \
  from yaaf.core.models import Base; Base.metadata.create_all(engine())"

.venv/bin/yaaf-server &     # API, dashboard, dispatcher, janitor
# 3. The server does NOT execute runs. Without a runner, every run sits in
#    run_queue forever and the company never does anything at all.
.venv/bin/yaaf-runner &
.venv/bin/yaaf seed-roles roles/
```

Open **http://localhost:8080/app** and log in with the admin token.

4. is the one you do not have to do: the chat lane is a container, so in
subprocess mode the supervisor stands down with one log line and the boardroom
answers through ordinary queued runs instead.

> **This is not a free mode.** The agents are real Claude agents, paid for with
> the API key you attach to the project. `runtime: mock` swaps them for scripted
> stand-ins, but no behaviours ship with yaaf — see
> [The mock runtime](#the-mock-runtime) — so a mock project reads its context
> and exits. It is a test harness, not a demo.

> `YAAF_SANDBOX_MODE=subprocess` provides **no isolation**. It exists for dev
> machines and CI, and yaaf refuses it unless you configure it explicitly.

### The mock runtime

`runtime: mock` (Settings → Advanced) routes runs to `MockBackend` instead of
the Claude Agent SDK. A behaviour is a plain Python function registered against
an agent name; the tests and the eval harness register a company's worth of
them (`evals/behaviors.py`) and drive planning, decomposition, claims,
completions, evidence and every governance trip at $0. That is what CI runs.

**Nothing is registered by default.** On your own install a mock agent reads its
context pack and exits, which is exactly right for testing the platform and
useless as a demo. If you want to watch a scripted company move, run the eval
harness (`python -m evals.run`) rather than pointing a project at mock.

## The real thing — your own server

```bash
curl -fsSL https://get.docker.com | sh          # if docker isn't there yet
sudo usermod -aG docker "$USER"                 # then log out and back in

git clone <your-yaaf-remote> yaaf && cd yaaf
./deploy_single_server.sh
```

That is the whole install. The script is idempotent — re-run it after a
reboot, after a config change, or when you are not sure what state the box is
in. It refuses to overwrite secrets and refuses to re-seed a role library that
already has entries, so the second run is safe by construction.

When it finishes it prints how to log in. Open the dashboard, create a
project, connect billing in Settings, give your teams their credentials, and
press start — [CREATING_A_PROJECT.md](CREATING_A_PROJECT.md) walks through
that in plain English.

| Flag | Effect |
|---|---|
| *(none)* | dashboard on `127.0.0.1:8080`, reachable over an SSH tunnel |
| `--bind 0.0.0.0` | publish it — read the warning it prints |
| `--port 9090` | somewhere other than 8080 |
| `--skip-roles` | do not touch the role or crew libraries |

### What it actually does

Worth knowing, because when something breaks you debug these, not the script:

1. **Preflight** — that you are in the checkout root, that the docker daemon
   is reachable (falling back to `sudo docker` when the `usermod` has not
   taken effect yet), that `openssl` and `curl` exist.
2. **Host paths** — `/var/lib/yaaf/{workspaces,artifacts}`. Sandboxes are
   sibling containers, so a workspace path must be the same string on the host
   and in the runner or the mount resolves to nothing.
3. **Secrets** — generates `deploy/.env` if absent, never rewrites it. See
   [The two secrets](#the-two-secrets).
4. **Port binding** — writes `deploy/docker-compose.override.yml`. The
   `!override` tag matters: without it compose *appends* to `ports` and you
   keep the original `0.0.0.0` binding you were trying to remove.
5. **Both images** — `yaaf-sandbox:latest` is a separate `docker build` that
   compose knows nothing about. Skipping it leaves agent runs on a stale image.
6. **Schema** — waits for migrations, then checks `alembic current` really
   says `(head)`.
7. **Role library** — loads `roles/*.json` over HTTP, and refiles the roles
   already there into their hiring categories (no new versions, so no live
   team goes behind its prompt). Then `crews/*.json`, on the same
   only-what-is-missing contract.

### Living with it

```bash
./update_single_server.sh    # pull, back up, rebuild, migrate, verify
./kill_single_server.sh      # stop, keep data      (--wipe destroys the database)
```

**Updating, including updates that carry migrations, needs no special step.**
Migrations run from the image entrypoint at container start. `update` pulls,
takes a `pg_dump` into `backups/` *before* rebuilding, rebuilds both images,
then verifies the schema reached head rather than assuming it did. It refuses
to pull onto a dirty working tree, and says out loud when an update touches
`migrations/`.

| Flag | Effect |
|---|---|
| `--no-pull` | rebuild what is already checked out |
| `--no-backup` | skip the dump (you are on your own) |
| `--keep-chat` | leave resident chat sessions on the old code |

Rolling back a migration you regret means restoring the dump it took first:

```bash
gunzip -c backups/<file>.sql.gz | docker compose exec -T db psql -U yaaf -d yaaf
```

`kill` keeps your data by default — the Postgres volume, `deploy/.env`,
workspaces and artifacts all survive, so `deploy` brings the same company
back. `--wipe` destroys the database volume and asks you to type `DESTROY`
first. It also clears the sandbox containers the runner launched: those are
siblings rather than compose services, so `docker compose down` leaves them
behind, and a long-running box accumulates dozens of exited ones.
`--sandboxes` does only that cleanup; `--keep-sandboxes` skips it. The sweep
is box-wide, which is right for a single-server deployment and surprising if
you ever run two stacks on one host.

### The two secrets

Compose reads `deploy/.env` from the directory you run it in, which is why
they go in a file rather than on the command line: both values must be
**identical on every boot**, and both fail quietly rather than loudly if they
are not. `deploy/.env.example` is an annotated template if you would rather
fill them in by hand; `deploy/.env` itself is gitignored.

**`YAAF_ADMIN_TOKEN`** — the bearer token for the admin/chairman API and the
dashboard login. Any opaque string; it is compared verbatim, so length and
alphabet are yours to choose. Omit it and compose substitutes
`dev-admin-token`, the value printed throughout these docs.

**`YAAF_MASTER_KEY`** — envelope-encrypts every stored secret (API keys, MCP
URLs, connector credentials). This one is not free-form: it must be a Fernet
key, 32 url-safe base64 bytes *with* padding. `openssl rand -base64 32`
satisfies that, and so does the canonical generator if you have the library:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

What does **not** work is `secrets.token_urlsafe(32)`, the obvious Python
idiom — it strips the base64 padding, so it looks correct and then raises
`Fernet key must be 32 url-safe base64-encoded bytes` at the first secret you
store.

Changing the master key strands every secret already stored; leaving it unset
makes the server refuse to read or write secrets at all, because compose
substitutes an empty value. Neither is recoverable — back it up.

---

## Reaching a server you SSH into

`./deploy_single_server.sh` binds the dashboard to `127.0.0.1` precisely so
that a remote box does not publish it. Nothing listens on the network, so
there is nothing to firewall. Reach it by forwarding the port from your
laptop:

```bash
ssh -N -L 8080:localhost:8080 you@your-server
```

Then open **http://localhost:8080/app**. The script prints the admin token
when it finishes; read it back any time with:

```bash
grep YAAF_ADMIN_TOKEN deploy/.env
```

Then follow [CREATING_A_PROJECT.md](CREATING_A_PROJECT.md). Have an Anthropic
API key ready: you give it to the project in Settings, and no agent can run
until you do. That key is the one credential nothing here generates for you.

### Doing it by hand

If you would rather not run the script — or you are debugging what it did —
this is the same sequence. The only prerequisites are Docker with the compose
plugin, `git` and `openssl`; no Python and no Node, because the `api` image
builds the web UI itself.

```bash
sudo mkdir -p /var/lib/yaaf/workspaces /var/lib/yaaf/artifacts

{ echo "YAAF_ADMIN_TOKEN=$(openssl rand -base64 32)"
  echo "YAAF_MASTER_KEY=$(openssl rand -base64 32)"; } > deploy/.env

cat > deploy/docker-compose.override.yml <<'YML'
services:
  api:
    ports: !override
      - "127.0.0.1:8080:8080"
YML

docker build -f deploy/sandbox/Dockerfile -t yaaf-sandbox:latest .
(cd deploy && docker compose up -d --build)

# Check the schema before going further: `compose ps` and /healthz both look
# fine on a box whose migrations never applied, and the first symptom is
# otherwise a wall of 500s when you seed roles.
(cd deploy && docker compose exec -T api alembic current) | grep -q '(head)' \
  && echo "schema OK" \
  || echo "NO SCHEMA — run: docker compose exec -T api alembic upgrade head"

# roles/ is not in the image, so this is an HTTP load from the checkout.
set -a; . deploy/.env; set +a
for f in roles/*.json; do
  curl -fsS -X POST http://localhost:8080/admin/roles \
    -H "Authorization: Bearer $YAAF_ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    --data-binary @"$f" > /dev/null && echo "seeded ${f##*/}"
done
```

### Things that bite on a remote box

**Publishing 8080 is not firewalled by ufw.** Docker writes its own iptables
rules ahead of ufw's, so `ports: "8080:8080"` is reachable from anywhere the
box is, whatever `ufw status` claims. The loopback binding the script writes
is what actually keeps it private — binding to `127.0.0.1` means there is nothing to firewall.
Drop the override only when a TLS-terminating reverse proxy is in front,
because the admin token is a static bearer sent on every request.

**Hand-editing that binding without `!override` silently does nothing.** Compose *appends*
list values when it merges, so an override without the tag leaves you with
both bindings — the loopback one you wanted and the original `0.0.0.0` one
you were trying to remove. `docker compose config | grep -E 'host_ip|published'`
is the check: exactly one `published: "8080"`, carrying `host_ip: 127.0.0.1`.

**`docker compose exec api yaaf seed-roles roles/` looks like it works and
does nothing.** The image has no `roles/` directory, and the command globs an
empty path, prints nothing, and exits 0. `./deploy_single_server.sh` loads them
over HTTP; by hand it is the loop above, or from a checkout with the CLI
installed:
`YAAF_API=http://localhost:8080 YAAF_ADMIN_TOKEN=$(grep YAAF_ADMIN_TOKEN deploy/.env | cut -d= -f2-) yaaf seed-roles roles/`.

**Skipping the role seed is survivable only if your bundles carry their
roles.** An export from another install does carry them, and the project wizard
creates those at submit time. The bundles in [`examples/`](../examples/) do not:
they hire from the library by slug, so on an unseeded deployment their teams
land on the New Project page with the role picker showing "not in this
install's role library" — and a from-scratch project has nothing to staff
itself with either.

**A library seeded once does not grow by itself.** Both scripts load only the
slugs the library is missing, so a role added to the repository after your
first deploy arrives on the next `./update_single_server.sh`, and no role you
already have is re-versioned. By hand it is
`yaaf seed-roles roles/ --only-missing`.

**The sandbox image is not rebuilt by `docker compose up --build`.** It is a
separate `docker build`. Forgetting it on a new box means runs start against a
missing or stale image.

**Migrations apply on boot, and a failure is now fatal.** The entrypoint runs
Alembic before the server, and no longer tolerates it failing — a box whose
schema never applied used to boot anyway, answer /healthz with 200 (that only
proves the process is alive) and authenticate fine (auth never touches the
database), then 500 every real request with `relation "projects" does not
exist`. If the api container is restarting in a loop after an update, read
`docker compose logs api` — the migration error is the reason.

### When the install goes wrong

**Everything 500s and the logs say `relation "projects" does not exist`.** The
schema was never created. Check and fix:

```bash
cd deploy
docker compose exec -T api alembic current        # expect: 0001 (head)
docker compose exec -T api alembic upgrade head   # if it printed no revision
```

**The api container keeps restarting.** Migrations failed, and that is now
fatal by design rather than something you discover later through 500s.
`docker compose logs api` ends with the actual error.

**It hangs at "Database schema" with no output.** `api` and `runner` both run
`alembic upgrade head` at start, the whole chain runs in one transaction, and
creating `alembic_version` takes an `ACCESS EXCLUSIVE` lock on it — so a
concurrent `alembic current` waits for that commit rather than answering.
Current versions of the script time out, say they are waiting, and retry;
older ones sat silent. It is safe to Ctrl-C and re-run either way.

**The login says the token was rejected.** The API reads `YAAF_ADMIN_TOKEN` at
boot, so editing `deploy/.env` does nothing until the container restarts:

```bash
cd deploy && docker compose up -d      # picks up the new .env
```

**The role pickers are empty when creating a project.** The library was never
seeded. Re-run `./deploy_single_server.sh` — it seeds only when the library is
empty, so this is safe.

**A run starts and immediately dies.** Usually the sandbox image is missing or
stale, because `docker compose up --build` does not build it:

```bash
docker build -f deploy/sandbox/Dockerfile -t yaaf-sandbox:latest .
```

### What comes up

| Service | What it is |
|---|---|
| `db` | Postgres 16, with a healthcheck the other services wait on |
| `api` | The API, the dispatcher and janitor threads, the Slack mirror and socket threads, and the built web UI at `/app` |
| `runner` | Leases runs, launches sandbox containers, meters everything |
| `minio` | Optional S3-compatible artifact store (`--profile s3`) |

The `sandbox` network is open — no proxy, no allowlist (the chairman's
decision; see docs/AUTONOMY.md). It still isolates stacks from each other.

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
| `YAAF_ADMIN_TOKEN` | `dev-admin-token` | Bearer token for the admin/chairman API and dashboard login. Any opaque string. **Change it** — see [The two secrets](#the-two-secrets) |
| `YAAF_MASTER_KEY` | — | Fernet key (url-safe base64, 32 bytes, padded) for envelope-encrypting secrets — see [The two secrets](#the-two-secrets) |
| `YAAF_API_BASE_URL` | `http://localhost:8080` | How sandboxes reach the platform API |
| `YAAF_API_HOST` / `YAAF_API_PORT` | `0.0.0.0` / `8080` | |

### Runner and sandbox

| Variable | Default | Notes |
|---|---|---|
| `YAAF_WORKER_NAME` | `worker-1` | Identifies this runner in leases |
| `YAAF_SANDBOX_MODE` | `docker` | `docker` (isolation) or `subprocess` (dev/CI only — **no isolation**) |
| `YAAF_SANDBOX_IMAGE` | `yaaf-sandbox:latest` | |
| `YAAF_SANDBOX_NETWORK` | `yaaf_sandbox` | |
| `YAAF_WORKSPACES_ROOT` | `/var/lib/yaaf/workspaces` | Host path must equal container path |
| `YAAF_ARTIFACTS_ROOT` | `/var/lib/yaaf/artifacts` | |

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

`./update_single_server.sh` dumps the database into `backups/` before every
upgrade, which covers the "that migration was a mistake" case but is not an
offsite backup — `backups/` lives on the same disk as the volume it came from.

**`YAAF_MASTER_KEY` is not recoverable.** Lose it and every stored secret
becomes unreadable — you'd re-enter all credentials by hand. Keep it wherever
you keep your other irreplaceable keys, not only in `deploy/.env`.

Workspaces (`/var/lib/yaaf/workspaces`) are working state; back them up if
agents keep uncommitted work there, or attach a GitHub repo per project and let
git be the durable copy.

### Upgrades

```bash
./update_single_server.sh
```

Pull, back up, rebuild both images, apply migrations, verify. **An update that
carries migrations needs no extra step** — they run from the entrypoint at
container start, and the script checks `alembic current` really says `(head)`
afterwards instead of assuming.

By hand it is the same three things, minus the checks:

```bash
git pull
docker build -f deploy/sandbox/Dockerfile -t yaaf-sandbox:latest .   # sandbox image
cd deploy && docker compose up --build -d                             # control plane
```

Take a dump first if you do it that way — `update_single_server.sh` refuses to
migrate without one, and a migration that applies cleanly can still be one you
want to undo:

```bash
mkdir -p backups && (cd deploy && docker compose exec -T db pg_dump -U yaaf yaaf) \
  | gzip > "backups/yaaf-$(date +%Y%m%d-%H%M%S).sql.gz"
```

Pre-1.0: read the commit log before upgrading a deployment with running
projects — APIs are still moving.

### Scaling

Add runners, not API servers. Runs are leased with a TTL heartbeat, so several
`yaaf-runner` processes (or boxes) safely share one database. Each needs
`YAAF_DATABASE_URL`, `YAAF_MASTER_KEY`, a unique `YAAF_WORKER_NAME`, and access
to Docker.

### When a run dies

Nothing needs you. The janitor reaps runs past `run_timeout_s`, releases their
task claims back to the queue, and the **side-effect ledger** prevents the
retry from double-firing anything external — no duplicate PR, tweet, or
payment.

---

## Repository layout

```
yaaf/server     API, policy engine (the refusals), dispatcher, janitor,
                evidence verifier, Slack bridge
yaaf/runner     leases runs, launches sandbox containers, meters everything
yaaf/core       models, constitution schema, crypto, config, artifacts
yaaf/cli        `yaaf` — admin/observe CLI, `yaaf spend-report`
web/            the chairman's dashboard (React, served at /app)
roles/          starter role library — thirty-two roles in six hiring aisles:
                general, tutor, adversary, markets desk, game studio,
                data-science bench
crews/          fourteen pre-staffed teams the hiring desk offers
deploy/         Dockerfiles and compose
evals/          golden-transcript evals — regression-gate for prompt edits
tests/          invariant suite over HTTP, chaos tests, eval checks
migrations/     Alembic
examples/       eleven importable projects (.md + .json)
docs/           this documentation
```
