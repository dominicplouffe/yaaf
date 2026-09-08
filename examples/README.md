# Examples — two you could do without yaaf, and ten you couldn't

Every example here has to answer one question, and answers it at the top of its
page: **why not just a Claude Skill?**

For the first two the honest answer is *"you could — that's the point."* They
exist to teach you the loop on something you can check yourself in five minutes.
For the other ten the answer names the specific thing that breaks: nobody is
awake at 02:00; a chat session cannot be woken by your app; a resumed session
does not know it already opened that pull request; one agent cannot be its own
skeptic; a link nobody fetched is not evidence.

**Everything needs one thing: an Anthropic API key.** No data subscriptions, no
vendor accounts. The single exception is example 04, which needs a **free**
GitHub account, and needs one because doing something in the world and proving
it happened is the entire lesson.

**Except to look first, which costs nothing.** Every file here runs as a fire
drill on a bare checkout — no deployment, no key, no dollars:

```bash
yaaf rehearse examples/01-your-first-hour.json
```

It runs the spec as a real project with scripted agents in place of models, and
walks you through what the platform actually does with it: the plan proposed,
the gate closing, the board opening, work claimed and closed, and every point
the project stops and waits for **you**. Reading that once before you spend
anything is the cheapest hour in this directory.

---

## Part 1 · Fundamentals

**A Claude Skill can do both of these.** Do them anyway. You are not here for
the deliverable — you are here to write a goal, watch a plan get proposed,
accept it yourself, and see the platform decide whether the work was done.
Everything in Part 2 assumes you have.

| # | Example | What it is | Shape | What you learn |
|---|---|---|---|---|
| 01 | [Your first hour](01-your-first-hour.md) | A briefing note on any subject | Deliver | The whole loop, as small as it goes: propose → **you accept** → work → verified |
| 02 | [Settle an argument](02-settle-an-argument.md) | A sourced answer to a question you're stuck on | Deliver | Sourced evidence — and the first real difference: a citation the *platform* fetches, hashes and snapshots |

## Part 2 · Why yaaf exists

Each of these breaks without the platform, for the reason in the last column.

| # | Example | What it is | Shape | What a Claude session can't do here |
|---|---|---|---|---|
| 03 | [A desk the world wakes up](03-a-desk-the-world-wakes.md) | Signed events arrive; an agent wakes and triages | **Operate** | Be listening. A signed endpoint wakes a *named* agent at 03:00, under every rate brake |
| 04 | [An overnight crew](04-an-overnight-crew.md) | Nightly maintenance PRs on a repo you own | **Operate** | Be awake at 02:00 — and know, after a crash, that it already opened that PR |
| 05 | [A tutor that adapts](05-a-tutor-that-adapts.md) | One lesson a week that reacts to your answers | **Operate** | Wake up next Sunday, holding what you got wrong in week three |
| 06 | [A red team that can block](06-a-red-team.md) | An adversary whose sign-off gates the build | Deliver | Be its own skeptic. A *different* agent holds a requirement the builders cannot close |
| 07 | [Tell me what changed](07-tell-me-what-changed.md) | A weekly brief on something you depend on | **Operate** | Run on Monday without you, and not re-report week one's news on week nine |
| 08 | [A game you can play](08-a-game-you-can-play.md) | A one-file browser game that's actually fun | Deliver | Put three roles with conflicting charters in a room and hand you the argument |
| 09 | [One idea, thirty days](09-one-idea-thirty-days.md) | Find a product idea and prove someone wants it | **Explore**, solo | Hold a bet to a kill date you can't quietly move, on evidence it didn't write |
| 10 | [Read the filings](10-read-the-filings.md) | A company note you could defend to a skeptic | Deliver | Produce the *record* — verified claims, logged decisions, sampled work |
| 11 | [Numbers you can defend](11-numbers-you-can-defend.md) | An answer from data, and the check that re-derives it | Deliver | Be checked. Every headline figure recomputes from a pinned copy, judged by an agent that did not write it |
| 12 | [A content engine](12-a-content-engine.md) | Posts published to your blog, graded by who replied | **Operate** | Be read by someone who didn't write it — and grade itself months later on a reply it cannot author |

Domains, so you can find yourself in one: writing, research, ops and
integration, software maintenance, education, QA, monitoring, games, product
discovery, regulated research, data analysis, content and publishing.

## The short version of the argument

**A skill is a capability. yaaf is what wields it.** They are not alternatives —
yaaf *runs* your skills: a role's `tool_permissions.skills` is an allowlist
handed straight to the Agent SDK, so a role can be granted exactly the skills it
should have and no others.

What the platform adds is not cleverness. It is what happens across a hundred
sittings you are not present for: something deciding what to do next, a
definition of done that isn't self-graded, a budget that stops by itself, memory
with a curator, and a record you can hand to someone who asks how you know.

See the README's [Isn't this just a Claude Skill?](../README.md#isnt-this-just-a-claude-skill)
for the long version.

---

## Importing one

**Prerequisite:** the role library must hold the roles these designs hire —
they name roles by slug rather than carrying their prompts.
`./deploy_single_server.sh` and `./update_single_server.sh` both load whatever
the library is missing, so a `git pull` that brings new roles is enough;
by hand it is `yaaf seed-roles roles/ --only-missing`. If a team arrives on the
New Project page reading *"not in this install's role library"*, that is the
step that has not run since the role was added.

**In the dashboard**, which is the intended path:

1. **+ New project → Start from a setup file**, and pick the `.json`.
2. The form fills in: shape, goal, teams, constitution, and the org.
3. **Pick the LLM credential that pays** (an administrator creates one under
   Settings → LLM credentials; the file cannot carry a key), **edit the goal**
   where it shouts in capitals, and check your email and timezone.
4. Create.

Two things the form owns rather than the file, so glance at them before you
submit:

- **The knobs on the form win.** Queue ceiling, drought hours, budget and
  approval threshold prefill from the file — but the reporting cadence, the
  reporting window, the timezone and "require verification" come from the
  form's own fields.
- **The autonomy preset wins.** Supervised or Autonomous is your choice on the
  form; the file cannot make it for you. Every example here assumes
  **Supervised**, because watching yourself accept a plan is the point of Part
  1. See [docs/AUTONOMY.md](../docs/AUTONOMY.md).

**From the CLI**, which honors the whole constitution as written:

```bash
mkdir -p seed/projects seed/roles
cp roles/*.json seed/roles/
cp examples/01-your-first-hour.json seed/projects/

export YAAF_API=http://localhost:8080 YAAF_ADMIN_TOKEN=…
yaaf create-user you@example.com "Your Name"
yaaf seed seed --chairman you@example.com     # created in draft, never started

# Prompts for the key, never echoes it, and prints the credential's id.
# One credential can pay for many projects; create it once, assign it per project.
yaaf create-llm-credential "Production"
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
produces a project that putters at $8/hour.

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
everyone once; example 11 is the one where it bites hardest, because there the
checks are the deliverable.

## The roles they hire

From [`roles/`](../roles/). These examples hire fifteen of them:

| Slug | Role | Used by |
|---|---|---|
| `ceo` | CEO / Project Manager | every example except 10 |
| `writer` | Writer / Content | 01, 02, 07 |
| `researcher` | Researcher / Analyst | 02, 07 |
| `support` | Customer Support | 03 |
| `engineer` | Software Engineer | 03, 04, 06, 11 |
| `adversary` | Red Team / Adversarial Reviewer | 06 |
| `data-analyst` | Data Analyst | 11 |
| `tutor` | Tutor / Curriculum Builder | 05 |
| `game-developer`, `game-ux-designer`, `game-analyst` | A game studio | 08 |
| `gtm` | Go-to-Market | 09 |
| `cio`, `equity-analyst`, `compliance-officer` | A markets desk | 10 |

The library holds more than these — a full markets family (eleven roles), a
fourth game role, and the rest of a general-purpose project (`blog-writer`,
`marketer`, `sales`). Browse them at **Roles** in the dashboard.

Roles are **versioned**. Edit the JSON, re-seed, and existing teams keep running
their pinned version until you adopt the new one — so a prompt edit cannot
silently change a project that is mid-flight.

## Writing your own

The fastest route to a good project of your own is to take the example closest
in *shape* — not in subject — and change the goal:

- **One thing, then stop** → Deliver (01, 02, 06, 08, 10, 11)
- **The same thing on a rhythm, improving** → Operate (03, 04, 05, 07)
- **A question you can't write requirements for yet** → Explore (09)

Then read [docs/CREATING_A_PROJECT.md](../docs/CREATING_A_PROJECT.md), which
walks the form in plain English, and steal acceptance tests from whichever
example is closest.

And before you build it here, ask the question this folder is organised around:
**if you could finish it in one sitting, you probably should.** yaaf earns its
keep on the work you cannot sit through.
