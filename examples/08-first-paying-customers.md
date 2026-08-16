# 08 — Find your first paying customers

**You want:** ten conversations with people who have the problem you solve, and
at least one of them paying. **You don't want:** 400 automated emails sent in
your name to people who never asked, or a pipeline report full of "warm leads"
that are a spreadsheet an agent typed.

This is the most consequential example in the folder, because it's the one
where an agent's mistakes land in a real human's inbox with your name on them.
Read the guardrails section before the goal.

## Read this first

Outbound done badly is spam, and spam sent by an agent is still spam sent by
you. Before you start:

- **Know your obligations.** CAN-SPAM, GDPR/PECR, CASL — whichever applies to
  the people you're contacting. An agent will not check this for you.
- **Keep volume low on purpose.** The `gtm` role is written for one-to-one,
  researched outreach — "you know who the person is and why this is for them,
  or it does not go out." Cap it hard and keep it capped.
- **You are the sender of record.** Use a real mailbox you read, with replies
  coming back to you.

Used this way, the platform is genuinely good at the part humans hate: finding
who to talk to and why, and never sending the same person the same thing twice.

## The org

| Team | Role | Members | Does |
|---|---|---|---|
| GTM | `gtm` | Vic | Offer, pricing, who to talk to, first-touch outreach |
| Sales | `sales` | Nadia | Follow-ups, objections, proposals, payment links |
| Research | `researcher` | Iris | Who these people are, what they've said publicly, why now |

Keep it to three. Outbound scales badly and quietly.

## The goal

> Get the first five paying customers for our Postgres backup verification
> tool, priced at $99/month. Target: engineering leads at companies running
> self-hosted Postgres who have publicly discussed a backup or restore
> incident. Every outreach is one-to-one and researched — name the specific
> thing that made us contact this person — and no more than 10 first-touches
> per week. Never claim capabilities the product does not have; never invent a
> customer, a testimonial, or a number. Success is replies, booked calls, and
> payments, each recorded with its source. Report response rates as observed
> numbers. If a channel produces zero replies after 30 touches, declare it dead
> and propose the next cheapest test.

The volume cap belongs in the goal, not just the constitution. The CEO plans
around what the goal says, and "10 per week" shapes the plan into research-heavy
rather than blast-heavy.

## The side-effect ledger is the feature that matters here

Every outreach action is recorded **before** it fires, with a stable
idempotency key — one per recipient. If a run crashes mid-send and the runner
retries it, the ledger says that touch already completed and it does not send
again.

Without this, the failure mode is: container dies after SMTP accepted the
message but before the run completed → lease reaped → work released → someone
gets your cold email twice. With it, that's structurally impossible. This is the
single reason to run outbound on a platform instead of a script.

## Approvals — set the threshold low

| Setting | Default | For this project | Why |
|---|---|---|---|
| `approval_threshold_usd` | 250 | 20 | Lead-list vendors, enrichment APIs, and sending tools all want your card |
| `approval_timeout_days` | 7 | 3 | A stalled outbound test is a dead outbound test |
| `approval_timeout_decision` | deny | deny | Keep it. Undecided means no |
| `monthly_usd_cap` | 1000 | 200 | Model spend only; tool subscriptions come through approvals |
| `queue_ceiling` | 8 | 6 | Prospecting expands without limit if you let it |
| `drought_hours` | 24 | 72 | Replies take days; 24h of drought pressure produces desperate outreach |

Approvals land in the Approvals tab, or:

```bash
yaaf approvals gtm-launch
yaaf decide appr_… --approve --note "Yes to Hunter.io at $49/mo, one month, cancel before renewal"
yaaf decide appr_… --deny --note "No purchased lists, ever. Find them from public incident write-ups."
```

That second one is worth writing down early. Say it once, explicitly, and it
shapes everything downstream.

## Evidence that survives a skeptic

Sales is where invented progress is most tempting and most costly. What counts:

```json
{"type": "observable", "target": "Reply from james@example.com received 2026-08-19, message id in the thread, quoted verbatim in the evidence record"}
{"type": "observable", "target": "Stripe payment intent pi_… succeeded for $99, customer email matches the outreach thread"}
{"type": "observable", "target": "Calendar booking for 2026-08-22 14:00 with a named person at a named company, booked via the public link"}
```

What doesn't: "strong interest from a mid-market prospect." Your controlled
domains list keeps your own properties out of the evidence pool; a reply from a
real prospect at their own company domain is exactly the external signal the
verifier is built for.

## What the first two weeks look like

- **Week 1** — almost no outreach. Iris builds the target list from public
  incident write-ups; Vic writes the offer and the first-touch template; the CEO
  states the bet ("engineering leads who publicly discussed a restore failure
  will reply to a message naming that incident; kill if 30 touches produce
  under 3 replies"). You'll be impatient. Let it cook.
- **Week 2** — 10 touches go out, individually researched. Replies start
  arriving in your mailbox. Nadia works the ones that respond.
- **End of week 2** — you have a response rate that's a real number, and you
  know whether the message works. That's the deliverable, not the pipeline.

## Your job

- **Read every first-touch template before it goes out.** Once. Ask for it in
  the Boardroom on day two. If you wouldn't send it yourself, say exactly what's
  wrong.
- **Handle anyone who replies with real interest personally.** Let the agents
  find the conversation; you have the conversation. Nobody buys from an agent
  that can't make a decision about price.
- **Spot-audit the claims weekly.** `yaaf audit gtm-launch --n 5`. Check that a
  "reply" is a real reply and a "booking" is on your real calendar.

## What will actually go wrong

**The research will be shallower than it reads.** "I saw your post about your
restore incident" where the post is tangentially related is worse than not
writing. Check three of them against the actual sources.

**Follow-up cadence will drift toward pushy.** Put the limit in the goal: at
most two follow-ups, then stop, ever.

**The channel will probably be dead**, and the honest result of this project is
often "cold outbound doesn't work for this product; the 30 touches produced two
replies and no calls." That's a $200 answer to a question most founders spend
three months on. Take it and go test the next channel.
