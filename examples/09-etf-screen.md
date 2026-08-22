# 09 — Screen the fund universe, and say what you excluded

**You want:** a shortlist of at most twelve funds for a core allocation, with
the reasoning attached. **You don't want:** a ranking by expense ratio.

**Import:** `09-etf-screen.json`.

| | |
|---|---|
| Shape | **Deliver** — screen it, shortlist it, stop |
| Teams | Screen (`quant-researcher`), Diligence (`equity-analyst`), Risk (`risk-manager`) |
| Budget | $200/month |
| Needs | A market-data credential for holdings and spreads |

## The screen the goal actually asks for

Headline fee is the least interesting number. The goal names four axes and the
plan should follow them:

1. **Total cost of ownership** — fee, tracking difference against the stated
   index, spread at typical size, securities-lending revenue returned.
2. **Liquidity that survives a bad week** — not average volume on a calm day.
3. **Structure** — physical or synthetic, domicile, tax treatment, lending
   policy.
4. **Overlap** — pairwise holdings overlap between candidates. Twelve funds
   that are one bet is a shortlist of one, and this is the risk seat's job.

## Bulk data goes in files, not in context

A holdings universe is tens of thousands of rows. The roles are told: fetch
once into the workspace with `data_fetch`, aggregate with `python3`, report the
aggregate. A count you computed is a fact; one you eyeballed is a guess — and a
row that never entered a context was never billed for on every turn after.

Mark a data source **bulk** in Settings and the platform enforces it: the
source is not mounted as tools at all, so its rows cannot reach a context by
accident.

## Acceptance tests worth copying

```json
{"type": "command",    "command": "python -m screen.run --assert-overlap-below 0.6 --out deliverables/shortlist.csv"}
{"type": "observable", "target": "deliverables/shortlist.csv has at most 12 rows and every figure carries an as-of date and a source"}
{"type": "observable", "target": "deliverables/excluded.md names every fund cut in the final round and the axis that cut it"}
```

The third is the one that makes the deliverable trustworthy. A shortlist with
no visible exclusions is an assertion; one that shows its cuts is an argument.

## What to watch

- **As-of dates.** Fund data goes stale fast. Every number should say when.
- **Licensing.** Holdings files often carry redistribution terms. The prompt
  says check before pulling; check that it did.
