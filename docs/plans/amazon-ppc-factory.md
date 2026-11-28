# Amazon PPC Factory: a fifth kind of project

## In one paragraph

An Amazon PPC Factory works one Amazon advertising account through the MCP
data source that holds it. It has two modes. **Audit** (built) investigates the
account and ships one reviewed story: what the business sells and what it can
afford to pay for a sale (against what it pays today), what is working and
should be left alone, what is not and what changing it is worth a month, where
10k, 25k, 50k or 100k more a month would sell the most, and a week-by-week plan
of Amazon changes with check-ins and stop rules. **Operate** (scaffolded,
closed) will keep an account inside goals it is given, on a rhythm.

## Why it is a harness, not a checklist

The first version was eighteen hard-coded checks the crew filled in. The
chairman's verdict on its output was right: superficial. A check cannot know
that a keyword with no sales this month sold well last month because the
product was out of stock; that two campaigns on one keyword advertise
different products; that a 100%+ ACoS is the profitable choice for a vitamin
whose subscribers stay a year. Those are judgements an investigator makes
against this account's data. So the work moved to the agents, the way a coding
agent works on code — and the platform kept only what makes the result
trustworthy, each held on the server (house rule 1):

| What the chairman needs to trust | How it is held | Attack |
|---|---|---|
| The crew knows what a senior Amazon strategist knows | The playbook (`yaaf/ppc/playbook/*.md`, from the research below) is handed to every seat of a PPC Factory as skills at lease (`daemon._offer_kind_skills`); the brief points to the runbook first | `test_every_seat_gets_the_playbook_as_skills`, `test_the_brief_points_at_the_runbook_and_the_reach` |
| Every seat computes the same number the same way | The kit (`yaaf/ppc/kit.py`): statistics, economics, bids, forecast, backtest — standard library only, served verbatim (`/agent/ppc/kit`, the `ppc_kit` tool) and imported by the platform itself | `test_the_kit_is_served_verbatim_to_a_ppc_factory_only`, `test_the_kit_runs_on_the_standard_library_alone`, the kit tests |
| No made-up numbers | Every PPC read through the gateway is recorded with the fields it returned; the receipt rides back to the agent (`data_fetch` returns it). A claim citing a receipt not on file, another project's, or a field the pull did not contain, is refused | `test_a_ppc_read_through_the_gateway_carries_its_receipt`, `test_a_claim_must_cite_a_pull_on_file_and_a_field_it_returned` |
| Never use proprietary fields | Every cited field passes `yaaf.ppc.fields`, an allowlist that fails closed; entered values (target/max ACoS, tags, COGS, margin, goals) are refused by name. The owner's own answers are `owner_inputs`, labelled as his | `test_each_field_is_amazons_derived_or_refused`, `test_an_entered_field_is_refused_wherever_it_is_cited`, `test_an_owner_answer_is_his_and_labelled` |
| "If the MCP server does not give you the data, we don't do this section" | Areas declare the data they need; a claim about an unreachable area is refused, and the platform lists every unreachable area as not covered itself | `test_an_area_the_data_cannot_reach_is_not_claimed_and_is_listed` |
| A big claim was tested | A claim worth $1,000+ a month, and every forecast, carries how it was tested and what came out | `test_a_careless_report_is_refused` |
| Someone can execute it | Every move names the Amazon entity, today's value, the new value, why that number, its week, its check-in and its stop rule; a $1,000+ fix with no move is refused; tiers agree with their moves and cite a tested forecast | `test_a_careless_report_is_refused` |
| What good looks like for *this* business | `economics.bands` is required: today's ACoS by campaign job beside what this business can afford, with how it was derived | `test_a_careless_report_is_refused` |
| Somebody else checked it | The pages render as a draft. Only a `ppc-review.json` by a different seat, of the latest version, passing all ten rubric criteria and re-deriving every load-bearing number within 5%, makes it final, attests delivery and lets the audit complete | `test_a_seat_cannot_review_its_own_audit`, `test_a_review_of_another_version_or_skipping_a_number_is_refused`, `test_only_a_passing_review_makes_the_audit_final_and_complete` |
| The pages say only what the report says | The platform renders the page, the presentation, the workbook and the change sheet from the accepted report; crew-written ones are refused | `test_the_pages_are_the_platforms_to_write`, `test_the_deck_and_workbook_carry_the_reports_numbers` |
| No data, no plan | The plan gate holds until a source offers ad performance | `test_the_plan_cannot_hold_without_ad_data` |

What is deliberately *not* held by code: whether a claim is wise, whether the
story is good, whether the tone is right. That is the reviewer's job against a
written rubric (the generator/evaluator split: a separate skeptical grader is
far more reliable than a writer grading itself), and the owner's.

## The investigation (the runbook)

1. Understand the business — what is sold, price, consumable or durable,
   concentration, seller or vendor, brand strength, season.
2. Decide what good looks like — first-order break-even from price, fees and
   returns (product cost from the owner, or a range); lifetime value from
   repeat purchase and Subscribe & Save; the affordable ACoS by job.
3. Find the demand — SQP funnel shares, capture gaps, competitors.
4. See how ads capture it — every best practice as a hypothesis tested on this
   account; incrementality of branded spend; new-to-brand.
5. Find what holds growth back — inventory cover, Buy Box, price, ratings,
   returns, listing conversion.
6. Price what more money buys — pools, marginal returns, ceilings, backtest.
7. Turn it into moves — bid formula, budgets, harvest-and-negate, rollout,
   check-ins, stop rules, change sheet.
8. Write the story and have it reviewed against the $60k-test rubric.

## The second round of research: what makes an audit worth paying for

- **The yardstick is the business, not a benchmark.** Published "good ACoS"
  ranges for supplements alone run from 12–22% to 35–50%. The right number
  comes from first-order economics and, for consumables, lifetime value:
  Amazon Subscribe & Save churn is reported at 3–6% a month and daily-habit
  products keep subscribers 10–16 months, so acquisition can be profitable
  well past 100% first-order ACoS. Judge acquisition on cost per new customer
  against payback (one supplement account's non-branded campaigns ran at 1.5×
  first-order break-even and paid back within 9 months).
- **Statistics before verdicts.** (1 − p)^n for zero-order streaks;
  two-to-three times clicks-per-order before negating; shrink small samples;
  split recent from prior and look for stock, price, Buy Box, deals and season
  before blaming the ad; attribution lag and restatement.
- **Incrementality.** Branded search ads are often largely non-incremental
  (eBay's experiments), less so where organic rank is weak (Edmunds);
  practitioners running holdouts commonly find 20–40% of spend defending owned
  sales. Recommend holdouts with a pre-set threshold, not cuts.
- **Demand.** SQP is the only Amazon data showing the whole market per query;
  read impression → click → cart → purchase share, rank gaps by market
  purchases, and use it to cap forecasts.
- **Constraints.** Four weeks of cover before scaling; stockouts cost rank and
  subscribers; return-adjusted ACoS; frequently-returned badge.
- **DSP and AMC.** Prospecting on new-to-brand and branded-search lift,
  retargeting on incrementality; AMC for NTB by campaign, path, overlap,
  gateway ASINs, LTV.
- **Harness design.** Analytics accuracy is a context and verification
  problem: canonical definitions (the kit), a runbook skill plus reference
  skills, provenance on every answer (receipts), and a separate adversarial
  reviewer (the review gate).

Each skill file carries its sources.

## The first round of research: the levers

Before writing anything, the ecosystem was read end to end: Amazon's own
support and launch pages, the Ads API report catalogue, practitioner audit
checklists, and 2026 change logs. What came out of it, and why each matters:

- **Formats.** Sponsored Products (the workhorse), Sponsored Brands (product
  collections now AI-curated 3–10 ASINs since January 2026; video; brand video;
  new-to-brand metrics; Brand Store page views), display ads (Sponsored Display
  renamed in 2026: contextual, views and purchases remarketing, optimized
  targeting), Sponsored TV, DSP / DVA+, and as of unBoxed 2026 one console,
  Amazon Ads Agent, with Full-Funnel Campaigns.
- **Targeting.** Auto campaigns' four groups (close, loose, substitutes,
  complements) each take a bid; manual broad/phrase/exact; product and category
  targets with refinements; negative exact and phrase.
- **Bidding.** Down only, up and down (up to +100% at top of search, +50%
  elsewhere), fixed; placement multipliers 0–900% for top of search, rest of
  search (since January 2024), product pages and Amazon Business; audience bid
  boosts; schedule bid rules (raise only).
- **Budgets.** Time in budget, estimated missed impressions, clicks and sales,
  recommended budgets.
- **Measurement.** 7-day click attribution for seller Sponsored Products, 14 for
  vendors, Sponsored Brands and display; halo ("other SKU") sales; up to 72 hours
  of cancellations; view-through counting changed on January 1, 2026; unified
  reporting GA June 8, 2026, legacy report pages retired December 2026.
- **Market data.** Brand Analytics Search Query Performance (market and brand
  share of impressions, clicks, carts, purchases per query), top-of-search
  impression share (90-day lookback), search-term impression share.
- **Incrementality.** Attributed is not incremental; branded search is the least
  incremental spend when the brand owns the organic result and nobody else bids;
  holdout tests are the honest answer to an open question. TACOS is the guard.
- **2026 exposure.** AI shopping prompts auto-enabled and billed per click from
  March 25, 2026 (US); Sponsored Products auto-enrolled in off-Amazon influencer
  placements from August 10, 2026, where placement multipliers do not apply.
- **Retail readiness.** Sponsored Products and display stop serving when a
  product loses the featured offer or goes out of stock; Sponsored Brands keeps
  running while any featured product is in stock.
- **Practitioner checks.** Spend concentration, zero-order spend past a
  clicks-per-sale threshold (2–3× the clicks one order takes), duplicate and
  auto/manual overlap, placement conversion spread vs flat multipliers, budget
  caps on profitable campaigns, negative coverage both ways, attribution window
  vs purchase cycle, bid vs CPC gaps, winners with low impression share, profit
  after ads per product against its own break-even.
- **Scaling.** Marginal ACOS, not average; low impression share on efficient
  targets and budget-capped profitable campaigns are the cheapest headroom;
  SQP queries where purchase share ≥ click share; inventory as the ceiling.

These became the eighteen levers in `yaaf.ppc.SECTIONS` and the scale ledger
method in the doctrine.


## Operate, scaffolded

What exists: `PpcMode.OPERATE`; `shape` answers it (runs on cycles, reads as
Operate); the form shows it as "coming soon"; the founder schema offers audit
only; creation refuses it with a sentence. What it needs before it opens:

1. **Goals as server-held boundaries.** A goal grammar for target ACOS, TACOS
   ceiling, monthly budget and floor on sales, stored on the project and judged
   by the platform each cycle from the same data source (a standing obligation
   whose checker reads the account, like a Code Factory's upkeep promises).
2. **Writes through approvals.** Bid, budget and negative changes are write
   tools, denied by default at the gateway. Operate opens the narrowest set,
   each call recorded (the gateway already audits every write), with a change
   cap per cycle and a dollar cap per change that refuses above it.
3. **A boundary breach is a state change.** Spend past the budget or ACOS past
   the ceiling for N days pauses the factory's write tools and opens a flag,
   with nobody asked first — the Code Factory's canary, for money.
4. **Learning across cycles.** Each cycle's changes and their measured effects
   become the next cycle's pack, as Operate already does.

## Sibling footprint (CLAUDE.md rule)

Diffed against Code Factory. Wired (plus, for the harness: kind skills at lease and in chat, read receipts in the gateway, the kit endpoint and tool, the review gate in the artifact route, the reviewed state on the dashboard): models and migration (`ppc_mode`), shape,
create/clone/export (via integrations), founder schema and doctrine, MCP create
tempo line, plan gate, delivery readiness, artifact route, context pack,
setup checklist, evidence floors and attestation, observe dashboard, New
Project form, Overview panel, mode label, roles, crew, example, README, tests.

Not wired, on purpose: GitHub, changes, releases, merge gate, sandbox, coding
key, repo guidance, upkeep, pressure, provenance (all code-specific). The
rehearsal runs on mock models with no data source, so a PPC audit rehearses
up to its plan gate and stops there, which is the truth about an audit with no
data. CLI and MCP readers show the kind through `shape.label` and `mode`.

## Known limits, said plainly

- **The review is a model grading a model**, against a written rubric and with
  numbers re-derived from receipts. It is much stronger than self-grading and
  weaker than a human; the chairman reads a reviewed audit, not an infallible
  one.
- **Receipts prove a pull happened and what fields it returned, not that the
  arithmetic on it is right.** The reviewer's re-derivation is what checks the
  arithmetic, and only for load-bearing claims.
- **Forecast curves fitted on daily variation are confounded by demand.** The
  playbook tells the crew to control for it or call the curve an upper bound;
  the backtest measures prediction, not causation. Planned budget steps
  (Operate) are the real fix.
- **Coverage is read from tool names and descriptions** and fails closed. A
  generic query tool (Qore's `query_dataset`) makes every report-backed area
  reachable; an empty result is reported as not covered, by the crew.
- **The change sheet uses Amazon's bulk vocabulary** but bulk templates change;
  the owner is told to compare headers with his own download before uploading.
- **The platform's first-pass checks still exist** (`ppc_analysis`, Qore
  adapter) as leads; nothing requires the crew to carry them.

## Sources

Amazon Ads support and launch pages (bidding strategies; placement
adjustments incl. rest of search, Amazon Business; budgets page; top-of-search
impression share; targeting report; purchased product report; attribution;
Sponsored Brands; display ads; Amazon Ads Agent and unBoxed 2026; reserve
share of voice; Marketing Stream; schedule bid rules), the Ads API v3 report
types, and practitioner write-ups from Ad Badger, Nova, My Amazon Guy, Seller
Labs, Teikametrics, Helium 10, Intentwise, Pacvue, Tinuiti, Michael Bennett,
CommonThread, ppc.land and others, read November 2026.
