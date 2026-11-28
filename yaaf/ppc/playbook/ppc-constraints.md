---
name: ppc-constraints
description: Load to find what limits growth outside the campaigns - inventory and days of cover, stockout risk, Buy Box, price and coupons, ratings and reviews, returns and return-adjusted ACoS, and listing conversion. Use before recommending more spend on any product.
---

# What holds growth back

Ads multiply what the product page and the supply chain can deliver. Before
any "spend more" line, check the product can take it.

## Inventory

- **Days of cover** = available + inbound units ÷ average daily units sold
  (use the recent rate, and the rate the new spend would add). Data: FBA
  inventory planning, restock recommendations, AFN inventory.
- Amazon's own guidance to FBA sellers is about four weeks of cover; practitioner
  guidance is 2-4 weeks minimum before raising ad budgets.
- **A stockout costs more than the lost days.** Sellers report weeks of rank
  recovery after short stockouts (one widely cited study: a 7-day stockout lost
  ~34% of organic rank position across top keywords), and subscribers can be
  lost permanently. For a subscription product this is the most expensive
  failure an audit can cause.
- Spend on an ASIN that is out of stock or nearly out is waste; flag it with
  dollars. Cap every scale tier by the units the stock (plus inbound) can sell
  in the month; say when the binding limit is inventory, not ads.

## Buy Box and offer

Lost Buy Box means ads stop serving or serve someone else's offer; Subscribe &
Save subscribers can migrate to the competing offer. Check featured-offer share
(item offers, competitive pricing) for advertised ASINs.

## Price, coupons, deals

Price changes move conversion more than bids do. Note price and coupon changes
inside the window and treat periods across them separately. Deal days inflate
conversion; exclude or separate them when estimating normal performance.

## Ratings and reviews

Compare rating and review count with the competitors winning the same queries
(`ppc-competitors`). A listing a star below the field loses clicks no bid buys
back; say so and price it as a listing fix, not an ad fix.

## Returns

- Return rate per ASIN (FBA customer returns ÷ units) against its category:
  consumables are typically 2-4%, home 4-8%, electronics 8-12%, apparel 15-30%.
- Above the category threshold, Amazon charges a returns processing fee and can
  show a "frequently returned" badge that cuts conversion.
- **Return-adjusted ACoS** = ad spend ÷ (ad sales × (1 − return rate)). For a
  high-return product, the ACoS the console shows overstates what the ads earn.
- Return reasons (customer returns report, returns insights) tell you whether
  ads are attracting the wrong shopper (a query that brings returns) or the
  product disappoints (a listing / product issue).

## Listing conversion

From SQP: high click share with low purchase share is a page problem. From
Business Reports: unit session percentage by ASIN versus its own history.
More traffic to a page that does not convert raises ACoS; fix the page first,
and say so.

## Sources

- Four weeks of cover (Amazon guidance via Pacvue): https://pacvue.com/?p=538
- 2-4 weeks before scaling ads: https://www.sellerlabs.com/knowledge-base/how-inventory-impacts-amazon-ppc-performance/
- 7-day stockout and rank loss: https://www.sellersprite.com/en/blog/The-Impact-of-Out-of-Stock-on-Keyword-Rankings
- Stockouts lose subscribers: https://jarvio.io/blog/amazon-replenishable-product-strategy
- Buy Box and S&S migration: https://ensobrands.com/amazon-subscribe-and-save-seller/
- Return rates by category and the processing fee: https://novadata.io/resources/answers/what-is-a-good-refund-rate-by-category
