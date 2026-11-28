---
name: ppc-dsp-amc
description: Load when the account runs Amazon DSP or has Amazon Marketing Cloud - prospecting vs retargeting, new-to-brand, brand halo, branded search lift, path to conversion, overlap, gateway ASINs and lifetime value queries, and how to judge upper funnel without crediting it for sales it did not cause.
---

# DSP and Amazon Marketing Cloud

## DSP: two different jobs

- **Prospecting** (new audiences): judge on new-to-brand rate, cost per new
  customer, and lift in branded searches and detail-page views. Direct ROAS is
  low (practitioners see roughly 1-3×); that is expected.
- **Retargeting** (viewers, cart abandoners, lapsed buyers): judge on ROAS and
  on incrementality — it is the DSP spend most likely to take credit for sales
  that were coming anyway. Practitioners see roughly 3-6× initial ROAS.

**Read from the data:** spend, impressions, reach and frequency, DPV, ATC,
purchases, NTB purchases and NTB sales, brand halo ("BH") conversions (sales of
any ASIN of the brand), branded searches attributed to ads.

**Questions that matter:**
- What share of DSP spend is retargeting vs prospecting, and what does each buy
  per new customer?
- Did branded search volume (SQP) and organic sales move when prospecting
  spend moved?
- Is frequency so high that the same shoppers see 20+ impressions?
- Is retargeting reaching shoppers who already bought (loyalty) when the goal
  was acquisition?

## AMC: questions standard reports cannot answer

AMC is free for any sponsored-ads advertiser (since Sept 2025, from the ads
console). If the source can run AMC queries, the high-value ones are:
- **New-to-brand by campaign / targeting / placement** — the real cost per new
  customer, including for Sponsored Products.
- **Path to conversion** — which combinations of ad types precede purchases
  and new-to-brand purchases.
- **Overlap** — shoppers exposed to both sponsored ads and DSP; does the
  combination convert better than either alone?
- **Gateway ASINs** — which first purchase leads to the most repeat value.
- **Customer lifetime value by acquiring campaign** — the lifetime value a
  campaign's new customers went on to produce.
- **Time to conversion** — how long purchase decisions take (feeds attribution
  lag and bid strategy).

AMC results are aggregated with privacy thresholds; small cells are suppressed.
Say when a result is suppressed rather than reading zero.

## Cautions

- Brand halo and view-through make upper-funnel ROAS look higher than its
  incremental value; never add halo sales to SP sales twice.
- Measure DSP through TACoS and branded-search lift as well as attributed ROAS.
- A holdout (geo or audience) is the proof; recommend one when DSP spend is
  large and its value is the open question.

## Sources

- Prospecting vs retargeting ROAS bands, NTB focus: https://www.sellerlabs.com/knowledge-base/amazon-dsp-explained-when-its-worth-it-for-smbs/
- Brand halo definition (Amazon DSP reporting guide): https://m.media-amazon.com/images/G/09/AdvertisingSite/AmazonDSPLearningHub/CampaignManagement/Amazon_DSP-SS_Reporting_Guide_EN.pdf
- AMC available to sponsored-ads advertisers; first four analyses: https://www.darkroomagency.com/observatory/amazon-marketing-cloud
- AMC instructional queries (LTV, NTB gateway, path, overlap, time to conversion): https://help.intentwise.com/intentwise-explore-amc
