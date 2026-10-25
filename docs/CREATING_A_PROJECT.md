# Creating a project — a plain-English walkthrough

This guide walks you from nothing to a running AI project, using a real
example: an **Amazon PPC audit** — a team of agents that pulls a customer's
advertising data from a data source, analyzes it, and produces a report a
human could hand to a paying client.

You don't need to know how yaaf works internally. You need about ten minutes,
an Anthropic API key, and a clear idea of what you want done.

## What you're actually creating

A project is a small org that runs itself. It has:

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

The project keeps working while you're away. Agents wake up when there's a
reason to (a message from you, a task ready, a scheduled check-in), do one
unit of work, and go back to sleep. Everything is recorded.

## Before you start, have these ready

1. **The stack running** — `http://localhost:8080/app` loads and you can sign
   in. You chair whatever you found; add co-chairs or observers afterwards
   from the project's Settings → People.
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

## Step 0 — Which way in

There is one way to start a project: **+ New project**, at the top of the
sidebar and on the Projects page. It opens a fork, not a form, and asks the one
question that decides everything after it:

- **Describe it, and yaaf drafts it** — you write a paragraph, the Founder
  writes the project. Choose this for a first project, or whenever you know the
  outcome you want but not the org that gets you there.
- **Fill it in myself** — the four-step form below. Choose this when you have
  done it before, or you already know the goal and the roster.

Neither is a commitment. Both end on the same review screen, nothing is created
until you sign it there, and each screen carries a link across to the other —
including one under the goal box, which is where people usually discover they
wanted the Founder after all. A setup file exported from another project is the
third way in, offered on the same screen; it prefills the form.

Everything below describes filling the form yourself, and it still works. But
the two hardest decisions on that form — the goal, and who to hire — are the
two the product can now argue with you about. **Describe it, and yaaf drafts
it** opens a conversation instead of a form. Write what you
want in a paragraph; the Founder answers with a whole project — goal, shape,
roster, and the handful of constitution rules it can justify — and then tells
you what it assumed, what it added that you did not ask for, and what it still
wants to know. You argue; it revises. When the draft is right, **Review &
create** drops the whole thing into the form below, where you change anything
you like, run the fire drill for nothing, and create it.

It needs one thing first: a key for the Founder, set once under Settings →
Founder. The quickest is **one you already added** as an LLM credential —
one click, and rotating that credential rotates this too; adding your first
LLM credential offers it, ticked. Or give the Founder a key of its own, from
any provider — it runs on that provider's frontier model unless you name
another. Either way it pays for drafting only, and no project's agents ever see
it. Every project you create still pays its own way, and the Founder knows it:
it reads the list of LLM credentials — each with what the roles' model classes
would run on if it paid — names the one that pays in its draft when there is
only one, and asks you which when there are several. Whichever pays, the role
library is the same: roles name a class of model, and the project's provider
supplies it ([PROVIDERS.md](PROVIDERS.md)). That is an instruction to the model, not a rule the server can
enforce, so the draft panel says which credential it chose — look before you
click Review.

Two things worth knowing:

- **The conversation is not thrown away.** When you create the project, it is
  handed to your new CEO as its day-one briefing. You do not have to explain
  yourself twice.
- **The goal is graded, not generated and forgotten.** The Founder grades its
  own draft against [GOAL_GRAMMAR.md](GOAL_GRAMMAR.md) — the ten things a
  goal has to contain — before it shows you anything. That file is worth
  reading whether or not you use the Founder; it is what separates a goal that
  works from one that putters.

## Step 1 — Create the project

Choose **Fill it in myself**. It runs in four steps — the job, the org, the
money and the rules, then review — because those are the four decisions that
need you; everything else is derived, set by a preset, or something you can do
later in Settings. Nothing is hidden behind a step: **All on one page** shows
the whole thing as one scroll whenever you want it, and the choice sticks.

**Name.** Anything you like. The slug — the short lowercase ID
(`ppc-audit`) — is picked for you from the name and deduped against your
existing projects; edit it only if you care what it says.

**The goal — spend your time here.** This is the single most important field.
The CEO plans everything from it, so write it the way you'd brief a
competent human running the project for you. (This is the field the Founder
above exists for; [GOAL_GRAMMAR.md](GOAL_GRAMMAR.md) is the long version of the
advice that follows.)

- Say the **outcome**, not the activities: what exists at the end?
- Name the **deliverable** and who it's for.
- Give a **deadline** only if there really is one — a date the world imposes,
  or one you actually want. Not "this feels like a two-month job": agents are
  not a human team, and the median project here finishes its whole accepted
  scope inside a day. A date nobody needs becomes the pace.
- Say what **inputs** exist and where they come from.

A good goal for our example:

> Audit the customer's Amazon PPC account and produce a plain-English deck a
> non-technical business owner can act on: where money is being wasted, which
> campaigns to fix first, and expected savings. The account data comes from
> the connected "ppc" data source — use real numbers only, never invented
> ones. Final deck delivered by August 20th.

A weak goal ("analyze the PPC data") gets you a project that putters. Be as
specific as you'd be with a person.

**What shape is this?** Three cards, and the choice is **frozen at creation**
because the rules differ by shape:

- **Explore** — chase a goal with no fixed end. Bets, evidence, and your call
  on when it has earned its keep. *"Find an idea and get one sale in 60 days."*
  Explore takes an optional deadline: open scope, bounded time. Deliver takes
  one too, as a target rather than a promise — it must say where the date came
  from, and the CEO is shown it next to the platform's own projection so a date
  somebody chose cannot quietly become the pace.
- **Deliver** — ship one thing, then stop. *"An audit of the customer's PPC
  account."* This is our example.
- **Operate** — a Deliver that starts again on a rhythm, and gets better each
  cycle. *"A weekly market brief, tracked and improved."* You pick the rhythm
  (daily, weekly, monthly, quarterly) in the constitution.

Outside Explore, the project **plans before it builds**. Its first obligation
is a plan: requirements, ordered into stages, each estimated, adding up to
something the budget can pay for. Until that holds, no work may be created at
all. That is a refusal in the server, not a nudge — and it is the difference
between a project that finishes and a pipeline with no terminus.

**Chairman email** — yours. You're the human in this org chart: it names you
on approvals and boardroom messages, and if the server has SMTP configured
(`YAAF_SMTP_HOST` in `deploy/.env`) it's also where needs, assists and budget
alerts get mailed. Remembered for your next project.

**Billing.** Pick which **LLM credential** pays — one an administrator
created under Settings → Providers: an **Anthropic API key** (budget in
dollars, the normal choice) or a **Claude subscription token** (budget in
tokens). The credential decides the budget's unit; nothing is pasted here and
no value ever enters a sandbox. Leave it unselected and the project can be
created but not started: agents cannot run keyless. A draft from the Founder
arrives with this already chosen when the Founder asked you, or when there
was only one credential to choose. (The mock runtime, under **advanced…**, is
the $0 exception for tire-kicking the platform itself.)

**Who runs it? / How does it run?** Two rows of preset cards. The first is
the authority dial — who holds the plan, scope, commitment and verdict verbs.
**Autonomous is selected for you**: the CEO accepts its own plan, signs the
platform's projected date and judges its own unverifiable closes, every one
audited, mailed to you and reversible. Pick **Supervised** if you would rather
hold those four yourself; it is the same five rules the other way round, and
either is one amendment away later. (A project created from a spec file or by
a model client follows the same default — see
[AUTONOMY.md](AUTONOMY.md#picking-a-level).)

**How often should it think?** The rule that decides the bill, and the one
nothing used to ask about. **Live** (~150 runs a day — something happens during
the day and the project should notice), **Working hours** (~40 — wakes when
there is work, quiet at night), **A few times a day** (~12 — wakes on a
schedule and otherwise sleeps, with agents no longer buying a run just to read
each other's messages). It is pre-selected from the shape you picked above,
because Explore and Deliver both end and an Operate project on a weekly rhythm
does not need to be awake 150 times a day. Change it if that is wrong.

**How good does it have to be, and what may it spend?** A separate question, on
purpose: these two used to be one card called "How does it run?", and of the
twenty-two rules that govern pace it set exactly one — so the cheap option was
a smaller budget attached to a project that woke just as often, and there was
no way to ask for work done *well* but *rarely*. **Full send** (nothing spared,
strongest model on the seats that write and check), **Balanced** (the default),
**Keep it cheap** (small budget, a wall close behind it), **Show me proof**
(evidence counts only once the platform verifies it).

A card just fills the Constitution fields below — adjust any of them, now or
later in Settings, where you can also re-pick either card outright and have it
land as one amendment with your reason on it.

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
- **Pick roles yourself** — thirty-two roles in six aisles (who decides, who finds
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

**You are picking a starting roster, not a permanent one.** Your CEO staffs the
project itself from here on: it hires roles from the same library and stands
down ones whose job is finished, up to the `max_teams` rule, without asking
you. That is deliberate, and it is safe for an unglamorous reason — hiring buys
no runs. The daily ration and the monthly cap belong to the *project*, so a
bigger roster spreads the same allowance thinner rather than raising it.

So hire for the front half and let the CEO shrink it. A project is
front-loaded: requirements, design, build, validation and QA crowd the start,
and steady execution afterwards needs a fraction of the seats. While it writes
the plan, the CEO records for each team the requirement or stage after which
that role is done, and the platform stands the team down when that comes true.
Every change lands in your boardroom, and the Team page carries **stand
down** and **recall** so you can overrule any of it.

**Constitution.** These are the hard rules the project lives under — spending
caps, how many tasks can be open at once, how long the CEO can go without
real evidence before the system pushes back. The defaults are sensible;
set the **monthly budget cap** to what you're actually willing to spend.
Changing one later is a formal amendment, made in Settings → Rules in force,
with a written reason the CEO is shown. There is no waiting period: a rule you
decide on binds from the very next agent call. What stands in for the old
cooling-off is the accounting — a written reason, an audit entry, a new
constitution version, and mandatory disclosure to the CEO. The project still
cannot move its own goalposts; you never needed to be stopped from moving
yours.

The constitution card also carries your reporting cadence — **hours between
unprompted status reports** (default 1), an optional reporting window like
`09:00-20:00`, and your timezone (guessed from the browser).

**Secrets & variables (optional).** If you already know the keys the agents
will need, add them here: a name, the value, and tick which roles receive
them. Agents get them as environment variables in their sandboxes and never
see the values in chat. You can also do this later in Settings.

If the credential *is* a file — a Google service-account key, an SSH key, a
kubeconfig — press **choose a file…** instead of typing. Granted roles then get
the **path** to it, which is what the tooling expects; the platform writes the
file into the sandbox outside the workspace and it goes away with the run. See
[Credentials that are files](INTEGRATIONS.md#credentials-that-are-files).

**Data sources (optional).** MCP servers can be connected right here too —
name, URL, optional token, roles — tested before the project starts. Or add
them later in Settings (next section).

Leave **"Start immediately"** checked and click **Create project**. (It
needs a credential — without one the project is created stopped.)

## Step 2 — Connect billing (if you skipped it at creation)

Open your new project → **Settings** → "How agents pay for Claude". Pick a
credential from the list, click **Save assignment**, then **Test connection**
— if the key is bad, you find out now, not at 9am tomorrow. No credentials in
the list? An administrator adds one under Settings → Providers; a
subscription credential also shows there what Anthropic says is left on its
plan.

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

Those variables are listed under *Secrets & variables* in a locked group, "Set
up by your data sources", with a link back here — they belong to the source and
are edited with it. One data source is one thing, edited in one place.

### Changing one

Open the row's **⋯** menu and press **Edit**. The stored address is fetched and shown in full —
an address is not a password, and you can't fix a port you can't see. Change
what you need and save; leaving the token box blank keeps the current token,
so a URL-only edit never disarms the auth header. There's a tickbox to remove
a token outright when a server stops needing one.

## A note on secrets and who can see them

You are the chairman. Whoever can change a project's credentials can read them
back — you and your co-chairs, never an observer — and the box's own
credentials are the instance admin's. Every stored project value — service
credentials, a data source's address and token, the Slack tokens, the webhook
signing secrets — is masked in the UI and one press of **Reveal** from being
readable, with a copy button and **Replace value** to rotate it; each reveal is
logged to that credential's history. A value you mark **Plain value** (a
timezone, an account id) is shown as it is instead, to the same people.
Debugging a connection shouldn't require a database client. The
LLM credentials projects pay with live above projects, under app-wide
Settings, where they can be renamed and rotated but are never shown back.

That is a *display* choice for you, and it changes nothing about the agents.
A role still receives only the credentials you granted it, still as
environment variables in its own sandbox, and is still told the variable's
*name* and never its value. The two are different doors: the eye lives behind
your own sign-in, and no agent can reach it.

One credential the eye cannot help with: a **binary** file credential — a
`.p12`, a DER certificate. It says its size and that it is not text, because a
screenful of base64 debugs nothing. A text one (a service-account JSON, a PEM)
reveals as its own text and can be edited in place to rotate.

## Step 4 — Follow the checklist

Go to the project's **Team** page. If anything about the setup is wrong or
missing, a **Setup & health** card sits at the top naming each problem in
plain English with a link to fix it — a dead or missing billing credential, a credential that
should be a URL but isn't, a team running an outdated role version. Fix the
red **problem** rows; amber **suggestion** rows are advice you can act on or
put away with **Got it**. When the card collapses to one green line, the project
is properly configured.
That's the rule to remember: **no card = ready.**

## Step 5 — Say hello

Open **Chat** and talk to the CEO like you'd talk to a new hire on
day one. It already knows the goal; give it whatever else a person would
need:

> The data source is connected — engineers have it as "ppc". The customer
> cares most about wasted ad spend. Deadline is firm. Ask me here if you're
> blocked; I check in mornings and evenings.

Then let it work.

**In a Deliver or Operate project, the next thing that happens is the plan.**
The CEO proposes requirements — things that must be *true* at the end, each
with an acceptance test and an estimate — and they appear on the **Roadmap**
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

In an Explore project there is no plan and no Roadmap page. The CEO states a
falsifiable bet with a kill date instead, and the evidence rules do the rest.

You can watch all of it in **Roadmap**, **Tasks** and **Team**, and in
**Activity** when you want the receipts.

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
  project that's waiting on you says so.
- **Decide approvals.** Any spend over the threshold you set lands in a
  banner across the top of every tab, so you cannot miss it by being on the
  wrong page. Undecided approvals expire to "no" after a week — the
  project degrades gracefully while you're on vacation; it never deadlocks.
- **Spot-check.** **Tasks → Check the agents' work** deals you a random sample of
  recent completed work and evidence claims. Fail anything that doesn't hold up —
  the CEO is told its team's work is suspect. You are the one reviewer that
  doesn't approve everything put in front of it.
- **Watch the money if you want to** — the money line on the project's Inbox warns at
  80%. Crossing 100% does **not** stop the project — it is a plan, and going
  over obliges the CEO to account for what the extra bought. The wall is
  `hard_stop_multiple` times the plan (3x by default), and that is the only
  thing that pauses anything. Set `stakes` to say how brave to be about the
  difference: a project trading real money should not hesitate the way one
  posting to LinkedIn should.

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

1. New project → goal above → **Deliver** → credential picked → three one-person
   teams (researcher, engineer, writer) → `ppc` data source + engineer role →
   budget cap → Create.
2. (Or later: Settings → billing → pick a credential → Save assignment → Test.)
3. (Or later: Settings → Data sources → `ppc` + the MCP URL + engineer
   role → Save, auto-tested.)
4. The project's Inbox → fix anything the health card names → card disappears.
5. Chat → brief the CEO.
6. Roadmap → accept the requirements you want, cut the rest, sign the date.
7. Done. Check back in the morning.
