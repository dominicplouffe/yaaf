---
name: ppc-competitors
description: Load to understand who the brand competes with on its most valuable searches - using SP-API catalog, competitive summary and item offers to compare price, rating, reviews and offer, and to decide when ads can win share and when they cannot.
---

# Who wins the searches that matter

The point is not a list of competitors. It is one decision per important query:
**can more ad money win share here, or is the brand outmatched on something
ads cannot fix?**

## Build the comparison

1. Take the brand's top queries by market purchases from SQP (or top converting
   search terms if SQP is absent).
2. Find who wins them: the competitive summary / share-of-search data, the
   search-term report's ASIN-shaped terms, and catalog search for the query.
3. For each serious competitor ASIN, pull from the catalog and offers APIs:
   price (and price per unit/serving), rating, review count and its growth,
   Buy Box state, Prime / delivery speed, pack size, and whether it advertises.
4. Put the brand's ASIN beside them.

## Decide

| What you see | What it means |
|---|---|
| Comparable price and rating, brand's purchase share > impression share | Ads can win share — scale candidate |
| Brand 20%+ more expensive per unit, or a star lower | Ads will buy clicks that do not convert; listing/price first |
| Competitor out of stock or losing Buy Box | Short window: raise bids on their terms now, with an end date |
| Competitor targeting the brand's own ASIN pages | Defend with own-ASIN product targeting if it converts |
| Brand converts competitor ASIN targets cheaply | Conquest candidate; judge on cost per new customer |

## Cautions

- Use only Amazon's data (catalog, offers, competitive summary, SQP). Do not
  estimate competitors' sales from third-party guesses and present them as
  facts.
- Prices move; record the date of every pull.
- A competitor's high ad presence is not evidence it is profitable for them.

## Sources

- Competitive analysis grounded in intent and proof, not copying: https://www.shulex.com/blog/amazon-ppc-competitor-analysis
- What to compare (price, reviews, Buy Box, share of voice): https://www.karooya.com/blog/how-to-do-competitor-analysis-to-thrive-on-amazon/
