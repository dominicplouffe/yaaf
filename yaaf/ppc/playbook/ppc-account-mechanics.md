---
name: ppc-account-mechanics
description: Load when examining how the campaigns themselves work - structure, match types, search-term waste and harvesting, negatives, bids versus CPC, placements and multipliers, bidding strategies, budgets and serving hours, auto targeting groups, Sponsored Brands and Display, halo, dayparting, and recent Amazon platform changes. Every item is a hypothesis to test, not a finding.
---

# How the campaigns capture demand

Each item below is a question with the data that answers it and what "fine"
looks like. None is a finding until this account's numbers, tested
(`ppc-statistics`), say so. An account that is fine on an item gets told so.

## Measurement first

- Ratios from totals, never averages of ratios.
- Say which sales: attributed same-SKU, or including halo (shopper clicked A,
  bought B). An ASIN whose ads mostly sell other ASINs is a gateway.
- Attributed is not incremental (`ppc-incrementality`).
- 1 Jan 2026 changed view-through counting for Sponsored Brands,
  impression-billed display and DSP in the store; year-over-year comparisons of
  view-attributed sales across that date are not like for like.

## The items

1. **Structure.** Can a stranger tell each campaign's job (brand, generic,
   competitor, discovery, launch) from its name and targets? One home per
   (product, query, match type). Different products on the same query are
   fine; the same product twice competes with itself.
2. **Search-term waste.** Terms past the click threshold for *their* product's
   conversion rate with no orders — after the recent-vs-prior and confounder
   checks — and irrelevant terms (wrong product, use, audience). Check stale
   negatives too: a negative blocking a term that converted last quarter.
   Leave discovery spend below the threshold alone; it finds next quarter's
   winners.
3. **Harvesting.** Converting search terms (two or more orders at an
   acceptable cost for the campaign's job) not yet targeted exactly, paired
   with a negative exact where they were found so they are not bought twice.
4. **Bids vs CPC.** Bid far above the CPC paid leaves room to test lower; CPC
   at the bid means the auction is tight. Targets above their job's bar with
   enough clicks: bid down to the bar (`ppc-execution-plan` bid formula).
   Winners with low top-of-search share: the cheapest growth in the account.
5. **Placements.** Sponsored Products placement multipliers run 0-900% for top
   of search, rest of search, product pages (and Amazon Business). Compare
   conversion and cost per order by placement; a 2× spread with flat
   multipliers pays the same for unequal clicks. Since 10 Aug 2026 existing SP
   campaigns are enrolled by default in off-Amazon creator placements, where
   placement adjustments do not apply — check their cost and sales.
6. **Bidding strategy.** Down only; up and down (can raise bids up to 100%);
   fixed. Up-and-down stacked on a large top-of-search multiplier can bid many
   times the base: compute the real ceiling.
7. **Budgets.** A campaign meeting its job's bar that runs out of budget early
   loses sales; one failing its bar that never runs out keeps spending. Use
   serving hours / time in budget. Move money from the second to the first
   before asking for more.
8. **Auto targeting groups.** Close match, loose match, substitutes,
   complements each take a bid; one default bid across all four is usually
   wrong. Check which converts; do not assume.
9. **Product targeting.** Own-ASIN defence, competitor conquest, category
   targets: judge each on its job's bar.
10. **Sponsored Brands / video / Display / Sponsored TV.** Judge upper-funnel
    formats on new-to-brand share and cost per new customer, and on TACoS
    movement, not on ACoS alone.
11. **Halo and coverage.** Products with real sales and no ads; ads on products
    with no traffic; gateway ASINs that bring new customers to the brand.
12. **Dayparting.** Hourly conversion patterns. Most accounts have none worth
    acting on; say so when that is true.
13. **AI shopping placements.** Since 2026, Sponsored Products and Brands can
    appear in Amazon's AI shopping assistant (Rufus, now Alexa for Shopping)
    as sponsored prompts, billed per click. Report exposure where the data
    shows it; do not lecture.

## What to bring to the story

Only what moved money: each item that matters becomes a claim with dollars a
month and its test; each item that is fine and was checked goes in "leave
alone" with what was checked.

## Sources

- Attribution windows: https://help.intentwise.com/amazon-attribution
- Sponsored prompts in Amazon's AI shopping assistant: https://advertising.amazon.com/library/news/agentic-shopping-advertising
- Negation thresholds: https://adbrew.io/blog/click-threshold-for-search-term-negation/
