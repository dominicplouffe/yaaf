---
name: ppc-scaling-forecast
description: Load to answer where an extra $10k, $25k, $50k or $100k a month would go and what it would sell at today's ACoS or TACoS - finding pools of headroom, marginal not average returns, ceilings, incrementality haircuts, lifetime value weighting, the response-curve forecast and its backtest.
---

# Pricing what more money buys

The owner asked a specific question: for $10k, $25k, $50k and $100k more a
month, where would each dollar go and what would it sell, at today's ACoS or
today's TACoS? Answer it with evidence; say when a tier does not fill.

## 1. Find the pools

A pool is one place the account can absorb more spend, backed by its own data:

| Pool | Evidence it exists | Ceiling |
|---|---|---|
| Budget-capped winners | Campaign meets its bar and runs out of budget (serving hours < 24) | Spend at full-day serving (≤ 3× today) |
| Low share winners | Meets its bar, low top-of-search / search-term impression share | Share of a market that exists (SQP volume) |
| Converting terms not yet targeted | Search-term orders at an acceptable cost | ~1.5× their current search-term volume |
| Share-of-search gaps | SQP: purchase share ≥ click share, low impression share | Market purchases × achievable share |
| Under-invested acquisition | Cost per new customer well under the payback limit (`ppc-business-economics`) | Ceiling from search volume and stock |
| Formats not yet run (SB video, DSP prospecting) | — | **Not capacity.** A test with a budget and a falsifier |

Every pool is capped by inventory: units the stock plus inbound can sell this
month (`ppc-constraints`).

## 2. Marginal, not average

The next dollar always buys less than the average dollar did. Scaling decisions
on averages overspend. For each pool fit how sales respond to spend:

- `ppckit.forecast.fit_exponent(history)` fits sales = k·spend^b on daily
  history (≥ 21 days and real movement in spend), shrunk toward a stated prior
  when the history is thin.
- Control for demand where you can (`ppc-statistics`: spend and sales rising
  together on busy days biases b upward). If you cannot, say the curve is an
  upper bound and widen the range.
- `ppckit.forecast.allocate(pools, baseline, window_days)` sends each dollar
  where the next one is cheapest and stops when the marginal dollar would push
  the blend past today's ACoS (or TACoS).

## 3. Incremental and lifetime value

- Discount attributed sales by job before using them for TACoS: branded
  traffic is largely non-incremental (`ppc-incrementality`); state the factors
  used.
- For acquisition pools in a repeat-purchase business, also report the
  lifetime value of the new customers bought (NTB share × lifetime
  contribution). A tier can fail at today's ACoS and pass on payback; say
  both, so the owner chooses.

## 4. Prove the forecast

`ppckit.forecast.backtest(campaign_daily)`: fit each campaign on the first two
thirds of its history, predict the last third from its actual spend, compare
with a naive same-ROAS model, and report the typical miss. That miss is the
range on every tier ("$52k ± $14k"). If the curve model does not beat naive,
say so and use the simpler one.

## 5. Write the tiers

For each tier: how much of it fits; where it goes (pool by pool with dollars);
expected sales low / mid / high; resulting ACoS and TACoS; new customers bought
where NTB data exists; what binds first (search volume, stock, efficiency).
A tier that does not fill says how much does, and what would have to change
(a listing fix, new products, inventory) to absorb the rest.

## Sources

- Marginal ROAS decides scaling: https://ecomclips.com/blog/amazon-ppc-budget-optimization-how-to-scale-ad-spend-profitably/
- Marginal iROAS is always below average: https://www.bellstatistics.com/glossary/iroas
- Saturation, lag and confounding in spend models: https://prescientai.com/blog/measure-amazon-ads-incremental-impact-on-amazon-sales
