# Factory plugins: new kinds of project from a GitHub repo

## In one paragraph

A **factory plugin** adds a kind of project to yaaf (an Amazon PPC audit, a
grant-application factory, a security-review factory) without touching core.
It lives in its own public GitHub repo. An operator pastes the repo's URL into
yaaf; yaaf pins it to a commit, builds it in a sandbox, and runs a
**conformance suite** that answers one question before the kind can be used:
*was this plugin built in the shape the plugin spec requires?* That means every
minimum requirement, plus the extended requirements for each capability the
plugin claims. A plugin's code never runs inside yaaf-server. It runs in a
sandboxed **plugin host** with no network, no secrets and no database, and it
can only answer the questions core asks it: accept or refuse this artifact,
what to tell the seats, what is still owed, what the page shows. Core owns all
the glue: tokens, receipts, reviews, storage, gateway calls and surfaces. So
the house rules hold for a plugin exactly as they hold for core, because only
core enforces anything.

## Why

The Amazon PPC Factory (branch `amazon-ppc-factory`) was written as a fifth
built-in kind. To exist it reached into roughly twenty core files:

| What the kind needed | Where it went in by hand |
|---|---|
| A kind: its modes, whether it ships once or runs on cycles, its fixed lead | `core/shape.py`, `core/models.py` (a `ppc_mode` column, migration 0087), `roles/`, `crews/` |
| Knowledge for every seat | `runner/daemon._offer_kind_skills`, `yaaf/ppc/playbook/*.md` |
| Agent tools | `runner/sandbox_entry.py` (`ppc_kit`, `ppc_analysis`), `/agent/ppc/*` |
| A paragraph in every brief | `server/contextpack.py` → `ppc.block()` |
| Gates on artifacts | `api/agent.artifact_put`: check the report, check the review, platform-only file names |
| Gates on plan and completion | `policy.evaluate_plan` (`data_owed`), `policy.delivery_outstanding` (`report_owed`) |
| An observer on data-source reads | `datasource_tools.forward` → `ppc.record_read` receipts |
| A setup step | `server/checklist.py` (`ads_data`) |
| A dashboard section and a create form | `api/observe._ppc_view`, `web/src/pages/NewProject.tsx`, `project/Overview.tsx` |
| Creation, cloning, the Founder, MCP | `api/admin.create_project`, `clone_project`, `founder/tools.py`, `mcp/tools_create.py` |

Each row is a hook point. A sixth kind would touch the same twenty files again,
and CLAUDE.md names the failure that follows: a right design wired into half
the places it belongs. A plugin API is that table made into a typed, versioned
interface, so core calls *every registered kind* at each point instead of naming
one.

The review of that branch found three bugs: a reviewer could choose the number
its re-derivation was compared against, a receipt whose fields could not be
parsed let any field be cited, and a self-review was possible after another
seat republished the report. Every plugin that has receipts or a review would
write those same mechanisms again and get them wrong again. So they become
**core primitives** that a plugin declares and does not implement (§4.4).

## 1. Principles

1. **A plugin only tightens.** Core's rules always run first and always hold.
   A plugin can add a refusal, add an owed item to a gate, or add text to a
   brief. It can never remove a core refusal, approve something core refused,
   or widen what an agent may do. This is the invariant that makes running
   third-party rules safe. It is also the first attack in the conformance
   suite (§6, MIN-14).
2. **Plugins are pure; core does the side effects.** Every hook takes a
   read-only snapshot and returns data: a verdict, text, a list or bytes. It
   never calls the database, the network or the store. When a plugin needs the
   world, such as a data-source pull, it asks core through the host's callback
   channel. Core checks the request against the project's permissions, makes
   the call and records it.
3. **Fail closed.** If the plugin host crashes, times out or returns something
   that does not validate, a gate that asked it refuses with "this project's
   plugin could not be consulted", and a plan or completion gate counts it as
   owed. It is never read as an accept.
4. **Pinned per project.** A project is created against one plugin version
   (repo + commit SHA) and stays on it, the way its constitution freezes at
   creation. Moving a running project to a new version is an explicit,
   recorded act by the chairman (§5.5).
5. **Declared, then proved.** Everything a plugin does is declared in its
   manifest, and the conformance suite proves the code matches the
   declaration. Core does not call a hook that was not declared, and the
   suite fails a declared hook that does not behave.
6. **No plugin migrations.** A plugin never changes the schema. Its
   per-project state is one JSON document (`kind_state`) that core stores and
   versions. The plugin changes it only by returning a patch from a hook.

## 2. What a plugin repo looks like

```
yaaf-amazon-ppc/
├── yaaf-plugin.toml          # the manifest: the declaration the suite holds the code to
├── pyproject.toml            # a normal package; dependencies pinned with hashes
├── requirements.lock         # (or uv.lock) hashes for every transitive dependency
├── src/yaaf_amazon_ppc/
│   ├── __init__.py           # exposes `plugin: yaaf_plugin_api.FactoryPlugin`
│   ├── contract.py           # pure checks: the report, the review, the fields
│   ├── kit.py                # served verbatim to seats as a file
│   └── adapters/qore.py      # how to read one vendor's MCP tools
├── roles/ppc-lead.json       # validated against core's role schema
├── roles/ppc-reviewer.json
├── crews/amazon-ppc-audit.json
├── skills/*.md               # the playbook, handed to every seat at lease
├── ui/view.json              # the project-home section, in core's view schema (§4.9)
├── fixtures/                 # conformance inputs the plugin ships: see §6.3
│   ├── sources/qore/         #   recorded tool listings and results for a fake MCP source
│   ├── artifacts/accept/<artifact name>/   # at least one case each gate accepts
│   └── artifacts/refuse/<artifact name>/   # at least one it refuses, each with <case>.expect.json
└── tests/                    # the author's own attacks, run by `yaaf plugin check` and at install
```

### 2.1 The manifest

```toml
[plugin]
id          = "amazon-ppc"              # [a-z0-9-]{3,40}; unique per install
name        = "Amazon PPC Factory"
version     = "1.2.0"                   # the author's semver, shown to people
api         = ">=1.0,<2"               # the yaaf_plugin_api versions it was written for
module      = "yaaf_amazon_ppc:plugin"  # the FactoryPlugin object
license     = "MIT"
homepage    = "https://github.com/acme/yaaf-amazon-ppc"
description = "Audits one Amazon advertising account and ships a reviewed report."

[kind]
key   = "amazon_ppc"                    # becomes the project kind "plugin:amazon_ppc"
lead  = "ppc-lead"                      # fixed lead role (from roles/), or omit to let the chairman pick
crews = ["amazon-ppc-audit"]

  [[kind.modes]]
  key    = "audit"
  shape  = "ships_once"                 # ships_once | runs_on_cycles: how core treats it everywhere
  open   = true
  [[kind.modes]]
  key    = "operate"
  shape  = "runs_on_cycles"
  open   = false                        # declared, refused at creation (as PPC_MODES_OPEN does today)

[capabilities]                          # each one opts the plugin into its hooks and its extended suite (§6.2)
brief           = true
plan_gate       = true
completion_gate = true
coverage        = true                  # classify_tools: what the connected data can reach
state           = true                  # the plugin writes kind_state, so migrate_state is required
artifacts       = ["ppc-audit.json"]    # names whose publication this plugin gates
platform_pages  = ["ppc-audit.md", "ppc-audit.pptx", "ppc-audit.xlsx", "ppc-changes.csv"]
review          = { of = "ppc-audit.json", artifact = "ppc-review.json", rederive_tolerance = 0.05 }
receipts        = true                  # or { sources = ["qore"] }: record reads; claims may cite them
agent_files     = { kit = "src/yaaf_amazon_ppc/kit.py" }   # served verbatim to seats (§4.6)
setup_steps     = ["ads_data"]
view            = "ui/view.json"
create_fields   = true                  # extra fields on the New Project form (§4.10)

  [capabilities.data_actions.analysis]  # platform-run pulls (§4.7)
  description = "Pull the account and run the standard checks"
  tools       = ["get_stats", "get_timeseries", "query_dataset"]   # the gateway refuses any other
  outputs     = ["ppc-data.json", "ppc-analysis.json"]           # reserved: no seat may publish them

[limits]                                # optional; core caps each at its own ceiling
hook_timeout_s   = 10
action_timeout_s = 600
memory_mb        = 512
```

The manifest's JSON Schema is published with the API package
(`yaaf_plugin_api/schema/manifest.schema.json`), so an author's editor and an
AI factory writing a plugin can validate it before any install.

## 3. Where plugin code runs (option 2: pure hooks, out of process)

```
yaaf-server ──JSON-RPC over a socket──▶ plugin host (one per installed plugin version)
   │   ▲                                    ├─ sandbox: the same modes as runs (docker | host | subprocess)
   │   └──── callbacks: gateway.call,       ├─ --network none, read-only code, tmpfs scratch
   │         artifact.read, receipt.get     ├─ no secrets, no DB URL, no master key, no run token
   ▼                                        └─ CPU, memory and wall-clock caps from [limits]
 enforces, stores, records
```

- **The host** is a small, fixed program, `yaaf_plugin_api.host`. It lives
  in the API package, not in core, because the sandbox holds only the API and
  the plugin; none of yaaf's own dependencies. It imports the plugin's module from a read-only
  mount and answers one JSON-RPC method per hook. Each message is validated
  against the API's schemas on both sides.
- **Warm, not per call.** Gates run inside `artifact_put` and the plan check,
  in the request path, and a container per call would add about a second to
  each. The server keeps a small pool of hosts per plugin version, recycles
  each after N calls or on any error, and restarts one that dies. A recycled
  host loses no state, because hooks have none: everything is in the snapshot.
- **Callbacks are the only way out.** A host may call back into core during a
  hook for exactly three things: read an artifact of *this* project by name
  and version, look up a receipt of *this* project, and, only in a data action
  (§4.7), make a data-source call. Core checks each one against the project
  and the tool permissions, the same check the agent gateway uses, and records
  each one. The host holds no token: the callback socket is bound to one
  project's call at a time.
- **Development.** `subprocess` mode runs the host as a plain process, with no
  isolation, as runs do today. It exists for authors' laptops and CI, and the
  existing rule about host-mode success tests extends to it: refused on a box
  holding the Docker socket unless the operator opts in.

What a malicious or broken plugin can do under this model: refuse everything
in its own kind, accept a bad artifact *of its own kind* (it is that kind's
rules, and core's rules still hold), and write misleading text into its own
brief, pages and skills. What it cannot do: read another project, read a
secret, reach the network, write to the database, act as an agent, or loosen
any core rule. The text it writes is operator-trusted content, like a role in
the library, and the install screen says so.

## 4. The Python API spec (`yaaf_plugin_api`, v1)

A separate, small, dependency-free package published from this repo
(`packages/yaaf_plugin_api/`), versioned on its own. Plugins depend on it;
core implements it. Everything below is frozen at `1.x`: additions are minor
versions, and a removal or a change of meaning is `2.0`. The host refuses a
plugin whose `api` range excludes the installed API.

### 4.1 The plugin object

```python
from yaaf_plugin_api import FactoryPlugin, hook

class AmazonPPC(FactoryPlugin):
    # Each hook is optional; declaring the capability in the manifest makes it required.
    @hook.brief
    def brief(self, ctx: ProjectSnapshot) -> Brief: ...

    @hook.plan_gate
    def plan_owed(self, ctx: ProjectSnapshot) -> list[Owed]: ...

    @hook.completion_gate
    def completion_owed(self, ctx: ProjectSnapshot) -> list[Owed]: ...

    @hook.check_artifact("ppc-audit.json")
    def check_report(self, ctx: ProjectSnapshot, upload: Upload) -> Accept | Refuse: ...

    @hook.check_review("ppc-review.json")
    def check_review(self, ctx: ProjectSnapshot, review: ReviewUpload,
                     subject: ArtifactBody) -> Accept | Refuse: ...

    @hook.render
    def render(self, ctx: ProjectSnapshot, accepted: ArtifactBody,
               reviewed: bool) -> list[Page]: ...

    @hook.classify_tools
    def capabilities(self, tools: list[ToolInfo]) -> Coverage: ...

    @hook.fields_of_read
    def fields(self, tool: str, result: ToolResult) -> ReadFields: ...

    @hook.data_action("analysis")
    def analysis(self, ctx: ProjectSnapshot, gateway: Gateway) -> ActionResult: ...

    @hook.setup_step("ads_data")
    def ads_data(self, ctx: ProjectSnapshot) -> StepState: ...

    @hook.view
    def view(self, ctx: ProjectSnapshot) -> ViewData: ...

    @hook.create_fields
    def create_fields(self) -> list[FieldSpec]: ...

    @hook.validate_create
    def validate_create(self, spec: CreateSpec) -> list[str]: ...   # refusal reasons, empty = ok

plugin = AmazonPPC()
```

### 4.2 What a hook is given

```python
@dataclass(frozen=True)
class ProjectSnapshot:
    project_id: str
    kind: str                     # "amazon_ppc"
    mode: str                     # "audit"
    plugin_version: str           # the commit SHA this project is pinned to
    kind_state: Mapping           # the plugin's own JSON, read-only here
    today: date                   # core's clock, so hooks stay deterministic
    coverage: Coverage            # from classify_tools over the tools this project may call
    artifacts: tuple[ArtifactMeta, ...]  # name, version, sha256, size, published_by_seat, run_id
    requirements: tuple[RequirementMeta, ...]
    seats: tuple[SeatMeta, ...]   # id, role slug, team: no tokens, no transcripts
    owner_inputs: tuple[OwnerAnswer, ...]
    lane: Literal["run", "chat", "page"] | None
```

The snapshot holds metadata only. Bodies come through
`ctx.read_artifact(name, version=None)` and receipts through
`ctx.receipt(id)`. Both are callbacks (§3), so a hook that does not need a
50 MB file never pays for one.

### 4.3 What a hook returns

```python
Accept(data: bytes | None = None,          # the bytes to store (e.g. the normalised report), or as uploaded
       state_patch: dict | None = None,    # JSON-merge-patch on kind_state
       note: str | None = None)            # shown to the publishing seat
Refuse(problems: list[str])                # every reason, in words an agent can act on; ≥1
Owed(text: str, key: str)                  # one thing a gate is still waiting for; stable key
Brief(text: str)                           # ≤ 8,000 chars; core places it after its own rules
Page(name: str, media_type: str, body: bytes)  # name must be in platform_pages; type on the allowlist
```

Core validates every return value. Anything malformed is treated as a host
failure and fails closed (§1.3).

### 4.4 Primitives core provides, so plugins don't rewrite them

These are the mechanisms every rigorous factory needs and the PPC review found
easy to get wrong. A plugin declares them in the manifest. Core implements and
attacks them once.

| Primitive | Declared as | What core holds, for every plugin |
|---|---|---|
| **Receipts** | `receipts` | Every data-source read by a seat of this project is recorded with its result hash and the fields the plugin's `fields_of_read` returns. A read whose fields could not be determined is recorded as `fields_unknown`, and **cannot** back a field citation (the PPC bug, closed once). `ctx.receipt(id)` returns only this project's receipts. |
| **Review** | `review = {of, artifact, rederive_tolerance}` | The review is bound to one exact `sha256` of the subject. The reviewer must be a seat that published **no** version of the subject with that hash, and no version at all if `independent = "strict"`. Load-bearing numbers come from the *subject*, never the review: the plugin's `check_review` gets `subject.load_bearing` (`claim id → reported value`) from its own accepted subject, and core does the tolerance arithmetic. A pass makes the rendered pages final and calls `attest_shipped`. |
| **Platform pages** | `platform_pages` | No seat may publish these names. Core stores what `render` returns, marks it draft until a review passes, and gives it the subject's reader and requirements. |
| **Reserved artifacts** | `data_actions` outputs | What a data action stores is the platform's. A seat cannot publish those names, and `ctx.artifacts` marks them `by_platform=True`. |
| **Agent files** | `agent_files` | Served verbatim over `/agent/plugin/<id>/file/<name>`, written into the workspace by one generic sandbox tool, `plugin_file`. |

### 4.5 Briefs, plan gate and completion gate

- `brief` text is appended to the system prompt after core's rules and
  `repo_guidance`, under a heading core writes ("THIS PROJECT'S KIND:
  <name>, plugin <id>@<short sha>"), so a reader of a transcript can always
  tell plugin text from platform text.
- `plan_gate` returns owed items. Each one keeps the plan from holding,
  exactly where `evaluate_plan` appends `data_owed` today.
- `completion_gate` returns owed items. Each one is added to
  `delivery_outstanding`, so the project cannot complete while one remains.
  Core always appends the review primitive's own item ("no passing review of
  the latest <subject>") when `review` is declared, so a plugin cannot forget
  it.

### 4.6 Agent tools

A plugin does not add sandbox tools: the tool list is core's and is
regression-gated (house rule 3). It gets three generic ones, which appear only
in a plugin project, and only for the capabilities it declared:

| Tool | Does |
|---|---|
| `plugin_file(name)` | Writes a declared agent file (the PPC kit) into the workspace |
| `plugin_action(name, args)` | Runs a declared data action (§4.7) and returns its summary |
| `plugin_describe()` | The kind, its mode, its coverage, its artifacts and their gates, as data |

### 4.7 Data actions (platform-run pulls)

A data action is how a plugin computes from the account itself, as
`ppc_analysis` does. It runs in the plugin host with `action_timeout_s`, and
it gets a `Gateway` whose only method is `call(tool, arguments) -> ToolResult`.
Core:

- allows only tools this project's chairman has allowed (`kind == read`,
  `ALLOW`, not gone), checked on every call;
- records each call as a receipt;
- turns an unreachable or signed-out source into a `ToolError` the plugin can
  handle, never into a crash of the endpoint (the PPC 500, closed once);
- runs the action with no database transaction open, so a five-minute pull
  holds no lock;
- stores what `ActionResult.artifacts` returns under the reserved names, and
  returns `ActionResult.summary` to the agent.

### 4.8 Setup steps

`setup_step` returns `StepState(done: bool, text: str, section: str)`. Core
puts the step in the project's checklist after the key and the same steps
that exist for every kind. A required step blocks the first run, exactly like
the PPC branch's `ads_data`.

### 4.9 The project-home view

No plugin JavaScript reaches the dashboard. A plugin declares a view in core's
**view schema**: a small set of components the SPA already knows how to draw
(`status`, `coverage`, `artifact`, `checklist`, `table`, `metric`,
`markdown`, `link`). `view(ctx)` fills it with data. The SPA renders it
generically on the project home and in `GET /observe/projects/{id}` under
`kind_view`. This keeps the attack surface at zero new code paths in the
browser, and means a plugin's page works in the CLI and over MCP too, as data.

### 4.10 Creating a project

`create_fields` declares extra fields (type, label, help, enum, required)
using a subset of JSON Schema. The New Project form, `POST /admin/projects`,
the MCP `create_project` tool and the Founder all read them from the registry.
`validate_create` adds refusals to the ones core already makes. The answers
land in `kind_state.create`, and a setup file and a clone carry them, which
replaces today's `integrations.ppc_mode` trick.

### 4.11 Determinism

Hooks other than data actions must be deterministic for a given snapshot,
artifact bodies and receipts. Core passes `today` and never lets a hook read
the clock, the environment or randomness (the host seeds and patches them).
The conformance suite calls each hook twice on the same input and fails a
plugin whose answers differ (MIN-11). This is what lets a reviewer, a
re-install or a later audit reach the same verdict.

## 5. Installing from a public GitHub URL

### 5.1 Who

The **operator** (install admin) installs and removes plugins: it is code the
whole install will run. A **chairman** chooses a plugin kind when creating a
project, from the kinds that installed and passed. In v1 a chairman cannot
install one; the Founder can *suggest* a plugin URL to the operator.

### 5.2 The flow

```
POST /admin/plugins  {"url": "https://github.com/acme/yaaf-amazon-ppc", "ref": "v1.2.0"}
```

1. **Resolve.** Only `https://github.com/<owner>/<repo>` (optionally
   `/tree/<ref>`) is accepted. `ref` defaults to the default branch. It is
   resolved to a **commit SHA** with `git ls-remote`, and from then on only
   the SHA is used: a moved tag never changes an installed plugin. A private
   repo is refused as "not found or not public" in v1.
2. **Fetch.** A shallow clone at that SHA into an install directory: no
   submodules, no LFS, a size cap (default 50 MB), and refused if it has
   symlinks out of the tree.
3. **Read the manifest.** It is validated against the manifest schema. The
   `api` range must include the installed API, `id` and `kind.key` must not
   collide with core kinds or another installed plugin, and declared
   role, crew and skill files must exist.
4. **Build.** In a sandbox *with* network, for this step only: create a venv
   and `pip install --require-hashes` from the lock file, then the plugin with
   `--no-deps`. An unhashed or unpinned dependency fails here (MIN-03). The
   built venv is then frozen read-only, and the plugin host mounts it with
   network off.
5. **Conform.** Run the conformance suite (§6) against the built plugin in
   the sandbox, and the plugin's own `tests/` with the harness (§7).
6. **Record.** A `plugin_versions` row: repo, SHA, manifest, the full
   conformance report, the build log, and a status of `ready` (every minimum
   requirement and every declared extended requirement passed) or `failed`.
   A failed version is kept so the report can be read. It cannot be chosen
   for a project.

The install runs as a background job (`plugin_install` in the run queue, or
a janitor-leased job), because a build and suite take minutes.
`GET /admin/plugins/{id}/versions/{sha}` shows its progress and report.

### 5.3 What the suite is not

It checks **shape**: that the plugin is built the way the spec says, declares
what it does, does what it declares, and keeps every promise the platform
needs from a plugin. It does not check whether its rules are wise or its
arithmetic right. Bugs aside, a plugin that passes is correctly built; a
plugin that fails is told exactly which requirement it missed and why.

### 5.4 Removal and disabling

Disabling a version stops new projects choosing it. Projects already on it
keep running, because their rules are pinned. Removing a version that any
project is on is refused and names the projects.

### 5.5 Upgrading a running project

`POST /admin/projects/{id}/plugin` `{"version": "<sha>"}` by the chairman:
allowed only to a `ready` version of the same plugin id. The new version's
`migrate_state(old_state, from_version)` hook (required when `kind_state` is
non-empty and the version changes, MIN-15) returns the new state. The change
is in the audit log and the daily brief. Already accepted artifacts keep the
verdict they had. Only new publications meet the new rules.

## 6. The conformance suite

Each requirement has a stable id, a sentence, and an automated check. The
report lists every one as `pass`, `fail` (with the reason and the input that
failed it) or `n/a` (an extended requirement for a capability not declared).

### 6.1 Minimum requirements (every plugin; any failure fails the install)

| Id | Requirement | How it is checked |
|---|---|---|
| MIN-01 | The manifest validates against the schema, and `api` includes this install's API | schema + version range |
| MIN-02 | `id` and `kind.key` are well-formed and unique; reserved names don't collide with core's or another plugin's | registry lookup |
| MIN-03 | Every dependency is pinned with a hash; the build installs with `--require-hashes` | the build step |
| MIN-04 | The module imports in the host with no network, and exposes one `FactoryPlugin` | host start |
| MIN-05 | Every capability in the manifest has its hook, with the API's signature; no hook exists that wasn't declared | introspection against the API's hook registry |
| MIN-06 | The kind declares ≥1 mode, ≥1 open mode, and each mode is exactly one of `ships_once` / `runs_on_cycles` | manifest |
| MIN-07 | Roles, crews and skills validate against core's schemas (role taxonomy, crew shape, skill size limits); a declared fixed lead exists and leads nothing else | core's own validators (`test_role_taxonomy` rules) |
| MIN-08 | Every hook returns a schema-valid value on the plugin's fixtures | run each hook on `fixtures/` |
| MIN-09 | Every hook survives the fuzz corpus without raising: empty, huge, wrong-typed, invalid UTF-8, deeply nested, unknown fields | core's corpus × every hook |
| MIN-10 | Hooks stay inside `hook_timeout_s` and `memory_mb` on the fixtures and the corpus | host metering |
| MIN-11 | Hooks are deterministic: the same snapshot gives the same answer twice, with clock, randomness and environment changed between calls | double call |
| MIN-12 | Hooks make no network call, write nowhere but scratch, and touch no environment secret | host sandbox telemetry (denied syscalls and connects are counted) |
| MIN-13 | Every gate has ≥1 accept fixture and ≥1 refuse fixture, and gives the expected verdict on each | `fixtures/artifacts/` |
| MIN-14 | **A plugin only tightens:** run core's own invariant attacks on a project of this kind with the plugin answering "accept everything" (and with the real plugin); every core refusal still fires | the core attack subset, replayed against a plugin project |
| MIN-15 | If `kind_state` is ever written, `migrate_state` exists and round-trips the fixtures' states | fixtures |
| MIN-16 | The plugin's own `tests/` pass under the harness, and there is ≥1 attack test per declared gate (house rule #2) | `yaaf plugin check` runs them; a gate with no test that refers to it fails |
| MIN-17 | A refusal's text names what to fix: every `Refuse.problems` entry is non-empty, ≤500 chars, and contains no stack trace | on the refuse fixtures and the corpus |

### 6.2 Extended requirements (for each declared capability)

| Capability | Id | Requirement |
|---|---|---|
| `artifacts` | EXT-ART-1 | Garbage, a wrong JSON shape, a valid-shaped artifact citing another project's data, and a 10× size-cap file are each refused, never accepted or crashed |
| | EXT-ART-2 | An accepted artifact's `data`, re-submitted, is accepted again (idempotent normalisation) |
| `platform_pages` | EXT-PAGE-1 | `render` returns only declared page names, only allowlisted media types, and is deterministic; a seat publishing a page name is refused by core |
| | EXT-PAGE-2 | Draft and reviewed renders differ only in the draft mark (core diffs them) |
| `review` | EXT-REV-1 | A review by the subject's publisher, by a seat that published the same hash, of a stale hash, with a missing load-bearing number, and with a number outside tolerance is refused. Core runs these; the plugin's `check_review` must not accept any of them |
| | EXT-REV-2 | A review that sets its own `reported` value cannot move the comparison (the PPC finding) |
| | EXT-REV-3 | Only a passing review makes the project completable; a later `return` takes that back |
| `receipts` | EXT-RCT-1 | `fields_of_read` returns fields for each recorded fixture result, returns `unknown` (never `[]`) when it cannot tell, and a claim citing a field the pull lacks, a forged receipt id, or another project's receipt is refused |
| `data_actions` | EXT-ACT-1 | On the fake source the action completes and stores only its declared artifacts |
| | EXT-ACT-2 | With the source unreachable, signed out, returning `isError`, timing out mid-pull, or with one tool switched off, the action returns a `ToolError`-shaped summary or partial result. It never raises past the host, and the endpoint never answers 500 |
| | EXT-ACT-3 | It calls only tools named in its manifest's `data_actions.<name>.tools` |
| `plan_gate` / `completion_gate` | EXT-GATE-1 | Owed items have stable keys across calls, and the gate clears on the fixture state that should clear it |
| `brief` | EXT-BRF-1 | Within the size cap; deterministic; no text that imitates core's headings (`CORE RULES`, `REFUSED`, and so on, a fixed list) |
| `setup_steps` | EXT-STP-1 | Each step is done or not done on its fixture states, with a non-empty text |
| `view` | EXT-VIEW-1 | `view.json` validates against the view schema, and `view(ctx)` fills it on every fixture state |
| `create_fields` | EXT-CRT-1 | The fields are a valid subset of JSON Schema; `validate_create` refuses the plugin's own refuse fixtures and accepts the accept ones; a closed mode is refused |
| `runs_on_cycles` mode | EXT-CYC-1 | The standing obligations it declares are expressible in core's cycle model (core validates them; operate modes stay `open = false` until this exists in core) |

### 6.3 What the plugin ships for the suite

`fixtures/` is required (MIN-08, MIN-13):

- `sources/<vendor>/tools.json` and `sources/<vendor>/calls/*.json`: a
  recorded listing and results, from which the harness builds a **fake MCP
  source** (generalised from `tests/ppc_fake_qore.py`);
- `artifacts/accept/<artifact name>/<case>` and
  `artifacts/refuse/<artifact name>/<case>`, one folder per gated name (each
  `artifacts` entry and the review artifact). Every refuse case has
  `<case>.expect.json` beside it, `{"contains": [...]}`, naming the words its
  refusal must say; an accept case may have one;
- `states/*.json`: snapshots at the moments that matter (no data, data
  connected, report published, reviewed).

### 6.4 The report

```json
{"plugin": "amazon-ppc", "sha": "40e0b7b…", "api": "1.0", "status": "failed",
 "minimum": {"passed": 16, "failed": 1},
 "extended": {"review": "pass", "receipts": "fail", "data_actions": "pass"},
 "results": [
   {"id": "EXT-RCT-1", "status": "fail",
    "why": "fields_of_read returned [] for fixtures/sources/qore/calls/get_stats.json; return Unknown() when the fields cannot be read",
    "input": "fixtures/sources/qore/calls/get_stats.json"}]}
```

The same report comes from the CLI, the REST API, the MCP tool and the
install screen.

## 7. The testing harness (`yaaf-plugin-sdk`)

Authors, and AI factories writing plugins, need the install's verdict before
they push. `pip install yaaf-plugin-sdk` gives:

- `yaaf plugin new <id>` scaffolds a repo that already passes the minimum
  suite (manifest, empty hooks, fixtures, one attack test per stub gate);
- `yaaf plugin check [path | github-url] [--ref]` runs **the same** build
  and conformance suite the install runs, with the same report. A green
  `check` and a failed install on the same SHA is a bug in yaaf;
- `yaaf_plugin_sdk.testing`, the pytest fixtures:
  - `plugin_client`: a real in-memory yaaf (the app built once per worker,
    as `tests/conftest.py` does) with the plugin loaded through a real host,
    so tests drive it over HTTP as an agent would (house rule #2);
  - `kind_project(client, mode=...)`, the equivalent of `make_project`;
  - `fake_source(client, "qore")` from `fixtures/sources/`;
  - `seat(client, role)` for a run token for a seat of the project;
  - `attacks`: helpers for the common ones (`publish_as(seat, name, bytes)`,
    `forge_receipt()`, `review_own_subject()`).

The SDK is versioned with the API package, so a plugin's CI can pin both.

An example attack an author writes:

```python
def test_a_report_citing_a_field_the_pull_lacked_is_refused(plugin_client, fake_source):
    project = kind_project(plugin_client, mode="audit")
    analyst = seat(plugin_client, project, "ppc-analyst")
    receipt = analyst.data_fetch("qore", "get_stats", {...})["receipt"]
    report = load_fixture("artifacts/refuse/cites-absent-field.json", receipt=receipt)
    r = analyst.publish("ppc-audit.json", report)
    assert r.status_code == 422 and "did not contain" in r.text
```

## 8. Surfaces

### 8.1 Data model

| Table / column | Holds |
|---|---|
| `plugins` | id, name, repo URL, enabled |
| `plugin_versions` | plugin id, SHA, ref asked for, manifest, API version, status, conformance report, build log, installed by/at |
| `projects.kind` | `null` for built-in modes, `"plugin:<key>"` for a plugin kind (built-in `mode` stays and is set from the mode's `shape`: `ships_once` → `deliver`, `runs_on_cycles` → `operate`, so every existing reader keeps working) |
| `projects.kind_mode`, `projects.plugin_version_id`, `projects.kind_state` | the plugin mode, the pin, the plugin's JSON |

This replaces `ppc_mode` and migration 0087. `shape.py` answers
`ships_once` / `runs_on_cycles` / `lead_role` / `label` from the registry
for a plugin kind.

### 8.2 REST

| Route | Who | Does |
|---|---|---|
| `POST /admin/plugins` | operator | install from a URL + ref (§5.2) |
| `GET /admin/plugins`, `GET /admin/plugins/{id}` | operator, chairman (read) | installed plugins, versions, status |
| `GET /admin/plugins/{id}/versions/{sha}` | operator, chairman (read) | manifest and conformance report |
| `POST /admin/plugins/{id}/versions/{sha}/disable`, `DELETE …` | operator | §5.4 |
| `GET /admin/kinds` | anyone who can create | built-in modes + ready plugin kinds, with modes, lead, crews, create fields |
| `POST /admin/projects` | chairman | gains `kind`, `kind_mode`, `kind_fields` |
| `POST /admin/projects/{id}/plugin` | chairman | §5.5 |
| `GET /observe/projects/{id}` | readers | gains `kind`, `kind_mode`, `plugin`, `kind_view` |
| `GET /agent/plugin/describe`, `GET /agent/plugin/file/{name}`, `POST /agent/plugin/action/{name}` | seats of a plugin project | §4.6 |

Every new route is classified for the MCP surface (`yaaf/server/mcp/surface.py`),
which the surface test already requires.

### 8.3 MCP

- `kinds_list`: the same as `GET /admin/kinds`, so Claude and other clients
  know what can be created;
- `create_project` gains `kind`, `kind_mode`, `kind_fields`, validated by the
  same code as REST;
- `plugin_install(url, ref)` (operator principals only), `plugins_list`,
  `plugin_report(id, sha)`;
- `plugin_spec()` returns the API version, the manifest schema, the view
  schema, and the requirement table (§6). An AI factory asked to "write a
  yaaf plugin for X" reads this, not a scraped README.

### 8.4 The Founder and the CLI

The Founder learns kinds from the registry: its prompt lists ready plugin
kinds with their descriptions, and its create tool takes their fields. A
spec naming a kind that isn't installed is refused with the plugin's URL if
the spec carries one. The CLI gets `yaaf plugins install|list|report|disable`
and `yaaf plugin check`.

### 8.5 The web app

- **Settings → Plugins** (operator): paste a GitHub URL and optional ref, see
  progress, read the conformance report as a requirement checklist, and
  disable or remove a version. The install screen states the trust line from
  §3 in one sentence: a plugin's text reaches your agents, and its code runs
  sandboxed.
- **New Project**: plugin kinds appear beside the built-in ones, labelled
  with the plugin and version. The form draws their `create_fields`, and a
  fixed lead and crew behave exactly as the Code Factory's and the PPC
  branch's do (`FIXED_LEADS`).
- **Project home**: the `kind_view` section, drawn by the generic renderer.
  The setup checklist shows plugin steps. A "plugin <id>@<sha>" chip links to
  its report.
- **Project Settings**: the pinned version, and "move to version…" (§5.5).

### 8.6 Sibling footprint

Before this is called done, diff its footprint against the Code Factory's
(CLAUDE.md). The surfaces a kind must reach are in the "Why" table. Each one
gets a plugin path or a written reason it doesn't need one: daily brief,
agenda, change stamp, MCP reader, CLI, clone, setup file export and restore,
fire-drill report, golden evals.

## 9. The Amazon PPC Factory as the first plugin

The PPC branch is not merged into core. It becomes `yaaf-amazon-ppc`, the
reference plugin, and the framework is built by removing every PPC line from
core. Each removal that still passes the PPC tests is evidence the API is
enough.

| PPC branch today | Becomes |
|---|---|
| `shape.py` PPC functions, `PpcMode`, `ppc_mode` column | `[kind]` in the manifest; `projects.kind*` |
| `yaaf/ppc/__init__.py` (contract, `check_report`, `check_review`) | the plugin's `contract.py`, behind `check_artifact` / `check_review` |
| `server/ppc.py` receipts, `reviewed`, `report_owed` | core's receipt and review primitives |
| `server/ppc.py` `data_owed`, `block` | `plan_gate`, `brief` |
| `analyse` + adapters | `data_action("analysis")` with the core `Gateway` |
| `/agent/ppc/kit`, `ppc_kit` tool | `agent_files = ["kit"]`, `plugin_file` |
| `_publish_audit_page`, deck, workbook | `render` + `platform_pages` |
| `checklist.ads_data` | `setup_step("ads_data")` |
| `observe._ppc_view`, `Overview.tsx` PPC section | `view.json` + `view()` |
| `NewProject.tsx` PPC branches | `create_fields`; `FIXED_LEADS` read from `/admin/kinds` |
| `tests/selftest_invariants/test_ppc_factory.py` | the plugin's `tests/` on the SDK harness, plus what core's suites now cover |

The three bugs from its review are fixed by construction: EXT-REV-2,
EXT-RCT-1 and EXT-ACT-2.

## 10. Build order

Each step ships with its attacks (house rule #2) and keeps the suite green.

1. **API package and manifest schema.** `packages/yaaf_plugin_api`: types,
   hook decorators, JSON schemas, the requirement table as data. No core
   change yet.
2. **Plugin host.** `yaaf.plugins.host` in the sandbox image, the JSON-RPC
   protocol, the pool, fail-closed handling, and the callbacks. Attack: a
   host that hangs, crashes, returns garbage, or tries to reach the network
   or read the environment.
3. **Registry and data model.** `plugins`, `plugin_versions`, the
   `projects.kind*` columns, and `shape.py` reading the registry. A
   *local-path* install for development first.
4. **Core primitives.** Receipts, review, platform pages and reserved
   artifacts, each with the attacks of §6.2 written against core directly.
5. **Hook points.** Wire `brief`, `plan_gate`, `completion_gate`,
   `check_artifact`, `setup_step`, `view` and `create_fields` into the places
   in the "Why" table, plus the MIN-14 "only tightens" replay.
6. **Conformance suite and SDK.** `yaaf plugin check`, the fixtures format,
   the fake MCP source, the report.
7. **GitHub install.** URL resolution, the pinned fetch, the hashed build,
   the background job, and the report UI.
8. **Extract PPC.** Move the PPC branch into `yaaf-amazon-ppc`. Install it
   from its GitHub URL in CI as an end-to-end test of the whole path.
9. **Surfaces.** MCP tools, the Founder, the CLI, the Settings → Plugins
   page, the generic view renderer, and the sibling-footprint pass.

## 11. Decisions to confirm

1. **Operator-only install in v1.** A chairman picks from installed kinds.
   Recommended, because install puts code on the whole box.
2. **Public repos only in v1.** Private repos need a GitHub credential on
   the install path and can follow.
3. **No plugin UI code.** The view schema only. A plugin that needs a
   richer page waits for a new component in core.
4. **Built-in kinds stay built in.** Code Factory is not extracted in this
   work. Once the API has carried PPC, extracting it is the stress test.
5. **Operate modes stay closed for plugins** until core has a cycle model a
   plugin can declare obligations against (EXT-CYC-1).
6. **A plugin cannot add sandbox tools.** Only the three generic ones. This
   keeps the agent-visible surface regression-gated by core.

## Status

**Step 1 is built** (`packages/yaaf_plugin_api`, API 1.0, no core change):

- `types`: the snapshot, the uploads and every return type, all frozen;
- `hooks`: `FactoryPlugin`, `@hook.*`, `expected(manifest)`,
  `check_plugin` (MIN-05) and `check_return` (the validators MIN-08, MIN-17
  and the extended suites use);
- `manifest`: `yaaf-plugin.toml` parsing and validation, plus the
  cross-field rules a schema cannot say;
- `view`: the component set and checks for the declaration and the data;
- `requirements`: the §6 table as data, with which part of yaaf decides
  each one (`checked_by`);
- `repo` and `report`: the static report for a checkout, also available as
  `python -m yaaf_plugin_api check <path> [--json]`. A requirement only a
  build, host, harness or core can decide is `pending`, never `pass`;
- `schema/manifest.schema.json` and `schema/view.schema.json`;
- `examples/grant-factory`: a complete small plugin that passes every check a
  checkout can decide.

The attacks are in `tests/selftest_invariants/test_plugin_api.py`. The schema
and the validator must refuse the same bad manifests. A typo'd capability is
refused, not ignored. Code that misses, adds or mis-sizes a hook is named. A
return core couldn't act on is named. The requirement ids must match this doc.
The package must depend on the standard library only.

Two refinements from building it: the API answers "fields could not be read"
with `ReadFields.unknown()` and refuses an empty tuple, which closes the PPC
receipt bug at the type. And a plugin hands core the load-bearing numbers in
`Accept.load_bearing` when its artifact is accepted, so the review primitive
compares re-derivations against the subject's numbers and never the review's.

**Step 2 is built** (the plugin host and all three launchers):

- `yaaf_plugin_api.wire`: frames (4-byte length + JSON, capped at 64 MB) and a
  codec whose decoder builds only the API's own dataclasses. A message naming
  any other type is a `WireError`.
- `yaaf_plugin_api.host`: the host program. It imports the plugin, answers
  one hook per request, and gives a hook's snapshot its callbacks (and a data
  action its `Gateway`). Plugin output goes to stderr, so fd 1 stays the
  protocol's. It clears the environment, writes no bytecode, and seeds
  randomness per call. An audit-hook guard is armed before the plugin's first
  line and never disarmed. It refuses network, processes, native code, writes
  outside scratch, reads outside the plugin and Python, and changes to the
  environment, and it reports each attempt so core fails the call even when the
  plugin swallowed the PermissionError.
- `yaaf/plugins/host.py`: core's side.
  - `HostSpec` (one plugin version), `Host` (one process), `HostPool` (warm
    hosts per version, recycled after 200 calls or any failure that killed one),
    and `ask_or_fail_closed` with a fail-closed answer for every hook kind.
  - Core re-checks which callbacks a hook kind may make, because the host is
    the plugin's process. It validates every answer with `check_return`, and
    kills a host that timed out, died, broke the protocol or reached out.
  - Launchers: `subprocess` (dev and CI only, refused on a box holding the
    Docker socket by the same rule as host success tests); `host` (a bubblewrap
    jail with `--unshare-net`, the plugin and API mounted read-only, scratch the
    only writable mount); and `docker`, which fails closed for now, saying why.
- Attacks: `tests/selftest_invariants/test_plugin_host.py`. A plugin that
  hangs, dies, raises, exits, returns garbage or an unusable value, prints a
  forged frame, connects out, starts a process, loads ctypes, writes outside
  scratch, reads the parent's environment or a server file, or reaches out at
  import. A host that lies: it asks for `gateway.call` from a brief, names a
  foreign type, answers the wrong id, sends a huge or non-JSON frame, or never
  starts. Each ends in the refusing answer. Removing the guard turns 9 of these
  red, and removing core's callback check turns the forged-gateway one red.

### Decided: a docker install reaches plugin hosts through the runner

In a docker deployment only the runner holds the Docker socket. The API
server, which asks the gates, does not and should not: it is the
internet-facing process, and the socket is root on the box. So the **runner
brokers plugin hosts** (`yaaf/plugins/broker.py`). Rejected alternatives: a
dedicated socket-holding service (a second root-equivalent process to run and
patch), and giving the API the socket.

- The runner listens on port 24250 on the stack's default network only. The
  runner is not on the sandbox network, so no agent container can reach it.
- The server's `BrokerLauncher` opens one TCP connection per host and answers
  a challenge with an HMAC under a key derived from `YAAF_MASTER_KEY`, which
  both services already hold. It names the plugin to run.
- The broker refuses any path outside `YAAF_PLUGINS_ROOT`. It starts the
  sandbox image with flags it alone decides: `--network none`, a read-only
  root and plugin, noexec tmpfs scratch, all capabilities dropped,
  `no-new-privileges`, and memory and pid caps. It then proxies the host's
  frames byte for byte. Core checks them on the server exactly as it does for
  a local host. Closing the connection removes the container.
- Plugins live under `YAAF_PLUGINS_ROOT` at the same path on the host, the api
  (read-write: it installs) and the runner (read-only). The sandbox image
  carries the API package at `/opt/yaaf-plugin-api/src`.
- If the runner is down, plugin calls fail closed with "is the runner up?".
- Attacks: `tests/selftest_invariants/test_plugin_broker.py`. A wrong key or a
  path outside the root starts nothing. A traversal or a symlink out of the
  root is refused. A host dies with its connection. The flags are the
  broker's. The compose file keeps the broker off the sandbox network.
  Removing the HMAC check turns a test red.
- Not exercised in CI: the Docker SDK attach path itself (`_DockerHost`), since
  CI has no daemon to start a container in. Its flags are checked as data, and
  the broker is tested with a local process in the container's place.

**Step 3 is built** (the registry and data model):

- Tables `plugins` (id, name, the kind it owns, enabled) and `plugin_versions`
  (version, source, manifest, status, report, where its code sits), plus
  `projects.kind`, `kind_mode`, `plugin_version_id` and `kind_state`
  (migration 0087). A plugin project's built-in `mode` will be set from its
  plugin mode's shape (`shape.mode_for_shape`), so the readers that draw
  Explore/Deliver/Operate keep working unchanged.
- `yaaf/plugins/registry.py`: install from a local path (development only,
  behind `YAAF_ALLOW_LOCAL_PLUGINS`). The checkout is copied under
  `YAAF_PLUGINS_ROOT` as `local-<sha256 of its files>`, so the same files are
  one version and a changed file is a new one. At install the registry decides
  MIN-02 (a plugin keeps its kind; no two plugins share one), and MIN-04 and
  MIN-05 by starting the code in a sandboxed host and comparing the hooks it
  reports with the manifest. A failed version keeps its report and loses its
  code. Statuses are `pending | ready | failed | disabled`. A new project may
  choose only `ready`, or `pending` on a box with `YAAF_ALLOW_UNVERIFIED_PLUGINS`.
  Nothing is `ready` until the conformance suite (step 6) runs.
- REST (`yaaf/server/api/plugins.py`). The instance admin uses
  `POST /admin/plugins/local`, `…/disable` and `DELETE …`. Anyone signed in
  uses `GET /admin/plugins`, `GET …/versions/{version}` and `GET /admin/kinds`
  (built-in modes plus offered plugin kinds). Every write is audited with its
  actor. A version a project is pinned to cannot be removed. All six routes are
  classified as withheld from MCP, with reasons; `/admin/kinds` joins the MCP
  create tool in step 9.
- Attacks: `tests/selftest_invariants/test_plugin_registry.py`. Local install
  is off by default. Only the admin installs. Code that misses a declared hook,
  or reaches out at import, is recorded failed and never offered. A pending
  version is offered only where allowed. Two plugins cannot share a kind, and a
  plugin cannot change its own. A path that is not a checkout is refused. A
  pinned version cannot be removed.
- Not yet carried: a plugin project's kind fields in clone and setup-file
  export. Nothing creates a plugin project until step 5, and step 5 must add
  them there (the sibling-footprint rule).

**Step 4 is built** (core's primitives, `yaaf/plugins/primitives.py`). It took
`check_artifact` and `render` from step 5, because the review needs the
subject's accepted numbers:

- **Gated artifacts.** `artifact_put` asks the plugin's `check_artifact`
  before a byte is stored. A refusal stores nothing. An accept may normalise
  the bytes, patch `kind_state` (JSON merge patch), and name the load-bearing
  numbers, which core keeps in `plugin_artifacts` beside the row.
- **Reserved names.** A seat publishing a platform page or a data action's
  output is refused.
- **Platform pages.** `render` is asked for a draft when the subject is
  accepted and a final version when a review passes. Pages are stored as the
  platform's, with the subject's reader and requirements. A render that fails
  refuses the publication.
- **Review.** Core reads a fixed envelope (`subject_sha256`, `verdict`,
  `rederived`; documented on `ReviewUpload`):
  - The review must name the latest subject's exact hash.
  - Its seat must not have published those bytes (or any version, under
    `independent = "strict"`), even after another seat republished them.
  - Every load-bearing number is compared, within the manifest's tolerance,
    with what the *subject* reported. A review's own `reported` is never read.
  - Then the plugin's `check_review` applies its rubric. A pass attests
    delivery.
  - `delivery_outstanding` holds completion until the latest review passes the
    latest subject, so a new subject version or a later `return` takes it back.
- **Receipts.** The gateway records each read by a seat of a plugin project
  that declares receipts, with the fields `fields_of_read` names, and
  `data_fetch` hands the receipt to the agent. A read whose fields can't be
  told (or whose plugin can't be asked) is recorded with `fields = None`. It
  proves the read happened and backs no field citation. Receipt lookups answer
  only for their own project.
- Every plugin call fails closed. A host that can't be asked refuses the
  publication with "could not be consulted".
- Attacks: `tests/selftest_invariants/test_plugin_primitives.py` (17 tests),
  publishing over `/agent/artifacts` with a real host. Restoring the PPC
  branch's two review bugs (reading the review's own `reported`; judging
  independence by the latest publisher) each turns a test red.

**Step 5 is built** (hook points, and creating a plugin project):

- **Creation** (`yaaf/plugins/kinds.py`). `POST /admin/projects` takes `kind`,
  `kind_mode` and `kind_fields`, and an import posts the same body. Before a
  row exists, the server refuses:
  - a kind not offered on this box;
  - a mode the plugin doesn't declare, or doesn't declare open;
  - a `mode` that disagrees with the plugin mode's shape;
  - a lead other than the plugin's fixed lead (an unnamed lead is filled);
  - fields its `create_fields` does not describe (required, type, choices,
    unknown keys), and whatever its `validate_create` refuses.

  A plugin that cannot be asked refuses the creation. The project stores its
  kind, mode and pinned version, `kind_state = {"create": fields}`, and the
  built-in `mode` its shape behaves as.
- **Export, import and clone.** Export carries `kind`, `kind_mode` and
  `kind_fields`, so an import goes through every creation check (the pinned
  version is this box's code and is not exported). A clone keeps the source's
  pinned version, and is refused if that version may no longer be chosen. It
  carries the creation fields, not the plugin's later state.
- **Hook points** (`yaaf/plugins/points.py`), each only adding to core's answer:
  - the brief goes after core's rules under a heading core writes
    ("THIS PROJECT'S KIND: …");
  - `plan_gate` items keep the plan from holding (`evaluate_plan`);
  - `completion_gate` items keep the project from completing
    (`delivery_outstanding`), beside the review primitive's own;
  - setup steps appear on the checklist as `plugin:<key>`;
  - the dashboard carries `kind`, `kind_mode` and `kind_view` (the declared
    view, its validated data, or why it could not be read).

  A plugin that is down owes a plan item and a completion item, marks its steps
  not done, has an empty brief, and shows its view as unreadable.
- **A plugin only tightens (MIN-14, first slice).** On a plugin project's gated
  artifact, core's own refusals (a path for a name, bad base64, an invented
  requirement) fire before the plugin is asked. Step 6 replays core's wider
  attacks against every plugin's project.
- The example plugin now declares a plan gate and create fields.
- Attacks: `tests/selftest_invariants/test_plugin_kinds.py` (18 tests).
- Deliberately left for step 9 (surfaces), as the sibling-footprint rule asks
  us to say: the MCP `create_project` tool and the Founder don't take a plugin
  kind yet; the web form, project home and Settings don't draw one; the CLI,
  daily brief and agenda don't name the plugin. A plugin's setup steps show on
  the checklist but can't be skipped, and none is required to start.

**Step 6 is built** (the conformance suite, and running it yourself):

- `yaaf_plugin_api.conformance` is one suite with two runners. It is handed a
  `call` that asks a hook and returns the validated answer or raises
  `Failure`:
  - an author runs it with `python -m yaaf_plugin_api check --full`, through
    `client.LocalHost` (the same host program, as a subprocess);
  - core runs it at install through the sandboxed host pool
    (`registry.conformance_results`).

  A test runs both on the same broken plugin and expects the same failing
  requirements.
- What it asks:
  - every gate's accept and refuse fixtures (refusals must say what their
    `.expect.json` names), the stored bytes again (EXT-ART-2), and the fuzz
    corpus (`corpus.py`: empty, non-UTF-8, 2,000-deep, 4 MB and more), which a
    gate may refuse and must never accept;
  - the review's fixtures against the subject the plugin itself accepted, with
    `$SUBJECT_SHA256` substituted;
  - draft and final renders;
  - `fields_of_read` on recorded and hostile results;
  - each data action on a fake source built from `fixtures/sources/`, and with
    the source unreachable, signed out, erroring, timing out, or with its tools
    switched off; a call to an undeclared tool fails EXT-ACT-3;
  - brief, gates, steps, view and `migrate_state` in each fixture state (a
    state's `clears` names gates that must owe nothing there);
  - `create_fields` and `validate_create` on create fixtures and hostile fields.

  Every pure hook is asked twice, and different answers fail MIN-11. A timeout
  fails MIN-10, an escape MIN-12, a stack-trace refusal MIN-17, and a bad answer
  fails its capability's own requirement as well as MIN-08 or MIN-09.
- Requirements core holds for every plugin (MIN-14, EXT-REV-1/2/3) are reported
  as held by core, citing their tests. EXT-CYC-1 fails for any open cycling
  mode until core has a cycle model.
- MIN-16: each gate must be named by a test in `tests/`, and the plugin's tests
  must pass under pytest (`client.run_tests`: only the API and the plugin on the
  path, an empty environment). On an install, they run in subprocess mode. In
  host and docker mode they wait for the GitHub build sandbox (step 7), so a
  local install there stays `pending`.
- Install now decides everything:
  - MIN-03 for a local install: it builds nothing, so a plugin declaring
    dependencies fails and is told to install from GitHub.
  - MIN-07: each role goes through the library's own `create_role` rules in a
    nested transaction, the lead must be a lead, and crews must be crews led by
    it. A version that passes adds its roles to the library (existing ones are
    left alone); a failed one adds nothing.
  - A version is `ready` only when every applicable requirement passes, and
    `ready` is offered on any box, with no development flag.
- The suite caught two real gaps in the example plugin. It patched `kind_state`
  without declaring `state` (so it had no `migrate_state`), and no test attacked
  its review gate. The fuzz corpus also made its gates catch `RecursionError`.
  All three are fixed, and the example's roles and crew are now real library
  entries.
- Attacks: `tests/selftest_invariants/test_plugin_conformance.py` (18 tests,
  each breaking the example one way and expecting that requirement to fail), and
  `test_plugin_registry.py`, where a suite failure or a bad role fails the
  install and a ready version adds its roles.
- Not built: the in-app SDK harness (`plugin_client`, a real yaaf for authors'
  HTTP tests, §7). Authors' tests run against the API and their plugin. The
  `yaaf plugin new` scaffold is also not built.
