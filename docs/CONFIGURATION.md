# Configuration

yaaf reads its settings from environment variables that start with `YAAF_`.

Two kinds of settings, kept apart on purpose:

- **Operational** (this page): how the install runs. Database, ports, keys,
  sandboxes, email. Set by whoever runs the server.
- **Governed**: how a project behaves. Budget, deadline, wake limits,
  approvals. Stored in the database, per project, and changed in the dashboard.
  Never in environment variables. See [GOVERNANCE.md](GOVERNANCE.md).

**Model API keys are not environment variables.** Add them in the dashboard
under Settings → Providers, or with `yaaf create-llm-credential`. They are
stored encrypted.

## What you must set

| Install | You set by hand | Recommended |
|---|---|---|
| Docker (`deploy_single_server.sh`) | Nothing. The script creates `YAAF_MASTER_KEY`. | `YAAF_PUBLIC_BASE_URL` if public, SMTP for email alerts |
| Native (`yaaf setup`) | Nothing. Setup creates everything in `~/.yaaf`. | SMTP for email alerts |
| Your own setup | `YAAF_DATABASE_URL`, `YAAF_MASTER_KEY`, `YAAF_SANDBOX_MODE`, `YAAF_API_BASE_URL` | Everything under [Core](#core) |

## Where to put them

**Docker.** Put them in `deploy/.env`, then re-run `deploy/deploy_single_server.sh`.
Containers read settings only when they start.

Only these variables are passed from `deploy/.env` into the containers:
`YAAF_MASTER_KEY`, `YAAF_PUBLIC_BASE_URL`, the `YAAF_SMTP_*` group,
`YAAF_CHAIRMAN_WEBHOOK_URL`, and the four [second-stack](#running-a-second-stack)
variables. For anything else, add it to `deploy/docker-compose.override.yml`:

```yaml
services:
  api:
    environment:
      YAAF_MCP_REDIRECT_HOSTS: "claude.ai,claude.com,my-client.example.com"
```

**Native.** `yaaf setup` writes what it needs. To change something, export it
before `yaaf up`, or edit the service with `systemctl --user edit yaaf`.

**Anywhere else.** Export them, or put them in a `.env` file in the folder you
start `yaaf-server` and `yaaf-runner` from.

---

## Core

| Variable | Default | What it does |
|---|---|---|
| `YAAF_DATABASE_URL` | `postgresql+psycopg://yaaf:yaaf@localhost:5432/yaaf` | Where all state lives. Postgres for servers. SQLite works too: `sqlite:////path/to/yaaf.db` |
| `YAAF_MASTER_KEY` | *(none)* | Encrypts every stored secret. **Required. Never change it. Back it up.** See [below](#the-master-key). |
| `YAAF_API_HOST` | `0.0.0.0` | Address the server listens on |
| `YAAF_API_PORT` | `24247` | Port the server listens on |
| `YAAF_API_BASE_URL` | `http://localhost:24247` | How the runner and sandboxes reach the server. Change it if you change the port. |
| `YAAF_PUBLIC_BASE_URL` | *(none)* | The https address people use, like `https://yaaf.example.com`. Needed for claude.ai, OAuth data sources, and secure cookies. |
| `YAAF_MCP_REDIRECT_HOSTS` | `claude.ai,claude.com,localhost,127.0.0.1` | Hosts an MCP client may return to after sign-in. Widen with care. |

### The master key

`YAAF_MASTER_KEY` must be a Fernet key: 32 random bytes, url-safe base64,
with padding. Make one with either:

```bash
openssl rand -base64 32
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Do not use Python's `secrets.token_urlsafe(32)`. It drops the padding and
fails the first time you save a secret.

- **If you change it,** every stored key becomes unreadable.
- **If you lose it,** the same. There is no recovery.
- **If it is empty,** the server refuses to save or read any secret.

Docker keeps it in `deploy/.env`. Native mode keeps it in `~/.yaaf/master.key`.
Keep a copy somewhere safe, away from the machine.

## Runner and sandboxes

| Variable | Default | What it does |
|---|---|---|
| `YAAF_SANDBOX_MODE` | `docker` | Where agent runs execute. `docker`: a container per run. `host`: a bubblewrap jail on this machine. `subprocess`: no isolation, development only. |
| `YAAF_WORKER_NAME` | `worker-1` | This runner's name. Must be unique when you run several. |
| `YAAF_SANDBOX_IMAGE` | `yaaf-sandbox:latest` | Docker image for agent runs |
| `YAAF_SANDBOX_NETWORK` | `yaaf_sandbox` | Docker network sandboxes join |
| `YAAF_WORKSPACES_ROOT` | `/var/lib/yaaf/workspaces` | Project working folders. In Docker the host path and container path must match. |
| `YAAF_ARTIFACTS_ROOT` | `/var/lib/yaaf/artifacts` | Files agents produce |
| `YAAF_STACK` | `deploy` | Labels the containers this install owns. See [second stack](#running-a-second-stack). |
| `YAAF_ALLOW_HOST_SUCCESS_TESTS` | `false` | `subprocess` mode only. Lets agent-written tests run on the host. **Keep it off on any real server.** |

### Native (host) mode only

| Variable | Default | What it does |
|---|---|---|
| `YAAF_HOST_RUN_CPUS` | `2.0` | CPU cap per run. `0` means no cap. |
| `YAAF_HOST_RUN_MEMORY_GB` | `2.0` | Memory cap per run, in GB. `0` means no cap. |
| `YAAF_HOST_JAIL_BINDS` | *(empty)* | Extra read-only paths a run may see, separated by `:`. Use it when a CLI like Codex lives in your home folder. `yaaf setup` fills this for you. |

## Email and alerts

Optional. Without them, alerts only go to the logs and the dashboard.

| Variable | Default | What it does |
|---|---|---|
| `YAAF_SMTP_HOST` | *(none)* | Mail server. Setting it turns email on. |
| `YAAF_SMTP_PORT` | `587` | Use 587 (STARTTLS). Port 465 does not work. |
| `YAAF_SMTP_USERNAME` | *(none)* | Leave empty for a relay that needs no login |
| `YAAF_SMTP_PASSWORD` | *(none)* | For Gmail, an App Password |
| `YAAF_SMTP_FROM` | the username | Sender address. Must be one your provider lets you send as. |
| `YAAF_CHAIRMAN_WEBHOOK_URL` | *(none)* | Any URL that accepts a JSON POST with a `text` field, like a Slack incoming webhook |

You get alerts for budget pauses, approvals waiting on you, and runs that die.

## Artifact storage (S3)

Optional. By default artifacts are files under `YAAF_ARTIFACTS_ROOT`. To use
S3, MinIO or Cloudflare R2, install `pip install 'yaaf[s3]'` and set:

| Variable | Default |
|---|---|
| `YAAF_ARTIFACT_S3_ENDPOINT` | *(none; setting it turns S3 on)* |
| `YAAF_ARTIFACT_S3_BUCKET` | `yaaf-artifacts` |
| `YAAF_ARTIFACT_S3_ACCESS_KEY` | *(none)* |
| `YAAF_ARTIFACT_S3_SECRET_KEY` | *(none)* |

Set the same values on both the server and the runner.

The bundled MinIO (`docker compose --profile s3 up -d`) reads
`MINIO_ROOT_USER` (default `yaaf`) and `MINIO_ROOT_PASSWORD`. **Change the
password**; the default is for development.

## Timing

You rarely need these. All values are seconds.

| Variable | Default | What it does |
|---|---|---|
| `YAAF_DISPATCH_POLL_S` | `2.0` | How often the dispatcher looks for work |
| `YAAF_JANITOR_POLL_S` | `15.0` | How often the janitor enforces rules |
| `YAAF_RUNNER_POLL_S` | `2.0` | How often a runner looks for queued runs |
| `YAAF_LEASE_HEARTBEAT_S` | `20.0` | How often a running job checks in |
| `YAAF_LEASE_TTL_S` | `90.0` | How long without a check-in before a run is declared dead |

## Running a second stack

To run a staging copy on the same server as production, set all four in the
second stack's `deploy/.env`. Leave them unset for a normal install.

| Variable | Default | Why it must differ |
|---|---|---|
| `YAAF_STACK` | `deploy` | Otherwise both stacks share containers and the database volume |
| `YAAF_STATE_ROOT` | `/var/lib/yaaf` | Otherwise both write the same workspaces and artifacts |
| `YAAF_SANDBOX_NETWORK` | `yaaf_sandbox` | Otherwise one stack's runs can reach the other's server |
| `YAAF_SANDBOX_IMAGE` | `yaaf-sandbox:latest` | Otherwise building one stack's image changes the other's runs |

`YAAF_STATE_ROOT` is read by the compose file and scripts only. Use the same
`YAAF_MASTER_KEY` as production if you will restore a production backup.

## Deploy scripts

| Variable | What it does |
|---|---|
| `YAAF_ADMIN_EMAIL` | In `deploy/.env`: the account to make admin on a fresh install. Same as `--admin-email`. |
| `YAAF_ACCEPT_NATIVE` | Set to `1` to skip the native installer's "are you sure" question |

## Command line (`yaaf`)

| Variable | Default | What it does |
|---|---|---|
| `YAAF_API` | `http://localhost:24247` | Which yaaf the CLI talks to |
| `YAAF_TOKEN` | *(none)* | A personal token, instead of `yaaf login`. For scripts. |
| `YAAF_HOME` | `~/.yaaf` | Native mode's data folder |
| `YAAF_WEB_DIST` | `web/dist` | Where the built dashboard is |
| `YAAF_TERMINAL` | *(auto)* | Terminal command for the desktop extras, like `foot` or `kitty` |

## Set by yaaf, not by you

Inside a sandbox, yaaf sets its own variables for each run (`YAAF_RUN_ID`,
`YAAF_MODEL`, `YAAF_PROVIDER`, `YAAF_API_TOKEN` and others). Do not set them
yourself.

`YAAF_ADMIN_TOKEN` is retired. People sign in with accounts. If an old
`deploy/.env` still has it, delete the line.
