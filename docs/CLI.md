# The yaaf command

Everything the dashboard does, from a terminal. Every command takes `--help`.

## Getting it

The command comes with yaaf. A native install puts it in `~/.local/bin`. To use
it from your laptop against a server, install it there and point it at the
server:

```bash
python3 -m venv ~/.yaaf-cli && ~/.yaaf-cli/bin/pip install "git+https://github.com/dominicplouffe/yaaf"
export YAAF_API=http://localhost:24247     # through your SSH tunnel
yaaf login
```

`yaaf login` asks for your email and password once and keeps a personal token.
Scripts can pass a token in `YAAF_TOKEN` instead.

Most commands talk to the server over its API. A few talk to the database
directly and must run where yaaf runs: `users`, `spend-report`,
`rotate-master-key`, `backup` and `restore`. On Docker, run those inside the
`api` container:

```bash
docker compose -f deploy/docker-compose.yml exec api yaaf users list
```

## Run it on this machine

| Command | What it does |
|---|---|
| `yaaf setup` | Set up a native install step by step: checks, account, service, key, Founder. Safe to re-run. |
| `yaaf up --host` | Start server, runner and dashboard in one go. `--no-isolation` for development only. |
| `yaaf update` | Back up, pull, rebuild what changed, restart. `--check` shows what would change. |
| `yaaf doctor` | Check the whole install and say how to fix what's wrong. |
| `yaaf backup` | Database, master key and artifacts in one file. `--workspaces` adds working folders. |
| `yaaf restore FILE` | Put a backup back. Stop yaaf first. |
| `yaaf rotate-master-key` | Re-encrypt every stored secret under a new key. Stop yaaf first. |
| `yaaf desktop install` | App menu entries, notifications and the morning letter. `remove` undoes it. |
| `yaaf theme sync` | Match the dashboard to your desktop theme. |

## Sign in

| Command | What it does |
|---|---|
| `yaaf login` | Sign in and keep a token for this server |
| `yaaf logout` | Revoke this machine's token |
| `yaaf whoami` | Who you are signed in as, and where |

## Projects

| Command | What it does |
|---|---|
| `yaaf rehearse SPEC` | Run a project spec with scripted agents, for $0. Shows where it would stall and what it would cost. |
| `yaaf create-project SPEC` | Create a project from a spec, like the ones in `examples/` |
| `yaaf projects` | Every project: status, spend, and what waits on you |
| `yaaf status` | Is everything OK, in plain sentences |
| `yaaf start P` / `pause P` / `stop P` | Start or resume, pause, or end a project for good |
| `yaaf configure P` | Change a project's runtime or billing credential |
| `yaaf amend P` | Change one of the project's rules |

`P` is a project's slug or id.

## What needs you

| Command | What it does |
|---|---|
| `yaaf desk` | Decide what's waiting, one item at a time, in the terminal |
| `yaaf signal` | One line for a status bar: how many things wait on you |
| `yaaf letter` | The morning letter across all your projects |
| `yaaf approvals P` | Approvals waiting, and what happens if you do nothing |
| `yaaf decide ID` | Approve or deny one, with a note to the CEO |
| `yaaf asks P` | Questions the CEO asked you |
| `yaaf flags P` / `ack-flag` | What the CEO must tell you, and your answer |
| `yaaf ack-heads-up` | Mark a heads-up as read |
| `yaaf say P "MESSAGE"` | Speak to the CEO in the boardroom |
| `yaaf extend ID DATE REASON` | Give a hypothesis its one-time extension |
| `yaaf unfreeze` | Let agents in a frozen thread talk again |
| `yaaf audit P` / `audit-evidence` | Spot-check finished work, and record your verdict |

## Look inside a project

| Command | What it shows |
|---|---|
| `yaaf briefing P` | What happened since you last looked |
| `yaaf agenda P` | What is scheduled next |
| `yaaf board P` | Open tasks, who holds them, and why they exist |
| `yaaf org P` | Teams, agents and when each wakes |
| `yaaf activity P` | Who spent the runs, and on what |
| `yaaf runs P` | Recent runs: who, why, result, cost |
| `yaaf channels P` / `tail CHANNEL` | Channels, and their latest messages |
| `yaaf inbox P` | Each agent's unread messages |
| `yaaf evidence P` | Every claim filed, and what was verified |
| `yaaf hypotheses P` | An Explore project's bets and their kill dates |
| `yaaf dashboard P` | Everything the project page reads, as JSON |

## Money

| Command | What it shows |
|---|---|
| `yaaf spend` | Cost by provider, model or project, per day, week or month |
| `yaaf receipt P` | What a project cost, and what it bought |
| `yaaf ledger P` | Every metered line this month |
| `yaaf spend-report` | Spend analysis straight from the database |

## Models and keys

Covered in [PROVIDERS.md](PROVIDERS.md).

| Command | What it does |
|---|---|
| `yaaf providers` | Every provider yaaf can pay, and what each needs |
| `yaaf create-llm-credential NAME` | Add a model key (prompted, never echoed) |
| `yaaf llm-credentials` | List keys and the projects using them. Never shows values. |
| `yaaf edit-llm-credential` / `delete-llm-credential` | Rename or replace a key; delete an unused one |
| `yaaf hide-llm-credentials` / `unhide-llm-credentials` | Take keys off the menus without deleting them |
| `yaaf llm-credential-usage` | What's left on a subscription plan |
| `yaaf pay-provider P PROVIDER CRED` | Let a project pay a second provider |
| `yaaf smoke CRED` | One tiny real call, to prove a key works |
| `yaaf check-provider CRED` | Can these models do yaaf's work? Shows the cost first. |
| `yaaf discover-models CRED` | What models this key can reach, with prices |
| `yaaf models` | What `@frontier`, `@balanced` and `@fast` mean on each provider |
| `yaaf set-lineup` / `repick-lineup` | Choose the model behind a class |
| `yaaf set-default-provider` | The provider used before a project pays one |
| `yaaf set-provider-ceiling` | Most runs at once on a provider, across the install |
| `yaaf import-prices` / `set-model-price` | Load prices from the feed, or set one by hand |
| `yaaf upkeep` | Keep models and prices current (on by default) |
| `yaaf set-web-search` | The search service for models that can't search |
| `yaaf set-platform-key` | The key the Founder drafts with |

## People

| Command | What it does |
|---|---|
| `yaaf create-user EMAIL NAME` | Invite someone. Prints their one-time setup link. |
| `yaaf people list` / `link` / `promote` / `demote` | Manage accounts on the install (admins only) |
| `yaaf people deactivate` / `reactivate` | Cut someone off at once, or let them back |
| `yaaf members list P` / `add` / `role` / `remove` | Who is on a project, and as what |
| `yaaf members hand-on` | Make someone else the chairman |
| `yaaf users bootstrap` / `setup-link` | From the server: make the first admin, or reset a password |

## Claude and other MCP clients

| Command | What it does |
|---|---|
| `yaaf mcp-token mint NAME --scopes read` | A token for an MCP client. Scopes: `read`, `steer`, `create`, `configure`. |
| `yaaf mcp-token list` / `revoke` | See and revoke client tokens |

## Libraries and Slack

| Command | What it does |
|---|---|
| `yaaf seed-roles roles/` / `seed-crews crews/` | Load the role library and ready-made teams. `--only-missing` skips ones you have. |
| `yaaf save-seed DIR` / `seed DIR` | Export roles and projects as specs; load them onto an empty install |
| `yaaf set-secret` | Store an encrypted project secret |
| `yaaf settings` | Show the install-wide settings |
| `yaaf set-slack` / `slack-channels P` | Connect Slack, and create a project's rooms |

## Environment

| Variable | What it does |
|---|---|
| `YAAF_API` | Which server to talk to. Default `http://localhost:24247`. |
| `YAAF_TOKEN` | A personal token, instead of `yaaf login` |
| `YAAF_HOME` | Native install folder. Default `~/.yaaf`. |

All other settings are in [CONFIGURATION.md](CONFIGURATION.md).
