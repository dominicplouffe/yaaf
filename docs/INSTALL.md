# Install

Three ways to run yaaf. Pick the one that fits.

| | Docker | Native | From source |
|---|---|---|---|
| **For** | A server, or any machine you want to run it on for real | Your own Linux desktop or laptop | Working on yaaf itself |
| **Agents run in** | A fresh container per run | A bubblewrap jail per run | A plain process (no isolation) |
| **Database** | Postgres | SQLite | SQLite |
| **Time** | ~10 minutes (image builds) | ~5 minutes | ~5 minutes |

All three need a model key to do real work: from Anthropic, OpenAI, Google,
or any provider yaaf supports. A local Ollama needs no key.

Want to see it work before installing anything? Run a project for $0 with
scripted agents:

```bash
git clone https://github.com/dominicplouffe/yaaf && cd yaaf
python3 -m venv .venv && .venv/bin/pip install -e .     # Python 3.12+
.venv/bin/yaaf rehearse examples/01-your-first-hour.json
```

---

## Option 1: Docker

**You need:** Linux with Docker and the compose plugin, `git`, `openssl` and
`curl`. No Python or Node; the images build everything.

**1. Install Docker** (skip if you have it):

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
```

Log out and back in so the group change applies.

**2. Install yaaf:**

```bash
git clone https://github.com/dominicplouffe/yaaf && cd yaaf
deploy/deploy_single_server.sh --admin-email you@example.com
```

The script creates the secrets, builds both images, starts Postgres, the
server and the runner, applies the schema, loads the role library, and makes
you the admin. It ends by printing a one-time link to set your password.

It is safe to run again at any time.

**3. Open the dashboard.** It listens on `127.0.0.1:24247` only, so nothing is
exposed to the network.

- On this machine: open **http://localhost:24247/app**.
- On a remote server: tunnel from your laptop, then open the same address.

  ```bash
  ssh -N -L 24247:localhost:24247 you@your-server
  ```

Open the setup link, choose a password, and you are in.

**Options:**

| Flag | Effect |
|---|---|
| `--admin-email EMAIL` | The first admin's account |
| `--port 9090` | Use another port |
| `--bind ADDRESS` | Listen on another address. Repeatable. `0.0.0.0` exposes it to every network, so put TLS in front first. |
| `--skip-roles` | Don't load the role library |

To run it on a public server, read [DEPLOYMENT.md](DEPLOYMENT.md).

---

## Option 2: Native (no Docker)

**You need:** Linux (Arch, Debian or Ubuntu tested). The installer adds `git`,
`bubblewrap`, `node`, `npm` and `uv` if they are missing.

```bash
git clone https://github.com/dominicplouffe/yaaf && cd yaaf
deploy/install_desktop.sh
```

The installer sets up yaaf, then runs `yaaf setup`, which asks for:

1. **Your account.** An email and a password (12+ characters).
2. **How to start yaaf.** As a background service that starts at login, or a
   plain background process.
3. **Your model provider and key.** The key is checked before it is saved.
   Most providers also need a web search key, because most roles browse.
4. **Desktop extras** (optional). App menu entries and notifications.

Then open **http://localhost:24247/app**.

Run `yaaf setup` again at any time. It skips what is already done.

**Before you choose native mode, know this:** each agent runs on your machine,
inside a jail that hides your home folder and yaaf's keys. But it can use your
network (including local services) and read system files under `/usr`, `/etc`
and `/opt`. If the machine holds anything you would not hand to a stranger's
script, use Docker.

**Ubuntu 23.10 and later** blocks bubblewrap by default. `yaaf setup` stops and
tells you. To allow it, save this as `/etc/apparmor.d/bwrap-userns`:

```
abi <abi/4.0>,
include <tunables/global>

profile bwrap-userns /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap-userns>
}
```

Then load it and run setup again:

```bash
sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns
```

Arch, Fedora and Debian need nothing extra.

**Day to day:**

| Task | Command |
|---|---|
| Stop | `systemctl --user stop yaaf` |
| Start | `systemctl --user start yaaf` |
| Update | `yaaf update` (backs up the database first) |
| See what an update brings | `yaaf update --check` |
| Open to your network | `yaaf setup --bind <address>` |
| Health check | `yaaf doctor` |

Data lives in `~/.yaaf`: the database, the master key, workspaces and
artifacts. Back up that folder.

---

## Option 3: From source

For contributors. Needs Python 3.12+ and Node 20+.

```bash
git clone https://github.com/dominicplouffe/yaaf && cd yaaf
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
(cd web && npm install && npm run build)
.venv/bin/yaaf up --no-isolation --email you@example.com
```

`yaaf up` creates a master key and a SQLite database in `~/.yaaf`, starts the
server and the runner, makes you the admin, loads the role library, and prints
your sign-in link. Ctrl-C stops everything.

`--no-isolation` means agents run as you, with no sandbox. yaaf will not pick
this for you. For real agents on your machine, use `yaaf up --host` instead
(needs `pip install -e ".[host]"` and bubblewrap).

Run the tests:

```bash
.venv/bin/pytest -q -n auto
```

See [CONTRIBUTING.md](../CONTRIBUTING.md) before you open a pull request.

---

## After you install

1. **Add a model key.** Settings → Providers. Agents cannot run without one.
2. **Check the install.**

   ```bash
   yaaf login
   yaaf doctor
   ```

   `doctor` lists anything that is wrong and how to fix it.
3. **Create a project.** In the dashboard, click New Project, or import one of
   the ready-made specs in `examples/`.
4. **Connect Claude** (optional):

   ```bash
   yaaf mcp-token mint my-laptop --scopes read
   claude mcp add --transport http yaaf http://localhost:24247/mcp \
     --header "Authorization: Bearer ymt_…"
   ```

   Claude Desktop and Cursor use the same URL and header. claude.ai needs a
   public address; see [DEPLOYMENT.md](DEPLOYMENT.md#connect-claudeai).

## If something goes wrong

| Symptom | Fix |
|---|---|
| Forgot your password | `yaaf users setup-link you@example.com` (Docker: prefix with `docker compose -f deploy/docker-compose.yml exec api`) |
| Every page errors with `relation "projects" does not exist` | The schema is missing. Run `docker compose exec api alembic upgrade head` from `deploy/`. |
| The `api` container keeps restarting | A migration failed. `docker compose logs api` shows why. |
| No roles to pick when creating a project | The library was not loaded. Re-run `deploy/deploy_single_server.sh`. |
| A run starts and dies at once | The sandbox image is missing. `docker build -f deploy/sandbox/Dockerfile -t yaaf-sandbox:latest .` |
| Runs never start | No runner is running. Check `docker compose ps`, or `yaaf doctor`. |
| Anything else | `yaaf doctor` |
