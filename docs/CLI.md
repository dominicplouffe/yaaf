# The `yaaf` CLI

Everything the dashboard does, scriptable. Useful for provisioning, CI, and
poking at a running deployment.

```bash
export YAAF_API=http://localhost:8080
export YAAF_ADMIN_TOKEN=…
```

Both have fallbacks: `YAAF_API` defaults to `http://localhost:8080`, and the
token is read from `deploy/.env` (or `.env`) if you run from the repo root, so
in practice you can usually just type `yaaf`.

Every command that takes a project accepts **either a slug or an id**.

---

## Setup

| Command | What it does |
|---|---|
| `yaaf seed-roles [DIR] [--only-missing]` | Load every `*.json` in the directory (default `roles/`) into the role library. Re-running creates new role *versions* — which is how an edited prompt reaches a running install. `--only-missing` skips the slugs the library already has, which is what you want after an update adds roles to the checkout; it still refiles those roles' **category and domains**, because those live on the role rather than the version and so can move without putting a live team behind its prompt. |
| `yaaf seed-crews [DIR] [--only-missing]` | Load every `*.json` in the directory (default `crews/`) into the crew library — the pre-staffed teams the hiring desk offers. Crews are not versioned: one is read at hiring time and copied into the project's own teams, so re-seeding an edited crew can never reach a project that already started. Run it after `seed-roles`. |
| `yaaf create-user EMAIL NAME` | Create a user. Idempotent by email. |
| `yaaf create-project SPEC_FILE` | Create a project from a JSON spec (shape below). |
| `yaaf set-secret NAME [--project P] [--scopes a,b] [--kind service\|billing]` | Store an encrypted secret. **The value is prompted** — never echoed, logged, or left in shell history. Omit `--project` for a platform-level secret. |
| `yaaf settings` | Show the app-wide settings — whether a model credential is installed and how the Slack workspace is configured. Values are never printed. |
| `yaaf set-platform-key [--mode api_key\|subscription]` | Install or rotate the **Founder's** model credential (value prompted). It is never injected into a sandbox and never reaches a project's agents — there is no fallback. |
| `yaaf set-slack [--bot-token] [--signing-secret] [--app-token] [--chairman U…] [--prefix P] [--visibility private\|public] [--auto-create/--no-auto-create]` | Configure the app-wide Slack workspace every project inherits. Token flags prompt for the value. |
| `yaaf slack-channels PROJECT` | Create the Slack rooms this project is missing and print their names. Idempotent — a mapped room is left alone, a name Slack already holds is adopted. |
| `yaaf configure PROJECT [--runtime …] [--billing-secret-id …] [--billing-mode …]` | Update outward wiring only. **Never the constitution** — cadence, concurrency and every other limit is an amendment. |
| `yaaf amend PROJECT --reason "…" --set rule=value` | Change a constitution rule. Repeatable `--set`; values are parsed as JSON where possible, so `--set runs_per_project_per_day=80` gives a number and `--set ceo_daily_at='"09:00"'` gives a string. An invalid overlay is refused whole. |
| `yaaf save-seed [DIR]` | Write every role and project *design* to a folder as JSON. Plumbing and secrets stay behind. |
| `yaaf seed [DIR] --chairman EMAIL [--roles-only]` | Restore such a folder onto an install. Projects come back **in draft** — restoring a backup never starts spending. |

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
     "model_overrides": {"Ada": "claude-opus-5"}}
  ],
  "solo": false
}
```

- `mode` is `explore` (default), `deliver`, or `operate`, and is **frozen at
  creation** — the rules differ by shape. Outside Explore the project starts
  owing a plan and a task serving no requirement is refused.
- `deadline` (ISO datetime) is Explore's shape only: open scope, bounded time.
  Deliver promises through a commitment and Operate runs on cycles, so a bare
  date is refused there.
- `constitution` holds **overrides** onto the defaults; omit it for stock rules.
  A full rulebook (what an export carries) validates fine too.
- `integrations` is wiring, never limits: `runtime` (`agent_sdk` | `mock`),
  Slack mapping, data sources. Anything that caps or paces an agent lives in
  the constitution.
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
| `yaaf projects` | Every project with status and headline numbers. |
| `yaaf rehearse SPEC_FILE [--days N] [--usd-per-run N] [--json]` | **A fire drill: run the spec as a project for nothing, before you fund it.** Needs no deployment and no key — `yaaf rehearse examples/01-your-first-hour.json` works on a fresh checkout. See [FEATURES §13](FEATURES.md). |
| `yaaf briefing [PROJECT] [--hours N] [--seen]` | **What happened since you last read one.** Omit the project for every live project. Composed from the record at zero tokens, so it works on a paused project and a broke one. Reading never moves your mark; `--seen` does, and only to the edge of the window it just printed. |
| `yaaf agenda PROJECT [--days 14]` | **What it is scheduled to do next**: the wall-clock wakes, the standing meeting, the cycle roll, the kill dates, the clocks that decide themselves, and the promised date with the platform's projection beside it. |
| `yaaf dashboard PROJECT` | The full project dashboard payload. |
| `yaaf board PROJECT` | The task board. |
| `yaaf org PROJECT` | Teams, leads, employees, who's awake. |
| `yaaf channels PROJECT` | Channels and their ids. |
| `yaaf tail CHANNEL_ID [--limit 30]` | Recent messages, formatted, frozen threads marked. |
| `yaaf runs PROJECT [--limit 20]` | Recent runs: agent, wake reason, cost, status. |
| `yaaf ledger PROJECT` | Spend entries. |
| `yaaf evidence PROJECT` | External evidence and verification status. |
| `yaaf hypotheses PROJECT` | Live bets and their kill dates. |
| `yaaf approvals PROJECT` | Pending approvals. |
| `yaaf audit PROJECT [--n 5]` | A random spot-audit sample of recent evidence and completed tasks. |

## Chairman actions

| Command | What it does |
|---|---|
| `yaaf say PROJECT "message"` | Speak in the boardroom — your only channel. |
| `yaaf decide APPROVAL_ID --approve\|--deny [--note …]` | Decide an approval. |
| `yaaf extend HYPOTHESIS_ID NEW_KILL_DATE REASON` | Your one-time extension of an expired bet (ISO datetime). |
| `yaaf unfreeze MESSAGE_ID` | Release a thread the pair circuit breaker froze. |
| `yaaf audit-evidence EVIDENCE_ID --ok\|--bad [--note …]` | Record your verdict on a piece of evidence. |

Constitution changes go through `yaaf amend` (above) or Settings → **Rules in
force**. Either way they carry a written reason, an audit entry, and disclosure
to the CEO — see [GOVERNANCE.md](GOVERNANCE.md#changing-a-rule).

Scope decisions — accepting a proposed requirement, cutting one, signing or
moving a delivery date, pulling a review lever — are dashboard actions
(**Plan** and **Review** tabs) or direct API calls; there is no CLI shorthand,
because each needs a reason you would want read back at the next review.

## MCP client tokens

The chairman's MCP server ([MCP_SERVER.md](MCP_SERVER.md)) has its own
principal — never the admin token, which is unscoped, unattributable and
cannot be revoked one client at a time.

| Command | What it does |
|---|---|
| `yaaf mcp-token mint NAME --chairman EMAIL [--scopes read] [--project SLUG]…` | Mint a client token. Printed once; only its hash is stored. |
| `yaaf mcp-token list` | Every client, what it holds, when it was last used. |
| `yaaf mcp-token revoke CLIENT_ID` | Revoke it. The row survives — the audit trail names it. |

```bash
yaaf mcp-token mint dom-laptop --chairman you@example.com --scopes read --project ppc-audit
# → {"token": "ymt_…", "scopes": ["read"], "projects": ["proj_…"]}
```

Scopes are `read`, `steer`, `create`, `configure`, and `read` is implied by all
of them. Start with `read` alone: it is most of the value and none of the risk
— a client holding it sees every project and signs for nothing. Omitting
`--project` means every project that chairman chairs, which is a narrowing
already; naming one narrows it further.

## Spend analysis

```bash
yaaf spend-report [PROJECT] [--days N] [--all-projects]
```

Breaks LLM spend down by agent, role, model, and wake reason. Defaults to the
current calendar month.

> This one reads the **database directly**, not the API: it needs
> `YAAF_DATABASE_URL`, not `YAAF_API`/`YAAF_ADMIN_TOKEN`. Run it where the
> server runs, or point `YAAF_DATABASE_URL` at the same Postgres.

---

## A full provisioning script

```bash
export YAAF_API=http://localhost:8080 YAAF_ADMIN_TOKEN=…

yaaf seed-roles roles/                                        # once per deployment
yaaf create-user you@example.com "Your Name"
yaaf create-project my-spec.json
yaaf set-secret anthropic --project my-slug --kind billing    # value prompted
yaaf configure my-slug --billing-secret-id secr_…
yaaf start my-slug

yaaf say my-slug "Morning. What's the plan for the week?"
```

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
