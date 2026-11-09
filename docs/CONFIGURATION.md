# Configuration

yaaf reads its settings from environment variables that start with `YAAF_`.

Two kinds of settings, kept apart on purpose:

- **Operational** (this page): how the install runs. Database, ports, keys,
  sandboxes, email. Set by whoever runs the server.
- **Governed**: how a project behaves. Budget, deadline, wake limits,
  approvals. Stored in the database, per project, and changed in the dashboard.
  Never in environment variables.

**Model API keys are not environment variables.** Add them in the dashboard
under Settings → Providers, or with `yaaf create-llm-credential`. They are
stored encrypted. See [PROVIDERS.md](PROVIDERS.md).

## What you must set

| Install | You set by hand | Recommended |
|---|---|---|
| Docker (`deploy_single_server.sh`) | Nothing. The script creates `YAAF_MASTER_KEY`. | `YAAF_PUBLIC_BASE_URL` if public, SMTP for email alerts |
| Native (`yaaf setup`) | Nothing. Setup creates everything in `~/.yaaf`. | SMTP for email alerts |
| Your own setup | `YAAF_DATABASE_URL`, `YAAF_MASTER_KEY`, `YAAF_SANDBOX_MODE`, `YAAF_API_BASE_URL` | Everything under [Core](#core) |

## Where to put them

**Docker.** Put them in `deploy/.env`, then re-run `deploy/deploy_single_server.sh`.
Every variable in that file reaches both the `api` and `runner` containers.
Containers read settings only when they start.

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
| `YAAF_API_HOST` | `127.0.0.1` | Address the server listens on. The Docker image sets `0.0.0.0` inside the container; the published port decides who can reach it. |
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

- **If you lose it,** every stored secret is unreadable. There is no recovery.
- **If you edit it by hand,** the same. To change it, rotate it (below).
- **If it is empty,** the server refuses to save or read any secret.

Docker keeps it in `deploy/.env`. Native mode keeps it in `~/.yaaf/master.key`.
Keep a copy somewhere safe, away from the machine.

**Rotating it.** This re-encrypts every stored secret under a new key, all at
once. If any value can't be read, nothing changes.

| Install | Command |
|---|---|
| Docker | `deploy/rotate_master_key.sh` (backs up, stops yaaf, rotates, updates `deploy/.env`, starts yaaf) |
| Native | `systemctl --user stop yaaf`, then `yaaf rotate-master-key`, then `systemctl --user start yaaf` |

Keep the old key. Backups taken before the rotation can only be read with it.
The Docker script saves it as `deploy/.env.<date>.bak`; native mode keeps it
as `~/.yaaf/master.key.<date>.old`.

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

Which notices become email is each project's own choice, in Settings → Rules
in force → Rhythm & reporting:

- **Email me when** — by default, only when your CEO is waiting on you (a
  decision, a question, a need, a heads-up). Widen it to add alarms (the
  project paused, a promise broken, a credential dead), or everything,
  including the platform's own reminders. Or turn email off.
- **Minutes to answer in the app before you are emailed** — default 60. A
  request you answer or snooze in the app before then is never emailed.
- **A daily briefing at this time** — the one daily email, set on its own.

The webhook always receives every notice at once, whatever these say.

These settings are for the platform's alerts to you only. A project's own
email address is set separately, below.

## A project's email address

A project can have its own mailbox. The CEO can email a finished deliverable to
someone and read the reply, and people on the list can write to the project
first. That someone can be a person or another agent, on yaaf or anywhere else.

### Projects whose work runs through email

Some projects have no job until mail arrives. "Watch the inbox; when an email
comes in, check its attachment and reply with the results" is one. Mark such a
project **its work runs through email**. You can do that in three places:

- the Founder sets it when the brief describes email, and says so in the ledger;
- the new-project form has a checkbox under **Before it can start**;
- a setup file can carry `"needs_mail": true`.

You can create such a project straight away. It **cannot start** until its
mailbox is set up and on. Resuming it from a pause, or reopening it after it
finishes, is refused the same way. Each of these says so:

- the setup checklist, as a required **Email** step that cannot be skipped;
- the project's Start button, which reads **Set up email to start**;
- the health card and the status line;
- `yaaf create-project` and `yaaf start`, which link to the dashboard page.

The mailbox is set up in the dashboard, because its password should be typed
where it is masked and the servers tested where the result can be shown.

If the mailbox is turned off or removed while the project runs, the CEO is told
plainly that no mail can arrive. The health card fails until it is back.

The need is part of the project's design: it travels in exports and to clones.
The mailbox itself never does. A clone waits for an address of its own, and a
drill runs without one and says so in its report.

### Setting up the mailbox

Set it up in the project's **Settings → Email**:

| Field | What it is |
|---|---|
| Project email address | The From address. Replies come back to it. |
| SMTP server, port, security | For sending. Port 587 with STARTTLS, or 465 with TLS. |
| IMAP server, port, folder | For reading. Always TLS. The inbox is read every minute, and nothing is marked read. |
| Username, password | Used for both servers. Gmail needs an app password. Microsoft 365 may not allow passwords for SMTP and IMAP at all. |
| Who the CEO may email | The addresses the CEO may write to. Every other address is refused. |
| Listed addresses may write first | On by default for a project whose work runs through email. See below. |
| Verify senders | On by default. See below. |

**Test connection** signs in to both servers and sends nothing.

The `YAAF_SMTP_*` variables are never used for project email, even when no
mailbox is set up. In that case the CEO is refused and told to ask you.

### What the server enforces

- **Only the CEO sends**, and only to listed addresses. Taking an address off
  the list ends the CEO's conversation with it.
- **The password stays on the server.** It is stored encrypted as a project
  secret that cannot be granted to any role. The platform sends the CEO's
  email itself, so no sandbox ever holds the login.
- **What the CEO attaches must be a shipped deliverable.** The CEO publishes it
  with `artifact_put` first, so the record shows exactly which version went to
  whom.
- **An email reaches the CEO only if:**
  - it is not an out-of-office reply;
  - it comes from a listed address;
  - your mail server vouches for the sender (DMARC, or DKIM or SPF aligned with
    the From domain);
  - it answers an email the project sent, or **Listed addresses may write
    first** is on.

  Anything else is written to the audit log and goes no further. Mail in the
  box from unrelated senders is left alone.
- **A listed address writing first opens a new conversation.** It wakes the
  CEO, who answers on that thread. At most 20 conversations a day can be
  opened this way.
- **What arrives is treated as material, never instructions.** An accepted
  email wakes the CEO and counts as platform-attested external evidence. The
  CEO reads it as a correspondent's words.
- **Attachments are kept for the agents.** Files up to 10 MB each, and 25 MB
  per email, are stored and copied into the project workspace under
  `mail-inbox/<thread>/<message>/`, where any agent can open them. File names
  are made safe, and anything over the limits is listed with the reason. They
  never appear on the Deliverables shelf. In a workspace that is a git clone,
  `mail-inbox/` is added to the local git excludes, so an agent can't commit a
  client's document. In **Activity → Email** you can download each file. It
  always arrives as a download and is never opened in the browser.
- **Limits.** At most 30 emails a day per project. A thread freezes at 40
  messages, so two agents cannot reply to each other forever. Reopen a frozen
  thread from your Inbox or from **Activity → Email**.

If your mail server records no `Authentication-Results` header, untick
**Verify senders**. Then anyone who types a listed address into a From line is
read as that person.

Drills, clones and backups never carry the mailbox or its password. Set it up
again on purpose.

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
`MINIO_ROOT_USER` (default `yaaf`) and `MINIO_ROOT_PASSWORD`. The deploy script
writes a random password to `deploy/.env`; there is no default. Its ports
listen on `127.0.0.1` only. Point yaaf at it with
`YAAF_ARTIFACT_S3_ENDPOINT=http://minio:9000`.

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
| `YAAF_IMAGE_REGISTRY` | Where to pull published images from. Default `ghcr.io/dominicplouffe`. Set it for a fork that publishes its own. |
| `MINIO_ROOT_PASSWORD` | Written by the deploy script for the optional MinIO store. See [S3](#artifact-storage-s3). |

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
