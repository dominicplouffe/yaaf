---
name: ppc-demand-sqp
description: Load to find where the market is and what share the brand wins - Search Query Performance (SQP) funnel shares by query and ASIN, capture gaps, branded vs generic demand, and how to turn share gaps into ad, listing or price actions.
---

# Where the demand is

Ads reports show what the brand's ads did. Brand Analytics' Search Query
Performance (SQP) shows **the whole market** for each query — every shopper's
impressions, clicks, add-to-carts and purchases — and the brand's share of
each. It is the only Amazon data that tells you how much more there is to win.

## Read the funnel in order

impression share → click share → cart-add share → purchase share

| Pattern | Diagnosis | Lever |
|---|---|---|
| Low impression share, high click/purchase share | Wins when seen, rarely seen | More ad coverage (exact match, top-of-search), organic rank — **this is where new money goes** |
| Impression share fine, click share drops | Main image, price shown, rating, title | Listing / price, not bids |
| Click share fine, cart/purchase share drops | Detail page, price, reviews, delivery promise | Listing / offer; more ad money makes it worse |
| Real query volume, near-zero impressions, zero ad spend | Coverage gap | Add the query (if relevant) |
| Heavy spend on a query with a low market purchase rate | A browsing query | Bid down or negate |

Rank opportunities by **gap × the query's market purchases**: a two-point gap
on a query with 40,000 monthly purchases beats a twenty-point gap on one with
200. The capture gap (purchase share − impression share) is a quick screen:
large and positive means under-served.

## Tag every query by job

Branded (own brand), competitor brand, generic/category, attribute long-tail.
Branded share tells you how much of the account is defending sales it would
largely get anyway (`ppc-incrementality`); generic share is the growth market.

## Connect it to the ads

Join SQP queries to the search-term report (same normalised text):
- query volume and the brand's share vs the ad spend on it;
- where ads already buy most of the brand's impressions (paid-dependent) vs
  where organic carries it;
- whether ad clicks convert better or worse than the brand's overall purchase
  share suggests.

## Ceilings for the forecast

SQP gives the market's purchases per query per week. That caps what any amount
of money can buy: if the market buys 3,000 units a month and the brand has 22%,
taking it to 30% is about 240 more units, not unlimited. Pass these ceilings to
the scale forecast.

## Cautions

- SQP is weekly (and monthly/quarterly); align windows with the ads data.
- Shares are relative to the brand's own ASINs, not a competitor's; ASIN-level
  SQP exists for detail.
- Small queries have noisy shares; use market volume thresholds and pool weeks.
- Percent fields may arrive as 0.41 meaning 41% or 0.41%; check the scale on a
  known row before computing.

## Sources

- Funnel diagnosis and fixes: https://marketplaceadpros.com/guides/amazon-search-query-performance-report-guide/
- Rank fixes by gap × purchases: https://www.sellerlabs.com/knowledge-base/search-query-performance-your-best-free-keyword-data/
- Capture gap (purchase − impression share): https://ensobrands.com/amazon-search-query-performance-report/
- Click → purchase drop is a conversion problem: https://www.sellermate.ai/resources/guides/search-query-performance
