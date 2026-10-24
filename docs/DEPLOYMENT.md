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

**The short way:** from a checkout with the dashboard built (`cd web && npm install
&& npm run build`), `yaaf up --no-isolation --email you@example.com` does every step
below: it keeps a master key and a SQLite database in `~/.yaaf`, creates the schema,
starts the server and the runner, makes you the first admin and prints your sign-in
link, seeds an empty role library from `roles/` and `crews/`, and prints the address.
Ctrl-C (or any SIGTERM) stops both processes. `--no-isolation` is required on
purpose: it runs agents as you, with no sandbox, and yaaf will not choose that for
you — set `YAAF_SANDBOX_MODE=docker` instead on anything other than a dev machine.

The compose deployment does four things for you that you have to do by hand
here. Miss any one and you get a project that looks alive and never moves — so
each is called out.

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"      # Python 3.12+
.venv/bin/python -m pytest tests/ -n auto    # 796 tests, offline; 1 skips without Postgres

# 1. The dashboard is built, not shipped: web/dist is gitignored, and without
#    it the API serves a JSON note instead of /app.
cd web && npm install && npm run build && cd ..

# 2. YAAF_MASTER_KEY encrypts every stored secret. Without it, saving the
#    project's Anthropic key answers 500 and the project can never start.
export YAAF_DATABASE_URL=sqlite:////tmp/yaaf.db \
       YAAF_SANDBOX_MODE=subprocess \
       YAAF_API=http://localhost:8080 \
       YAAF_MASTER_KEY=$(.venv/bin/python -c \
         "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

.venv/bin/python -c "from yaaf.core.db import engine; \
  from yaaf.core.models import Base; Base.metadata.create_all(engine())"

.venv/bin/yaaf-server &     # API, dashboard, dispatcher, janitor
# 3. The server does NOT execute runs. Without a runner, every run sits in
#    run_queue forever and the project never does anything at all.
.venv/bin/yaaf-runner &

# 4. Nobody can sign in to a box with no admin. This makes you one and prints
#    a one-time link to choose your password; `yaaf login` then gives the CLI
#    a personal token of its own.
.venv/bin/yaaf users bootstrap --email you@example.com --name "You"
.venv/bin/yaaf login --email you@example.com
.venv/bin/yaaf seed-roles roles/
.venv/bin/yaaf seed-crews crews/   # the "start from a crew" picker reads these
.venv/bin/yaaf doctor              # checks all of the above, names any fix
```

If the API is not on port 8080, also export `YAAF_API_BASE_URL`: it is what
the runner calls and what the bootstrap link is built from, and it defaults to
`http://localhost:8080` — so on another port the runner talks to nothing, or
to somebody else's server, without an error you would notice.

Open the link `bootstrap` printed, choose a password, and you are in the
dashboard at **http://localhost:8080/app**. See [AUTH.md](AUTH.md) for who
may do what once more people are on the box.

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
an agent name; the tests and the eval harness register a project's worth of
them (`evals/behaviors.py`) and drive planning, decomposition, claims,
completions, evidence and every governance trip at $0. That is what CI runs.

**Nothing is registered by default.** On your own install a mock agent reads its
context pack and exits, which is exactly right for testing the platform and
useless as a demo. If you want to watch a scripted project move, run the eval
harness (`python -m evals.run`) rather than pointing a project at mock.

## The real thing — your own server

```bash
curl -fsSL https://get.docker.com | sh          # if docker isn't there yet
sudo usermod -aG docker "$USER"                 # then log out and back in

git clone <your-yaaf-remote> yaaf && cd yaaf
deploy/deploy_single_server.sh
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
| *(none)* | dashboard on `127.0.0.1:8080`, reachable over an SSH tunnel — or whatever the last run published on |
| `--bind 0.0.0.0` | publish it on every interface — read the warning it prints |
| `--bind 127.0.0.1 --bind 192.168.0.5` | repeatable: one published port per address, so localhost and the LAN address both answer |
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
   [The master key](#the-master-key).
4. **Port binding** — writes `deploy/docker-compose.override.yml`. The
   `!override` tag matters: without it compose *appends* to `ports` and you
   keep the original `0.0.0.0` binding you were trying to remove.
5. **Both images** — `yaaf-sandbox:latest` is a separate `docker build` that
   compose knows nothing about. Skipping it leaves agent runs on a stale image.
6. **Schema** — waits for migrations, then checks `alembic current` really
   says `(head)`.
7. **The first admin** — on a box with none, makes the account named by
   `--admin-email` (or `YAAF_ADMIN_EMAIL` in `deploy/.env`, or what you type
   when asked) the admin and prints its one-time setup link. A box that has an
   admin is left alone.
8. **Role library** — mints a ten-minute personal token inside the `api`
   container, loads `roles/*.json` over HTTP with it, and refiles the roles
   already there into their hiring categories (no new versions, so no live
   team goes behind its prompt). Then `crews/*.json`, on the same
   only-what-is-missing contract.

### Living with it

```bash
deploy/update_single_server.sh    # pull, back up, rebuild, migrate, verify
deploy/kill_single_server.sh      # stop, keep data      (--wipe destroys the database)
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
workspaces and artifacts all survive, so `deploy` brings the same project
back. `--wipe` destroys the database volume and asks you to type `DESTROY`
first. It also clears the sandbox containers the runner launched: those are
siblings rather than compose services, so `docker compose down` leaves them
behind, and a long-running box accumulates dozens of exited ones.
`--sandboxes` does only that cleanup; `--keep-sandboxes` skips it. The sweep
is box-wide, which is right for a single-server deployment and surprising if
you ever run two stacks on one host.

### The master key

Compose reads `deploy/.env` from the directory you run it in, which is why it
goes in a file rather than on the command line: the value must be **identical
on every boot**, and it fails quietly rather than loudly if it is not.
`deploy/.env.example` is an annotated template if you would rather fill it in
by hand; `deploy/.env` itself is gitignored.

There is no login secret beside it any more. `YAAF_ADMIN_TOKEN` was retired —
people sign in with an account ([AUTH.md](AUTH.md)) — and a line left over in
an old `deploy/.env` is read by nothing; the scripts say so and it is safe to
delete.

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
substitutes an empty value. Neither is recoverable — back it up. Migrations
read it too — `0034` decrypts stored billing keys to tell API keys from
subscription tokens — so run `alembic upgrade` where it is set, which the
container entrypoints already do.

---

## Reaching a server you SSH into

`deploy/deploy_single_server.sh` binds the dashboard to `127.0.0.1` precisely so
that a remote box does not publish it. Nothing listens on the network, so
there is nothing to firewall. Reach it by forwarding the port from your
laptop:

```bash
ssh -N -L 8080:localhost:8080 you@your-server
```

Then open **http://localhost:8080/app** and sign in. On a first install the
script ends with the one-time link that sets the first admin's password; lost
it, or forgot the password later? From a shell on the box:

```bash
docker compose -f deploy/docker-compose.yml exec api yaaf users setup-link you@example.com
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

echo "YAAF_MASTER_KEY=$(openssl rand -base64 32)" > deploy/.env

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

# The first admin, and a short-lived token to load the library with.
(cd deploy && docker compose exec -T api yaaf users bootstrap --email you@example.com \
   --base-url http://localhost:8080)
TOKEN=$(cd deploy && docker compose exec -T api yaaf users token --first-admin \
          --name deploy --minutes 10)

# roles/ is not in the image, so this is an HTTP load from the checkout.
for f in roles/*.json; do
  curl -fsS -X POST http://localhost:8080/admin/roles \
    -H "Authorization: Bearer $TOKEN" \
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
because passwords and session cookies cross that connection in cleartext
otherwise. Set `YAAF_PUBLIC_BASE_URL` to the https origin when you do: the
session cookie is marked `Secure` from it.

**Hand-editing that binding without `!override` silently does nothing.** Compose *appends*
list values when it merges, so an override without the tag leaves you with
both bindings — the loopback one you wanted and the original `0.0.0.0` one
you were trying to remove. `docker compose config | grep -E 'host_ip|published'`
is the check: exactly one `published: "8080"`, carrying `host_ip: 127.0.0.1`.

**`docker compose exec api yaaf seed-roles roles/` looks like it works and
does nothing.** The image has no `roles/` directory, and the command globs an
empty path, prints nothing, and exits 0. `deploy/deploy_single_server.sh` loads them
over HTTP; by hand it is the loop above, or from a checkout with the CLI
installed:
`YAAF_API=http://localhost:8080 yaaf login && yaaf seed-roles roles/` (an
instance admin's account — the library is the box's).

**Skipping the role seed is survivable only if your bundles carry their
roles.** An export from another install does carry them, and the project wizard
creates those at submit time. The bundles in [`examples/`](../examples/) do not:
they hire from the library by slug, so on an unseeded deployment their teams
land on the New Project page with the role picker showing "not in this
install's role library" — and a from-scratch project has nothing to staff
itself with either.

**A library seeded once does not grow by itself.** Both scripts load only the
slugs the library is missing, so a role added to the repository after your
first deploy arrives on the next `deploy/update_single_server.sh`, and no role you
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

**Nobody can sign in.** A box upgraded from before accounts existed has no
admin until one is made — the chairman's own email keeps every project they
chair:

```bash
deploy/update_single_server.sh --admin-email you@example.com
```

A forgotten password is a fresh setup link, from a shell on the box:
`docker compose -f deploy/docker-compose.yml exec api yaaf users setup-link you@example.com`.
Ten wrong passwords for one address lock that address for fifteen minutes.

**The role pickers are empty when creating a project.** The library was never
seeded. Re-run `deploy/deploy_single_server.sh` — it seeds only when the library is
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

### Providers, and what they need from the network

Every provider is reached from two places: the **sandbox**, where a seat's run
calls its provider's API (yaaf's loop copies `yaaf/runtimes/wire.py` into the
image for that), and the **control plane**, which calls the same APIs for the
Founder, a credential's **Check**, model discovery and `yaaf smoke`. Both need
outbound HTTPS to the providers an install pays — and the control plane needs
it to `oauth2.googleapis.com` too if Vertex AI is one of them, because it mints
Vertex's tokens there (the service account key never enters a sandbox).

- **A local model server** (Ollama, LM Studio, vLLM) is reached *from the
  sandbox*, so its credential's address is the server's name on the sandbox
  network — `http://ollama:11434/v1`, not `localhost` — and the server must be
  attached to that network (`YAAF_SANDBOX_NETWORK`).
- **The price feed** is read by the control plane when an administrator
  imports prices (Settings → Model prices → Import). An install with no route
  to `raw.githubusercontent.com` imports the snapshot shipped with yaaf, or
  points the feed at a mirror of its own.
- **The web search backend**, when one is configured, is called from the
  sandbox, like the providers.

After deploying, `yaaf smoke <credential>` makes one real, trivial call per
credential through the wire its seats will use — the quickest proof that a
key, an address and a network path all work before a project depends on them.

---

## Exposing the MCP server to claude.ai

Only needed if you want to chair from the browser. Claude Code and Claude
Desktop reach `http://localhost:8080/mcp` with a minted token and need none of
this ([MCP_SERVER.md](MCP_SERVER.md)).

claude.ai connects **inbound, from Anthropic's servers**, so the install needs a
public hostname and TLS. Two things follow, and the second one bites.

**Set the origin.** The OAuth audience is derived from it, so a wrong value
breaks every token with a message about the wrong resource:

```bash
YAAF_PUBLIC_BASE_URL=https://yaaf.example.com
```

Leaving it unset is worse than getting it wrong, because it reports as neither.
Both `.well-known` documents build every endpoint they publish from this value,
and unset it falls back to `YAAF_API_BASE_URL` — `http://api:8080` under
compose, a name that resolves only inside that network. The documents still
answer 200. The connector reads them, cannot reach the registration endpoint
they name, and says only that it could not register with your sign-in service,
which sends you to look at OAuth. The server logs a warning at startup naming
the advertised origin; if a connector cannot register, read that line first.

**Publish four paths and nothing else.** Publishing the box publishes `/admin`
and the dashboard's password form to the internet, and a sign-in page nobody
outside needs is a sign-in page somebody outside can guess at. The public
listener serves discovery, the consent flow and the MCP endpoint; everything
else 404s and stays on your private listener.

```nginx
server {
    server_name yaaf.example.com;
    listen 443 ssl;

    location = /mcp                          { proxy_pass http://127.0.0.1:8080; }
    location ^~ /.well-known/oauth-           { proxy_pass http://127.0.0.1:8080; }
    location ^~ /oauth/                       { proxy_pass http://127.0.0.1:8080; }
    location / { return 404; }
}
```

```caddy
yaaf.example.com {
    @public path /mcp /.well-known/oauth-* /oauth/*
    handle @public { reverse_proxy 127.0.0.1:8080 }
    handle { respond 404 }
}
```

**Sign in with your own account.** The consent screen authenticates a *person*
— the same email and password as the dashboard, asked for again even when a
dashboard session exists, because handing a machine a credential is the moment
to prove it is still you. A personal token is refused there on purpose.

Then in claude.ai: **Settings → Connectors → Add custom connector**, URL
`https://yaaf.example.com/mcp`. It registers itself (RFC 7591), sends you to
your own consent screen, and you tick the scopes it may hold. Start with `read`
alone — a client reaches the projects you are a member of, deciding only where
you chair or co-chair, so scopes are the rest of the narrowing.

If a client's redirect host is not `claude.ai`, registration refuses it — widen
`YAAF_MCP_REDIRECT_HOSTS` deliberately, never by default.

---

## Configuration

Everything **operational** is environment (`YAAF_`-prefixed, also readable from
a `.env`). Everything **governed** lives in the database, per project — never
in env vars. See [GOVERNANCE.md](GOVERNANCE.md).

### Core

| Variable | Default | Notes |
|---|---|---|
| `YAAF_DATABASE_URL` | `postgresql+psycopg://yaaf:yaaf@localhost:5432/yaaf` | SQLite works for dev |
| `YAAF_MASTER_KEY` | — | Fernet key (url-safe base64, 32 bytes, padded) for envelope-encrypting secrets — see [The master key](#the-master-key) |
| `YAAF_ADMIN_EMAIL` | — | Read by the deploy and update scripts only: the account to make the first admin on a box with none. People sign in with accounts — see [AUTH.md](AUTH.md); `YAAF_ADMIN_TOKEN` is retired and nothing reads it |
| `YAAF_API_BASE_URL` | `http://localhost:8080` | How sandboxes reach the platform API |
| `YAAF_API_HOST` / `YAAF_API_PORT` | `0.0.0.0` / `8080` | |

### Runner and sandbox

| Variable | Default | Notes |
|---|---|---|
| `YAAF_WORKER_NAME` | `worker-1` | Identifies this runner in leases |
| `YAAF_SANDBOX_MODE` | `docker` | `docker` (isolation) or `subprocess` (dev/CI only — **no isolation**) |
| `YAAF_ALLOW_HOST_SUCCESS_TESTS` | `false` | Under `subprocess` mode only, lets an agent-authored success test run on a host that holds the Docker socket. **Leave it off on a deployed runner** — there the socket is root on the box. The test suite sets it for itself. |
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
  same one the Team page renders. All green = the UI hides the card.
- Dispatcher, janitor, and Slack threads log at INFO to the `api` container. A
  plan meter throttled by Anthropic logs one WARNING naming the token digest and
  how long it is staying quiet; it never retries through a `Retry-After`. A
  token Anthropic *refuses* logs the same way with its status code — usually an
  expired subscription token, and the chairman pressing **Check now** is told so
  on screen, so the log is corroboration rather than the only notice.

### Backups

Everything that matters is in **Postgres** (`pgdata` volume) plus the
**artifacts** directory. Back up both.

`deploy/update_single_server.sh` dumps the database into `backups/` before every
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
deploy/update_single_server.sh
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

Take a dump first if you do it that way — `deploy/update_single_server.sh` refuses to
migrate without one, and a migration that applies cleanly can still be one you
want to undo:

```bash
mkdir -p backups && (cd deploy && docker compose exec -T db pg_dump -U yaaf yaaf) \
  | gzip > "backups/yaaf-$(date +%Y%m%d-%H%M%S).sql.gz"
```

Pre-1.0: read the commit log before upgrading a deployment with running
projects — APIs are still moving.

#### What "the bar" release does to projects that already exist

Rehearsed rather than reasoned about: a project was created on the previous
commit, the migrations were applied over it, and the answers below are what
that project actually did afterwards. Migration `0026` adds one nullable
column and backfills nothing.

**Nothing is re-hired.** The new craft dial (`stakes: high` buying a stronger
model and deeper reasoning for the seats that produce or check the deliverable)
is applied *at hire*, so it never reaches back into a roster that already
exists. A project that had `stakes: high` set before the upgrade came out the
other side with every seat still on the model its role names. `deliverable_review`
reads as `none` for any constitution that never mentioned it.

**Three things do change, and only the first needs no action from you:**

- **Agents are told something different on their next run.** System prompts are
  rebuilt every wake, so the house style's split lands immediately: the
  length rule now applies to messages and chat, and a deliverable is aimed at
  the goal's beneficiary and standard instead of at brevity. This is the one
  change with no opt-in, and it is the point of the release — a blog writer
  used to read "a longer answer is not a better one" before it read its task.
- **The role library does not reach running teams.** A team keeps the role
  version it was hired on, so the rewritten `writer`, `researcher` and
  `marketer` prompts and the new `editor` role apply to **new projects only**.
  To adopt them on a project that already exists, use *adopt latest role
  version* on the team (`POST /admin/teams/{id}/repin`) — per team, and
  deliberately, which is the same door the drift warning has always pointed at.
- **A team added to a `stakes: high` project is hired on the new terms.** It
  will run on a stronger model than its existing siblings, and cost more per
  run than they do. `add_team` also stopped dropping the per-employee wakes and
  model overrides its own spec carried, which it had been doing silently.

**One thing that can start refusing.** `effort` is now a closed set
(`low|medium|high|xhigh|max`) rather than free text, on roles and on the new
per-seat override. Re-seeding a role whose effort is spelled anything else now
returns 422 instead of storing a value that would have killed the run inside
the sandbox. Roles already in the database are not re-validated.

#### What the managed-credentials release does to projects that already exist

Migration `0034` moves every project's billing key out of the project and
into the app-wide list of LLM credentials, **without touching its encrypted
value or its assignment**: a project that paid with a key before the upgrade
pays with the same key afterwards, and its next run cannot tell the
difference. Three things are worth knowing:

- **The names are placeholders.** A migrated key is called
  `Migrated <old name> (<id>)`, because two projects could each have had a key
  named `anthropic-api-key` and the shared list needs distinct names. Open
  Settings → LLM credentials once and rename them; the Team page of every
  project on a credential shows the name.
- **Its billing mode is read from the project it paid for.** A key no project
  was attached to is decrypted to tell an API key from a subscription token —
  which is why the migration needs `YAAF_MASTER_KEY`. A project that had been
  attached to the Founder's own credential (the old attachment endpoint
  allowed it) gets a copy as a proper LLM credential — one copy per billing
  mode those projects were using — so rotating the Founder's key never again
  changes what a project pays with. A stored key the master key cannot read
  is kept and labelled an API key rather than aborting the migration.
- **Two API calls change their answer.** Storing a project secret with
  `kind: billing` returns 422 and points at Settings; deleting a credential a
  project is assigned to returns 409 until it is reassigned. `yaaf set-secret
  --kind billing` is gone with the first; `yaaf create-llm-credential`
  replaces it.

There is no downgrade: the migration refuses to move shared credentials back
into projects. Take the dump first, as above.

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
deploy/         Dockerfiles, compose, and the install/update/stop scripts
evals/          golden-transcript evals — regression-gate for prompt edits
tests/          invariant suite over HTTP, chaos tests, eval checks
migrations/     Alembic
examples/       eleven importable projects (.md + .json)
docs/           this documentation
```
