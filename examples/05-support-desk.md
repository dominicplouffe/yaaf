# 05 — A support desk that feeds the roadmap

**You want:** the people who use your product to get a real answer within the
hour, and the patterns in what they hit to reach whoever decides what gets
built. **You don't want:** a bot that cheerfully invents a workaround that
doesn't work, or a support queue that's a black hole nobody mines.

This example is also the best one for the Slack bridge, because support is the
work you most want to watch from your phone.

## The org

| Team | Role | Members | Does |
|---|---|---|---|
| Support | `support` | Ren (lead), Kai | Reproduces, answers, records what users reveal |
| Fixes | `engineer` | Mo | Small fixes the support team surfaces, once the CEO agrees they're worth it |

The `support` role runs on `claude-haiku-4-5` by default — support volume is
high and most tickets are not hard. Change it in `roles/support.json` and
re-seed if your product's problems are gnarlier.

## The goal

> Run first-line support for Acme. Every reported problem is reproduced before
> it is answered — never suggest a fix you have not seen work. Reply within one
> business hour during 9–6 ET. When three or more users hit the same wall, file
> a suggestion for the CEO with the observed count and the exact reproduction,
> not a summary impression. Be honest about what is broken: "this is broken and
> here is when it won't be" beats a workaround that fails on the second try.
> Success is measured in resolved reports and in problems that reached the
> roadmap, not in tickets closed.

The "three or more" is deliberate. It converts anecdote into signal and gives
the CEO something it can prioritize against.

## Wire Slack

Settings → **Slack bridge**. You'll need a Slack app with:

| Field | Where it comes from |
|---|---|
| Bot token (`xoxb-…`) | OAuth & Permissions |
| Signing secret | Basic Information |
| App token (`xapp-…`) | App-Level Tokens — this one enables Socket Mode |
| Your Slack member ID (`U…`) | Your Slack profile |

Then map channels: a **boardroom** channel for you ↔ CEO, a **leadership**
channel, and one per team room. Invite the bot to each.

Two things worth understanding:

**Socket Mode means no public URL.** You can run yaaf on the machine under your
desk and still chat with your CEO from Slack on the train.

**Only your member ID gets through inbound.** Messages from anyone else in the
boardroom channel are mirror-only — a colleague can read the conversation and
cannot instruct your CEO. Leave the field blank and the bridge is outbound-only.

The bus in Postgres stays the source of truth; Slack is a mirror plus your
inbound path. Mapped channels mirror outward in about 15 seconds, and a Slack
outage never blocks the company — it just goes quiet in Slack.

## Where the tickets come from

yaaf doesn't ship a helpdesk. Give the support team an inbox it can reach and
tell it in the goal:

- **A shared mailbox via IMAP/API** — add credentials as a scoped secret, and
  the support role reads and replies to it.
- **An MCP server fronting your existing helpdesk** (Zendesk, Intercom, Front,
  a homegrown DB). Settings → Data sources, granted to the support role only.
  This is the cleanest path if you already have a ticket system.
- **A Slack channel your users are in**, mirrored through the bridge.

Whichever you pick, name it in the goal so the CEO plans around it: *"Reports
arrive through the connected 'helpdesk' data source; check it every wake."*

## The rule that saves you from the nightmare scenario

Every reply to a user is an **external side effect**, and the role records it
before it sends, with one idempotency key per reply. If a run crashes after
sending and the runner retries it, the ledger says that message already fired
and the user does not get the same apology twice.

This is not a nice-to-have in support. It's the difference between an agent
system you can point at real humans and one you can't.

## Complaints are external evidence

Here's the pleasant inversion. In most projects, external evidence is scarce and
hard-won. In support, it arrives all day: a user's complaint, question, or
thanks is a real signal from a real person outside the project, recorded with
its source. This is the one org where `drought_hours` at its default of 24 is
exactly right, and a drought genuinely means something is wrong — nobody is
writing in, which is either an outage in your intake or a product nobody uses.

## Success tests for support work

```json
{"type": "observable", "target": "Ticket 4471 reproduced on a clean account (steps recorded in the run), reply sent quoting the exact workaround, user confirmed or 48h elapsed"}
{"type": "command", "command": "pytest tests/test_import_csv_bom.py -q"}
{"type": "observable", "target": "suggestions/2026-08-14-csv-bom.md names 3+ affected users with ticket ids and one verbatim reproduction"}
```

The middle one is the good sign: a support pattern turned into a failing test
that a fix has to make pass.

## Constitution settings that matter here

| Setting | Default | For this desk | Why |
|---|---|---|---|
| `monthly_usd_cap` | 1000 | 300 | Volume work; watch it for a month, then tune |
| `wakes_per_agent_per_hour` | 6 | 12 | A one-hour reply SLA needs more frequent wakes than a build team |
| `runs_per_project_per_day` | 150 | 300 | Ticket volume is the driver here, not the plan |
| `queue_ceiling` | 8 | 12 | Tickets legitimately queue; this isn't invented work |
| `drought_hours` | 24 | 24 | Keep the default — a real drought here is a real alarm |

Raise `wakes_per_agent_per_hour` deliberately, not reflexively. It's the
runaway-loop brake, and support is the org most likely to get two agents
thanking each other. The pair breaker (10 messages) backs you up, but the wake
cap is the first line.

## Your job

- **Read the suggestions, not the tickets.** The tickets are the team's job.
  The patterns are yours.
- **Spot-check answers against reality.** `yaaf audit support-desk --n 5`, then
  try one workaround yourself on a clean account. Five minutes a week keeps the
  desk's answers as good as your product's reputation needs them to be.
- **Decide the fixes.** The engineer team should be small and only fed by the
  CEO from validated patterns. Resist letting it self-serve from the ticket
  queue — that's how a support desk turns into an unsupervised refactor.
