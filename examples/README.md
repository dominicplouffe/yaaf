# Examples — ten projects, in the order worth doing them

These are not ten variations on one idea. They are ten different jobs, in ten
different domains, arranged so that each one shows you a part of yaaf the last
one didn't. Run them in order and you will have used essentially the whole
platform on things you can judge for yourself.

**Everything here needs one thing: an Anthropic API key.** No data
subscriptions, no vendor accounts, no signups. Where a project needs outside
data it uses something free and public (SEC EDGAR, a project's own changelog)
or ships the file in this repo. If an example ever asks you to buy something
before you can try it, that is a bug — tell us.

## Start here

**[01 — Your first hour](01-your-first-hour.md).** One writer, four
requirements, a few dollars, done before lunch. It exists so you watch the
machinery once on something you can check in five minutes.

## The ten

| # | Example | What it is | Shape | What it teaches you |
|---|---|---|---|---|
| 01 | [Your first hour](01-your-first-hour.md) | A briefing note on any subject | Deliver | The whole loop, as small as it goes: propose → **you accept** → work → done |
| 02 | [Settle an argument](02-settle-an-argument.md) | A sourced answer to a question you're stuck on | Deliver | Sourced evidence, and what stops an agent blurring answer and *sourced* answer |
| 03 | [A tool that runs](03-a-tool-that-runs.md) | A small CLI, with tests | Deliver | **`command` tests the platform runs itself** — where "done" stops being an opinion |
| 04 | [Docs a stranger can follow](04-docs-a-stranger-can-follow.md) | A quickstart, proven by executing it | Deliver | Holding *prose* to a command test; two teams with opposing incentives |
| 05 | [A tutor that adapts](05-a-tutor-that-adapts.md) | One lesson a week that reacts to your answers | **Operate** | Cycles, scope carried forward, last cycles' narratives handed over verbatim |
| 06 | [Make sense of a spreadsheet](06-make-sense-of-a-spreadsheet.md) | An analysis of a messy CSV *(data included)* | Deliver | Bulk data in files not context; saying what the data **cannot** answer |
| 07 | [Tell me what changed](07-tell-me-what-changed.md) | A weekly brief on something you depend on | **Operate** | Per-agent schedules, external evidence, and the **drought** rule |
| 08 | [A game you can play](08-a-game-you-can-play.md) | A one-file browser game that's actually fun | Deliver | A three-team org that argues; "done" when it's a *feel* judgement |
| 09 | [One idea, thirty days](09-one-idea-thirty-days.md) | Find a product idea and prove someone wants it | **Explore**, solo | Bets with kill dates, controlled domains, verified external evidence |
| 10 | [Read the filings](10-read-the-filings.md) | An initiation note from a company's own filings | Deliver | A review gate before anything ships; the strictest sourcing in the set |

Between them: research, software, technical writing, education, data analysis,
monitoring, game development, product discovery, and financial research. If
none of those is your job, the shapes still transfer — read 03 and 10 and you
will see how.

## Importing one

**Prerequisite, once per deployment:** the role library must be loaded, since
these designs hire from it. `./deploy_single_server.sh` does it for you;
otherwise run `yaaf seed-roles roles/`.

**In the dashboard**, which is the intended path:

1. **+ New project → Start from a setup file**, and pick the `.json`.
2. The form fills in: shape, goal, teams, constitution, and the org.
3. **Paste your Anthropic key**, **edit the goal** where it shouts in capitals,
   and check your email and timezone.
4. Create.

Two things the form owns rather than the file, so glance at them before you
submit:

- **The knobs on the form win.** Queue ceiling, drought hours, budget and
  approval threshold prefill from the file — but the reporting cadence, the
  reporting window, the timezone and "require verification" come from the
  form's own fields.
- **The autonomy preset wins.** Supervised or Autonomous is your choice on the
  form; the file cannot make it for you. Every example here assumes
  **Supervised**, because watching yourself accept a plan is the point of the
  first few. See [docs/AUTONOMY.md](../docs/AUTONOMY.md).

**From the CLI**, which honors the whole constitution as written:

```bash
mkdir -p seed/projects seed/roles
cp roles/*.json seed/roles/
cp examples/01-your-first-hour.json seed/projects/

export YAAF_API=http://localhost:8080 YAAF_ADMIN_TOKEN=…
yaaf create-user you@example.com "Your Name"
yaaf seed seed --chairman you@example.com     # created in draft, never started

# Prompts for the key, never echoes it, and prints the secret's id.
yaaf set-secret anthropic-api-key --project first-hour --kind billing
yaaf configure first-hour --billing-secret-id secr_…   # the id it just printed
yaaf start first-hour
```

Then watch it: `yaaf dashboard <slug>`, `yaaf board <slug>`, `yaaf runs <slug>`,
`yaaf spend-report <slug> --days 7`. Talk to your CEO with
`yaaf say <slug> "…"`, and spot-check with `yaaf audit <slug> --n 5`.

## Three things every example has in common

**The goal is the product.** The CEO plans from it and nothing else. Each of
these ships a goal you edit rather than one you copy, and the part you must
change is IN CAPITALS so you cannot miss it. Copy the *shape*: what exists at
the end, who it is for, where the inputs come from, and — the part people skip
— what would count as working, stated before anyone starts. "Analyze the data"
produces a company that putters at $8/hour.

**Done is defined before work starts.** Every requirement and every task carries
a test, and there are exactly two kinds:

```json
{"type": "command",    "command": "cd /workspace && python3 -m unittest discover -s tests -q"}
{"type": "observable", "target":  "deliverables/memo.md carries a source URL for every factual claim"}
```

A `command` is run by the **platform**, in a sandbox, not by the agent that
wants it to pass. An `observable` names something a stranger could check, and
lands in your spot-audit sample. Prose ("verify it works well") is refused at
the API, because it is a test that cannot fail.

**An acceptance test may only rely on what survives.** Every run gets a fresh
container; the **workspace** persists, the environment does not. So a check can
use what the sandbox image ships — `python3` and the standard library,
`pandas`, `duckdb`, `node`, `jq`, `git`, `curl` — or something committed to the
workspace. It cannot use a package an earlier run pip-installed. This trips
everyone once; example 03 is built around it.

## The roles they hire

From [`roles/`](../roles/). These examples use nine of them:

| Slug | Role | Used by |
|---|---|---|
| `ceo` | CEO / Project Manager | every example except 10 |
| `writer` | Writer / Content | 01, 02, 04, 06, 07 |
| `researcher` | Researcher / Analyst | 02, 06, 07 |
| `engineer` | Software Engineer | 03, 04 |
| `tutor` | Tutor / Curriculum Builder | 05 |
| `game-developer`, `game-ux-designer`, `game-analyst` | A game studio | 08 |
| `gtm` | Go-to-Market | 09 |
| `cio`, `equity-analyst`, `compliance-officer` | A markets desk | 10 |

The library holds more than these — a full markets family (eleven roles), a
fourth game role, and the rest of a general-purpose company (`blog-writer`,
`marketer`, `sales`, `support`). Browse them at **Roles** in the dashboard.

Roles are **versioned**. Edit the JSON, re-seed, and existing teams keep running
their pinned version until you adopt the new one — so a prompt edit cannot
silently change a project that is mid-flight.

## Writing your own

The fastest route to a good project of your own is to take the example closest
in *shape* — not in subject — and change the goal:

- **One thing, then stop** → Deliver (01, 02, 03, 04, 06, 08, 10)
- **The same thing on a rhythm, improving** → Operate (05, 07)
- **A question you can't write requirements for yet** → Explore (09)

Then read [docs/CREATING_A_PROJECT.md](../docs/CREATING_A_PROJECT.md), which
walks the form in plain English, and steal acceptance tests from whichever
example is closest.
