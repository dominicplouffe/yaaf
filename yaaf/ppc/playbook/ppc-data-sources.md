---
name: ppc-data-sources
description: Load before pulling any data - how to discover what the connected source offers, which Amazon reports answer which audit question, the Qore datasets known to exist, receipts and citing evidence, and the rule that only Amazon's own fields count as evidence.
---

# Getting the data, and proving where it came from

## Discover, do not assume

Sources change. Start every audit with `data_fetch(server)` (no tool) to list
what the source offers and what is switched off. If the source has catalogue
tools (Qore: `get_report_catalog`, `get_dataset_info`, `get_table_fields`,
`consult_data_librarian`), read them before writing a query. A table name in
this file is a hint, not a promise.

## Which Amazon data answers which question

| Question | Amazon data |
|---|---|
| Spend, sales, ACoS by campaign / day | Sponsored Products / Brands / Display campaign reports |
| Keywords, targets, bids | Targeting reports + campaign/ad group/keyword entities |
| What shoppers searched | Search-term reports |
| Placements | Placement reports |
| Serving hours, budget | Budget/campaign status history, Marketing Stream |
| Total and organic sales, sessions, conversion | Business Report / sales and traffic by ASIN |
| Market share by query | Brand Analytics Search Query Performance (brand and ASIN) |
| Repeat customers | Brand Analytics Repeat Purchase Behavior |
| Subscribe & Save | S&S performance / forecasting reports |
| New-to-brand | SB / SD / DSP reports; AMC |
| Inventory and cover | FBA inventory planning, restock, AFN inventory |
| Returns | FBA customer returns, returns insights |
| Fees | Fee preview / product fees estimate |
| Catalog, price, rating, competitors | SP-API catalog, item offers, competitive pricing / summary |
| DSP | DSP campaign / line item reports |
| Paths, overlap, LTV | Amazon Marketing Cloud |

## Qore (known when this was written)

- `get_timeseries` (organisation / campaign daily), `get_stats` (keyword,
  target, product, SQP, search-term levels), `get_search_term_metrics`,
  `get_campaign_placement_stats`, `get_stats_by_hour`.
- `query_dataset` over tables including campaign (`api_gvcampaignch`), ad
  group (`api_gvadgroupch`), search term (`api_gvsearchtermreportch`),
  placement (`api_campaignplacementreportch`), SQP weekly brand and ASIN views
  (`api_sqprweeklyview`, `api_sqprasinweeklyview`), repeat purchase
  (`api_repeatpurchasech`), FBA inventory planning / restock / AFN inventory
  (`api_fbainventoryplanningch`, `api_restockinventorych`,
  `api_afninventorych`), customer returns (`api_fbacustomerreturnsch`,
  `api_returnsinsight*`), DSP (`dsp_*`), market basket, coupons and
  promotions, Subscribe & Save metrics.
- `get_competitive_summary`, `searchcatalog`, `itemoffer`, `get_listing` for
  catalog and competitors; AMC tools (`run_amc_report`, `get_amc_report_data`)
  where an AMC instance exists.
- Write tools exist on Qore (bulk actions, create campaigns). **An audit never
  calls them.**

Confirm names and columns with `get_table_fields` before querying.

## Pull to disk, cite the receipt

Use `data_fetch` for anything larger than a lookup. It writes the result to
your workspace and returns a `receipt`. Every claim's evidence cites the
receipts its numbers came from and the fields used; the platform checks that
each receipt is a real pull from this project and that every cited field was
in it. Keep the processing script in `analysis/` and name it in the evidence.

## Only Amazon's data

Evidence is Amazon's records and arithmetic on them alone (serving hours,
organic sales = total − ad sales, TACoS, return rate…). A value somebody typed
into a platform — target or max ACoS, tags, bid rules, cost of goods, margin,
"campaign goal" — is never evidence and is refused wherever it is cited, even
if the source offers it. Platform groupings (portfolios a tool invented,
"managed" flags) are not Amazon entities; group by campaign, ad group, keyword,
target, ASIN. The owner's own answers to your questions are labelled
`owner_inputs`, never mixed with data.
