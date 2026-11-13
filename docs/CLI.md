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
| `yaaf desktop install` | yaaf in the top bar (Omarchy: a bar panel; elsewhere: a tray icon with a menu), notifications, app menu entries and the morning letter. `remove` undoes it. On Omarchy it restarts the shell when the bar widget changed. |
| `yaaf desktop indicator` | The tray icon and its menu, for desktops that host a system tray (Ubuntu, KDE, Xfce, …). Started at login by `desktop install`. |
| `yaaf desktop open URL` | Open one dashboard screen the way this desktop opens the dashboard. Only yaaf's own address. |
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
| `yaaf create-project "BRIEF"` | Describe a project in words and the Founder drafts it. See below. |
| `yaaf create-project --quick "BRIEF"` | No questions: the Founder takes its own recommendations and the project starts. See below. |
| `yaaf create-project --from-file SPEC` | Create a project from a spec, like the ones in `examples/` |
| `yaaf rehearse SPEC` | Run a project spec with scripted agents, for $0. Shows where it would stall and what it would cost. |
| `yaaf projects` | Every project: status, spend, and what waits on you |
| `yaaf status` | Is everything OK, in plain sentences |
| `yaaf start P` / `pause P` / `stop P` | Start or resume, pause, or end a project for good |
| `yaaf configure P` | Change a project's runtime or billing credential |
| `yaaf amend P` | Change one of the project's rules |

`P` is a project's slug or id.

### Creating a project

`create-project` holds the same conversation as **New Project → Describe it** in
the dashboard. Say what you want, the way you'd brief someone you just hired:

```bash
yaaf create-project "A weekly digest of what changed in our competitors' pricing"
```

The Founder drafts the whole project: goal, mode, team and rules, and prints
each step as it goes. It also asks about anything it had to guess. The
questions show up as soon as it asks them, usually within half a minute, while
it is still drafting, and each one comes with its own answer already picked:

- **Enter** keeps the recommended answer.
- **A letter** picks another option.
- **Anything else you type** is sent as your own words.
- **Who pays** never has an answer picked for you; Enter leaves it open.

Your answers are sent when the draft is done. Then the Founder asks another round
or calls the draft settled. At that point you choose:

- **create it.** The project is created and its CEO gets the conversation as a
  briefing in the boardroom. Then `create-project` finishes setting it up (see
  [After it's created](#after-its-created)).
- **say something.** Change anything, in words.
- **write the draft to a file.** The file works with `yaaf rehearse` and
  `yaaf create-project --from-file`.
- **quit.** The conversation is kept.

### Quick: no questions

When the brief is all there is (a job handed over by another agent, or a
question you just want answered), skip the conversation:

```bash
yaaf create-project --quick "Research the best printer for home use and give me a short ranked list"
```

The Founder still works out the questions it would have asked, takes its own
recommendation on each, and the project is created and started, usually within
a minute. The command prints the team and every decision it took on your behalf,
and the CEO gets the same list in its briefing. `--wait` stays until the project
finishes and prints what it delivered.

A quick project gets no data sources, secrets, email, controlled domains or
repository, because nobody is there to supply them. One credential pays every
seat: `--billing NAME`, or else the one your last project paid with, or else the
only one there is. If the brief needs more than that, use the conversation. The
dashboard's **Just start it** button and the MCP tool `quick_project` do the same.

The conversation lives on the server. `yaaf create-project` with no brief picks
up the one you left open, including one you started in the browser. Ctrl-C stops
the waiting but not the Founder.

**Where the time goes.** A Founder turn takes about half a minute. Pass
`--timing` to see, after each turn, how long it took to start, each model call
and the tool it asked for, the tokens the turn wrote (thinking included), and
each tool's own run:

```
[founder turn 31s: cli start 1s · model 28s (3 calls, 101.9k in [98.8k cached] / 2.7k out, 372 thinking) · propose_bundle 1s · ask 0s | slowest calls: 16s/3 out→mcp__founder__ask, ...]
```

The goal is graded against the goal grammar after every draft, by the
platform, beside the turn rather than inside it. The verdict opens the
Founder's next turn, and anything it finds missing becomes a question.

The server logs the same line for every turn, including failed ones (logger
`yaaf.founder`). The thread API keeps the full breakdown on each Founder turn
as `timing`.

The Founder needs a key of its own, or one of your LLM credentials, to draft
with. An admin sets it with `yaaf set-platform-key`. `--from-file` does not
need the Founder.

### After it's created

Both ways of creating a project, the conversation and `--from-file`, end the
same way.

**The keys your agents need are asked for here.** A setup file lists the
credentials it was exported with, by name only. Each role also declares the
keys it needs, such as a GitHub token. For each key the project doesn't already
hold, you're asked to paste it. Nothing you paste is shown, and the key is
stored for exactly the roles that need it, as `yaaf set-secret` would. Press
Enter to skip one. Skipped keys are listed at the end with the
`yaaf set-secret` command and the dashboard link that finish them. A file
credential, such as a service-account JSON, isn't pasted; you get the
`yaaf set-secret --from-file` command for it instead.

**The rest is listed with links to the dashboard.** This is the project's
setup checklist, the same list as **Finish setting up** on its Inbox: model
key, brief, data sources, secrets, integrations and people, plus **email** for a
project whose work runs through it. Each unfinished step comes with a link to
the Settings section that does it. Data sources are always finished in the
dashboard. Adding one checks its address, and some need a sign-in with their
provider, which takes a browser. A mailbox is finished there too: its password
is typed where it is masked, and its servers are tested where the result can
be shown.

Links open on `YAAF_PUBLIC_BASE_URL` when it's set on your machine, and on
`YAAF_API` otherwise. Through an SSH tunnel, that's an address that works from
your laptop.

**Starting it.** A project with no model key can't run, so you aren't offered a
start. `create-project` tells you how to assign a key: in the dashboard, or with
`yaaf configure P --billing-secret-id ID`. A project whose work runs through
email isn't offered a start until its mailbox is on either, and every blocker
is named. The draft says "Email: …" before you create it, and `yaaf start`
refuses with the link to **Settings → Email**. Otherwise you're asked whether
to start it, or `--start` starts it without asking.

**From a script.** With `--from-file` and no terminal, nothing is asked. The
created project's JSON is the only thing on stdout. The keys still needed and
the checklist go to stderr.

## What needs you

| Command | What it does |
|---|---|
| `yaaf desk` | Decide what's waiting, one item at a time, in the terminal |
| `yaaf signal` | One line for a status bar: `Y`, `Y 3` when decisions or unread CEO messages wait, `Y ✕` when yaaf is down. `--format waybar` for Waybar's JSON, `--format panel` for everything the click-open panel shows. |
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
