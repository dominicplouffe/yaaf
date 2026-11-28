---
name: ppc-business-economics
description: Load when you need to understand what the account sells and to derive what ACoS / TACoS this business SHOULD run at - first-order break-even, repeat purchase and Subscribe & Save lifetime value, payback, cost per new customer, and targets by campaign job. Use before judging any ACoS as good or bad.
---

# What this business should spend to grow

A benchmark is not a target. Published "good ACoS" ranges for the same
category disagree by a factor of four (supplements are quoted anywhere from
12-22% to 35-50%), because the right number depends on three things about
*this* business: what an order earns after Amazon, what a customer is worth
after the first order, and what each campaign is for. Derive all three from
the account's own data, then compare with today.

## 1. The portrait (five lines, before any campaign)

| Question | Where it comes from |
|---|---|
| What is sold, in what category, at what price? | Catalog (SP-API catalog / `searchcatalog`, `get_listing`), Business Report sales by ASIN |
| How concentrated? (top ASINs' share of sales) | Business Report / sales-and-traffic by ASIN |
| Consumable (repeat) or durable? | Category + Repeat Purchase Behavior report + Subscribe & Save |
| Brand owner or reseller? Seller or vendor? | Brand Registry (Brand Analytics access), attribution window in ads data |
| Known brand or unknown? | Branded query volume and share in SQP; branded share of ad spend |
| Lifecycle: launch, growth, mature, declining? | Sales trend by ASIN, review count growth, organic share trend |
| Season? | Same window last year if available; category seasonality |

Seller accounts credit Sponsored Products sales for 7 days after a click,
vendor accounts for 14; Sponsored Brands, Display and DSP use 14. Write down
which, it changes how fresh "recent" data can be trusted.

## 2. First-order economics

Per unit, from Amazon's data:

```
contribution_1 = price − referral_fee − fulfilment_fee − storage_per_unit
                 − return_cost − product_cost
break_even_ACoS (first order) = contribution_1 / price
```

- Referral fee: category rate (Health & Personal Care is 8% under $10 and 15%
  above in most US categories; check the fee preview / fee estimate tool for
  the real figure per ASIN — never assume).
- Fulfilment and storage: FBA fee preview / fee estimate per ASIN.
- Return cost: return rate (FBA customer returns ÷ units shipped, per ASIN)
  × (refund admin + return processing fee if over threshold + unsellable share
  × product cost).
- **Product cost is not Amazon data.** Ask the owner. If he has not answered,
  show break-even across a range (product cost at 15%, 25%, 35% of price) and
  say which assumption every downstream number rests on. Never take a cost
  from an advertising platform field.

`ppckit.economics.first_order_break_even(...)` does this arithmetic.

## 3. What a customer is worth after the first order

This is where most audits are wrong, and where consumables win.

**Data:** Brand Analytics Repeat Purchase Behavior (orders, unique customers,
repeat customers, repeat-customer sales share, by brand and ASIN);
Subscribe & Save performance (active subscriptions, new subscriptions,
retention at 30/90 days, S&S share of units); new-to-brand metrics on
Sponsored Brands, Sponsored Display and DSP (a purchase is new-to-brand when
the shopper has not bought the brand in the prior 12 months); AMC lifetime
value and new-to-brand queries where available.

**Two models, use the one the data supports and say which:**

- *Repeat rate model* (no subscription data): with a repeat probability `r`
  between orders, expected extra orders ≈ r / (1 − r). Cap the horizon at 12
  months unless the data shows a longer one.
- *Subscription survival model* (S&S data): with monthly churn `c`, a
  subscriber's expected life is about 1 / c months; Amazon Subscribe & Save
  churn is reported around 3-6% a month and daily-habit products often keep
  subscribers 10-16 months (sources below). Use the account's own retention
  figures when present; label anything else an assumption.

```
lifetime_contribution = contribution_1 + Σ repeat orders × contribution_repeat
LTV ACoS ceiling (on the first order) = lifetime_contribution / first_order_price
payback_months = acquisition_cost / monthly contribution per retained customer
```

Repeat orders usually cost no ad spend but carry the S&S discount; use the
repeat-order price, not the first-order price. Use 12 months as the default
horizon (Amazon's own long-term sales metric projects 12 months of value from
new-to-brand shoppers) and say so.

**Consequence:** for a vitamin or supplement with real subscription retention,
the lifetime ceiling is routinely **above 100% ACoS on the first order**, and
running acquisition campaigns at 100%+ ACoS can be the profitable choice. The
audit must say this plainly when the data supports it, and must not when it
does not. Two traps:
1. Ads also buy existing customers. Only the new-to-brand share of ad orders
   earns the lifetime multiple; branded campaigns mostly reach people who
   already buy. Weight by NTB share per campaign job.
2. Repeat rates are brand averages. A product with a 9% repeat rate does not
   inherit the 45% of its sibling. Compute per ASIN or product family.

`ppckit.economics.lifetime_value(...)`, `ltv_acos_ceiling(...)` and
`payback_months(...)` implement these.

## 4. Targets by job, not one number

Group campaigns by what they are for, then judge each against its own bar:

| Job | Judge it on | Typical bar |
|---|---|---|
| Defend brand (own brand terms, own ASINs) | Incremental sales; cost per sale vs organic alternative (`ppc-incrementality`) | Low ACoS expected; high ACoS here is waste |
| Acquire (generic / category terms, auto) | Cost per new-to-brand customer vs payback target | Can exceed first-order break-even when LTV supports it |
| Conquest (competitor terms and ASINs) | Cost per new customer; win rate | Expensive; justified only by LTV |
| Launch (new ASIN) | Rank and review velocity on a capped budget with an end date | Loss accepted, bounded in time and dollars |

Practitioner bands for wellness brands put launches at 60-100%+ on capped
budgets and competitor conquesting at 45-70%; treat such bands as context for
the reader, never as this account's target. The account's target comes from
sections 2-3. Judging acquisition on cost per new customer against a payback
target, rather than on ACoS, is the method that keeps the campaigns that grow
the business: one supplement account's non-branded campaigns ran at about 1.5×
first-order break-even and still paid back within 9 months.

## 5. TACOS and the whole business

TACoS (ad spend ÷ total sales) is the number tied to the P&L. A rising ACoS with
a falling TACoS usually means ads are lifting organic sales; a falling ACoS
with a rising TACoS often means campaigns were trimmed to their most efficient
terms while organic sales shrank. Read them together, by product. For
seven-figure supplement sellers, blended TACoS of roughly 12-18% is reported;
one $201M supplements account held 19.6% TACoS at ~50% ACoS for three years —
context that a high ACoS is not by itself a problem.

## 6. The comparison that sells the audit

Put it on one page: today's ACoS and TACoS by job, beside the band this
business can afford, with the evidence. Then say what it means in dollars a
month — under-investing in acquisition, over-paying to defend the brand, or
"you are inside your band; the gains are elsewhere".

## Sources (context, not evidence about this account)

- Benchmarks disagree: https://sequencecommerce.com/amazon/acos-calculator/ ,
  https://novadata.io/amazon-kpi/profitability/good-acos ,
  https://amzdudes.com/amazon-acos-complete-guide-to-definition-benchmarks-and-how-to-reduce-it/
- Supplement break-even and low returns: https://novadata.io/resources/blog/amazon-break-even-acos-guide
- S&S churn 3-6%/month: https://eightx.co/blog/average-ecommerce-subscription-churn-by-billing-period-2026
- Subscriber life by product type: https://www.sponsoredprofit.com/amazon-subscribe-and-save/
- Judge acquisition on payback, 1.5× break-even example: https://www.sponsoredprofit.com/amazon-new-to-brand/
- Wellness ACoS targets by purpose: https://www.amplivus.com/blog/scale-amazon-ppc-wellness-brands
- Amazon long-term sales (12-month NTB value): https://skai.io/blog/prime-day-and-beyond-forecasting-long-term-success-with-amazon-and-skai/
- Supplement payback 3-6 months, subscription 1-3: https://eightx.co/blog/supplement-cac-payback-benchmarks
- $201M account, 49.9% ACoS, 19.6% TACoS: https://unitix.pro/case-studies/dietary-supplements-nine-figure-scale
- Supplement TACoS 12-18%: https://titannetwork.com/amazon-advertising-spend-guide/
