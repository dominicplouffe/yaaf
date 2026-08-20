# The dashboard — every screen, and what you do there

The chairman's cockpit is a React SPA served by the API at **`/app`**. Log in
with your admin token (`YAAF_ADMIN_TOKEN`).

Everything below is also reachable from the [CLI](CLI.md), but the dashboard is
the intended way to run a company.

---

## Sidebar

| | What it's for |
|---|---|
| **Desk** | The landing page. Everything needing you, across every company. |
| **Projects** | Your portfolio — one comparable row per company. |
| **Roles** | The role library, and each role's blast radius. |
| **New project** | The creation wizard. |

---

## Desk — "what needs me?"

The daily triage screen, zoned in the order you'd actually work it:

1. **Waiting on you** — decisions only a human can make: pending approvals,
   paused projects (with why), expired bets awaiting extend-or-kill, frozen
   threads.
2. **Worth watching** — nothing is blocked, but something is drifting: budget
   pace, an evidence drought, a bet nearing its kill date.
3. **While you were away** — what moved since you last looked.
4. **One row per company** — status, budget, open work, last activity, all
   comparable at a glance.

**The hero state is an empty desk.** If nothing needs you, the screen says so.

## Projects

The portfolio view: every company with its status, spend against cap, open
work, and last run. Start, pause, and stop live here and in each project's
header.

## Roles

The platform-wide role library. Each role shows its current version, its
system prompt, model and effort, tool permissions, secret scopes — and its
**blast radius**: which projects run it, at which version.

Editing a role creates a **new version**. Live teams keep the version they
pinned; they move only when you **repin** them, which the health checklist will
remind you about. An edit never silently changes a running company.

## New project

The wizard: name and slug, the goal (the CEO plans from this and nothing else),
your email, billing mode, controlled domains, which teams to hire, and any
constitution overrides. [CREATING_A_PROJECT.md](CREATING_A_PROJECT.md) walks
through it in plain English; [`examples/`](../examples/) has ten worked setups
you can copy.

You can also start from an exported **setup file** (Settings → Duplicate &
export on any project).

---

# Inside a project

Four daily tabs, then two you open when something looks wrong.

## Overview — the state of the company

- **Health checklist** — anything misconfigured, in plain language, with a fix
  link: dead billing, a credential of the wrong shape, an untested data source,
  a team on an outdated role version. **A healthy project shows nothing here.**
- **Needs you** — the same decisions the Desk surfaces, scoped to this project.
- **Is it working?** — verified external evidence over time, and the
  CEO-judgment metric: how much of what the CEO called progress survived
  verification.
- **Your deliverable** — the most recent shipped artifact.
- **Watch** — drifting-but-not-blocked signals.
- **The plan** — live bets and the open board.
- **The money** — spend against the cap in the project's native unit (USD or
  tokens), with pace.
- **The company** — the org roster: teams, leads, employees, and who's awake.

## Boardroom — talk to your CEO

A chat, not a ticket queue. Every running project keeps a resident CEO chat
session, so replies land in seconds, with a typing indicator. When background
runs are in flight the banner says so ("the CEO is working — 2 runs in
flight"), so silence is never ambiguous.

This is your **only** channel. You don't post to team rooms — that discipline
is the product.

A rule you state here can become a rule the platform holds: the CEO files it as
a **directive** citing your actual message, and it's enforced from the next
call. See [GOVERNANCE.md](GOVERNANCE.md#the-rules).

The same conversation is reachable from Slack — see
[INTEGRATIONS.md](INTEGRATIONS.md#slack).

## Work — the plan and whether it's been proven

- **Bets and their tasks.** Hypotheses set the direction with explicit kill
  dates; tasks do the work under them. Each bet shows the proof it has earned
  so far, and you can extend one (once, with a reason) or kill it.
- **Spot-check the agents' work.** A random sample of recent completed tasks
  and evidence, dealt to you to check yourself. Mark each good or bad; your
  verdict is recorded. This is optional — it's a spot-check, not a summons.
- **All proof.** Every piece of external evidence with its verification status.
  Only verified or platform-attested evidence counts toward the drought and the
  external-evidence floor. Snapshots are viewable.

## Deliverables — the output

- **Published** — finished artifacts in the artifact store, with preview and
  download.
- **Working files** — the agents' workspace, read-only, so you can see work in
  progress rather than only what shipped.

## Records — "what happened?"

Four ways of asking the same question, kept out of the daily path:

| View | Shows |
|---|---|
| **Runs** | Every model call: agent, team, why it woke, what it cost, and the full trace. |
| **Messages** | Every channel — including the ones you can read but not speak in. |
| **Memory** | What the company chose to remember, per scope, with its curation history. |
| **Decisions** | Everything you decided, and everything that decided itself: expired approvals, auto-pauses, killed bets, frozen threads. |

## Settings

Grouped by necessity, not by subsystem. The required path is Brief → Money →
Data; everything after is occasional.

**The work**

- **Brief** — the goal the CEO plans from. (Controlled domains — the ones that
  can't count as external evidence — are set when the project is created.)
- **Money** — billing mode and credential, a live check that the credential
  actually works, and the budget in force shown read-only. The numbers
  themselves are constitution rules, so the card links to Rules in force rather
  than offering a second editor for them.
- **Data & credentials** — MCP data sources (one form: name, URL, roles, Save,
  plus a live check) and scoped variables/credentials for your teams.

**Connections**

- **Integrations** — the Slack bridge and the GitHub workspace.

**Governance**

- **Rules in force** — all 39 constitution rules, each with its own help text
  and its stock default, behind six tabs: Money, Pace & brakes, How work is
  run, Autonomy, Memory, Rhythm & reporting. Edits span tabs; a tray at the
  foot of the pane lists every pending one, takes the required written reason,
  and shows before/after before it lands. Changes take effect from the next
  agent call. The change history and a raw-JSON escape hatch fold away at the
  bottom. Deep link with `?s=rules&t=<tab>`.

**Housekeeping**

- **Duplicate & export** — **clone** into a running twin on this install
  (credentials and all, optionally aimed at a new goal), or **export** a
  portable setup file with secrets and plumbing stripped.
- **Advanced** — runtime (`agent_sdk` or `mock`) and the webhook signing
  secrets. The CEO's cadence (`ceo_heartbeat_hours`, `ceo_daily_at`,
  `timezone`) and `max_concurrent_runs` are constitution rules, not config:
  they live under Rules in force, and `PATCH /admin/projects/{id}` refuses them
  with a 422 pointing at the amendments endpoint.

---

## Chairman powers, in one list

Everything you can do, and nothing you can't:

- Set the goal and amend the constitution (with a reason).
- Speak in the boardroom.
- Approve or deny approvals.
- Extend an expired bet — once — or kill one.
- Unfreeze a thread the circuit breaker froze.
- Spot-audit completed work and evidence.
- Start, pause, stop a project.
- Configure billing, credentials, data sources, and integrations.
- Clone, export, and repin teams to new role versions.

You never edit tasks, post to team rooms, or touch memory.
