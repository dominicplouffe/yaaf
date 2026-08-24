# Creating a project — a plain-English walkthrough

This guide walks you from nothing to a running AI company, using a real
example: an **Amazon PPC audit** — a team of agents that pulls a customer's
advertising data from a data source, analyzes it, and produces a report a
human could hand to a paying client.

You don't need to know how yaaf works internally. You need about ten minutes,
an Anthropic API key, and a clear idea of what you want done.

## What you're actually creating

A project is a small company. It has:

- **A CEO agent** that plans: it decides what to work on, creates tasks, and
  reports to you. It's the only agent you ever talk to.
- **Teams of worker agents** (researchers, engineers, writers…) that do the
  work. Each team has a lead who breaks work down and hands it out.
- **You, the chairman.** You set the goal, decide what counts as in scope,
  sign the delivery date, approve spending, answer the CEO's questions, and
  spot-check the work. You don't manage tasks — that's the CEO's job, and the
  system enforces it.
- **A plan, if the project ships something.** In a Deliver or Operate project
  the root object is the **requirement**: a thing that must be true at the end,
  carrying a test the platform runs itself. Tasks exist to make requirements
  true, and a task serving no requirement is refused.

The company keeps working while you're away. Agents wake up when there's a
reason to (a message from you, a task ready, a scheduled check-in), do one
unit of work, and go back to sleep. Everything is recorded.

## Before you start, have these ready

1. **The stack running** — `http://localhost:8080/app` loads and you can log
   in with your admin token.
2. **An Anthropic API key** (from console.anthropic.com) — this is how agents
   pay for their model calls.
3. **Any outside-service credentials the work needs** — a WordPress password,
   a GitHub token, an API key for some service. Optional; you can add them
   later.
4. **A data source URL, if the agents need data** — for the PPC audit, that's
   the address of the MCP server that serves the customer's advertising data,
   as a full URL like `https://api.example.com:3845/mcp`. Not a password, not
   a server name — the whole address. (The form will refuse anything else,
   on purpose.)

## Step 1 — Create the project

Click **+ New project**. It runs in four steps — the job, the company, the
money and the rules, then review — because those are the four decisions that
need you; everything else is derived, set by a preset, or something you can do
later in Settings. Nothing is hidden behind a step: **All on one page** shows
the whole thing as one scroll whenever you want it, and the choice sticks.

**Name.** Anything you like. The slug — the short lowercase ID
(`ppc-audit`) — is picked for you from the name and deduped against your
existing projects; edit it only if you care what it says.

**The goal — spend your time here.** This is the single most important field.
The CEO plans everything from it, so write it the way you'd brief a
competent human running the company for you:

- Say the **outcome**, not the activities: what exists at the end?
- Name the **deliverable** and who it's for.
- Give a **deadline** if there is one.
- Say what **inputs** exist and where they come from.

A good goal for our example:

> Audit the customer's Amazon PPC account and produce a plain-English deck a
> non-technical business owner can act on: where money is being wasted, which
> campaigns to fix first, and expected savings. The account data comes from
> the connected "ppc" data source — use real numbers only, never invented
> ones. Final deck delivered by August 20th.

A weak goal ("analyze the PPC data") gets you a company that putters. Be as
specific as you'd be with a person.

**What shape is this?** Three cards, and the choice is **frozen at creation**
because the rules differ by shape:

- **Explore** — chase a goal with no fixed end. Bets, evidence, and your call
  on when it has earned its keep. *"Find an idea and get one sale in 60 days."*
  Explore takes an optional deadline: open scope, bounded time.
- **Deliver** — ship one thing, then stop. *"An audit of the customer's PPC
  account."* This is our example.
- **Operate** — a Deliver that starts again on a rhythm, and gets better each
  cycle. *"A weekly market brief, tracked and improved."* You pick the rhythm
  (daily, weekly, monthly, quarterly) in the constitution.

Outside Explore, the company **plans before it builds**. Its first obligation
is a plan: requirements, ordered into stages, each estimated, adding up to
something the budget can pay for. Until that holds, no work may be created at
all. That is a refusal in the server, not a nudge — and it is the difference
between a company that finishes and a pipeline with no terminus.

**Chairman email** — yours. You're the human in this org chart: it names you
on approvals and boardroom messages, and if the server has SMTP configured
(`YAAF_SMTP_HOST` in `deploy/.env`) it's also where needs, assists and budget
alerts get mailed. Remembered for your next project.

**Billing.** Pick the key type — **Anthropic API key** (budget in dollars,
the normal choice) or a **Claude subscription token** (budget in tokens) —
and paste the key. It's stored encrypted, attached as the project's billing
credential, and never enters a sandbox. Leave it blank and the project can be
created but not started: agents cannot run keyless. (The mock runtime, under
**advanced…**, is the $0 exception for tire-kicking the platform itself.)

**Who runs it? / How does it run?** Two rows of preset cards. The first is
the authority dial (Supervised or Autonomous — who holds the plan, scope,
commitment and verdict verbs). The second sets the pace-money-proof numbers:
**Full send** (fast, cost secondary), **Balanced** (the default), **Slow &
steady** (small budget, fewer reports), **Show me proof** (evidence counts
only once the platform verifies it). A preset just fills the Constitution
fields below — adjust any of them, now or later in Settings.

**Controlled domains.** List web properties **you** control — your own
website, `github:your-account`. Why: agents must prove progress with
*external* evidence (things that happened out in the world), and anything on
a domain you control can't count as external. For an internal-deliverable
project like the audit, just leave your own domains here so nobody can game
the evidence rules.

**Who runs it.** Pick the CEO. Most projects want **CEO / Project Manager**;
a markets desk wants the **Chief Investment Officer**, which owns a mandate and
a research agenda instead of a delivery plan. This is the only agent you will
ever talk to, and it is frozen once the project starts.

**Teams.** Open the hiring desk. Two ways in, and both end up in the same
editable list of teams:

- **Start from a crew** — a named job with a team already on it. *Numbers you
  can defend* is the one for our audit: an Analysis team of two Data Analysts
  and a one-person Verification team that re-derives their numbers. Picking a
  crew fills the teams in, names included; change anything you like.
- **Pick roles yourself** — thirty roles in six aisles (who decides, who finds
  out what's true, who builds, who writes it up, who checks it, who sells it),
  with a search box and a filter for what each works on. Every role shows what
  it does in a sentence, how deep and expensive it is, and **what it needs** —
  a GitHub token, a market-data key — before you hire it rather than after a
  run fails asking for one.

For the audit example, by hand:

| Team | Role | Who's in it | What they'll do |
|---|---|---|---|
| Research | Researcher / Analyst | Suzy | Benchmarks, best practices |
| Audit Dev | Software Engineer | Mike | Pull and crunch the data |
| Report | Writer / Content | Janet | Turn findings into the deck |

One person per team is fine — a sole member automatically acts as their own
lead. Use two or three per team when there's genuinely parallel work.

Later, on a running project, **Add a team** opens the same desk — but it opens
on what your roster is missing rather than on the library: *"Nobody here checks
the numbers before you see them"*, with the two or three hires that close it.

**Constitution.** These are the hard rules the company lives under — spending
caps, how many tasks can be open at once, how long the CEO can go without
real evidence before the system pushes back. The defaults are sensible;
set the **monthly budget cap** to what you're actually willing to spend.
Changing one later is a formal amendment, made in Settings → Rules in force,
with a written reason the CEO is shown. There is no waiting period: a rule you
decide on binds from the very next agent call. What stands in for the old
cooling-off is the accounting — a written reason, an audit entry, a new
constitution version, and mandatory disclosure to the CEO. The company still
cannot move its own goalposts; you never needed to be stopped from moving
yours.

The constitution card also carries your reporting cadence — **hours between
unprompted status reports** (default 1), an optional reporting window like
`09:00-20:00`, and your timezone (guessed from the browser).

**Service credentials (optional).** If you already know the keys the agents
will need, add them here: a name, the value, and tick which roles receive
them. Agents get them as environment variables in their sandboxes and never
see the values in chat. You can also do this later in Settings.

**Data sources (optional).** MCP servers can be connected right here too —
name, URL, optional token, roles — tested before the project starts. Or add
them later in Settings (next section).

Leave **"Start immediately"** checked and click **Create project**. (It
needs the billing key — without one the project is created stopped.)

## Step 2 — Connect billing (if you skipped it at creation)

Open your new project → **Settings** → "How agents pay for Claude". Pick
**Anthropic API key**, paste your key, click **Connect**. One click stores
it encrypted, attaches it, and *tests it against the live API* — if the key
is bad, you find out now, not at 9am tomorrow. There's a **test** button
next to the active credential any time you want to re-check.

## Step 3 — Connect the data (if the work needs it)

Settings → **Data sources — MCP servers**. A data source *is* an MCP server:
a URL that offers tools. This is one form:

- **Name**: a short label, like `ppc`. Agents will see the source's tools
  under this name (`mcp__ppc__*`).
- **URL**: the full address of the MCP server. The form refuses anything
  that isn't a real URL.
- **Token**: only if the server requires one.
- **Roles**: tick who gets access — for the audit, just Software Engineer.
  The other roles never see it; that's the point.

Click **Save**. Everything else is automatic: the URL is stored encrypted,
and the next time an engineer runs, the source's tools are simply *there*.
The save also tests that the server answers, and tells you if it doesn't.

### Why a data source makes a variable

An agent's sandbox is a locked box. The only thing that gets inside is an
environment variable you granted to that role — there is no other channel.
So the address travels as one: saving `ppc` hands the granted roles
`$MCP_PPC_URL` (and `$MCP_PPC_TOKEN` if you gave a token), and at run time
the sandbox rebuilds the connection from those. **The variable is the
delivery mechanism, not a side effect.**

You'll see those variable names listed under the source's name in the table.
They don't appear in *Variables & credentials* — that card is for credentials
you create by hand. One data source is one thing, edited in one place.

### Changing one

Press **edit** on its row. The stored address is fetched and shown in full —
an address is not a password, and you can't fix a port you can't see. Change
what you need and save; leaving the token box blank keeps the current token,
so a URL-only edit never disarms the auth header. There's a tickbox to remove
a token outright when a server stops needing one.

## A note on secrets and who can see them

You are the chairman, and you are the only human on this system. Every stored
value — service credentials, a data source's address and token, the Slack
tokens, the billing key, the webhook signing secrets — is masked in the UI
and one press of the **eye** from being readable, with a copy button and an
inline edit to rotate it. Debugging a connection shouldn't require a database
client.

That is a *display* choice for you, and it changes nothing about the agents.
A role still receives only the credentials you granted it, still as
environment variables in its own sandbox, and is still told the variable's
*name* and never its value. The two are different doors: the eye lives behind
your admin token, and no agent can reach it.

## Step 4 — Follow the checklist

Go to the project's **Overview**. If anything about the setup is wrong or
missing, a **Setup & health** card sits at the top naming each problem in
plain English with a link to fix it — a dead billing key, a credential that
should be a URL but isn't, a team running an outdated role version. Fix the
red rows; when the card disappears, the project is properly configured.
That's the rule to remember: **no card = ready.**

## Step 5 — Say hello

Open the **Boardroom** and talk to the CEO like you'd talk to a new hire on
day one. It already knows the goal; give it whatever else a person would
need:

> The data source is connected — engineers have it as "ppc". The customer
> cares most about wasted ad spend. Deadline is firm. Ask me here if you're
> blocked; I check in mornings and evenings.

Then let it work.

**In a Deliver or Operate project, the next thing that happens is the plan.**
The CEO proposes requirements — things that must be *true* at the end, each
with an acceptance test and an estimate — and they appear on the **Plan** tab
marked *proposed*, which counts as nothing until you accept them. Read them
like a statement of work: is this the right list, in the right order, and does
each one have a test that could actually fail? Accept what you want, cut what
you don't. Once the plan holds, the board opens and the teams start claiming
work within a couple of minutes.

Then the CEO will propose a **delivery date** — one it does not write, because
the platform stamps it from its own projection of your measured pace. Sign it,
or replace it with your own. From that moment the promise is watched: if the
projection slips past it, new scope freezes and you are told, with the
arithmetic in front of you.

In an Explore project there is no plan and no Plan tab. The CEO states a
falsifiable bet with a kill date instead, and the evidence rules do the rest.

You can watch all of it in **Plan** and **Work**, and in **Records** when you
want the receipts.

## Your job from here (it's small, on purpose)

- **Accept the plan, and cut what doesn't belong.** Scope is yours: the CEO
  proposes, you decide. This is the single highest-leverage thing you do, and
  it is deliberately not delegated — a forecast whose denominator is set by the
  party being forecast is not a forecast. (You can hand this verb to the CEO;
  see [AUTONOMY.md](AUTONOMY.md).)
- **Sign the date, and decide what happens when it slips.** Cut something, or
  move the date with a reason. Those are the two honest options and the
  platform offers exactly those two.
- **Show up to the review.** Once a week (by default) the platform opens a
  standing meeting with its own numbers and the CEO's explanation of them. Six
  levers, each recorded with your reason and shown back at the next review
  beside what it bought.
- **Answer the boardroom.** The CEO escalates real blockers there. A blocked
  company that's waiting on you says so.
- **Decide approvals.** Any spend over the threshold you set lands in a
  banner across the top of every tab, so you cannot miss it by being on the
  wrong page. Undecided approvals expire to "no" after a week — the
  company degrades gracefully while you're on vacation; it never deadlocks.
- **Spot-check.** The **Work** tab deals you a random sample of recent
  completed work and evidence claims. Fail anything that doesn't hold up —
  the CEO is told its team's work is suspect. You are the one reviewer that
  doesn't approve everything put in front of it.
- **Watch the money if you want to** — the Overview budget meter warns at
  80% and the project pauses itself at 100% regardless.

What you *can't* do — create tasks, message worker teams, edit memory — is
deliberate. The discipline is the product.

## When things get refused (they will, and it's good)

Agents live under hard rules, and you'll see refusals in the run logs: too
many open tasks, a vague success test rejected, work refused during an
evidence drought. **A refusal is the system working**, not a bug. The CEO is
told exactly why and plans around it. Similarly, if a run fails because a
credential is wrong, you get notified and — after three strikes — the
project pauses itself rather than burning money against a wall.

## Quick reference — the audit project, start to finish

1. New project → goal above → **Deliver** → API key pasted → three one-person
   teams (researcher, engineer, writer) → `ppc` data source + engineer role →
   budget cap → Create.
2. (Or later: Settings → billing → paste API key → Connect, auto-tested.)
3. (Or later: Settings → Data sources → `ppc` + the MCP URL + engineer
   role → Save, auto-tested.)
4. Overview → fix anything the health card names → card disappears.
5. Boardroom → brief the CEO.
6. Plan → accept the requirements you want, cut the rest, sign the date.
7. Done. Check back in the morning.
