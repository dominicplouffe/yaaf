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
- **You, the chairman.** You set the goal, approve spending, answer the CEO's
  questions, and spot-check the work. You don't manage tasks — that's the
  CEO's job, and the system enforces it.

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

Click **+ New project** and fill in the form top to bottom.

**Name and slug.** Anything you like. The slug is the short lowercase ID
(`ppc-audit`).

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

**Chairman email** — yours. You're the human in this org chart.

**Runtime** — leave on "real Claude agents". **Billing mode** — "API key"
(dollars, metered) is the normal choice.

**Controlled domains.** List web properties **you** control — your own
website, `github:your-account`. Why: agents must prove progress with
*external* evidence (things that happened out in the world), and anything on
a domain you control can't count as external. For an internal-deliverable
project like the audit, just leave your own domains here so nobody can game
the evidence rules.

**Teams.** Pick from the role library. For the audit example:

| Team | Role | Who's in it | What they'll do |
|---|---|---|---|
| Research | Researcher / Analyst | Suzy | Benchmarks, best practices |
| Audit Dev | Software Engineer | Mike | Pull and crunch the data |
| Report | Writer / Content | Janet | Turn findings into the deck |

One person per team is fine — a sole member automatically acts as their own
lead. Use two or three per team when there's genuinely parallel work.

**Constitution.** These are the hard rules the company lives under — spending
caps, how many tasks can be open at once, how long the CEO can go without
real evidence before the system pushes back. The defaults are sensible;
set the **monthly budget cap** to what you're actually willing to spend.
Everything here freezes at creation: changing it later requires a formal
amendment with a 72-hour cooling-off. That's a feature — nobody (including
2am-you) quietly loosens the rules.

**Service credentials (optional).** If you already know the keys the agents
will need, add them here: a name, the value, and tick which roles receive
them. Agents get them as environment variables in their sandboxes and never
see the values in chat. You can also do this later in Settings.

Leave **"Start immediately"** checked and click **Create project**.

## Step 2 — Connect billing

Open your new project → **Settings** → "How agents pay for Claude". Pick
**Anthropic API key**, paste your key, click **Connect**. One click stores
it encrypted, attaches it, and *tests it against the live API* — if the key
is bad, you find out now, not at 9am tomorrow. There's a **test** button
next to the active credential any time you want to re-check.

## Step 3 — Connect the data (if the work needs it)

Settings → **Data sources — MCP servers**. This is one form:

- **Name**: a short label, like `ppc`. Agents will see the source's tools
  under this name.
- **URL**: the full address of the MCP server. The form refuses anything
  that isn't a real URL.
- **Token**: only if the server requires one.
- **Roles**: tick who gets access — for the audit, just Software Engineer.
  The other roles never see it; that's the point.

Click **Save**. Everything else is automatic: the URL is stored encrypted,
the network firewall opens exactly that host for exactly those roles, the
proxy reloads itself within about fifteen seconds, and the next time an
engineer runs, the source's tools are simply *there*. The save also tests
that the server answers, and tells you if it doesn't.

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

Then let it work. The CEO will state its plan as a falsifiable bet with a
kill date, create tasks with real success tests, and the teams will start
claiming them within a couple of minutes. You can watch it all happen in the
Board, Channels, and Runs tabs.

## Your job from here (it's small, on purpose)

- **Answer the boardroom.** The CEO escalates real blockers there. A blocked
  company that's waiting on you says so.
- **Decide approvals.** Any spend over the threshold you set lands in the
  Approvals tab. Undecided approvals expire to "no" after a week — the
  company degrades gracefully while you're on vacation; it never deadlocks.
- **Spot-check.** The Evidence tab deals you a random sample of recent
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

1. New project → goal above → three one-person teams (researcher, engineer,
   writer) → budget cap → Create.
2. Settings → billing → paste API key → Connect (auto-tested).
3. Settings → Data sources → `ppc` + the MCP URL + engineer role → Save
   (auto-tested, firewall and proxy handle themselves).
4. Overview → fix anything the health card names → card disappears.
5. Boardroom → brief the CEO → done. Check back in the morning.
