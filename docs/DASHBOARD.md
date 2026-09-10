# The dashboard — every screen, and what you do there

The chairman's cockpit is a React SPA served by the API at **`/app`**. Log in
with your admin token (`YAAF_ADMIN_TOKEN`).

Everything below is also reachable from the [CLI](CLI.md), but the dashboard is
the intended way to run a project.

---

## Sidebar

| | What it's for |
|---|---|
| **Desk** | The landing page. Everything needing you, across every project. |
| **Projects** | Your portfolio — one comparable row per project. |
| **Roles** | The role library, and each role's blast radius. |
| **Settings** | App-wide: the LLM credentials projects pay with, the Founder's own credential, and the Slack connection every project inherits. |
| **New project** | The creation wizard. |

---

## Settings — what is true above every project

Three sections, and none of them belongs to a project.

The page is a rail, one section at a time, and each rail item carries its
section's state — so "is anything wrong here?" is answered without opening
anything. The section is in the URL, so a link to the credentials list is a
link to the credentials list.

- **LLM credentials** — what projects pay Claude with. An administrator adds
  each one once, by name: an **Anthropic API key** (the project's budget is
  then in dollars) or a **Claude subscription token** (in tokens). Every row
  is filed by what is actually using it: **in use** (a live project pays with
  it), **idle** (only projects that have ended ever did), **unused** (no
  project ever was), and **hidden**. Filter by any of those, search by
  credential or project name, and — for a subscription in use — read the
  **plan meter**: what Anthropic says is left on the session and weekly
  windows, the same numbers as `/usage` in Claude Code, shown as a fact and
  never enforced. It is harvested from the credential's own agent runs — the
  SDK reports it on every run — so nothing is polled and nothing can be rate
  limited; a credential nothing has run on yet says it has no reading yet.
- **Hiding a credential** is the answer to a list nobody chose. Promoting
  per-project keys above projects made one managed credential per historical
  key, most of them belonging to projects that had already ended, and none of
  them deletable — a project still names what paid for it. Hiding takes a
  credential off this page, the project picker, the Founder's list and every
  MCP spec, and makes it unassignable; the row, the receipts and the finished
  projects' record of what paid for them all stay. It is one click to undo,
  works on a whole selection at once ("hide all N that nothing is using"), and
  is **refused while a live project can still spend on it** — so hidden can
  never mean a running project's money is unexplained. Rename or rotate a
  credential here and every assigned project's next run uses the new value.
  Deleting is only offered for a credential no project has ever named. No
  value is ever shown back — the reveal that works for a project's own secrets
  refuses these. Projects never store a billing key of their own: they pick
  one of these, under their own Settings → Money, on the New Project form, or
  by telling the Founder.
- **Founder model credential** — the key the **Founder** runs on. Install,
  rotate, run a live test against it, remove it, and pick the model it uses.
  It pays for product-wide conversations and **never reaches a project's
  agents**: no inheritance, no fallback. It is deliberately not one of the
  LLM credentials above.
- **Slack connection** — the bot/signing/app tokens for your Slack workspace,
  your member ID, and the channel naming convention, stored once. Every project
  inherits them, so
  connecting Slack for a new project is nothing but its channels — and yaaf
  makes the boardroom for you (`{prefix}-boardroom`; private by default, with
  you invited) and names it back on the "project created" screen. Leadership and
  the team rooms are one button away in the project's own Settings, so a new
  project does not fill your workspace with rooms nobody has spoken in. A
  project can override any field, or switch Slack off for itself, from its own
  Settings → Integrations.

No value is ever shown back — the screen says only whether one is installed,
because a settings page that can display a bot token is one that can leak it
into a screenshot.

---

## Desk — "what needs me?"

**Every row here is something only you can do.** That is the page's one rule.
If the CEO can resolve it, it is not a row — it is a log entry, and it lives on
the project it belongs to.

So the desk holds exactly six things: a bet past its kill date, a thread the
pair breaker froze, an approval running down to auto-deny, scope waiting to be
accepted, a delivery date waiting to be signed, and an observable close only you
can judge. Nothing else. A drought, a kill date still in the future, a budget on
pace, unaudited work — those resolve without you, so drawing them here as cards
only ever taught people to stop trusting the cards.

Rows are ranked by **time to consequence** — how long until the thing decides
itself. The soonest is the top row whoever owns it; the page never sorts by
which project was created first.

Every row says the same three things in the same order: what happened, **what
happens if you do nothing**, and the action.

The sidebar badge counts exactly these rows, and always has. The page now shows
exactly what the badge counts — before, it drew seven other kinds of row the
badge deliberately ignored, and a chairman reading "nothing needs you" over a
screenful of cards reasonably concluded one of the two was lying.

**What happened to everything else.** It is not gone and it is not silent. Each
project's **Overview → Watch** holds its own droughts, kill dates, budget pace,
memory pressure, unraised flags, and days that spent money and verified
nothing. The desk closes with a single line naming how many such things
are running and in which projects, so you can see they exist without being asked
to do anything about them.

**Only live projects appear.** Stopped and completed ones are the Projects
page's business; they get one line at the foot of the roster.

**The autonomy dial decides what is eligible for a row.** Where the
constitution says `ceo`, the CEO accepts its own scope, signs the platform's
projected date and judges its own observables, so those never reach this page.
Where the chairman holds a switch, the thing waiting on him is a row here:
proposed scope to accept, a date to sign, an observable close to judge. Each is
also chased by email until it is acted on — the page and the inbox read one
definition, so they cannot disagree. See [AUTONOMY.md](AUTONOMY.md).

What the dial never moves: **spend, legal, tool-request and assist approvals**
are undelegable at every setting, and the **spot-check** exists precisely
because a CEO may be judging its own closes. Authority is configuration;
integrity is structure, and the Desk draws that line where the server does.

### What happened while you were away

The desk is the present tense: it ranks what needs you now. The narrative of a
window you missed is not on this page — it reads in your inbox each morning
with `brief_daily_at` set, from `yaaf briefing`, and over MCP. It costs no
model call either way: it is composed from the record, which is what lets it
arrive on a project the platform paused for an exhausted budget.

The inbox is the same tense as the desk, and for the same reason: it carries
alarms and the things waiting on a decision from you, capped at
`mail_max_per_day` for the latter, and nothing else. A notice of something
already done reaches the webhook and the briefing, never your inbox — so the
desk and the inbox now agree about what is outstanding instead of the inbox
also replaying what is not.


**The hero state is an empty desk**, and it proves it rather than asserting
it: how many projects are working, when something last ran, and when the next
clock fires. *Nothing is wrong* and *nothing is happening* are different
screens — if you have projects that could be working and none is, the desk
says so instead of congratulating you.

## Projects

The portfolio view: every project with its status, spend against cap, open
work, and last run. Start, pause, and stop live here and in each project's
header.

## Roles

The platform-wide role library, grouped into the six aisles the hiring desk
uses — **Leadership & Direction**, **Research & Analysis**, **Build &
Engineering**, **Design & Content**, **Challenge & Control**, **Growth &
Customers** — with a search box, a filter for what a role works on (markets,
data, games, education, or any project), and a CSV export of the whole library.

A role's aisle and its domains live on the role, not the version, so refiling
one never stamps a new version and never puts a live team behind its prompt.
A role you write yourself can be left unfiled; it sits under **Uncategorised**
at the bottom of every list and is never filtered away.

Each role shows its current version, its
system prompt, model and effort, tool permissions, secret scopes — and its
**blast radius**: which projects run it, at which version.

Editing a role creates a **new version**. Live teams keep the version they
pinned; they move only when you **repin** them, which the health checklist will
remind you about. An edit never silently changes a running project.

## New project

Four steps, one per decision that actually needs you:

1. **The job** — name, and the goal the CEO plans from and nothing else. The
   goal gets a large box and a live checklist of the four things a brief should
   say (an outcome, a deliverable and who it's for, a deadline, where the inputs
   come from). Advisory — you can create with none of them ticked. Then the
   project's shape, which is frozen here.
2. **The org** — which CEO runs it, and the hiring desk: fourteen **crews**
   (a named job with a team already on it, eleven of them the `examples/`), or
   thirty-two roles by aisle with search, filters, depth and cost, and what each one
   needs before it can work.
3. **The money and the rules** — which LLM credential pays (the budget's unit
   follows it: dollars for an API key, tokens for a subscription), and the
   presets that fill the constitution. Every rule the preset wrote stays
   visible and editable, marked when you change one. A draft from the Founder
   arrives with the credential already chosen when it asked you, or when there
   was only one.
4. **Review & create** — the project as it will exist, what it still needs
   before it can start, and **Run a fire drill**: your exact spec run as a
   project on scripted agents, at no cost and creating nothing, reporting
   whether it gets off the ground, how often it would stop and wait for you,
   and what paces it. → [FEATURES §13](FEATURES.md)

Nothing is hidden behind a step: the **All on one page** toggle renders the
same state as one scroll, with the same fields and the same submit, and the
choice is remembered. Loading a setup file lands straight on **Review**. [CREATING_A_PROJECT.md](CREATING_A_PROJECT.md) walks
through it in plain English; [`examples/`](../examples/) has eleven projects you
import and run, in the order worth doing them.

You can also start from a **setup file** — an exported project design, or one
of the ten in [`examples/`](../examples/). Loading one fills the
whole form, brings any role definitions your library lacks, and lists the
credentials the design expects you to supply.

### Project shapes

Chosen once, at creation, and frozen — the rules differ by shape.

| Shape | What it is | Example goal |
|---|---|---|
| **Explore** | Chase a goal with no fixed end. Bets, proof, and your call on when it has earned its keep. No requirements, no Plan tab. | "Find an idea and get one sale in 60 days." |
| **Deliver** | Ship one thing, then stop. You accept what done means; the platform proves it. | "A security audit of our AWS estate." |
| **Operate** | A Deliver that starts again on a rhythm, and gets better each cycle. | "A weekly market brief, tracked and improved." |

Outside Explore, a task that serves no requirement is refused, and the project
starts owing a plan before any work may be created.

### Autonomy presets

Two cards on the same form decide who holds which verb — **Supervised** (you
accept scope, sign the date, judge unverifiable closes) or **Autonomous** (the
CEO does all three and tells you). Both are five ordinary constitution
switches, editable later. [AUTONOMY.md](AUTONOMY.md) has the detail.

### Tempo cards

**Live** (~150 runs a day), **Working hours** (~40), **A few times a day**
(~12) — fourteen ordinary constitution rules deciding how often the project
wakes: the daily and hourly run ceilings, how often each agent may wake,
whether agents buy a run just to read each other's messages, how often the CEO
plans, and how far ahead work may be scheduled. Pre-selected from the project's
shape; every rule stays editable.

This is the rule that decides the bill. Until it had a card, the only "cheap"
option on the form was a smaller budget attached to a project that woke exactly
as often as the most expensive one.

### The bar

Full send, Balanced, Keep it cheap, Show me proof — a bundle of ordinary
constitution numbers (budget and the wall behind it, drought hours, approval
threshold, verification, stakes, who reads the work before you do) that fills
the fields below it. Every field stays editable, here and later.

### Re-picking either one

All three sets of cards appear again in **Settings → Rules in force**, on the
tab that owns most of their rules. Picking one there stages the whole bundle
and files it as a single amendment with your written reason, in force from the
next agent call. A project whose rules were set individually is reported as
**hand-tuned** rather than rounded to the nearest card.

---

# Inside a project

Six tabs you live in, then two you open when something looks wrong. Every tab
carries the same header — status, what the project is working on, spend against
the cap, and the setup light — plus one line answering "why is nothing
happening?" when the answer is not obvious.

**Explore projects have no Plan tab.** They have no requirements by design —
their shape is bets and evidence — and a tab that only ever explains its own
absence is clutter. See [project shapes](#project-shapes) below.

## Overview — the state of the project

Read top to bottom: what needs you, what is true now, what happens next.

- **Needs you** — the decisions the Desk surfaces, scoped to this project, plus
  the flags your CEO has raised. Every row here has a verb: a flag is answered
  with **Noted**, optionally with a line the CEO reads. A flag whose condition
  was a moment — a cycle already missed, a month already over budget — closes
  when you answer it, because nothing else ever will; one whose condition is
  still true drops to **Watch** under your note, and clears when the arithmetic
  turns. A quiet project says "nothing waiting on you here."
- **What happened** — not on this screen. The Overview is the present tense;
  the narrative of a window you missed reads in the morning mail, from
  `yaaf briefing`, and over MCP. A panel of it sat here and was removed: it
  restated the Needs-you count directly above it and the calendar directly
  below it, and a reader doing that arithmetic twice trusts neither copy.
- **Is it working?** — working now, runs today against the daily ceiling, runs
  that ended early, and spend today.
- **What happens next** — the calendar. Fourteen days by default (7 or 30 on
  the toggle), read in the constitution's one timezone.
  - **Rhythms** are the recurring wakes — the CEO's daily standup, any agent
    you put on a clock, the planning heartbeat — each with its cadence, its
    true next firing, and a mark on every day it touches. The planning
    heartbeat says when it will be *skipped*, because a cadence that silently
    does not fire on an empty board reads as a broken one.
  - **Events** happen once, and the strip colours them by what kind of date
    they are: something that **resolves itself** (an approval auto-deciding, a
    bet's kill date, spot-checks expiring), a **deadline** (the promised
    delivery, a cycle roll, an Explore deadline), a **meeting** (the standing
    review), or the platform's own **projection** — drawn hollow, because a
    projection is arithmetic and not a promise anyone made.
  - It **explains its blanks**: why the next review is not scheduled while one
    sits open, that nobody is on a wall-clock wake, that a paused project has
    no schedule at all. A blank column where your weekly meeting should be is
    worse than a sentence saying why.

  Everything on it is computed from the same heartbeats and rules the
  dispatcher and the janitor read, so a wake drawn here is a wake that will
  actually run.
- **Your deliverable** — the most recent published artifact, drafts included.
- **Watch** — everything on this project that is running down a clock the CEO
  resolves, and that therefore never becomes a Desk row: an evidence drought
  (active, or halfway to one), a kill date still ahead, the date your burn rate
  lands on the cap, memory nearing its ceiling, a flag your CEO has not yet
  raised in the boardroom, a flag you have answered whose condition is still
  true, and a day that spent money while nothing outside the project verified
  anything. Nothing here is waiting on a
  click from you. It is the answer to "is this going
  well?", which is a different question from "what needs me?" — and the Desk
  now asks only the second.
- **The plan** — live bets, the open board against the queue ceiling, verified
  proof in the drought window, and the **CEO-judgment metric**: how much of
  what the CEO called progress survived verification.
- **The money** — spend against the cap in the project's native unit (USD or
  tokens), plus who is spending it, by team, over 30 days. A project paying
  with a subscription also shows the credential's plan meter here — the
  session and weekly windows Anthropic enforces before the cap ever does,
  with when each resets.
- **Setup & health** — anything misconfigured, in plain language, with a fix
  link: dead billing, a credential of the wrong shape, an untested data source,
  a team on an outdated role version. **A healthy project collapses this to one
  green line.**
- **The project** — the org roster: teams, leads, employees, who's awake, and
  each team's open work.

## Boardroom — talk to your CEO

<img src="screenshots/boardroom.png" alt="The Boardroom: a chat with the CEO, with the CEO's live status and today's spend beside it">

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

## Plan — scope, sequence, and the promise

The tab that answers "when will this be done, and how would you know?"

- **The commitment banner.** When the platform's projection lands past the
  promised date the banner says so in days, new scope freezes for the CEO
  (existing work continues), and your two levers are right there: cut a
  requirement, or move the date with a written reason.
- **The numbers.** Scope, estimated runs, days at the daily run cap, measured
  pace per week, waste (dead runs, with the worst offender named), and how many
  runs the remaining budget actually buys.
- **The burn-up.** Requirements settled true against scope, with the committed
  date and the projected date as two lines you can compare at a glance.
- **Stages and dependencies.** One toggle over the same data: stages in order,
  or the dependency graph with the server-computed critical path. Work in a
  stage beyond the open one is refused until the open one settles.
- **Each requirement** carries its status, its acceptance test, runs actual
  against estimated, what it has cost, and its failures. Your buttons live per
  row: accept, cut, reopen, restage, reestimate.

Requirements are proposed by the CEO and accepted by you — that split is what
keeps the denominator of the forecast out of the hands of the party the
forecast grades. Nothing but the platform ever writes `met`.

## Work — the board and whether it's been proven

- **Check the agents' work.** The platform deals you a random sample of recent
  observable closes: what the task had to prove, next to what the agent
  claimed. Held or Failed, two minutes. Unanswered samples expire to "not
  reviewed" and nothing blocks either way — sampling is a thermometer, not a
  gate.
- **The board.** Every task with its team, status, whether its proof is
  internal or external, and who holds it. Read-only by design: only the CEO
  creates tasks, only leads decompose, and only the platform marks
  command-tested work done. You redirect the plan from the Boardroom.
- **Proof, per bet.** Each bet carries the tally of what it has earned —
  verified, failed, pending — because "has this bet been proven?" is the join
  this page exists for. The evidence rows themselves are a record and read in
  **Records → Evidence**; only verified or attested evidence counts toward the
  drought and the external-evidence floor, which is why a busy-looking project
  can still be in a drought.

Bets (hypotheses) with their kill dates live here too: each shows the proof it
has earned, and you can extend one — once, with a reason — or kill it.

## Review — the standing meeting

Opened on schedule by the janitor, with zero tokens, regardless of budget or an
empty board. A review with nothing on the board is the one you most need.

- **The platform's numbers**, frozen at open: pace, requirements met since the
  last review, the commitment's state with promised and projected dates,
  budget, audit coverage, and the worst offender by dead runs.
- **The CEO's variance narrative** — why the numbers are what they are, filed
  on a reserved governance run — and what it recommends.
- **Your six levers**: cut scope, add scope, reword, move the date, raise the
  budget, accept the slip. Each is recorded with your reason, disclosed to the
  CEO, and shown at the *next* review beside what it bought.

## Deliverables — the output

One list of every file the project has, filed by the day it landed — because
"what did we make today" is the question this screen is opened with, and it
used to be answered by reading two lists with different shapes.

- **Folders by day.** Today first and already open; every other day is a row
  that carries its own count and how much of it was published. A project with
  thousands of files is a filing cabinet, not a scroll.
- **A row per file**, with the type, the name (its folder dimmed in front of
  it), whether it is **published** or **working**, its size and the time it
  landed. Published rows carry what the receipt says: the version, the
  requirements it satisfies, its hash. Earlier versions of the same deliverable
  fold under the one that replaced it.
- **Three filters and a search**, all of them in the URL so the view is a link:
  published or working, file type (the extensions this project actually has,
  with counts), a date window, and a search over names and folders that
  highlights what it matched.
- **The reader** opens the file as the thing it is: a report as a document, a
  page as a page (sandboxed — agent-written HTML never runs on this origin), a
  dataset as a table with a header that survives scrolling, JSON as a tree,
  JSON Lines as records, an image as a picture, a PDF in the browser's own
  viewer, and anything else as text with line numbers. Source is a toggle, not
  the only option. **←** and **→** walk the filtered list without leaving it;
  **Esc** closes; **/** puts you back in the search box.
- **It never lies about its own edges.** Past the listing cap the newest files
  are the ones kept, and the screen says how many are not shown.

## Records — "what happened?"

Five ways of asking the same question, kept out of the daily path:

| View | Shows |
|---|---|
| **Runs** | Every model call: agent, team, why it woke, what it cost, and the full trace. |
| **Messages** | Every channel — including the ones you can read but not speak in. |
| **Memory** | What the project chose to remember, per scope, with its curation history. |
| **Decisions** | Everything you decided, everything that decided itself (expired approvals, auto-pauses, killed bets, frozen threads), every rule change — and **what the CEO decided** on its own authority. |
| **Evidence** | Every claim the agents made and what the platform could check about it. |

### Evidence

Newest first, with the platform's verdict on each: platform-attested, a claim
it fetched and verified, one it fetched and refused, or one nothing could
check. It opens with that split stated as a sentence, because "how much of what
this project claims is checkable at all" is the question, and it is arithmetic
rather than homework. Open a row for the agent's full claim, the verifier's
notes, the snapshot of what it actually fetched, and the task the claim was
proving.

**Nothing here has a verdict button**, and that is the point of it being a
record. It used to sit at the foot of the Work tab under a queue that dealt
every claim as a card with pass/fail buttons and scored you on how many you had
got through — a percentage, of an optional job that never finishes. It also
asked you to re-judge rows the platform had already fetched and decided, writing
your verdict into the same column the verifier wrote, after which nothing could
tell "the fetcher confirmed this" from "a person believed it". The things that
genuinely need you — approvals, asks, and the platform's sampled audit — already
ask, chase and expire on their own clocks. The one action on an opened row is a
question, not a verdict: it opens the Boardroom with the claim already quoted.

### What the CEO decided

On an autonomous project the CEO accepts scope, cuts it, signs and resets the
delivery date, and judges unverifiable closes. All of that is audited and
emailed — and it is listed here too: what it did, its reason, and when.

It sits deliberately **outside** "Waiting on you". Nothing on it is pending and
nothing expires. It is a record you read, every line is reversible from the
Plan screen or Settings, and the dial that granted it is one amendment from
being turned back. Under the Supervised dial the list stays empty by design.

## Settings

Grouped by necessity, not by subsystem. The required path is Brief → Money →
Data; everything after is occasional.

**The work**

- **Brief** — the goal the CEO plans from. (Controlled domains — the ones that
  can't count as external evidence — are set when the project is created.)
- **Money** — which LLM credential this project pays with, picked from the
  ones under app-wide Settings; **Save assignment** applies to the next run and
  **Test connection** proves the credential works now rather than at 9am. The
  billing mode is the credential's, not a separate switch. Clearing the pick
  and saving **detaches** the credential — the project cannot run until one is
  assigned again, which is how a credential is freed for deletion. For a subscription
  credential the card also shows its plan meter — every project on that
  credential draws from the same windows, and at 100% Anthropic refuses calls
  until the window resets. The budget in force is shown read-only: the numbers
  themselves are constitution rules, so the card links to Rules in force rather
  than offering a second editor for them.
- **Data & credentials** — MCP data sources (one form: name, URL, roles, Save,
  plus a live check) and scoped variables/credentials for your teams.

**Connections**

- **Integrations** — the Slack bridge and the GitHub workspace. Slack starts
  out **inherited** from the app-wide connection, so the only thing this card
  usually needs is a channel map — the boardroom is filled in at creation, and
  **Create missing channels** makes leadership and the team rooms whenever you
  want them, naming the rooms as it makes them. Every field can be overridden
  for a project that lives in a *different* workspace, and **Turn off for this
  project** silences one project without unwiring the install.

**Governance**

- **Rules in force** — all 52 constitution rules, each with its own help text
  and its stock default, behind six tabs: Money, Pace & brakes, How work is
  run, Autonomy, Memory, Rhythm & reporting. Edits span tabs; a tray at the
  foot of the pane lists every pending one, takes the required written reason,
  and shows before/after before it lands. Changes take effect from the next
  agent call. The change history and a raw-JSON escape hatch fold away at the
  bottom. Deep link with `?s=rules&t=<tab>`.

**Housekeeping**

- **Duplicate & export** — **clone** into a running twin on this install
  (service credentials copied, the same LLM credential assigned, optionally
  aimed at a new goal), or **export** a full-backup setup file: the whole
  design plus every project service credential value, decrypted. The LLM
  credential travels by name only, and the form re-selects it on import if
  this install has one by that name. It holds the keys it names — store it
  like a password.
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
- Accept scope into the plan, and cut, reopen, restage, reestimate or reword a
  requirement.
- Sign the delivery date, or move it with a reason.
- Pull any of the six review levers, and close the review.
- Approve or deny approvals.
- Extend an expired bet — once — or kill one.
- Unfreeze a thread the circuit breaker froze.
- Spot-audit completed work and evidence.
- Mark a briefing read, which is what sets where the next window starts.
- Start, pause, stop a project.
- Configure billing, credentials, data sources, and integrations.
- Clone, export, and repin teams to new role versions.

You never edit tasks, post to team rooms, or touch memory.
