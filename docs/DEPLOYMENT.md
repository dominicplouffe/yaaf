# Deployment

How yaaf runs on a server, and how to keep it running. To install it the first
time, start with [INSTALL.md](INSTALL.md).

## The shape

One server, Docker Compose, four containers:

| Container | What it does |
|---|---|
| `db` | Postgres 16. Holds all state. |
| `api` | `yaaf-server`: the API, the dashboard, the dispatcher and janitor. |
| `runner` | `yaaf-runner`: starts one sandbox container per agent run. |
| sandboxes | Short-lived containers from the `yaaf-sandbox` image, one per run. |

Optional, via compose profiles: `minio` (S3 storage for artifacts, `--profile s3`)
and `public` (a locked-down listener for claude.ai, `--profile public`).

One server is the intended setup, not a starter kit. Agents spend their time
waiting on model providers, so you hit provider rate limits long before you
run out of server. Need more? Add runners (see [Scaling](#scaling)).

**Server size:** each agent run gets up to 2 CPUs and about 2 GB. Size the box
for the number of runs your projects allow at once (each project's
concurrency cap is a setting).

## What the deploy script does

`deploy/deploy_single_server.sh` runs these steps. Each one is safe to repeat.

1. **Checks** Docker, `openssl` and `curl` are present.
2. **Creates folders** at `/var/lib/yaaf/workspaces` and `/var/lib/yaaf/artifacts`.
3. **Creates `deploy/.env`** with a new `YAAF_MASTER_KEY`. Never overwrites it.
4. **Binds the port** to `127.0.0.1:24247` (in `deploy/docker-compose.override.yml`).
5. **Builds two images:** the app image (`deploy/Dockerfile`) and the sandbox
   image (`deploy/sandbox/Dockerfile`).
6. **Starts** the stack. Migrations run when the containers start.
7. **Checks** the schema reached the latest migration.
8. **Creates the first admin** if there is none, and prints a setup link.
9. **Loads** the role library and ready-made teams. Adds only what is missing.

## Reaching the dashboard

By default yaaf listens on `127.0.0.1` only. Nothing is exposed, so there is
nothing to firewall. Pick one way in:

| Way | How | When |
|---|---|---|
| **SSH tunnel** | `ssh -N -L 24247:localhost:24247 you@server` | Just you. Simplest. |
| **Private network** | Tailscale or WireGuard, then `--bind <that address>` | You and your team, from anywhere |
| **Reverse proxy with TLS** | nginx or Caddy in front, `YAAF_PUBLIC_BASE_URL` set to the https address | A public hostname |

Two warnings:

- **Docker ignores `ufw`.** A port Docker publishes on `0.0.0.0` is open to the
  world, whatever `ufw status` says. Binding to `127.0.0.1` is what keeps it
  private.
- **Never expose plain HTTP.** Passwords and session cookies would cross the
  network unencrypted. Put TLS in front first.

## Connect claude.ai

Claude Code and Claude Desktop connect to `http://localhost:24247/mcp` with a
token, and need nothing here. claude.ai connects from Anthropic's servers, so it
needs a public https address.

**1. Set the public address** in `deploy/.env`, then re-run the deploy script:

```bash
YAAF_PUBLIC_BASE_URL=https://yaaf.example.com
```

**2. Publish only four paths.** Everything else, including the dashboard and
`/admin`, stays private.

Caddy:

```caddy
yaaf.example.com {
    @public path /mcp /.well-known/oauth-* /oauth/*
    handle @public { reverse_proxy 127.0.0.1:24247 }
    handle { respond 404 }
}
```

nginx:

```nginx
server {
    server_name yaaf.example.com;
    listen 443 ssl;

    location = /mcp                  { proxy_pass http://127.0.0.1:24247; }
    location ^~ /.well-known/oauth-  { proxy_pass http://127.0.0.1:24247; }
    location ^~ /oauth/              { proxy_pass http://127.0.0.1:24247; }
    location / { return 404; }
}
```

Using Tailscale Funnel? `docker compose --profile public up -d` starts a Caddy
listener on `127.0.0.1:8081` with these rules already in place
(`deploy/public/Caddyfile`). Point the funnel at it.

**3. Add the connector.** In claude.ai: Settings → Connectors → Add custom
connector, URL `https://yaaf.example.com/mcp`. Sign in with your yaaf account
and pick the scopes. Start with `read`.

If the connector can't register, check `YAAF_PUBLIC_BASE_URL` first. The
server logs the address it advertises at startup.

## Updating

```bash
deploy/update_single_server.sh
```

It refuses to run on a checkout with local edits, then: pulls, backs up the
database to `backups/`, rebuilds both images, restarts, and checks the
migrations applied. Migrations need no extra step.

| Flag | Effect |
|---|---|
| `--no-pull` | Rebuild what is checked out |
| `--no-backup` | Skip the backup (not recommended) |
| `--keep-chat` | Leave running CEO chat sessions on the old code |

yaaf is pre-1.0. Read the commit log before you update a server with running
projects.

**Roll back** by restoring the backup the update took:

```bash
gunzip -c backups/<file>.sql.gz | docker compose -f deploy/docker-compose.yml exec -T db psql -U yaaf -d yaaf
```

**Native installs** update with `yaaf update`. It backs up the SQLite database
to `~/.yaaf/backups`, pulls, rebuilds only what changed, and restarts.

## Backups

Three things matter. Back up all three, off the server.

| What | Where | Why |
|---|---|---|
| **Database** | Docker volume `pgdata` | Everything yaaf knows |
| **Master key** | `deploy/.env` | Without it, every stored API key is unreadable. **It cannot be recovered.** |
| **Artifacts** | `/var/lib/yaaf/artifacts` | Files your agents produced |

Workspaces (`/var/lib/yaaf/workspaces`) are working files. Back them up if
agents keep work there, or connect a GitHub repo to the project and let git
hold it.

A manual database backup:

```bash
mkdir -p backups
docker compose -f deploy/docker-compose.yml exec -T db pg_dump -U yaaf yaaf \
  | gzip > "backups/yaaf-$(date +%Y%m%d-%H%M%S).sql.gz"
```

The backups the update script takes live on the same disk. They protect you
from a bad migration, not from a dead disk.

## Stopping

```bash
deploy/kill_single_server.sh
```

Stops everything and keeps all data. Run the deploy script to bring it back.

| Flag | Effect |
|---|---|
| `--wipe` | **Deletes the database.** Asks you to type `DESTROY` first. |
| `--sandboxes` | Only clean up leftover sandbox containers |
| `--keep-sandboxes` | Stop, but leave sandbox containers alone |

## Health

- `GET /healthz`: the process is alive.
- `GET /readyz`: the database is reachable and the schema exists. Returns 503
  if not. Point your monitoring here.
- `yaaf doctor`: checks the whole install and says what to fix.
- `yaaf smoke <credential>`: one real, tiny call per model key, to prove the
  key and network path work.
- Logs: `docker compose -f deploy/docker-compose.yml logs -f api runner`.

## Network

Agents need outbound HTTPS to the model providers you use. The server needs it
too (for key checks and the Founder), plus:

- `raw.githubusercontent.com` for the model price feed. Without it, turn
  upkeep off (`yaaf upkeep --disable`) and import prices by hand.
- `oauth2.googleapis.com` if you use Vertex AI.

Sandboxes have open outbound network access. A local model server (Ollama, LM
Studio, vLLM) must join the sandbox network (`YAAF_SANDBOX_NETWORK`), and its
address is its container name, like `http://ollama:11434/v1`, not `localhost`.

## Scaling

Add runners, not servers. Runs are leased from the database, so several
runners on several machines can share one database safely. Each runner needs:

- the same `YAAF_DATABASE_URL` and `YAAF_MASTER_KEY`
- its own `YAAF_WORKER_NAME`
- Docker, with the sandbox image built

## Two stacks on one server

To run a staging copy beside production, set these four in the second stack's
`deploy/.env`, and use another port (`--port 8081`):

```bash
YAAF_STACK=staging
YAAF_STATE_ROOT=/var/lib/yaaf-staging
YAAF_SANDBOX_NETWORK=yaaf_sandbox_staging
YAAF_SANDBOX_IMAGE=yaaf-sandbox:staging
```

Skip any one and the two stacks share containers, files, a network or code.
See [CONFIGURATION.md](CONFIGURATION.md#running-a-second-stack).

## Doing it by hand

The deploy script is the supported path. If you need to see every step:

```bash
sudo mkdir -p /var/lib/yaaf/workspaces /var/lib/yaaf/artifacts
echo "YAAF_MASTER_KEY=$(openssl rand -base64 32)" > deploy/.env

cat > deploy/docker-compose.override.yml <<'YML'
services:
  api:
    ports: !override
      - "127.0.0.1:24247:8080"
YML

docker build -f deploy/sandbox/Dockerfile -t yaaf-sandbox:latest .
(cd deploy && docker compose up -d --build)
(cd deploy && docker compose exec -T api alembic current)    # expect "(head)"
(cd deploy && docker compose exec -T api yaaf users bootstrap \
   --email you@example.com --base-url http://localhost:24247)
```

Then load the role library from a checkout with the CLI installed:

```bash
export YAAF_API=http://localhost:24247
yaaf login
yaaf seed-roles roles/ --only-missing
yaaf seed-crews crews/ --only-missing
```

Notes:

- The `!override` tag matters. Without it, compose keeps the original
  `0.0.0.0` binding as well.
- `docker compose up --build` does not build the sandbox image. Build it
  yourself.
- `docker compose exec api yaaf seed-roles roles/` does nothing: the image has
  no `roles/` folder. Load roles from the checkout, as above.
