---
name: ppc-statistics
description: Load before calling anything waste, dead, a winner, a loser, a trend or a forecast. Sample sizes, zero-order probability, Bayesian shrinkage, recent-vs-prior splits, attribution lag, confounders (stock, price, Buy Box, deals, seasonality, bid changes), backtests, and how to state confidence.
---

# Proving it

A brand manager can tell when an audit is guessing. These are the checks that
separate "this keyword is bad" from "this keyword stopped converting on 3 Oct,
when the product's price went up 20%; it converted at 14% before".

## Is there enough data?

**Zero orders is only evidence after enough clicks.** If the true conversion
rate is `p`, the chance of `n` clicks with no order is `(1 − p)^n`. At 10%
conversion, 10 clicks without an order happens 35% of the time by luck; 30
clicks, 4%. Use the product's own conversion rate (or its ad group's), not the
account's. The common practitioner floor of ~20 clicks is a minimum, not a
rule; two to three times the clicks-per-order is the defensible bar.
`ppckit.stats.zero_order_probability(clicks, cvr)` and
`clicks_needed(cvr, confidence)`.

**Shrink small samples toward their group.** A keyword with 3 orders from 12
clicks has not proven a 25% conversion rate. Use a beta prior from its ad group
or product (`ppckit.stats.shrunk_rate(successes, trials, prior_rate,
prior_strength)`), and give a credible interval, not a point
(`ppckit.stats.beta_interval`). Bids and verdicts use the shrunk rate.

**Say how sure you are, in words a manager reads:** "we would be wrong about
this about 1 time in 20". Use the kit's probabilities; never invent one.

## Recent versus before: the question you must always ask

A keyword with no sales in the last 30 days that sold well in the 30 before is
**not waste until you know why it stopped.** Always split the window
(`ppckit.stats.rate_change(...)` tests whether two periods' conversion or
cost-per-order differ by more than chance) and, when it changed, look for the
cause in this order:

1. **Stock.** Was the advertised ASIN out of stock or low (inventory history,
   FBA inventory planning)? Ads on an out-of-stock or slow-ship product do not
   convert.
2. **Buy Box / suppression.** Did the brand lose the featured offer?
3. **Price, coupon, deal.** Did price rise or a coupon end? Did a Prime Day /
   Lightning Deal inflate the "before" period?
4. **Bid, budget, placement change.** Did someone lower the bid so it now wins
   only bottom-of-page clicks? Did the campaign run out of budget earlier?
5. **Competition.** Did a competitor enter or cut price (SQP share moved,
   competitive summary)?
6. **Seasonality.** Same weeks last year? Category demand in SQP search volume.
7. **Data lag.** Is "recent" inside the attribution window (below)?

Only if none explains it, and the drop is statistically real, is it a
performance problem — and then the action may be a bid change, not a negative.

## Attribution lag and restatement

Sales are credited to the click date for up to 7 days (Sponsored Products,
seller) or 14 days (vendor SP, Sponsored Brands, Display, DSP), and reports are
restated after that; one study found attributed sales on the top 5% of
campaigns grew 19%+ between day 1 and day 17. Most conversions arrive within a
week, but end every window at least 3 complete days ago, treat the last 7-14
days as provisional, and never compare a provisional week with a settled one.
New-to-brand figures need at least 14 days.

## Confounders before causes

Before attributing a sales change to ads, check what else moved in the same
days: stock, price, coupons, deals, reviews/rating, Buy Box, listing changes,
seasonality, a competitor's stockout. If spend and sales rose together on busy
days, that is demand driving both, not ads driving sales — which is exactly the
bias in naïve spend-vs-sales curve fits. Control for demand (organic sales,
SQP search volume, day-of-week) or say the elasticity is an upper bound.

## Regression to the mean

The best-performing keyword of last month will usually do worse next month and
the worst will usually do better, by chance alone. Do not promise last month's
winner's ACoS for new money; shrink toward the group.

## Forecasts are claims too

Any forecast in the audit carries:
- the model and its inputs (`ppc-scaling-forecast`);
- an out-of-sample backtest: fit on the earlier part of the history, predict the
  later part from actual spend, and report the typical miss
  (`ppckit.forecast.backtest`);
- a range, not a point, from that miss.

## Comparing campaigns that compete for the same search

Two campaigns on the same keyword advertising **different products** are two
products competing for a shopper, which can be right. Only the **same product**
on the same keyword and match type in two places competes with itself. Before
calling it cannibalisation, check that the combined result would be better
consolidated (one winner per query in the search-term report), not just that
the overlap exists.

## Writing it down

Every claim states: the number, the count behind it (clicks, orders, days),
the window, the comparison, the test used and its result, and the confidence.
If a result fails these checks, it is a hypothesis for the plan's test
budget, not a finding.

## Sources

- (1 − p)^n and clicks-per-order thresholds: https://adbrew.io/blog/click-threshold-for-search-term-negation/
- Common 15-20 click floors: https://jarvio.io/blog/amazon-negative-keywords , https://captenamz.com/blog/amazon-negative-keywords/
- Attribution windows by ad type: https://help.intentwise.com/amazon-attribution
- Restatement and provisional data: https://www.optmyzr.com/blog/amazon-ads-reporting-delays/
- Most conversions within a week: https://teikametrics.com/author/chris
