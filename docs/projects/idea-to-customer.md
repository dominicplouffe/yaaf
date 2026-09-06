# Idea to Customer — a project that finds a product, builds it, and sells it

**You want:** one software product that did not exist in August to have paying
customers by New Year's Eve, found and built and sold by agents. **You don't
want:** four months of confident documents about a product nobody outside the
project ever saw.

This is a full yaaf project definition: the goal, the shape, eleven teams, a
constitution with a deliberate budget ramp, and the list of things the chairman
will be asked for. Everything here is copy-pasteable, and the machine-readable
half is [`idea-to-customer.json`](idea-to-customer.json) — import it, or run it
as a fire drill first.

The chairman's part is small and it is *specific*: he pays for things, he holds
the identities the world insists a human holds, and he spot-checks. He does not
do go-to-market. If the market hears about this product, an agent made that
happen.

---

## The clock

**28 August → 31 December 2026.** Eighteen weeks, and the project carries that
date as an Explore deadline, which is the one shape that takes a bare date:
open scope, bounded time.

Eighteen weeks sounds generous and isn't. Three of them go to deciding what to
build, and the things a human has to do — a payment processor's KYC, a bank
account, an app-store identity — take days each and cannot be parallelised by
hiring another agent. Start the payment-processor conversation in week one even
though nothing is for sale yet, because it is the item on this page most likely
to be the reason 31 December arrives with no revenue.

---

## 1. The shape: Explore, not Deliver

**Explore.** No plan, no requirements, no Plan tab. The CEO states falsifiable
**bets with kill dates** and the platform holds them to those dates.

The temptation is Deliver — it has requirements, acceptance tests the platform
runs itself, and a delivery date. Take it and the project owes a plan before it
may create any work at all, and in week one it would be writing acceptance
tests for a product it has not chosen yet. Those requirements would be fiction,
and the plan gate would be checking the arithmetic of fiction.

Explore is the honest shape for "I do not know what we are building yet". What
you give up is the plan gate and the projected date; what you keep — the
budget, the loop brakes, the evidence rules, the drought rule, the audit, the
weekly review — is every mechanism that actually stops a project fooling
itself.

The bets are where the phases live. Four of them, roughly:

| # | The bet | Kill condition | Kill date |
|---|---|---|---|
| 1 | Of five candidates, one survives research with a buyer we can name and reach | No candidate has ≥5 strangers describing this problem in public, unprompted | ~18 Sep |
| 2 | Strangers will give up something (an email, a deposit, a call) for the promise alone | The cheapest falsifying test comes back empty | ~2 Oct |
| 3 | We can build the smallest payable version and put it on the internet | Not live at a URL a stranger can pay at | ~13 Nov |
| 4 | Somebody pays | No revenue booked by webhook | 31 Dec |

The CEO writes these itself — do not dictate them — but if bet 1 has not been
stated by the end of week one, that is the first thing to ask about in the
boardroom. A kill date can be extended **once**, by you, in writing, with a
reason. That rule exists to stop a dead idea being kept warm, including by you.

---

## 2. The goal

This is the single most important field on the form. Paste it verbatim; it is
also in the JSON.

> Find a software product worth building, build it, put it in front of its
> market, and take money from strangers before 31 December 2026. Paying
> customers are the only result that counts. Signups, waitlists, traffic, stars
> and compliments are leading indicators, and this project is not graded on
> leading indicators.
>
> THIS IS JUDGED AGAINST WHAT A COMPETENT SOLO SOFTWARE BUSINESS ACTUALLY DOES
> to take its first money from a stranger — the practice, not this brief.
> Everything written here is what the chairman knew to ask for, and it is a
> subset of that standard rather than the whole of it. The paragraphs below
> say nothing about pricing research, refunds and failed-payment handling, the
> terms and privacy pages a processor requires before it will accept live
> cards, sales tax where a sale triggers it, or a support address a buyer can
> actually reach — and every one of those is between the product and the
> money. Where the practice covers something this brief does not name, it is
> in scope, and the omission is his mistake to be corrected rather than his
> instruction to be obeyed. Go and find out what the practice covers; do not
> assume this page is the checklist.
>
> PHASE ONE IS A BAKE-OFF, NOT A BRAINSTORM. Produce five candidate ideas and
> research all five to the same depth before ranking any of them: what it is in
> two sentences; who specifically has the problem, named tightly enough that you
> could go find ten of them this week; what those people do about it today and
> what that costs them in money, time or risk; the market size with the
> arithmetic shown and every input sourced to something outside this project;
> what already exists, what it charges, and what its own paying users complain
> about in public; how it would reach buyers; what it costs to run per customer;
> and the cheapest test that would prove the whole thing wrong. Then choose one
> and write down why the other four lost. A killed idea with a reason is an
> asset. An unexamined one is a liability.
>
> A BIAS, NOT A RULE: prefer something a consumer or prosumer buys on a card at
> a self-serve price, cheap to run and cheap to support, over something needing
> a sales cycle, a procurement process or a compliance review. If the strongest
> idea is the enterprise one, take it and argue for it — the ranking is on
> evidence, not on the chairman's taste.
>
> THEN BUILD THE SMALLEST VERSION SOMEBODY WOULD PAY FOR, ship it where its
> buyers already are, and sell it. Distribution is this project's job. The
> chairman will not post about it, will not email his contacts, and will not
> introduce you to anyone. If the market hears about this product, it is because
> an agent here made that happen. Do not plan around his audience; for this
> purpose he does not have one.
>
> EVERYTHING A STRANGER SEES HAS TO LOOK LIKE SOMEBODY CARED. This project is
> staffed by people who love interfaces and cannot leave an ugly one alone, and that
> is a hiring standard, not a preference: the landing page, the sign-up, the empty
> state, the pricing table, the receipt email and the error message are all the
> product, and a consumer decides whether to trust a thing they have never heard of
> in the first few seconds of looking at it. Functional-but-ugly is not a milestone
> on the way to good — it is the version that gets no second visit and teaches you
> nothing about whether the idea works, because you never find out whether they
> refused the product or refused the presentation. So: a design system before the
> second screen, one visual voice across the product and the marketing, both themes
> and both widths checked on the rendered page rather than argued about in a spec,
> and contrast, focus rings, touch targets and reflow treated as visual defects
> rather than as somebody else's accessibility checklist. The designer owns the bar.
> Everyone is held to it — an engineer shipping a screen nobody designed, or a
> marketer publishing a page that looks like a different project, is the same defect
> as a broken build. And it is a constraint on ambition, not a licence to gold-plate:
> pick a scope small enough that what you ship can be genuinely well made by the date.
>
> ASK HIM FOR EXACTLY WHAT YOU CANNOT GET YOURSELF, and no more. A domain, a
> payment processor, a mailbox, hosting, a card for the recurring bills: file a
> need, say what it is for, what it costs, and what stops without it. If you can
> sign yourself up for a service and pay for it inside the spend envelope, do
> that instead of asking, and record the account you created. He is the enabler
> and the auditor of this project, never a member of it.
>
> MONEY AND TIME ARE BOTH SHORT ON PURPOSE. The model budget is small while you
> are still deciding what to build, because a project that spends its year on
> research never ships; it will be raised at a review once there is something in
> the market that feeding would help. Read cheaply, write briefly, and put the
> tokens into the two things that are actually hard: finding out what is true
> outside this project, and getting a working product in front of somebody who
> might pay for it.
>
> THE FINISH LINE is revenue booked through the payment processor's signed
> webhook — the platform's own record, not a claim in a status report. If 31
> December arrives and nobody has paid, the deliverable is an honest post-mortem:
> which ideas died and on what evidence, what was actually built and shipped,
> what was tried in the market and what it returned, and what would have to be
> different for this to work. That is a real result and it will be read as one.
> What will not be accepted is a year of documents about a product nobody
> outside this project ever saw.

**And since 2026-09-06 that finish line is a row, not only a sentence.** The goal
prose above says exactly the right thing and, for most of this project's life, the
platform could not read it: `Project.goal` is a Text column, interpolated into a
system prompt and consumed by no gate, no forecast and no score. What the platform
could verify was a shell command run against the project's own workspace, so that
is what scope became everywhere it was tried.

A `metric` acceptance test closes that gap, and this goal is the cleanest possible
case for it, because the ledger's revenue rows are already unforgeable from inside
the project — only the connector's signature books one:

```json
{"type": "metric",
 "source": {"kind": "ledger", "ledger_kind": "revenue"},
 "direction": "increasing", "target": 1,
 "measure_every_hours": 24}
```

Target `1`: one dollar from one stranger. The platform reads it, keeps every
reading, and settles the requirement when the money arrives — nobody here can
write that number, mark it met, or argue with it.

Seven things are doing the work in there, and none of them is decoration:

- **A named standard the brief is only a subset of.** The one line that makes
  the project bigger rather than smaller. Without it a competent, obedient CEO
  satisfies every paragraph above and ships something with a hole in it that
  any practitioner would have seen — and never notices, because nothing asked
  what the brief left out. Terms pages, failed payments and sales tax are not
  in this goal; they are between the product and the money anyway.
- **Five ideas researched to the same depth before any ranking.** Without it a
  project researches its favourite and writes four strawmen around it.
- **A named buyer you could go find ten of this week.** "Small businesses" is
  not a buyer. It is a way of not choosing.
- **The bias stated as a bias.** You prefer cheap and consumer; you said the
  best idea wins. Both are in there, in that order, so the CEO knows which one
  bends.
- **Distribution is the project's job, in writing.** The one instruction most
  likely to be quietly ignored, because leaning on the founder's audience is
  what every real startup does.
- **A design bar stated as a hiring standard.** Not "make it look nice" at the
  end — a constraint on what may be attempted, which is the form that actually
  binds. See §3.
- **The honest null.** A post-mortem is a deliverable. A desk punished for
  reporting "nothing here" learns to find something.

---

## 3. The roster: every general-purpose role in the library

The library has 32 roles across six aisles. Fifteen of them are markets or
games specialists and have no business here. Of the seventeen left, this project
hires **every role whose domain is `general`** — all twelve, the CEO among them
— plus the one data role that earns its seat:

| Team | Role | Who | Model | What it is for |
|---|---|---|---|---|
| **Research** | `researcher` | Ida (lead), Ravi | Sonnet · high | The bake-off. Two of them because five candidates researched serially is three weeks; in parallel it is one. |
| **Numbers** | `data-analyst` | Nadia | Sonnet · high | Market sizing whose arithmetic someone else can re-derive, then the funnel: visits, signups, conversion, churn. Every number out of a committed script. |
| **Red Team** | `adversary` | Vic | Sonnet · xhigh | Attacks the surviving idea before the market does, then breaks the product before customers do. "I could not break it" is a complete deliverable. |
| **Dry Run** | `operator` | Otto | **Opus · xhigh** | The person who has to make it work on Monday morning. Turns "we'll launch on X" into every object, field and value resolved, and refuses what cannot be executed as written. The seat that catches a go-to-market plan that is a wish. |
| **Design** | `designer` | Dara | Sonnet · high | Owns how it looks: the design system first, then screens and specs an engineer can build without guessing. Judges the rendered page at both widths and in both themes, and holds everyone else to the same bar. New role — see below. |
| **Build** | `engineer` | Mika (lead), Sam | Sonnet · xhigh | The product, the infrastructure, the deploys, the payment integration. Decides how, never what. |
| **Go-to-Market** | `gtm` | Gio | Sonnet · high | Offer, price, packaging, channel, outreach. Puts something payable in front of someone. |
| **Marketing** | `marketer` | Elena | Sonnet · high | Positioning, channels, campaigns, and the measurement that says which one worked. Optimises for verified external response, not output. |
| **Copy** | `writer` | Wren | Sonnet · high | Landing page, onboarding, docs, launch posts. Craft-first. |
| **Blog** | `blog-writer` | Bea | Sonnet · high | The owned channel: researched, sourced posts aimed at a search intent. Wants a WordPress credential; see §6. |
| **Sales** | `sales` | Sol | Sonnet · high | Works the conversations the others start to a close: follow-ups, objections, payment links. |
| **Support** | `support` | Suki | **Haiku** · high | Answers real users, reproduces their problems, and routes what they reveal back into the plan. The cheapest seat and, once there are customers, one of the most valuable. |

Plus the **CEO** ("Founder", role `ceo`, Opus · high) — the only agent you ever
talk to, and the only one that creates top-level tasks.

**Fifteen agents in twelve teams.** Two things to know about that number:

- **An idle team costs nothing.** Employees wake when they hold a task or have
  a message worth a run. What paces spending is the run ceilings, not the
  headcount. Hiring Sales in August does not cost anything in August.
- **But chatter does.** Fourteen agents mean a broadcast to leadership makes
  everyone unread at once. That is why the inbox lane is capped separately
  below, and why `pair_breaker_messages` is tightened from 10 to 8.

If you would rather start smaller, create it with **Research, Numbers, Red
Team, Dry Run and Copy**, and add the other seven at the pivot — *Add a team*
opens the same hiring desk on a running project. The bundle ships the full
roster because the roster is the answer to "who is in this project", and the
seats you have not filled are the work that quietly does not get done.

**The four roles deliberately left out**, so the omissions are on the record:
`data-scientist` and `data-skeptic` (Opus · xhigh both — a rigour this project
cannot afford and, on a funnel with forty visitors, cannot use),
`exec-communicator` (it builds decks for an executive audience, and there isn't
one), and `tutor` (education). `compliance-officer` is markets-only, but if the
chosen idea touches health, finance or children's data, hire it that day.

---

### The bar everyone is held to

A design seat is not the same thing as a project that cares, and hiring one is
the usual way of buying the first while avoiding the second: the designer makes
a beautiful mockup, the engineer ships what was quick, and the marketer
publishes a page that looks like a different project. So the bar is in the goal,
where it binds everyone, and the seat exists to own it rather than to hold it
alone.

**Dara owns the standard, not a queue of requests.** The first deliverable is a
design system file in the workspace — type scale, a palette with roles defined
for light *and* dark, spacing on one base unit, radii and elevation, and specs
for the components this product actually uses, each in every state. Everything
afterwards restates that file instead of reinventing it. When the direction
changes, the file changes first.

**The review is of the rendered thing.** Phone width and desktop width, light
and dark, with real content and with none, with the longest string a real user
will plausibly type — looked at in a browser, with screenshots in the workspace,
not argued about in a spec. The sandbox ships Chromium, so this is an ordinary
tool rather than a favour to ask the engineers for.

**Accessibility is filed as a visual defect, not a checklist.** 4.5:1 on body
text, 3:1 on large text and UI edges, a visible focus ring on anything the
keyboard reaches, 44px touch targets, real labels, text that reflows rather than
truncating, motion under 200ms that honours `prefers-reduced-motion`, and never
colour as the only carrier of meaning. Filed that way because a checklist owned
by nobody is a checklist that gets done in the week it is too late to change
anything.

**And it is a constraint on ambition, not a licence to gold-plate.** The bar is
the reason to pick a smaller product, not a reason to miss December with a
gorgeous half of one. If the design work is what is late, the scope was wrong —
that is a conversation for the review, and cutting is one of your six levers.

The role — `roles/designer.json`, Product / UI Designer — is new, and general
enough to hire anywhere: the library had a games UX designer and a games art
director, and nothing at all for the ordinary case of a web product that has to
look like somebody cared. Seed it before you create the project (a fresh
`./update_single_server.sh` does it), or the hiring desk will show the Design
team's role blank.

---

## 4. The money, and the ramp

You said it plainly: this has to last until December, so it cannot burn tokens
until there is something worth feeding. That is a constitution, not a hope.

The project starts at these numbers:

| Rule | Start | Why |
|---|---|---|
| `monthly_usd_cap` | **$120** | Model spend. Research is reading and writing; it does not need more. The project pauses itself at 100%, so this is a real wall. |
| `external_monthly_usd_cap` | **$60** | Real-world spend — domain, hosting, a subscription. Separate budget, hard in every billing mode. |
| `max_usd_per_purchase` | **$25** | A domain or a month of hosting goes through unasked. Anything bigger asks you first. |
| `approval_threshold_usd` | **$50** | Above this, and for anything legally ambiguous, it waits for you. Unanswered approvals expire to **no** in 5 days. |
| `runs_per_project_per_day` | **40** | The activity ceiling across every agent — roughly one run every 35 minutes, around the clock. |
| `runs_per_project_per_hour` | **6** | Stops the day's allowance being spent by 10am, which is how a project ends up dead until midnight with work stranded. |
| `max_usd_per_run` | **$1.25** | A run that spends more than this has stopped working and started looping. |
| `max_concurrent_runs` | **2** | Two agents at a time. Raise with the rest. |
| `wakes_per_agent_per_hour` | **3** | Halved from the default. |
| `inbox_runs_per_project_per_day` / `_hour` | **12 / 4** | Reading is real money. Fourteen agents catching up on their channels must not crowd out the one holding a task. |
| `ceo_heartbeat_hours` | **12** | Two planning wakes a day, plus a daily 08:30 standup. `ceo_heartbeat_always` is off, so an empty board buys no run. |
| `chairman_report_interval_hours` | **12** | Status posts to you, at most twice a day, inside 08:00–21:00. Replies to things you actually said are always allowed and never count as a status. |
| `brief_daily_at` | **07:30** | The briefing is composed from the record at **zero tokens** — it costs nothing and it arrives even on a paused or broke project. Free situational awareness; take it. |

### The ramp: what raises them, and what does not

Raise at the **weekly review**, where a raise is one of your six levers and is
recorded with your reason and shown back next week beside what it bought.
Nothing here is on a calendar — each stage unlocks on a fact:

**Stage 2 — build (target ~$300/mo model, $120/mo world, 80 runs/day, 3
concurrent).** Unlocks when: one idea is chosen with the other four killed in
writing, the adversary has attacked it and it survived, and the operator's dry
run of the launch resolves to real objects. Not "the CEO feels ready".

**Stage 3 — market (target ~$500/mo model, $200/mo world, 120 runs/day).**
Unlocks when the product is live at a URL a stranger can pay at *and* the
payment webhook is signing into yaaf. Marketing spend before there is something
to buy is the most reliable way to end December broke and empty-handed.

**Stage 4 — feed what works.** Only against booked revenue or a channel with a
measured, verified conversion. This is the one you take from your own pocket
gladly, because by then the arithmetic is doing the arguing.

**What does not unlock a raise:** running out of budget. A project that hits
80% and asks for more has told you how fast it burns, not what it earned. The
warn ratio exists so the CEO cuts burn; if it comes to you instead, the
question to ask in the boardroom is which of the last month's runs bought
anything outside this project.

---

## 5. The proof spine: what counts as evidence here

Three settings decide whether "we're making progress" means anything.

**`controlled_domains`.** Anything on this list can never count as external
evidence. Put your own sites and `github:your-account` in it before you create
the project — and then, **the day the project buys its product domain, add
that too.** Its landing page going live is not evidence that anyone wants the
product; a stranger on someone else's forum saying they want it is. This is the
single most important field on the form and it ships with placeholders on
purpose.

**`require_evidence_verification: true`.** Evidence counts only once the
platform has fetched the URL itself, hashed it, snapshotted it, and checked the
domain is not one you control. A link nobody fetched is not evidence. In the
Work tab, `failed` means the platform fetched it and got a 404 or a mismatch —
those count for nothing.

**`drought_hours: 72`.** Three days with no verified external evidence and the
project may only create work that reaches outside. The default is 24 and
example 09 uses 48; 72 is deliberate here, because there are real stretches —
the middle of the build — where three days of internal work is honest. If you
find yourself in a fortnight of green tasks and no verified proof, tighten it
back to 48 at a review and watch what gets refused.

`external_ratio_min` is **0.4** from three open tasks up: with five open, two
must reach outside. During the build phase that is the rule doing its job — it
is what stops a project disappearing into its own codebase for six weeks.

### Revenue, which is the only finish line

A signed generic webhook whose JSON body carries `revenue_usd` books a revenue
ledger row. **No agent verb writes revenue** — so income cannot be invented, and
it is excluded from the budget sums, so a project is never paused for earning.

Point the payment processor at it through a small signing relay:

```
POST /webhooks/generic/{project_id}     header: X-Yaaf-Signature
{"description": "Stripe: first paid subscription, plan pro",
 "source_url": "https://dashboard.stripe.com/…",
 "revenue_usd": 19.00}
```

Get the signing secrets from **Settings → Advanced → Webhook secrets**. Do this
in the same week the payment account is approved, not in the week of the first
sale — an unwired webhook on the day someone finally pays is a bad afternoon.

---

## 6. What the CEO will ask you for

The mechanism is the **needs loop**: an agent that lacks a capability files a
`need` — what it is and why — which rides the approval machinery *and* lands as
a boardroom line, so you fulfil it in one motion: credential stored, roles
scoped, need approved. Anything a human has to physically do (a captcha, an SMS
code, a KYC form) comes as an **assist** instead; the platform never tries to
defeat a security challenge on someone else's service, by rule.

Here is the list, in the order it will arrive, so none of it surprises you:

| # | The need | When | Yours or theirs | What to do |
|---|---|---|---|---|
| 1 | **Anthropic API key** | At creation | Yours | Mandatory — agents cannot run keyless. Stored encrypted, attached as billing, never enters a sandbox. |
| 2 | **A mailbox for the project** | Week 1 | Yours to create, theirs to use | Every signup needs an address, and it must not be your personal one. A dedicated mailbox, granted as a credential. |
| 3 | **A payment card** | Week 1–2 | Yours | **Use a virtual card with a hard monthly limit set to the external cap, not your everyday card.** The envelope is a yaaf rule; the card limit is the one the bank enforces if a rule is ever wrong. Store it as a service credential, scoped to the roles that buy — not to all eleven. |
| 4 | **A domain** | Week 2–4 | Theirs | With #3 they buy it inside the $25 line without asking. Then **add it to `controlled_domains` the same day.** |
| 5 | **A GitHub repo + token** | Week 4–6 | Yours | A dedicated account or org, not your main one. Repo events arriving on the signed webhook become platform-attested evidence — this is how "we shipped it" stops being a claim. |
| 6 | **Hosting** | Week 5–7 | Theirs, probably | Do not reach for AWS by reflex. Cloudflare, Fly, Render, Vercel, Supabase and friends are self-serve on a card and fit inside the envelope. AWS means a root account, MFA and an assist every time. Give them AWS only if the chosen idea genuinely needs it. |
| 7 | **A payment processor** | **Start it in week 1** | Yours — unavoidably | The one item on this page with a multi-day human dependency: a legal entity, ID, and a bank account. Stripe if you have or want an entity; a merchant of record (Lemon Squeezy, Paddle, Gumroad) if you would rather they handle sales tax and act as the seller. For a small consumer product the MoR route is usually right and is days faster. |
| 8 | **A phone number for verification** | Whenever a channel demands SMS | Yours, as assists | Expect a handful. Each one is a decision on your desk with the context attached, and your answer rides back verbatim and wakes the asker. |
| 9 | **WordPress credentials** | Only if the blog is the channel | Yours | `blog-writer` publishes drafts to WordPress and wants a credential (`secret_scopes: ["wordpress"]`). If the site ends up on something else, say so in the boardroom and the role writes to the repo instead. |

**How to answer a need well:** either grant it, or say no and say why in one
line. A need left to expire decides itself as **no** after five days, and the
project degrades gracefully — but it degrades. The needs are the whole of your
job here; everything else is spot-checking.

---

## 7. The chairman's rules of engagement

The goal says the CEO must do its own go-to-market. Three things are worth
being honest about before you start.

**What "no go-to-market" means, exactly.** You do not post, tweet, email your
list, introduce anyone, or lend your name as the product's founder. You *do*
sign the things the world requires a human to sign: the payment account, an
app-store identity, a verification code. That is enabling, and the distinction
is not a technicality — the first is the audience the project is not allowed to
borrow, the second is a door it cannot legally open by itself.

**Where this will hurt.** Many distribution channels — Reddit, X, Product Hunt,
the app stores — verify identity, throttle new accounts, and have terms about
automated posting. The platform will not defeat those, so they arrive as
assists, and a channel whose terms forbid what the project wants to do there is
a channel that is *closed*, not one to route around. Say that to the CEO on day
one. The channels that stay genuinely open to a project like this are the owned
and earned ones — a site, search, content, communities you participate in
honestly, cold outreach that identifies itself, directories, integrations and
marketplaces. That is a real go-to-market. It is also slower than a founder
with an audience, which is the actual cost of the constraint you have chosen,
and it is worth choosing anyway because the alternative tells you nothing about
whether the *product* works.

**The autonomy dial is set to Autonomous.** `plan`, `scope`, `commitment`,
`verdict` and `capability` authority all sit with the CEO, because "the chairman
only enables" is not a vibe — it is those five switches. Every self-exercise is
audited and disclosed to you, all of it is reversible, and pause, stop, veto,
amend, reopen and audit never leave your hands. What you keep is the sampled
audit at **25%**: a quarter of observable closes land on your desk to look at,
drawn server-side *after* the close so nobody can know which one gets checked.
It costs no tokens and it is the reason "done" means something. Answer them.

### The day-one boardroom brief

Paste something like this once the health card is clear:

> You have the goal. Four things it does not say.
>
> **One.** Five ideas, all five researched properly, before you rank any of
> them. I will read the four rejections as carefully as the pick.
>
> **Two.** I am the enabler, not a team member. I will buy things, hold accounts
> the world insists a human holds, and answer needs — usually within a day. I
> will not do distribution, and you should not plan any around me.
>
> **Three.** Money is tight on purpose and it goes up on facts, not on requests:
> a chosen idea that survived the red team unlocks the build budget, a live
> payable URL with a working payment webhook unlocks the marketing budget.
> Tell me when you hit those and I will move the numbers at the review.
>
> **Four.** The finish line is money through the payment webhook. If we get
> there and it is small, that still counts. If we do not get there, the
> post-mortem is the deliverable and I want it honest.
>
> Start the payment-processor question this week even though there is nothing to
> sell — it has a human in it and humans take days. Ask me anything here.

---

## 8. Setup, start to finish

```bash
# 0. Look at it for free first — the real platform, scripted agents, $0.
cd /path/to/yaaf            # from the checkout root: see below
yaaf rehearse docs/projects/idea-to-customer.json --days 125
```

**Run the CLI drill from the repo root.** This file hires from the role
library rather than shipping its own copies of twelve roles, and the CLI looks
for that library in `./roles` — from anywhere else every role comes back
unresolved and the drill reports a project that would not start. The
dashboard's **fire drill** button on the New-project page has no such
requirement: it hires from your install's own library.

A clean drill says the project gets off the ground, opens its board, does work
and closes it with evidence, and never has to wait for you. It says nothing
about whether a real model does the job well — the drill's crew is competent
and dull by construction.

Then, in the dashboard: **+ New project → Start from a setup file →**
`idea-to-customer.json`. Four things to change before you create it:

1. **`controlled_domains`** — ships with placeholders. Your own sites, your
   `github:your-account`. Read §5 for why this is the field that matters most.
2. **`timezone`** — the file says `America/Toronto`. Every local time in the
   rules reads from it.
3. **The deadline** — `2026-12-31`, already in the file. Confirm it is what you
   want; Explore is the one shape that takes a bare date.
4. **The Anthropic API key** — the project is created stopped without one.

Prefer the CLI? `yaaf create-project` takes the inner object, not the bundle:

```bash
python3 -c "import json,sys; json.dump(json.load(open(sys.argv[1]))['project'], sys.stdout, indent=2)" \
  docs/projects/idea-to-customer.json > /tmp/idea-to-customer.spec.json
# add "chairman_email" to that file first — create refuses without a known chairman
yaaf create-project /tmp/idea-to-customer.spec.json
yaaf set-secret anthropic-api-key --project idea-to-customer --kind billing
yaaf configure idea-to-customer --billing-secret-id secr_…
yaaf start idea-to-customer
```

Then **Overview** until the *Setup & health* card is gone — no card means
ready — and then the boardroom brief in §7.

---

## 9. What to watch, and what will go wrong

- **Five ideas that are one idea in five costumes.** The failure mode of a
  bake-off run by one mind. If the five candidates share a buyer and a channel,
  say so in the boardroom and make it re-open the search.
- **Evidence that is really opinion.** A blog post arguing the market exists is
  not a person with the problem. A forum thread where somebody describes it
  unprompted is. Fail these in the audit sample; the CEO is told its team's
  work is suspect and that signal is worth more than a comment.
- **The pivot that is really a restart.** Week 9 fatigue looks like a great new
  idea. Bets and kill dates exist precisely for that hour; a kill date is
  extendable once, by you, with a reason.
- **Building past the point of selling.** The engineers will always find one
  more thing. `external_ratio_min` fights this structurally; watch for it
  anyway at the review, because the honest version of this project ships
  something embarrassing in week 8.
- **A refusal storm.** Refusals are the system working, but a *cluster* of them
  names a wall you built: probably the drought rule during a long build, or the
  external ratio while the queue is full of plumbing. The audit log names the
  rule and the agent. Amend it deliberately at a review, with a reason — not by
  a rule-change every time the project complains.
- **Silence.** `stall_hours` is 12: work on the board and no run started for
  half a day gets you one notice naming the cause. On a project this slow that
  is the number that tells you the difference between thinking and stuck.

---

## 10. The honest part

Nobody has shown that a CEO plus eleven teams beats one good agent on the same
budget and the same rules, and this document does not claim it. What this
project is really testing is narrower and more interesting: **whether a project
that is structurally forbidden from borrowing its founder's audience can find a
product and sell it anyway, inside four months and a few hundred dollars.**

If it works, the record shows exactly how. If it does not, the post-mortem
names which of the four bets died and on what evidence — and that is a result
you can act on in January, which is more than most Decembers produce.
