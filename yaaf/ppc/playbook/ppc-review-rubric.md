---
name: ppc-review-rubric
description: Load when reviewing a draft Amazon PPC audit (reviewer seats). The ten-criterion "$60k test" rubric, how to re-derive load-bearing numbers from receipts and scripts, and how to publish ppc-review.json. The audit stays a draft until a review passes.
---

# Reviewing an audit: the $60k test

You did not write this audit, and you are not here to improve its prose. You
are the brand manager's advocate and the numbers' skeptic. Your job is to find
what is wrong before he does. Agents overrate their own work; a separate,
skeptical reviewer is how that is caught.

## Re-derive the numbers

For every **load-bearing** claim (its monthly dollars are $1,000 or more, or it
is in the top three, the economics page or a scale tier):
1. Open its evidence: the receipts (pulled files) and the script.
2. Re-run the script, or write your own from the receipt file, and compute the
   number independently.
3. Record what you got. A difference over 5% (or over the claim's own stated
   range) fails the claim.

## Grade the ten criteria

Each is `pass`, `fail` or `n/a` (with why n/a), with a note saying what you
checked.

| id | Criterion | Fails when |
|---|---|---|
| R1 | **Knows the business** | No portrait; consumable/durable, seller/vendor, top products or lifecycle wrong or missing |
| R2 | **Right yardstick** | ACoS judged against a benchmark or a single number instead of this business's break-even, lifetime value and campaign job; repeat/S&S data available but ignored |
| R3 | **Traceable** | A number without a receipt and script; a non-Amazon field; an assumption presented as data |
| R4 | **Statistically sound** | "Dead"/"waste" without enough clicks for that product's conversion rate; no recent-vs-prior check; attribution lag ignored; confounders (stock, price, Buy Box, deals, season) not checked |
| R5 | **Incremental** | Branded / retargeting sales treated as growth; no new-to-brand view where data exists |
| R6 | **Sees the demand** | SQP (or the best available demand data) not used to size headroom |
| R7 | **Sees the constraints** | Spend increases on products without stock cover; returns, Buy Box, price or rating gaps ignored |
| R8 | **Executable** | A recommendation without entity, today's value, new value, why that number, week, check-in and stop rule |
| R9 | **Honest** | Padding; a healthy area not said to be healthy; a tier filled beyond the evidence; not-covered areas missing |
| R10 | **Tells a story** | No front-loaded answer; chapters without a takeaway; jargon instead of plain English |

## Verdict

`pass` only when every criterion is `pass` or justified `n/a` **and** every
load-bearing claim re-derived within tolerance. Otherwise `return`, and say
exactly what to fix — the claim id, the number you got, the check that is
missing. Do not soften it.

## Publish

`artifact_put` `ppc-review.json`:

```json
{"audit_sha256": "<sha256 of the ppc-audit.json version you reviewed>",
 "verdict": "pass" | "return",
 "criteria": {"R1": {"verdict": "pass", "note": "..."}, "...": {}},
 "rederived": [{"claim": "C3", "reported": 6400, "rederived": 6310,
                "matches": true, "note": "re-ran analysis/budget.py on receipt ..."}],
 "summary": "what must change, or why it passes"}
```

The platform refuses a review of a version that is not the latest audit, a
review by the audit's author, and a `pass` that skips a load-bearing claim.
