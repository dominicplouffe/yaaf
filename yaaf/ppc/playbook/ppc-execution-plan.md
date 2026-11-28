---
name: ppc-execution-plan
description: Load when turning findings and scale tiers into a plan a brand manager can execute - exact Amazon changes with today's and new values, bid and budget math, harvest-and-negate pairs, staged weekly rollout, check-ins with expected ranges, stop rules, and the change sheet / bulk file.
---

# From "spend $10k more" to what someone clicks

A forecast nobody can execute is worth nothing. Every line of the plan is an
Amazon change a person can make in Campaign Manager or by bulk upload.

## A move

Each move in the report carries:

| Field | Example |
|---|---|
| action | `budget` · `bid` · `placement` · `bidding_strategy` · `keyword_add` · `negative_add` · `target_add` · `pause` · `campaign_create` · `listing` · `inventory` · `test` |
| entity | campaign / ad group / keyword / target name **and Amazon ID** from the data |
| today → change to | daily budget $150 → $210 |
| why this number | "runs out of budget at 14:00 (serving 14.2 h); past $210 the marginal ACoS exceeds 28%" |
| expected monthly | +$1,800 spend, +$6,400 sales (range from the backtest) |
| week | 1, 2, 3… of the rollout |
| check | day 10: serving ≥ 20 h, extra sales $1.5k-2.1k, ACoS ≤ 31% |
| stop rule | after 14 days and 60+ extra clicks, ACoS > 34% → budget back to $150 |
| claims | the claim ids that justify it |

## The math behind the numbers

- **Bid for a target cost per order:** max CPC = order value × conversion rate
  × target ACoS (`ppckit.bids.target_bid`). Use the shrunk conversion rate,
  the product's order value, and the target for the campaign's *job*.
- **Budget for a capped campaign:** today's budget × 24 / serving hours is
  full-day; the forecast says how much of that is worth buying
  (`ppckit.bids.budget_for_full_day`).
- **Placement multiplier:** set it so the effective top-of-search bid reaches
  the bid the formula gives for top-of-search conversion
  (`ppckit.bids.placement_multiplier`). Remember up-and-down bidding can
  double it again.
- **Harvest:** exact keyword in the right campaign at the formula bid, **plus**
  a negative exact in the source campaign.
- **Negative:** only for terms that passed `ppc-statistics`; phrase negatives
  only when every variant they block is irrelevant.

## Rollout

Do not move all the money on day one; stage it so each change can be read:
- **Week 1:** budget increases on capped winners (fastest signal, least risk).
- **Week 2:** bids and placement multipliers.
- **Week 3:** new keywords and targets (they need time to learn).
- **Tests:** their own budgets and end dates, starting when the base is stable.
Read results 3+ days after the period ends (attribution lag).

## Check-ins and stop rules

Each move's expected range comes from its forecast and the backtest miss. Set
the stop threshold where it would rarely trigger by chance if the move were
working (e.g. ACoS beyond the upper end of the range after enough clicks for
the kit's `clicks_needed`). The stop rule protects the owner: every move either
proves itself or is switched off within about two weeks, with only that move's
spend at risk.

## The change sheet

The platform writes a change sheet (workbook tab and CSV) from the moves in
Amazon's bulk-operation vocabulary: Product, Entity, Operation, Campaign ID,
Ad Group ID, Keyword ID / Product Targeting ID, Campaign Name, Ad Group Name,
State, Daily Budget, Bid, Keyword Text, Match Type, Placement, Percentage.
Fill every ID from the data; a move without IDs cannot be bulk-uploaded and is
listed for manual change. Tell the owner to compare headers with a bulk file
downloaded from his own account before uploading.

## Sources

- Bulk operations: create / update / archive operations and columns: https://www.helium10.com/blog/bulk-files-update/
- Diminishing returns when scaling budgets: https://pare.so/blog/amazon-ppc-scaling-diminishing-returns-high-performing-campaigns-2026
