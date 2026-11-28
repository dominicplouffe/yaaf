---
name: ppc-incrementality
description: Load when judging whether ad sales are real growth or sales the brand would get anyway - branded search cannibalization, new-to-brand share, halo, organic lift (the flywheel), and how to estimate incrementality without a holdout and how to design one.
---

# Would this sale have happened anyway?

Amazon credits an ad with every sale within its window after a click. Some of
those shoppers were already on their way to buy. The money question is never
"what ACoS does this campaign show?" but "what would we lose if it stopped?".

## What the evidence says

- **Branded search ads are often largely non-incremental.** eBay's large
  field experiments found that when brand-keyword ads were switched off, most
  of the traffic moved to the organic result. The effect is not universal:
  at Edmunds, whose organic results ranked lower, about half the paid traffic
  was lost. On Amazon, a brand that ranks 1-3 organically for its own name with
  high purchase share and no competitor ads on that term is the eBay case.
- **Non-branded ads mostly reach new or infrequent customers**, where ads add
  the most.
- **Practitioners who run holdouts commonly find 20-40% of spend defending
  sales the brand already owns.** Treat that as a reason to test, not a number
  to apply.
- **Ad sales feed organic rank.** Sales velocity is read by Amazon's ranking
  as relevance; sustained velocity on a keyword tends to lift organic position,
  and falling TACoS with steady spend is the signature. Pausing ads can cost
  organic rank. So "cut it" is not automatically safe either.

## Estimate it from the data you have

1. **Tag campaigns and search terms** branded / competitor / generic / own-ASIN.
2. **Branded:** from SQP, the brand's organic position proxy (share of
   impressions not explained by ad impressions), purchase share on its own
   brand terms, and whether competitors advertise there (competitive summary,
   share of search). High share + no rivals = mostly non-incremental; rivals
   present = defence has value.
3. **New-to-brand share** of orders by campaign (Sponsored Brands, Display,
   DSP report it; AMC for Sponsored Products). Low NTB share means the campaign
   mostly re-buys customers.
4. **Natural experiments in the history:** days a campaign ran out of budget,
   was paused, or a bid was cut. Did total (not ad) sales of the product fall?
   Compare to similar days. State this as suggestive, not proof.
5. **TACoS by product over time** with ad spend changes.

Apply an incrementality factor per job when pricing scale tiers, and show it
(`ppc-scaling-forecast`). Never present attributed sales as incremental.

## Recommend a test, not a verdict, when the stakes are high

A holdout is the only proof. Design: pick the campaign (e.g. exact-match
branded), cut its bids to near-zero or pause it for 2 weeks in a period without
deals, watch **total** sales of the advertised products against the same
weeks' trend and a comparable product left on; decide the threshold before
starting (e.g. "if total sales fall less than 30% of the ad sales removed, the
spend moves to acquisition"). This becomes a line in the plan with its budget
and its stop rule.

## Sources

- eBay branded-search experiments and Edmunds contrast: https://arxiv.org/pdf/1912.09012
- Organic substitution summary: https://arxiv.org/pdf/2504.16151
- 20-40% of spend defending owned sales (practitioner): https://www.adverio.io/amazon-incrementality-measurement/
- Branded cannibalization in SQP, bid-down holdout: https://amplivus.com/blog/branded-traffic-cannibalization-sqp
- Flywheel and TACoS signature: https://ppc.land/tacos/
- Holdout design: https://eva.guru/blog/amazon-ad-incrementality-measurement/
