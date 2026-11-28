# Factory plugins

A factory plugin adds a new **kind of project** to yaaf: an Amazon PPC audit,
a grant application, anything with its own rules for what counts as done. Each
plugin lives in its own public GitHub repo. The instance admin installs it
from that repo's URL. After that, anyone who creates a project can choose the
plugin's kind.

This page is for people. It has two parts:

- [Running plugins](#running-plugins): installing, choosing, disabling and
  removing them (for the instance admin).
- [Writing a plugin](#writing-a-plugin): from an empty folder to a plugin that
  installs as `ready` (for authors).

Are you handing the work to an AI agent such as the yaaf Code Factory? Send it
**[PLUGIN_AGENT_BRIEF.md](PLUGIN_AGENT_BRIEF.md)**. That page is written for an
agent and holds everything it needs. The full design and its reasons are in
[plans/factory-plugins.md](plans/factory-plugins.md).

## What a plugin can and cannot do

A plugin is a set of **pure functions** (hooks). yaaf calls them in a
sandboxed process that has no network, no secrets and no shell. yaaf does
every side effect itself. So a plugin decides things. It never does things.

| A plugin decides | yaaf does |
|---|---|
| whether an uploaded artifact is accepted, and why not | stores it, versions it and shows it |
| what a plan or the finished project still owes | holds the plan or completion until nothing is owed |
| its review rubric | binds a review to the exact version reviewed, refuses a review by a seat that published that version, and compares the re-derived numbers with the artifact's own |
| the pages rendered from an accepted artifact (md, html, xlsx, csv, ...) | stores them; an HTML page gets a strict Content Security Policy |
| which fields a data-source read returned | records each read as a receipt that claims can cite |
| how a data action computes from the tools it names | calls only those tools, and only the ones the chairman allows |
| the project-home section, in data | draws it with yaaf's own components (plugins ship no UI code) |
| the New Project questions for its kind | asks them, and refuses answers the plugin refuses |

A plugin only **tightens** what yaaf allows. Each of yaaf's own refusals still
applies to a project of a plugin's kind (requirement MIN-14).

There are also things a plugin cannot do:

- add tools to the agents' sandbox;
- replace a built-in kind;
- open a cycle (`operate`) mode to the public;
- install itself.

## Running plugins

Only the **instance admin** can install, disable or remove a plugin. Signed-in
users can see what is installed and choose a `ready` kind for a new project.

### Install

There are three ways to install, and each one does the same thing:

- **Web:** Settings → Plugins. Paste the repo URL (and a ref) and press *Install*.
- **CLI:**

  ```bash
  yaaf plugins install https://github.com/OWNER/REPO --ref v1.0.0
  ```

- **REST:** `POST /admin/plugins` with `{"url": "https://github.com/OWNER/REPO", "ref": "v1.0.0"}`.

The URL must be a **public** `https://github.com/<owner>/<repo>`. `--ref` can
be a branch, a tag or a commit. yaaf pins the commit that the ref names at the
moment you install. Pushing to that branch later changes nothing that is
already installed.

An install is checked before it can be used. yaaf runs these steps in order:

1. Fetch the pinned commit and validate the manifest and the repo layout.
2. Build the dependencies from `requirements.lock` with `pip --require-hashes`.
   An unpinned or unhashed dependency fails the install.
3. Start the plugin in a sandboxed host and run the **conformance suite**.
   This checks every minimum requirement, plus the extended requirement for
   each capability the plugin declares. The plugin's own `tests/` run as well.
4. Check the plugin's roles, crews and skills against this box's library
   rules. Its roles and crews join the library when they aren't there already,
   so *New Project* fills in the kind's own team.

The result is a **report**, with one line for each requirement:

| Status | Meaning |
|---|---|
| `ready` | Every requirement that applies passed. The kind can be chosen. |
| `failed` | At least one requirement failed. The report names it and says what to change. Installing the same commit again runs every check again. |
| `pending` | The checks have not finished, or could not run. A pending kind cannot be chosen. |
| `installing` | It is still running. |
| `disabled` | You switched it off (below). |

`yaaf plugins install` waits for the result and prints any failures.
`--no-wait` returns at once.

Each install is a **version**. Commands name a version by its id, which is
the commit it was installed at (a local install's id is `local-<hash>`).
`yaaf plugins list` and Settings → Plugins show each id next to its release,
which is the `version` in the plugin's manifest. For example:

```bash
yaaf plugins report amazon-ppc a6ca8e0fc29acae3765d125074d04e522ab17cf2
```

### Choose a kind

Run `yaaf plugins kinds` to list what a project can be created as on this box.
In the web app, *New Project* lists plugin kinds next to the built-in ones and
asks the questions the kind declares. Through the REST API or MCP, pass
`kind`, `kind_mode` and `kind_fields` when you create the project. The
Founder's `list_kinds` tool and the MCP `catalog` tool (`what: "kinds"`)
return the same list.

Each project stays on the plugin version it was created with until its
chairman moves it (below).

### Update a plugin

When a plugin gets a fix:

1. The author bumps `version` in `yaaf-plugin.toml`, merges, and tags the
   release (say `v1.0.1`).
2. The instance admin installs the new tag, in Settings → Plugins or with
   `yaaf plugins install <url> --ref v1.0.1`. It is checked like any install.
   The old version stays installed.
3. **New projects** get the newest version that passed. If the new one failed,
   they keep getting the old one.
4. **Running projects** stay where they are until their chairman moves them:
   project Settings → *Plugin version* → *Move to this*, or
   `yaaf plugins move <project> <version id>`.

A move keeps the project's mode and lead, and runs the project's saved state
through the new version's `migrate_state`. Files already accepted keep their
verdict, and new files meet the new version's rules. The move is in the audit
log. yaaf refuses a move:

- to a version that did not pass its checks, or is disabled;
- to a version with another lead, or without the project's mode;
- when `migrate_state` fails, in which case the project stays where it was.

Moving back to an older version works the same way.

### Disable or remove

```bash
yaaf plugins disable amazon-ppc <version id>   # new projects can no longer choose it
yaaf plugins remove  amazon-ppc <version id>   # only when no project runs on it
```

A disabled version keeps serving the projects that already run on it.

### Docker mode

When `YAAF_SANDBOX_MODE=docker`, plugin hosts run inside the runner, and the
api reaches them through the runner's **broker**. The broker listens on the
stack's default network, which no agent container can reach. It checks every
request with a key derived from `YAAF_MASTER_KEY`. The stack in
`deploy/docker-compose.yml` is already set up for this: it shares the plugins
directory between the api and the runner, and points the api at `runner:24250`. The settings are in
[CONFIGURATION.md](CONFIGURATION.md#factory-plugins).

### Settings

| Variable | Default | What it does |
|---|---|---|
| `YAAF_PLUGINS_ROOT` | `/var/lib/yaaf/plugins` | Installed checkouts and their built environments |
| `YAAF_ALLOW_LOCAL_PLUGINS` | `false` | Development only. Lets the admin install from a path on this machine (`POST /admin/plugins/local`) |
| `YAAF_ALLOW_UNVERIFIED_PLUGINS` | `false` | Development only. Lets a `pending` version be chosen |
| `YAAF_PLUGIN_PIP_ARGS` | *(empty)* | Extra pip arguments for the dependency build, such as an internal mirror. The lock still decides what gets installed |

A production box leaves both development switches off. With them off, a kind
can be chosen only after a GitHub install that passed every requirement.

## Writing a plugin

You need Python 3.11 or newer and the `yaaf_plugin_api` package. That package
ships inside yaaf, and it has no dependencies of its own. Inside a yaaf
checkout, `yaaf plugin ...` and `python -m yaaf_plugin_api ...` are the same
commands.

### 1. Start from the scaffold

```bash
yaaf plugin new grant-writer --id grant-writer \
  --artifact application.json --review --pages --view --lead
```

This writes a repo that **already passes the full conformance suite**. It
contains the manifest, a working stub hook for each capability you asked for,
an accept fixture and a refuse fixture for each gate, an attack test for each
gate, and `AGENTS.md` for any coding agent that works in the repo later.
Check it straight away:

```bash
cd grant-writer
python -m yaaf_plugin_api check . --full
```

Start with only the capabilities you need. Adding one later means adding a
line to the manifest, a hook, its fixtures and a test.

| Flag | Declares | Hook it writes |
|---|---|---|
| `--artifact NAME.json` (repeatable) | a gated artifact | `@hook.check_artifact("NAME.json")` |
| `--review` | a review of the first artifact, `<stem>-review.json` | `@hook.check_review(...)` |
| `--pages` | a platform page `<stem>.md`, rendered from the first artifact | `@hook.render` |
| `--brief` | guidance added to every seat's brief | `@hook.brief` |
| `--plan-gate` / `--completion-gate` | what a plan or the finished project owes | `@hook.plan_gate` / `@hook.completion_gate` |
| `--view` | a project-home section (`ui/view.json`) | `@hook.view` |
| `--lead` | a fixed lead role `<id>-lead` (`roles/`) | — |
| `--create-fields` | New Project questions | `@hook.create_fields`, `@hook.validate_create` |
| `--receipts` | receipts for data-source reads | `@hook.fields_of_read` |
| `--action NAME --action-tool TOOL` | a platform-run data action | `@hook.data_action("NAME")` |
| `--setup-step KEY` | a setup checklist step | `@hook.setup_step("KEY")` |

The scaffold refuses a combination that cannot work before it writes any
file. For example, `--review` without `--artifact` is refused. So is a
directory that is not empty.

### 2. The repo

```
yaaf-plugin.toml      the manifest: identity, the kind, its modes, its capabilities
src/<package>/        the plugin; `plugin.module` names its FactoryPlugin object
requirements.lock     every dependency pinned with == and --hash (omit when there are none)
roles/ crews/         the kind's lead and crews, in yaaf's library shapes
skills/*.md           the playbook every seat of this kind is given
ui/view.json          the project-home section, if you declared one
fixtures/             what the conformance suite asks your hooks with
tests/test_*.py       your own tests, with at least one attack test per gate
```

The manifest declares, and the code must match it exactly. A declared
capability with no hook fails, and so does a hook that nothing declared
(MIN-05). The JSON Schemas for the manifest and the view are in
`packages/yaaf_plugin_api/src/yaaf_plugin_api/schema/`. Editors and agents can
validate against them.

### 3. Write the rules

Each hook gets a `ProjectSnapshot` (`ctx`). That is a read-only picture of the
project: its mode, the artifacts accepted so far (`ctx.latest(name)`,
`ctx.read_artifact(name)`), its coverage, its receipts (`ctx.receipt(id)`) and
`kind_state`. A hook returns a value and nothing else:

- `Accept(data=None, state_patch=None, note=None, load_bearing=None)`. `data`
  stores a normalised form. `state_patch` is the only way a plugin keeps
  state: a JSON merge patch on `kind_state`. If you write state, declare
  `state = true` and a `migrate_state` hook. `load_bearing` maps each claim id
  to the number the artifact reports. It is what a review is checked against.
- `Refuse([...])`, with every reason in words an agent can act on. Each reason
  must be non-empty and at most 500 characters, with no stack trace.
- `[Owed(key, text)]` from a gate. An empty list means nothing is owed. Keep
  the keys stable.

A hook must be **deterministic**: the same snapshot gives the same answer
every time. The host seeds `random` for each call. Do not read the clock;
read `ctx.today` instead. The host blocks any network call, any write outside
the scratch directory and any read outside the plugin's own files. A hook
that tries one fails the install.

### 4. Fixtures and tests

The suite asks your hooks the questions in `fixtures/`:

```
fixtures/artifacts/accept/<artifact>/<case>              a gate must accept it
fixtures/artifacts/refuse/<artifact>/<case>              a gate must refuse it ...
fixtures/artifacts/refuse/<artifact>/<case>.expect.json  ... with these words: {"contains": [...]}
fixtures/states/<name>.json                              project states (mode, artifacts, capabilities, kind_state)
fixtures/sources/<source>/tools.json, calls/<tool>.json  a recorded data source, for data actions
fixtures/create/accept|refuse/<case>.json                New Project answers
```

A case's `.expect.json` can add `"state": "<name>"` so the case is asked in
that state rather than on a new project. A review fixture can write
`"$SUBJECT_SHA256"`, and the suite substitutes the hash of the reviewed
artifact. Run `yaaf plugin spec` to print the full format, along with every
hook, every limit and every requirement.

Your `tests/` must pass, and each gate needs at least one attack test (a test
that submits something bad and asserts that it is refused). That is
requirement MIN-16, and it follows yaaf's own house rule that every rule ships
with its attack.

### 5. Check, push, install

```bash
python -m pytest tests -q
python -m yaaf_plugin_api check . --full
```

`check --full` runs the same suite that an install runs, through a local
host, and runs your tests too. When it reports no failures, only three
requirements stay `pending`, because only a real install can decide them:

- **MIN-02**: the id and the kind key are unique on that box.
- **MIN-03**: the hashed dependency build.
- **MIN-07**: your roles and crews against that box's library rules.

Push to GitHub, tag a release, and ask the instance admin to run
`yaaf plugins install https://github.com/you/your-plugin --ref v0.1.0`.

### When a requirement fails

Each failed line in the report says which requirement failed and why. These
are the most common:

| Requirement | Usually means |
|---|---|
| MIN-05 | A capability is declared with no hook, or a hook exists that nothing declared, or a hook's signature differs from the spec |
| MIN-08 / MIN-13 | A hook gave the wrong verdict on a fixture, or a gate has no accept or no refuse case |
| MIN-09 | A hook raised on the fuzz corpus. Catch bad input and `Refuse`; never raise |
| MIN-11 | A hook is not deterministic: the same snapshot gave two answers (it read the clock, say) |
| MIN-12 | A hook (or a library it imports) touched the network, a process, an environment variable or a file outside scratch |
| MIN-16 | Your tests failed, or a gate has no test that names it |
| MIN-17 | A refusal is empty, over 500 characters, or holds a traceback |

## Where things are

| | |
|---|---|
| The plugin API (types, hooks, manifest, suite, host, scaffold) | `packages/yaaf_plugin_api/` |
| A small complete example | `packages/yaaf_plugin_api/examples/grant-factory` |
| A large real one | [yaaf-amazon-ppc-factory](https://github.com/dominicplouffe/yaaf-amazon-ppc-factory) |
| yaaf's side (registry, hosts, broker, primitives) | `yaaf/plugins/` |
| Every CLI command | [CLI.md](CLI.md#factory-plugins) |
| The brief for an AI agent | [PLUGIN_AGENT_BRIEF.md](PLUGIN_AGENT_BRIEF.md) |
