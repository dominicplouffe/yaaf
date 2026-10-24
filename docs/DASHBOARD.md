# The dashboard — every screen, and what you do there

The chairman's cockpit is a React SPA served by the API at **`/app`**. Sign in
with your email and password; the first admin's comes from the one-time link
the install prints, and everybody else's from an invite (see
[AUTH.md](AUTH.md)). The browser holds a session cookie it cannot read from
script, and nothing is kept in `localStorage`.

Two places are about people rather than projects. **Settings → Account** is
yours: your password, the personal tokens the CLI and your scripts hold, and the
browsers you are signed in on. **Settings → People** is the instance admin's:
invite somebody (the link is shown, not mailed), send a fresh password link,
promote, deactivate. A project's own people — its chairman, co-chairs and
observers — are under that project's **Settings → People**, and anybody who is
only observing sees a line under the project header saying so, without the
Start and Stop buttons.

Everything below is also reachable from the [CLI](CLI.md), but the dashboard is
the intended way to run a project.

---

## Finding your way around

The app is built to be learned by browsing it. There is one sidebar, and it
always shows the same four things in the same order:

| | What it's for |
|---|---|
| **Home** | The landing page: one sentence on whether anything needs you, the decisions themselves, every project side by side, and what happened while you were away. |
| **Inbox** | Everything that needs you, across every project — and everything worth knowing that doesn't. The badge counts only the first. |
| **Projects** | Your portfolio — one comparable row per project. |
| **The project you are in** | A switcher (status, and how many things on it need you), then that project's five pages: Inbox, Team, Chat, Results, Details. |

Under the project's pages a **Live now** list names who is working, who is
waiting and who wakes next; click a name to open that agent. The **Role
library** and app-wide **Settings** sit at the foot, with your name, the theme
toggle and sign out. The **+** at the top starts a new project.

Every page has a breadcrumb and a **Live** freshness stamp at the top. On a
phone the sidebar folds into a drawer behind the menu button.

**Search and shortcuts.** **⌘K** (or **Ctrl+K**, or **/** on any page without
a search box of its own) opens a palette
that finds any page, project or person and runs the few app-wide actions
(theme, shortcuts, sign out). **?** lists every shortcut. **g** then a letter
goes to a page of the project you are in — **g o** its Inbox, **g t** Team, **g c** Chat,
**g r** Results, **g d** Details, and straight to a Details tab with **g m**
Roadmap, **g k** Tasks, **g f** Deliverables, **g s** Spend, **g v** Reviews,
**g a** Activity — and **g i** to the Inbox. No single
keypress ever decides anything: approving, denying and answering are always a
click on a labelled button.

Old links keep working. `#/desk` opens the Inbox, a project's `boardroom`
opens Chat, `progress` opens Roadmap, `progress/board` opens Tasks, and
`records/*` opens the matching Activity view — so a link in an old email or a
bookmark still lands somewhere true.

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
  SDK reports it on every run — and every row carries a **Check now** that gets
  a number for a credential no run is covering, which is the case that matters: a
  full window is what stops the runs. A window that has reset since it was last
  reported says it rolled over instead of showing the percentage it held while
  it was open. An open screen re-reads the meter every ten minutes so a tab left
  open all morning is not still showing the morning; that re-read is one local
  row. Asking Anthropic happens only when somebody presses for it, floored at
  one call per subscription every 15 seconds.

  **A press always says what it did**, in a line under the row: the numbers it
  found, or that it found the same ones, or why it could not ask — a throttle
  with its wait, or a token Anthropic refused, which offers this credential's
  own Edit form, because replacing the value is the only thing that fixes it.
  All of those used to return the stored row with no comment, which on screen is
  the same as a press that worked. The credential this button exists for is the
  one whose runs have stopped, and that is usually the one whose token is the
  problem, so the case most needing a sentence was the case that got none. A
  press inside the fifteen-second floor is waited out rather than reported, and
  rows sharing one subscription move together on one press.
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
- **Founder** — the key the **Founder** drafts on: one of the LLM credentials
  above (**Use** it, one click — the default offered when you add your first),
  or a key of its own. Install, rotate, run a live test against it, remove it,
  and pick the model it uses. It pays for drafting and **never reaches a
  project's agents**: borrowing runs one way — the Founder may use a
  project-paying credential, a project never uses the Founder's.
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

## Home — "anything for me, across everything?"

One sentence answers it — how many things need you, across how many projects,
when the soonest decides itself, and how many runs are in flight — and the page
then shows the decisions themselves (the first five, each with its button), a
card per project (status, one sentence on how it is doing, runs in flight,
spend against the cap, how many things on it need you), and **While you were away**: one line
per project that moved, from the same briefing your morning mail carries.

The sentence on each card is the project's one-line status — finished,
stopped, cannot run, paused, failing (its last three runs died), late, at
risk, decisions waiting, busy with nothing to show (twenty or more paid runs in
a week, from a project at least a week old, with nothing finished, met,
published or proven), gone quiet, on track — decided once on the server,
so the Projects page, the project's Inbox, `yaaf projects`, `yaaf status`
and Claude's `desk` and `project` say exactly the same thing.

It replaced the Desk as the first screen. The Desk answered "what needs me?"
very well and nothing else, so a chairman with four healthy projects opened the
app to "nothing needs you" and had to go elsewhere to learn whether anything was
happening at all.

## Inbox — "what needs me, and what should I know?"

<img src="screenshots/inbox.png" alt="The Inbox: decisions waiting on the chairman across every project, soonest consequence first, with the selected approval's full case and its Approve and Deny buttons">

Two panes, the way a mail client is: a list on the left, the selected item on
the right, **j**/**k** to walk the list. Five tabs:

| Tab | Holds |
|---|---|
| **Decide** | The decisions only you can make — the "needs you" list. This is the old Desk, unchanged in its rule, and the only tab the sidebar badge counts. |
| **Acknowledge** | What you must know but not decide: a problem the CEO was made to raise (answer it with **Noted**), and a heads-up you asked for by standing order (opening it notes it; **Reply to the CEO…** if you have something to say). Nothing there blocks or expires. |
| **Updates** | Each project's briefing — what moved since you last looked. |
| **Watching** | Clocks the CEO resolves: droughts, kill dates still ahead, budget pace, unaudited work. Nothing here waits on a click. |
| **Done** | Approvals and questions decided in the last seven days, and how — by you, or by their clock running out. |

A **project** filter narrows every tab to one project, and the tab, filter and
selected item are all in the URL, so a link to one decision is a link to that
decision.

### Needs you

**Every row here is something only you can do.** That is the tab's one rule.
If the CEO can resolve it, it is not a row — it is a log entry, and it lives on
the project it belongs to.

So it holds exactly what the Desk held: a bet past its kill date, a thread the
pair breaker froze, an approval running down to auto-deny, a question the CEO
asked you, scope waiting to be accepted, a delivery date waiting to be signed,
and an observable close only you can judge. A drought, a kill date still in the
future, a budget on pace, unaudited work — those resolve without you, so they
are under **Watching**, where drawing them never teaches anyone to stop trusting
the badge.

Rows are ranked by **time to consequence** — how long until the thing decides
itself. The soonest is the top row whoever owns it; the list never sorts by
which project was created first.

Every row says the same three things in the same order: what happened, **what
happens if you do nothing**, and the action. Open one and the right pane has
the whole case:

- **An approval** shows what was asked, by whom, the amount, the reason, and
  the time left before it denies itself, with **Approve** and **Decline…** side
  by side — declining asks for the reason the CEO re-plans from. Approving a
  spend asks once more, naming the amount, because a purchase cannot be taken
  back.
- **A question from the CEO** shows the question, the default the CEO falls
  back to if you stay silent, and a box to answer it. Your answer is posted to
  the boardroom as a reply to that question, so the CEO reads it where it reads
  everything else from you; if other questions are still open on that project,
  the pane says so, because one boardroom message answers all of them.
- **Scope, a date, a close to judge** link to the screen where the decision is
  made, with the consequence stated first.

The sidebar badge, Home, the project switcher and each project's Inbox
card all read the same list from the same request, so they cannot disagree.

**Only live projects appear.** Stopped and completed ones are the Projects
page's business.

**The autonomy dial decides what is eligible for a row.** Where the
constitution says `ceo`, the CEO accepts its own scope, signs the platform's
projected date and judges its own observables, so those never reach this tab.
Where the chairman holds a switch, the thing waiting on them is a row here:
proposed scope to accept, a date to sign, an observable close to judge. Each is
also chased by email until it is acted on — the tab and the email read one
definition, so they cannot disagree. See [AUTONOMY.md](AUTONOMY.md).

What the dial never moves: **spend, legal, tool-request and assist approvals**
are undelegable at every setting, and the **spot-check** exists precisely
because a CEO may be judging its own closes. Authority is configuration;
integrity is structure, and the Inbox draws that line where the server does.

**The hero state is an empty tab**, and it proves it rather than asserting it:
how many projects are working, when something last ran, and when the next clock
fires. *Nothing is wrong* and *nothing is happening* are different screens — if
you have projects that could be working and none is, the Inbox says so instead
of congratulating you.

**Snooze and Undo.** Every row you own has **Snooze ▾**: for an hour, until
tomorrow morning, or — for an approval or a question — until two hours before
it decides itself. A snoozed row leaves your Inbox, your badge and Home until
then, and waits under **Snoozed** at the foot of the list with **Wake now**.
It is yours alone (a co-chair's snooze hides nothing from you), and it can
never run past the moment the decision makes itself: the platform refuses a
later time. **Approve** and **Decline** wait 8 seconds before they are sent,
with an **Undo** in the corner; once sent, a decision is final, as it always
was. Decisions made from the CLI or over MCP are sent at once.

### Updates — what happened while you were away

Each project's briefing, composed from the record at zero tokens: scope that
settled, work that closed, proof the platform verified or refused, deliverables
published, what it cost, what decided itself in your absence, what the CEO
decided on its own authority, and what that leaves waiting on you. **Mark as
read** moves the start of that project's next window to now.

Flags the CEO raised to you sit in the same list. Answer one with **Noted**,
optionally with a line the CEO reads. A flag whose condition was a moment — a
cycle already missed, a month already over budget — closes when you answer it,
because nothing else ever will; one whose condition is still true moves to
**Watching** under your note, and clears when the arithmetic turns.

The briefing used to be kept off every screen, on the grounds that a narrative
sitting on top of a present-tense page restated its counts back at it. It now
has a tab of its own instead — beside the decisions rather than on top of them
— because "what happened?" is the second thing anybody opens an inbox for, and
answering it only in the morning mail sent people to their email to learn
about their own app. The same briefing still arrives by mail with
`brief_daily_at` set, from `yaaf briefing`, and over MCP.

The email is still only for alarms and the things waiting on a decision from
you, capped at `mail_max_per_day` for the latter. A notice of something already
done reaches the webhook, the briefing and this tab, never your email.

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
system prompt, model, effort and harness, tool permissions, secret scopes — and its
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
| **Explore** | Chase a goal with no fixed end. Bets, proof, and your call on when it has earned its keep. No requirements, no Roadmap page. | "Find an idea and get one sale in 60 days." |
| **Deliver** | Ship one thing, then stop. You accept what done means; the platform proves it. | "A security audit of our AWS estate." |
| **Operate** | A Deliver that starts again on a rhythm, and gets better each cycle. | "A weekly market brief, tracked and improved." |

A Deliver project whose every requirement is true tells you so, and then closes
itself after `complete_after_hours` (24 by default; Settings → Rules in force →
Autonomy) unless its scope moves: the project's Inbox says when, on its strip, and **Complete
the project…** does it now. A completed delivery's Inbox offers **Reopen…** for
14 days; a reopened one runs again and will not close itself until its scope has
moved.

Outside Explore, a task that serves no requirement is refused, and the project
starts owing a plan before any work may be created.

### Autonomy presets

Two cards on the same form decide who holds which verb — **Supervised** (you
accept scope, sign the date, judge unverifiable closes) or **Autonomous** (the
CEO does all three and tells you). Both are five ordinary constitution
switches, editable later. [AUTONOMY.md](AUTONOMY.md) has the detail.

### Tempo cards

**Live** (~150 runs a day), **Working hours** (~40), **A few times a day**
(~12) — thirteen ordinary constitution rules deciding how often the project
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

A project has five pages, each answering one question:

| Page | Answers |
|---|---|
| **Inbox** | What does this project need from me, and what should I know? The project's home. |
| **Team** | Who is doing my job, who do they report to, and what is each of them on? |
| **Chat** | Talk to the CEO who runs them. |
| **Results** | What has the team delivered, and who checked it? |
| **Details** | How it works inside — Roadmap, Tasks, Deliverables, Spend, Reviews, Activity, Settings, as tabs. Nothing there needs you. |

The sidebar carries the count of decisions waiting on you beside Inbox (the
same count as the Inbox above it, for this project), how many people are busy
beside Team, and marks Details when a review is open. Every page carries the
same notices when they apply: why nothing is happening when the answer is not
obvious, a project paused or stopped and why, and a review that is open and
waiting on you.

Every old address still works: `/overview` opens the project's Inbox, `/team`
the Team page, and `/roadmap`, `/tasks`, `/deliverables`, `/spend`, `/reviews`,
`/activity/…` and `/settings` the matching Details tab, with their filters
intact.

**Explore projects have no Roadmap tab.** They have no requirements by
design — their shape is bets and evidence — and a tab that only ever explains
its own absence is clutter. See [project shapes](#project-shapes) below.

## Inbox — the project's home

<img src="screenshots/cockpit-overview.png" alt="A project's Inbox: the header with its status and a one-line scoreboard, then the three things waiting on the chairman on this project with the selected one opened, and below it the CEO's latest word, what the CEO is watching, the calendar and setup and health">

**The header** says what kind of project this is (its shape, the date it is due,
how much the CEO decides on its own, your role on it), its name and status, the
project's one-line status (the same sentence as Home, with a link to the page
that fixes it when something is wrong), the goal (the full brief one click
away), and the controls: **Start**, **Pause the team**, **Stop**, and
**Message** the CEO. Under it, one line keeps score: on track or not, how many
must-be-true things are true (or bets in play, on an Explore project), how many
results have been delivered, what this month has cost against the cap, and
runs today. When the projection lands past the promised date, a banner says
so in days, with your two levers: cut scope, or move the date.

**The Inbox** is the same Inbox as the one in the sidebar, for this project
only — the same rows, the same tabs (Decide, Acknowledge, Updates, Watching,
Done), the same buttons, and the same count. It is one component in both
places, so the two can never disagree.

**Below it:**

- **From the CEO** — its latest message to you, the flags it has raised (answer
  each with **Noted**), and one-click starters that open Chat with the question
  already typed.
- **Watching** — clocks the CEO resolves, which therefore never become a
  Needs-you row: an evidence drought, a kill date, memory nearing its ceiling,
  a flag the CEO has not yet raised with you, one you answered whose condition
  is still true.
- **Coming up** — the next things on the calendar, and **Full calendar** for
  the fourteen-day view. Rhythms (the CEO's daily standup, the planning
  heartbeat, any agent on a clock) and one-off events coloured by kind — one
  that **resolves itself**, a **deadline**, a **meeting**, or the platform's
  **projection**, drawn hollow.
- **Setup & health** — anything misconfigured, in plain language, with a fix
  link. Each row is a **problem** (fix it) or a **suggestion** (advice; **Got
  it** puts it away for you until the finding changes). The badge beside the
  project's status counts the same rows. A healthy project collapses this to
  one green line.

## Team — who is doing the work

<img src="screenshots/team.png" alt="The Team page: the chairman's one-line box linking to the Inbox, the CEO beneath, and each team — a newly hired Risk team drawn dashed with the CEO's reason — with every person's state, what they are on, the model they run on and their month's cost">

The org chart, top to bottom:

- **You**, on one line: how many things wait on you, and **Inbox →**.
- **The CEO**, who reports to you and is the only agent you talk to.
- **Each team** with its lead and its people. Every person wears their state as
  a coloured word — **Working**, **Up next**, **Waiting on you**, **Blocked**,
  **Idle**, **Paused** — says in one line what they are doing, and names the
  **model** they run on and what they have **cost this month**. An active seat
  with no run in a week carries a **No run in N days** (or **Never run**)
  marker.
- **A large team stays readable.** Past four teams or ten people, teams wrap
  into a grid of cards instead of one row, the ones with someone at work,
  waiting or stuck come first, and each person is one line. A thirteen-team
  project fits the width of the page.
- **A team the CEO hired this week** is drawn with a dashed outline and a
  **new** badge, with who hired it, when, and the reason it gave. Hiring is the
  CEO's own verb — no approval waits — so this is where you see it happened;
  **Stand down…** is how you disagree. Teams whose job is done fold to one
  line under the chart, with **Recall** one click away.
- **+ Add a team** hires from the role library.

**List** shows the same people as a table with runs and spend today.

**Who works when**, under the chart, is one row per person across the day
around now: the last twelve hours as the runs that happened, a line at now,
and the next twelve as the wakes the platform will fire (dashed). On a large
team the people who did not run and have no scheduled wake are counted in one
line rather than drawn as empty rows — they work when handed work or a
message. Computed from the same heartbeats the dispatcher reads, so a wake
drawn here is a wake that will run.

### One person's page

<img src="screenshots/person.png" alt="One person's page: what Maya is working on now and the requirement it serves, her run in flight, her week, what she has made and who read it, the model she runs on and her month's cost, and her recent runs each with What they did">

**Click anyone** — on the chart, in the sidebar's Live now, on a task card —
and their page opens, with an address you can send:

- **Now** — what they are doing, what it is *for* (the goal, the requirement,
  the task), how long, and the run in flight with **What they have done so
  far** and its raw log.
- **Next** — the other work in their hands, each with what it is for.
- **This week** — finished, runs, cost, and runs today.
- **What they have made** — their published results and how each was checked.
- **How they work** — role and version, model and provider, this month's cost,
  whether they talk to you (only the CEO does), wakes this hour.
- **Recent runs** — each with **What they did**, and **Every run they have
  done** opens Activity › Runs filtered to them.

The main button is **Ask the CEO about** them, which opens Chat with a draft
naming them and what they are doing — because the CEO, not you, directs the
agents. **Pause** stops their wakes until you resume, after a confirm step.

## Chat — talk to your CEO

<img src="screenshots/boardroom.png" alt="Chat: a conversation with the CEO, with the CEO's live status, the questions it is asking you, and today's spend beside it">

A chat, not a ticket queue. Every running project keeps a resident CEO chat
session, so replies land in seconds, with a typing indicator. When background
runs are in flight the banner says so ("the CEO is working — 2 runs in
flight"), so silence is never ambiguous. An empty message box offers a few
starters — the questions chairmen actually ask — so the first message is never
a blank page.

Under each of the CEO's messages, **What they did** opens the run that wrote it:
what it changed on the platform, in order — tasks taken on or finished, files
published, messages written, requests filed, teams hired — and the tools it
reached for, from the platform's own rows rather than the model's account.

When the CEO has asked you something, the question sits beside the chat with
its default and its deadline, and **Answer** puts a reply to it in the box.
Any message you send answers every question still open.

This is your **only** channel, and it is the one the platform calls the
boardroom. You don't post to team rooms — that discipline is the product.
Every page that would otherwise invite you to change the work — the task
board, an agent's card — sends you here instead, with a draft that names
what you were looking at.

A rule you state here can become a rule the platform holds: the CEO files it as
a **directive** citing your actual message, and it's enforced from the next
call. See [GOVERNANCE.md](GOVERNANCE.md#the-rules).

The same conversation is reachable from Slack — see
[INTEGRATIONS.md](INTEGRATIONS.md#slack).

## Results — what the team delivered, and who checked it

<img src="screenshots/results.png" alt="Results: each published deliverable with who made it and who checked it — verified by YAAF, read by Juno — and the selected one's evidence: the check, the goal-to-requirement-to-task chain, and what the run that made it did">

One row per published deliverable (its newest version), newest first: who made
it, and who or what looked at it before it reached you, in the platform's words
— **verified by YAAF** (the platform ran the check and it passed), **read by**
a named reviewer (with what they said), **met by judgement** (an observable
nobody's script can check), or **not checked**. A judgement is never shown as a
verification. The counts across the top say how much of the shelf is which.

Pick a row for its evidence: who made it, every check, what it was **for** —
the chain from the project's goal through the requirement to the task — what
it cost to make (and how many of those runs failed), and **What they did to
make it**. **Open it** reads the file in Details › Deliverables; **Wrong
because…** sends your reason to the CEO in Chat.

Drafts and working files are not here. They are in **Details › Deliverables**,
the full file explorer.

## Details — how it works inside

One page with a tab per subject, so the sidebar can say what the project is.
Nothing on it needs you — anything that does is on Team and in the Inbox.

## Roadmap — scope, sequence, and the promise

The page that answers "when will this be done, and how would you know?"

- **The commitment banner.** When the platform's projection lands past the
  promised date the banner says so in days, new scope freezes for the CEO
  (existing work continues), and your two levers are right there: cut a
  requirement, or move the date with a written reason.
- **The numbers.** Scope, estimated runs, days at the daily run cap, measured
  pace per week, waste (dead runs, with the worst offender named), and how many
  runs the remaining budget actually buys.
- **The burn-up.** Requirements settled true against scope, with the committed
  date and the projected date as marks you can compare at a glance. **Deliver
  projects only** — work that comes round again has no line to burn up, so an
  Operate project gets "Owed every cycle" instead, which says which obligation
  slipped and in which turn. When a plan is too small or too young to have a
  shape — fewer than three settled, fewer than three in scope, or everything
  settled inside one day — the same facts are stated as numbers rather than
  drawn as a line through one point.
- **Stages and dependencies.** One toggle over the same data: stages in order,
  or the dependency graph with the server-computed critical path. Work in a
  stage beyond the open one is refused until the open one settles.
- **Each requirement** carries its status, its acceptance test, runs actual
  against estimated, what it has cost, and its failures. Your buttons live per
  row: accept, cut, reopen, restage, reestimate.

Requirements are proposed by the CEO and accepted by you — that split is what
keeps the denominator of the forecast out of the hands of the party the
forecast grades. Nothing but the platform ever writes `met`.

## Tasks — the board, and whether it's been proven

- **The board.** Five columns — **Ready**, **In progress**, **Being checked**,
  **Stuck**, **Done** — and a card per task with its team, who holds it, and
  the bet it serves. Click the holder to open that agent.
  Read-only by design: only the CEO creates tasks, only leads decompose, and
  only the platform marks command-tested work done. The banner over the board
  says so and offers **Ask the CEO for a change**, which opens Chat.
- **Check the agents' work.** The platform deals you a random sample of recent
  observable closes: what the task had to prove, next to what the agent
  claimed. Held or Failed, two minutes. Unanswered samples expire to "not
  reviewed" and nothing blocks either way — sampling is a thermometer, not a
  gate.
- **List & bets** — the same tasks as a table with their proof (internal or
  external), and the bets (hypotheses) with their kill dates. Each bet carries
  the tally of what it has earned — verified, failed, pending — because "has
  this bet been proven?" is the join this view exists for; you can extend one
  — once, with a reason — or kill it. The evidence rows themselves are a record
  and read in **Activity → Proof**; only verified or attested evidence counts
  toward the drought and the external-evidence floor, which is why a
  busy-looking project can still be in a drought.

## Spend — "is this worth what it costs?"

- **This month's budget** — spent against the cap in the project's native unit
  (USD or tokens), where you will land at the current pace, and the policy at
  each line: a warning at the warn ratio, a pause at 100%, and which credential
  pays. A project paying with a subscription also shows the credential's plan
  meter — the session and weekly windows Anthropic enforces before the cap ever
  does, with when each resets. **Change the budget…** goes to Settings.
- **Today** — spent today, runs today against the daily ceiling, runs that
  ended early, and the last 24 hours, with spend over the last fourteen days
  once there are three days of history.
- **By team** — who is spending it, sorted, with a line when one team costs
  more than all the others together.
- **The last 30 days, in detail** — tokens and runs by model, team or agent.

## Reviews — the standing meeting

Opened on schedule by the janitor, with zero tokens, regardless of budget or an
empty board. A review with nothing on the board is the one you most need. An
open review shows a banner on the project's Inbox and the Roadmap until you close it.

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
  requirements it satisfies, its hash — and how those were checked, in the
  platform's words: **verified** (it ran the acceptance test), **judged** (an
  observable somebody judged), or **not yet checked**. A judgement is never
  shown as a verification. Earlier versions of the same deliverable
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

## Activity — "what happened?"

<img src="screenshots/activity.png" alt="Details › Activity › Everything: every run, message and decision newest first, filterable by kind, person and window, each action saying on whose authority it happened, with View run and Export CSV">

**Everything** is the default: one list of every message, run, decision and
piece of proof, newest first, grouped by day. Filter it by kind — Messages,
Runs, **Your decisions**, **What the CEO decided**, **By your rules**, Proof —
by person, and by window (today, 7, 30 or 90 days); the filters live in the
address, so "Ada's runs this week" is a link. Every action says **on whose
authority** it happened: your decision, a rule you set (quoting your number —
"up to 4 teams", "decline after 7 days"), a verb your autonomy setting handed
the CEO, or what woke a run. **View run** opens that run; **Export CSV**
downloads the lines you are looking at.

The other views ask the same question one subject at a time:

| View | Shows |
|---|---|
| **What happened** | The briefing: what moved, what decided itself, and what is worth your eye, over a window you pick — since you last looked, 24 hours, or 7 days. **Mark as read** sets where the next window starts. |
| **Runs** | Every model call: agent, team, why it woke, what it cost, and the full trace. |
| **Decisions** | Everything you decided, everything that decided itself (expired approvals, auto-pauses, killed bets, frozen threads), every rule change — and **what the CEO decided** on its own authority. |
| **Proof** | Every claim the agents made and what the platform could check about it. |
| **Messages** | Every channel — including the ones you can read but not speak in. |
| **Memory** | What the project chose to remember, per scope, with its curation history. |

### Proof

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
question, not a verdict: it opens Chat with the claim already quoted.

### What the CEO decided

On an autonomous project the CEO accepts scope, cuts it, signs and resets the
delivery date, and judges unverifiable closes. All of that is audited and
emailed — and it is listed here too: what it did, its reason, and when.

It sits deliberately **outside** the Inbox's Needs-you tab. Nothing on it is
pending and nothing expires. It is a record you read, every line is reversible
from the Roadmap or Settings, and the dial that granted it is one amendment from
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

**Connections**

- **Data sources** — the MCP servers agents call as tools, one line each. Every
  source shows one state in words — *Working* (with its tool count), *Needs
  sign-in*, *Sign-in expired*, *Credential refused*, *Wrong address*, *Not an MCP
  server*, *Offers no tools*, *Blocked by the network*, *Not answering*, *Can't
  connect* — with the one button that fixes it beside it (Connect, Reconnect,
  Test again, Fix it) and the server's own sentence underneath. Test, edit and
  remove live in the row's **⋯** menu. **Add data source** opens a drawer that
  asks for the address first, checks it before anything is stored, and says how
  the server wants you to sign in — no sign-in, a pasted token, or OAuth — so
  the rest of the form follows from the answer.
- **Secrets & variables** — what agents receive as environment variables, one
  56px line each: the name agents see (`$NAME`), its type (**Secret**, **Plain
  value** or **File**), enough of the value to tell two apart (a plain value
  whole, a secret's last four characters, a file's name and size), who gets it
  as team chips, and when it last changed. Search, a **What ⟨team⟩ gets**
  filter, and an **Access map** — variables and data sources as rows, teams as
  columns — answer "what does this team have?" on one screen; ticks there stage
  with an amber ring and save as one batch. Every edit happens in a drawer with
  one **Save changes**: the type, the value (reveal, copy, replace — each reveal
  logged), who gets it, and the history in sentences. **Paste .env** splits a
  pasted file into rows for review. A credential an agent asked for shows at the
  top with **Add it**, which stores it, grants the asking team and answers the
  request in one motion. Values a data source or the Slack bridge owns are
  listed, locked, and link to where they are edited.

- **Integrations** — the Slack bridge and the GitHub workspace. Slack starts
  out **inherited** from the app-wide connection, so the only thing this card
  usually needs is a channel map — the boardroom is filled in at creation, and
  **Create missing channels** makes leadership and the team rooms whenever you
  want them, naming the rooms as it makes them. Every field can be overridden
  for a project that lives in a *different* workspace, and **Turn off for this
  project** silences one project without unwiring the install.

**Governance**

- **Rules in force** — every constitution rule, each with its own help text
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
