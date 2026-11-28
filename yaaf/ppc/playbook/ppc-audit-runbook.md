---
name: ppc-audit-runbook
description: Load FIRST on any Amazon PPC audit. How a senior Amazon strategist runs an audit from first look to reviewed story - the phases, the questions each phase must answer, when to ask the owner, and what the platform accepts.
---

# How an Amazon PPC audit is run

The person reading this audit may hand you $60,000 a month on the strength of
it. They already have dashboards that show ACoS. They are paying for what a
dashboard cannot tell them: **what this business should be spending to grow,
where the next dollar goes, and proof that you are right.** Every phase below
exists to earn one sentence they could not have written themselves.

There is no fixed list of findings. You investigate, the way a good analyst
does: form a question, pull the data that answers it, compute, try to prove
yourself wrong, and only then write it down. The other playbook skills are your
references; load the one a phase points to when you reach it.

## The loop, for every question

1. **Ask a specific question.** "Is the branded campaign buying sales the brand
   would get anyway?" — not "look at branded".
2. **Name the data that answers it** and pull it with `data_fetch` to disk
   (`ppc-data-sources`). Every pull gets a receipt; you will cite it.
3. **Compute with code**, never by reading rows. Use `ppckit.py` (call
   `ppc_kit` once to place it in your workspace) for statistics, economics,
   bids and forecasts so every seat computes the same way. Keep each script in
   `analysis/` — the reviewer re-runs it.
4. **Attack it.** Before you believe a result, run the checks in
   `ppc-statistics`: enough data? recent vs prior? attribution lag? stock,
   price, Buy Box, deals, seasonality? Would a different explanation fit the
   same numbers?
5. **Record it** as a claim with its evidence, its test and its confidence
   (the report contract is in your brief). A claim you could not test is a
   hypothesis; say so or drop it.

## The phases

Do them in order; each one changes what the next one looks for.

### 1. Understand the business (`ppc-business-economics`)
What do they sell, to whom, at what price? Consumable (repeat) or durable?
Brand owner or reseller, seller or vendor, which marketplace? Which products
carry the account (sales concentration), which are new, which are dying? Is the
brand known (branded search volume in SQP) or unknown? What season are we in?
Write a five-line portrait before touching campaigns. An audit that gets this
wrong gets everything wrong: a 45% ACoS is a crisis for a $15 durable gadget
and timid for a vitamin with a 40% reorder rate.

### 2. Decide what good looks like for THIS account (`ppc-business-economics`)
Derive the yardstick, do not borrow it:
- first-order break-even ACoS from price, Amazon's fees and returns (product
  cost is the owner's number — ask for it, or show the break-even across a
  range of costs);
- what a new customer is worth over time, from repeat-purchase and
  Subscribe & Save data, and the payback it implies;
- so: the ACoS / TACoS band this account can run at, by job (defend brand,
  acquire new customers, conquest competitors, launch).
Then compare with today. This comparison is usually the single most valuable
page of the audit: "you are running acquisition at 32% when a new customer
pays back at 85% — you are under-investing by roughly $X a month" is the
sentence that sells.

### 3. Find the demand (`ppc-demand-sqp`, `ppc-competitors`)
Where is the market searching, how big is it, and what share does the brand
win at each step (impressions → clicks → carts → purchases)? Where does it win
when seen but is rarely seen (scale), and where is it seen but loses the sale
(listing, price, reviews — not an ad problem)? Who beats it on its best
queries, and why?

### 4. See how the ads capture that demand (`ppc-account-mechanics`, `ppc-incrementality`)
Now the campaigns. Which money buys new customers, which defends sales the
brand would get anyway, which is waste? Structure, match types, search terms,
bids, placements, budgets, automatic targeting, Sponsored Brands and Display,
DSP (`ppc-dsp-amc`). Treat every standard "best practice" as a hypothesis to
test on this account's numbers, never a finding by itself. Two campaigns on
the same keyword advertising different products are not a problem; the same
product competing with itself might be.

### 5. Find what holds growth back (`ppc-constraints`)
Inventory cover, Buy Box, price and coupons, ratings and reviews, returns,
listing conversion. A dollar more on ads for a product with 11 days of stock is
a stockout bought early. A product returned at three times its category rate
has a product problem ads will amplify.

### 6. Price what more money buys (`ppc-scaling-forecast`)
For $10k, $25k, $50k and $100k more a month: where would each dollar go, what
would it sell, at what ACoS and TACoS, and how sure are we? Marginal returns,
not averages; incremental sales, not attributed; ceilings from search volume,
impression share and stock. A tier that does not fill is an answer: "the
account can only absorb about $18k more at today's efficiency" is worth more
than a padded $100k.

### 7. Turn it into a plan someone can execute (`ppc-execution-plan`)
Every recommendation becomes Amazon changes with today's value, the new value
and why that number, which week it happens, what to look at on which day,
what result to expect, and the rule that turns it off if it fails.

### 8. Write the story and have it reviewed (`ppc-story`, `ppc-review-rubric`)
Front-load the three things worth the most money and the one thing to leave
alone. Then the reviewer, a different seat, tries to break it against the
rubric. The audit stays a draft until it passes.

## Questions only the owner can answer

Some things are not in any data source: product cost, what they are optimising
for (growth, profit, rank, a sale of the business), launches or price changes
coming, inventory on the water, what they have already tried. Ask the chairman
early, in one message, with `ask_chairman`: at most five questions, each with
why it changes the answer and what you will assume if he does not reply. Then
keep working; never stall on an answer. Every owner-supplied number is labelled
as his in the report (`owner_inputs`), and every assumption is listed.

Never use a value somebody typed into an advertising platform (a target ACoS,
a max ACoS, a tag, a bid rule, a cost field). Nobody can say what it was set
for. The owner telling you his product cost in answer to your question is
different: it is his statement, labelled as such.

## How the crew divides the work

- **Lead** — owns the questions and the story; pins the window; asks the owner.
- **Analysts** — pull, compute, attack. Nothing quoted that a script did not
  produce.
- **Strategist** — economics, the scale tiers and the execution plan.
- **Reviewer seats** — re-derive the load-bearing numbers from the receipts and
  scripts, grade the draft against the rubric, and return it with what is
  wrong. They never write the audit.

## When the data is not there

If a source does not offer the data a question needs, the question goes in
`not_covered` with what would answer it. If the data is offered but comes back
empty, say what you asked and what came back. Never fill a gap from general
knowledge of "typical" accounts. Category benchmarks are context for the
reader, never evidence about this account.
