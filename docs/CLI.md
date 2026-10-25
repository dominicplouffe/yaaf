# The `yaaf` CLI

Everything the dashboard does, scriptable. Useful for provisioning, CI, and
poking at a running deployment.

```bash
export YAAF_API=http://localhost:8080      # the default, so usually omitted
yaaf login                                 # your email and password, once
```

`yaaf login` exchanges your password for a **personal token** — named, expiring
(30 days unless `--days` says otherwise, never more than 365), and kept in
`~/.config/yaaf/credentials.json` with mode 0600, one per API base. It is you:
it reaches what your account reaches and nothing more, and every write it makes
is recorded as `via token:<name>` so a script never reads as a click. `yaaf
logout` revokes it on the server and forgets it here; `yaaf whoami` says who
the CLI is. `YAAF_TOKEN` overrides the stored token, for a script that was
handed one. The static `YAAF_ADMIN_TOKEN` this used to read was retired — see
[AUTH.md](AUTH.md).

Every command that takes a project accepts **either a slug or an id**.

---

## Setup

| Command | What it does |
|---|---|
| `yaaf doctor` | Check the install end to end — server, your sign-in, the dashboard build, the master key, roles and crews, a key to pay with, the Founder's key, the runner, the public address, email — and print the command or page that fixes each failure. Exits 1 while a problem stands. |
| `yaaf seed-roles [DIR] [--only-missing]` | Load every `*.json` in the directory (default `roles/`) into the role library. Re-running creates new role *versions* — which is how an edited prompt reaches a running install. `--only-missing` skips the slugs the library already has, which is what you want after an update adds roles to the checkout; it still refiles those roles' **category and domains**, because those live on the role rather than the version and so can move without putting a live team behind its prompt. |
| `yaaf seed-crews [DIR] [--only-missing]` | Load every `*.json` in the directory (default `crews/`) into the crew library — the pre-staffed teams the hiring desk offers. Crews are not versioned: one is read at hiring time and copied into the project's own teams, so re-seeding an edited crew can never reach a project that already started. Run it after `seed-roles`. |
| `yaaf create-user EMAIL NAME [--admin]` | Invite somebody (instance admins only). Prints the one-time link that lets them choose a password — the box sends no mail, so hand it on. Idempotent by email. |
| `yaaf create-project SPEC_FILE` | Create a project from a JSON spec (shape below), or from a setup file like those in `examples/` — roles it carries that your library lacks are added first, and its credential is matched by name. |
| `yaaf set-secret NAME [--project P] [--scopes a,b] [--plain] [--kind service]` | Store an encrypted secret. **The value is prompted** — never echoed, logged, or left in shell history. Omit `--project` for a platform-level secret. Omit `--scopes` when rotating and the roles that have it keep it; `--scopes ''` grants nobody. `--plain` marks a setting that is not secret (a timezone, an account id) so the dashboard shows it as is; `--secret` masks it again. |
| `yaaf set-secret NAME --from-file PATH [--filename NAME]` | Store a credential that **is a file** — a service-account key, a PEM, a kubeconfig. No prompt: a key file is not something anyone retypes. Granted roles receive its *path*, not its contents; `--filename` names it inside the sandbox and defaults to the file's own name. |
| `yaaf llm-credentials [--include-hidden]` | List named LLM credentials and project assignments (no values). Each project says whether it is still live, and the credential says whether any of them are. |
| `yaaf create-llm-credential NAME [--mode api_key\|subscription] [--provider P] [--base-url URL]` | Create a managed credential; value is prompted (a local Ollama or LM Studio server takes none — press enter). `--provider` names who it pays (`anthropic` by default; `yaaf providers` lists every one — `openai`, whose `subscription` kind is the ChatGPT login `codex login` writes, pasted whole; `google`, whose `subscription` kind is the `oauth_creds.json` the Gemini CLI writes; `vertex`, whose value is a service account key), and a provider whose address is the credential's own — a custom server, a local one, an Azure resource, a Bedrock region, a Vertex project — takes its `--base-url`. |
| `yaaf providers` | Every provider this install can pay: its wire, the credential kinds it takes, and what `@frontier`, `@balanced` and `@fast` name on it. |
| `yaaf models` | The install's lineups, its default provider and its per-provider ceilings ([PROVIDERS.md](PROVIDERS.md)). |
| `yaaf set-lineup PROVIDER CLASS [MODEL]` | Name what a class (`frontier`, `balanced`, `fast`) is on a provider — omit the model to go back to the shipped one. Every class seat of every project that provider pays moves with it on its next run; the answer names any live seat it leaves unable to run. |
| `yaaf set-default-provider PROVIDER` | Where a project that pays nobody yet resolves its classes, and the Founder's before it has a key. |
| `yaaf set-provider-ceiling PROVIDER [N]` | At most N runs on a provider at once across every project — its rate limit, which the whole box shares. Omit N to lift it. |
| `yaaf import-prices [--snapshot] [--apply] [--provider P] [--model M…] [--overwrite-own] [--include-shipped]` | Price models from the price feed, or the dated snapshot shipped with yaaf: a preview model by model (`add`, `update`, `same`, `own`, `shipped`, `missing`), written only with `--apply`, each row labelled with where it came from. Prices you typed and the catalog's checked ones are kept unless asked. |
| `yaaf discover-models CREDENTIAL_ID` | What the provider serves for a credential, by the names a seat or a lineup uses — one free call to its own model list. |
| `yaaf check-provider CREDENTIAL_ID [--model M ...] [--probes-only] [--yes]` | Can these models do yaaf's work? Probes (exact instructions, nested tool arguments, a tool result used, parallel calls, the effort dial, search) and five small agent tasks per model, graded mechanically; prints the estimate and asks first, then each step's result. Exits 1 when a model fails. |
| `yaaf upkeep [--enable/--disable] [--run]` | Keep models and prices current: switch the daily pass (re-read each key's model list, refresh feed-sourced prices — never one typed by hand) on or off, or run it now. |
| `yaaf smoke CREDENTIAL_ID [--model M]` | One real, trivial call on a credential through the wire a seat on it runs: the model that answered, whether it called a tool, the usage and what it cost at this install's price. Spends a few hundred tokens of the credential's own money — the live check RUNTIMES.md's "a live seat on …" lines ask for. |
| `yaaf set-web-search BACKEND [--base-url URL] [--price USD]` | Name the search backend (`brave`, `tavily`, `exa`, `serper`, `perplexity`, `searxng`) every seat whose provider cannot search uses; the key is prompted. Each query is billed into the run at the price. |
| `yaaf pay-provider PROJECT PROVIDER CREDENTIAL_ID` | Pay a second provider on a project, for seats on its models — metered in the project's unit, against its one cap ([RUNTIMES.md](RUNTIMES.md)). |
| `yaaf set-model-price MODEL INPUT OUTPUT [--cache-read X]` | Price a model on this install, USD per million tokens — how a model yaaf ships no price for can be hired onto a harness that meters by price. A price you type is yours: no import replaces it unless asked. |
| `yaaf edit-llm-credential ID NAME [--rotate]` | Rename a credential; optionally replace its value for all assigned projects. |
| `yaaf delete-llm-credential ID` | Delete an unused credential after confirmation. Assigned credentials cannot be deleted. |
| `yaaf hide-llm-credentials ID…` | Take credentials off every menu — Settings, the project picker, the Founder's list, an MCP spec — without deleting them. Refused for any credential a live project can still spend on; each id is judged on its own and refusals come back with the reason. |
| `yaaf unhide-llm-credentials ID…` | Put hidden credentials back on the menus. Refused if another credential has taken the name in the meantime — rename it first. |
| `yaaf llm-credential-usage ID [--refresh]` | What Anthropic last reported was left on a subscription credential's plan (session and weekly windows, as `/usage` shows them). Every agent run on the credential reports its windows through the SDK, and reading them back costs nothing. `--refresh` asks Anthropic instead — the way to get a number for a credential no run is covering — floored at one call per subscription every 15s. The answer carries an `asked` block saying what the request did: `fresh`, `floored`, `throttled` (with the wait), `rejected` (the token was refused — replace the credential's value), `unreachable` or `unreadable`. A window that has reset since it was last reported shows the rollover rather than the number it had while it was open. API keys have no plan meter and say so. |
| `yaaf settings` | Show the app-wide settings — whether the Founder's credential is installed and how the Slack connection is configured. Values are never printed. |
| `yaaf set-platform-key [--mode api_key\|subscription] [--provider P] [--base-url URL] [--model M] [--from-credential NAME]` | Set the key the **Founder** drafts on: a new one (value prompted), of any provider, or one of your LLM credentials with `--from-credential`, so one key serves both. The Founder runs on the provider's frontier model unless `--model` names another. Never injected into a sandbox and never a project's billing key. |
| `yaaf set-slack [--bot-token] [--signing-secret] [--app-token] [--chairman U…] [--prefix P] [--visibility private\|public] [--auto-create/--no-auto-create]` | Configure the app-wide Slack connection every project inherits. Token flags prompt for the value. |
| `yaaf slack-channels PROJECT` | Create the Slack rooms this project is missing and print their names. Idempotent — a mapped room is left alone, a name Slack already holds is adopted. |
| `yaaf configure PROJECT [--runtime …] [--billing-secret-id …] [--billing-mode …]` | Update outward wiring only. **Never the constitution** — cadence, concurrency and every other limit is an amendment. |
| `yaaf amend PROJECT --reason "…" --set rule=value` | Change a constitution rule. Repeatable `--set`; values are parsed as JSON where possible, so `--set runs_per_project_per_day=80` gives a number and `--set ceo_daily_at='"09:00"'` gives a string. An invalid overlay is refused whole. |
| `yaaf save-seed [DIR]` | Write every role and project *design* to a folder as JSON. Plumbing and secrets stay behind. |
| `yaaf seed [DIR] --chairman EMAIL [--roles-only]` | Restore such a folder onto an install. Projects come back **in draft** — restoring a backup never starts spending. `--chairman` is your own email unless you are an instance admin restoring for somebody else. |

### Project spec shape

```json
{
  "slug": "my-project",
  "name": "My Project",
  "goal": "A goal the CEO can plan from — the outcome, the deliverable, who it is for, the deadline, and where the inputs come from.",
  "chairman_email": "you@example.com",
  "mode": "deliver",
  "billing_mode": "api_key",
  "controlled_domains": ["myproject.com"],
  "github_repo": "you/my-project",
  "constitution": {"queue_ceiling": 5, "monthly_usd_cap": 300},
  "integrations": {"runtime": "mock"},
  "ceo_role_slug": "ceo",
  "ceo_name": "CEO",
  "ceo_daily_at": "09:00",
  "teams": [
    {"role_slug": "engineer", "name": "Engineering",
     "employees": ["Ada", "Grace"], "lead": "Ada",
     "schedules": {"Grace": "07:30"},
     "model_overrides": {"Ada": "claude-opus-5-5"},
     "harness_overrides": {"Grace": "claude_cli"}}
  ],
  "solo": false
}
```

- `mode` is `explore` (default), `deliver`, or `operate`, and is **frozen at
  creation** — the rules differ by shape. Outside Explore the project starts
  owing a plan and a task serving no requirement is refused.
- `deadline` (ISO datetime) is Explore's window — open scope, bounded time —
  and, on Deliver, a **target** the CEO is told is a ceiling rather than a
  pace. On Deliver it needs `deadline_source`: `{"kind": "external", "url" or
  "note": ...}` for a date the world imposes, or `{"kind": "chairman", "note":
  why}` for one you want. If it is neither, write no date — scope is the
  honest lever, and the median project here finishes inside a day. Operate
  refuses a project date: its cycles carry their own due-by and its source.
  Deliver promises through a commitment and Operate runs on cycles, so a bare
  date is refused there.
- `constitution` holds **overrides** onto the defaults; omit it for stock rules.
  A full rulebook (what an export carries) validates fine too.
- `integrations` is wiring, never limits: `runtime` (`agent_sdk` | `mock`),
  Slack mapping, data sources. Anything that caps or paces an agent lives in
  the constitution. `runtime: mock` puts every seat on the scripted backend;
  otherwise each seat runs on its own harness.
- A team's `model_overrides`, `effort_overrides` and `harness_overrides` are
  per person, keyed by name; `ceo_model_override`, `ceo_effort_override` and
  `ceo_harness_override` are the CEO's. A model override is a model or a class
  (`@frontier`, `@balanced`, `@fast`, or `openai/@fast` for one provider's —
  [PROVIDERS.md](PROVIDERS.md)); a class runs on the billing credential's
  provider. A harness (`claude_agent_sdk`, `claude_cli`, `yaaf_loop`,
  `codex_cli`, `gemini_cli`, `opencode` — see [RUNTIMES.md](RUNTIMES.md)) is
  what drives the seat's model; absent, it is the role's, and the role's absent
  is the default of the credential kind that pays the seat. A seat whose
  harness cannot serve its model, take away a grant its role withholds, pay
  with the project's credential, or price its model when it meters by price —
  or whose class has no model on its provider — is refused at creation, with
  the reason.
- `credentials` pays each provider the billing credential does not, as
  `{"deepseek": "<credential id>"}` — one per provider, metered in the billing
  credential's unit. A seat on a provider the project does not pay is refused.
  An export carries them by name as `credential_names`.
- `controlled_domains` are the domains that **cannot** count as external
  evidence — your own site proving your own success is not proof.
- `ceo_daily_at` and a team's `schedules` are `"HH:MM"` daily wakes read in the
  constitution's timezone. A schedule naming nobody on the team is refused
  rather than dropped — a wake that silently fails to attach is missed at 09:00
  every day, with no error anywhere.
- `solo: true` places the CEO as the sole member of the only team (which must
  have no employees), so one brain plans and executes under identical policy.

A setup file from [`examples/`](../examples/) or from a project's
**Settings → Duplicate & export** is a superset of this: the same object under
a `project` key, plus `roles` and `secrets`. The dashboard's New-project form
loads one whole; from the CLI, feed the `project` object to `create-project`
(and `yaaf seed` does exactly that for a whole folder).

## Lifecycle

```bash
yaaf start my-slug
yaaf pause my-slug --reason "waiting on the client"
yaaf stop  my-slug --reason "shipped"
```

## Observation

| Command | Shows |
|---|---|
| `yaaf up --no-isolation [--email E] [--home DIR] [--port N]` | Start yaaf on this machine in one command — key, database, schema, server, runner, first admin, role library — and print the address. Refuses to pick the unsandboxed mode for you. See DEPLOYMENT.md. |
| `yaaf projects [--json]` | Every project as one line: status, spend against the budget, the hard stop, and how many things wait on you. `--json` for the raw payload. |
| `yaaf status [PROJECT] [--json]` | Is everything OK, in sentences: one paragraph across your projects, each project's status line (the same words Home, the project's Inbox and Claude's `desk` say), then everything waiting on you, with ids. `--json` for the status lines alone. |
| `yaaf rehearse SPEC_FILE [--days N] [--usd-per-run N] [--json]` | **A fire drill: run the spec as a project for nothing, before you fund it.** Needs no deployment and no key — `yaaf rehearse examples/01-your-first-hour.json` works on a fresh checkout. See [FEATURES §13](FEATURES.md). |
| `yaaf briefing [PROJECT] [--hours N] [--seen]` | **What happened since you last read one.** Omit the project for every live project. Composed from the record at zero tokens, so it works on a paused project and a broke one. Reading never moves your mark; `--seen` does, and only to the edge of the window it just printed. |
| `yaaf agenda PROJECT [--days 14]` | **What it is scheduled to do next**: the wall-clock wakes, the standing meeting, the cycle roll, the kill dates, the clocks that decide themselves, and the promised date with the platform's projection beside it. |
| `yaaf dashboard PROJECT` | The full project dashboard payload. |
| `yaaf board PROJECT` | The task board. |
| `yaaf org PROJECT` | Teams, leads, employees, who's awake, and which teams have a declared end. |
| `yaaf channels PROJECT` | Channels and their ids. |
| `yaaf inbox PROJECT` | Each agent's unread messages, newest first, and how many inbox reads the project has left — the messages the dashboard's inbox banner counts. |
| `yaaf tail CHANNEL_ID [--limit 30]` | Recent messages, formatted, frozen threads marked. |
| `yaaf runs PROJECT [--limit 20]` | Recent runs: agent, wake reason, cost, status. |
| `yaaf activity PROJECT [--since today]` | Who spent the runs: one row a seat, with runs, wake reasons, tokens, money and tasks closed. `--since` takes `today` (the run day, which starts at 00:00 UTC when the ration resets), `yesterday`, `7d`, a date in the project's timezone, or an ISO timestamp. |
| `yaaf ledger PROJECT` | Spend entries. |
| `yaaf evidence PROJECT` | External evidence and verification status. |
| `yaaf hypotheses PROJECT` | Live bets and their kill dates. |
| `yaaf approvals PROJECT` | Pending approvals. |
| `yaaf flags PROJECT` | What the platform obliged the CEO to tell you, and whether it has. |
| `yaaf asks PROJECT` | Questions the CEO put to you, live and settled. |
| `yaaf audit PROJECT [--n 5]` | A random spot-audit sample of recent evidence and completed tasks. |
| `yaaf receipt PROJECT [--json]` | **What it cost, and what the money bought.** Metered spend including the runs that died, beside only what the platform itself wrote: requirements it marked met after running their tests, external claims it fetched and hashed, closes it drew at random. Carries no agent-written text, so it is safe to paste in public. |

## Chairman actions

| Command | What it does |
|---|---|
| `yaaf say PROJECT "message"` | Speak in the boardroom — your only channel. |
| `yaaf decide APPROVAL_ID --approve\|--deny [--note …]` | Decide an approval. |
| `yaaf extend HYPOTHESIS_ID NEW_KILL_DATE REASON` | Your one-time extension of an expired bet (ISO datetime). |
| `yaaf unfreeze MESSAGE_ID` | Release a thread the pair circuit breaker froze. |
| `yaaf audit-evidence EVIDENCE_ID --ok\|--bad [--note …]` | Record your verdict on a piece of evidence. |
| `yaaf ack-flag FLAG_ID [--note …]` | Answer a flag the CEO raised. A moment closes here; a condition still true stays open under your note. |
| `yaaf ack-heads-up HEADS_UP_ID [--note …]` | Mark noted something you asked to be told. Nothing waits on it; a note is held until the CEO answers it. |

Constitution changes go through `yaaf amend` (above) or Settings → **Rules in
force**. Either way they carry a written reason, an audit entry, and disclosure
to the CEO — see [GOVERNANCE.md](GOVERNANCE.md#changing-a-rule).

Scope decisions — accepting a proposed requirement, cutting one, signing or
moving a delivery date, pulling a review lever — are dashboard actions
(**Plan** and **Review** tabs) or direct API calls; there is no CLI shorthand,
because each needs a reason you would want read back at the next review.

## People

Who sits on a project, and who is on the box. The roles are in
[AUTH.md](AUTH.md): a chairman decides and decides who decides; co-chairs
decide as peers; observers read.

| Command | What it does |
|---|---|
| `yaaf login [--email E] [--name N] [--days D]` / `yaaf logout` / `yaaf whoami` | Sign the CLI in and out; see above. |
| `yaaf members list PROJECT` | Everyone on a project and their role. Any member may read it. |
| `yaaf members add PROJECT EMAIL [--role co_chair\|observer]` | Put somebody with an account on a project. The chairman's alone. |
| `yaaf members role PROJECT EMAIL ROLE` / `yaaf members remove PROJECT EMAIL` | Change a member's role, or take them off — from their next request on. The chairman's alone. |
| `yaaf members hand-on PROJECT EMAIL` | Make somebody else the chairman; you stay on as a co-chair. The chairman may, and so may an instance admin for a chairman who has left — recorded as such. |
| `yaaf people list\|link\|promote\|demote\|deactivate\|reactivate EMAIL` | The box's accounts, for instance admins. `link` prints a fresh password link; `deactivate` ends every credential the person holds, and is refused while they chair a live project. |

**From a shell on the box**, `yaaf users` talks to the database directly — the
same trust as `yaaf spend-report`, because a shell that can reach the database
already is root on the box. It is how a box gets its first admin and a
locked-out admin gets back in:

| Command | What it does |
|---|---|
| `yaaf users bootstrap --email E [--name N] [--base-url URL]` | Make the first admin, on a box that has none, and print their setup link. Refused once any admin exists. |
| `yaaf users setup-link EMAIL [--base-url URL]` | A fresh one-time link that sets this person's password; earlier unused ones stop working. |
| `yaaf users token [EMAIL \| --first-admin] [--minutes M \| --days D] [--name N]` | Print a personal token and nothing else, for a script to capture. The deploy scripts mint a ten-minute one to load the role library. |
| `yaaf users has-admin` / `yaaf users list` | Exit 0 if the box has an admin; list the accounts. |

## MCP client tokens

The chairman's MCP server ([MCP_SERVER.md](MCP_SERVER.md)) has its own
principal — never your own login or personal token, which carry everything you
may do and are unattributable to any machine.

| Command | What it does |
|---|---|
| `yaaf mcp-token mint NAME [--scopes read]` | Mint a client token that acts for you. Printed once; only its hash is stored. |
| `yaaf mcp-token list` | Your clients (every client, for an instance admin), what they hold, when last used. |
| `yaaf mcp-token revoke CLIENT_ID` | Revoke it. The row survives — the audit trail names it. |

```bash
yaaf mcp-token mint dom-laptop --scopes read
# → {"token": "ymt_…", "scopes": ["read"]}
```

Scopes are `read`, `steer`, `create`, `configure`, and `read` is implied by all
of them. Start with `read` alone: it is most of the value and none of the risk
— a client holding it sees your projects and signs for nothing. A client
reaches the projects you are a member of, read fresh on every call, and may
steer or configure only where you chair or co-chair; scopes narrow that
further. Nobody mints a client in somebody else's name, an instance admin
included.

## Spend analysis

```bash
yaaf spend-report [PROJECT] [--days N] [--all-projects]
```

Breaks LLM spend down by agent, role, model, and wake reason. Defaults to the
current calendar month.

> This one reads the **database directly**, not the API: it needs
> `YAAF_DATABASE_URL`, not `YAAF_API` or a login. Run it where the server
> runs, or point `YAAF_DATABASE_URL` at the same Postgres.

---

## A full provisioning script

```bash
export YAAF_API=http://localhost:8080
yaaf login                                                    # an instance admin

yaaf seed-roles roles/                                        # once per deployment
yaaf create-project my-spec.json                              # you chair what you found
yaaf create-llm-credential "Production"    # once; value prompted, id printed
yaaf configure my-slug --billing-secret-id secr_…
yaaf start my-slug

yaaf say my-slug "Morning. What's the plan for the week?"
```

## Who pays

A project never holds a billing key of its own. An administrator creates each
**LLM credential** once — `yaaf create-llm-credential NAME` for an API key,
`--mode subscription` for a Claude subscription token — and projects are
assigned one:

```bash
yaaf llm-credentials                                  # names, types, assignments; never values
yaaf hide-llm-credentials secr_… secr_…               # retire the ones nothing is using
yaaf configure my-slug --billing-secret-id secr_…     # assign; the billing mode follows the credential
yaaf llm-credential-usage secr_…                      # a subscription's plan meter, as /usage shows it
yaaf edit-llm-credential secr_… "Production" --rotate # rename, or replace the value for every project on it
```

A spec file may carry `billing_secret_id` and is created already assigned.
`yaaf set-secret --kind billing` is refused: that door closed when credentials
moved above projects. Promoting per-project keys left most installs holding one
credential per historical key, so **hiding** is the tidy-up: a hidden credential
is off every menu and cannot be assigned, while the finished projects that spent
through it keep naming what paid for them. It is reversible, and it is refused
while a live project can still spend. A credential a project is assigned to cannot be deleted
until the project is reassigned or detached (`--billing-secret-id ""`). Clones
keep the assignment; a backup names the credential and never carries its
value, and no endpoint or command ever shows an LLM credential's value back.

## Before you spend anything

```bash
yaaf rehearse my-spec.json                  # does this configuration work?
yaaf rehearse my-spec.json --usd-per-run 2  # and what does it cost at your price?
```

Runs the real platform on the mock runtime against your spec: the walk from
roles resolving to a requirement settling true, every point the project would
stop and wait for you, the plan in runs against your caps, and which of the
four limits paces it. Opens no sockets, runs no commands out of the file, and
touches no database but the throwaway one it creates.

Exit code 0 when the project gets off the ground, 1 when it does not, 2 when
the spec itself could not be read — so it drops straight into CI beside your
other checks.

## Coming back after a few days

```bash
yaaf briefing                 # every live project, each on its own read mark
yaaf briefing my-slug --seen  # read one, and start the next window here
yaaf agenda my-slug           # and what the next fortnight holds
```

Set `brief_daily_at` (Settings → **Rules in force** → Rhythm & reporting, or
`yaaf amend`) and the same briefing arrives by email every morning without
anyone typing anything.
