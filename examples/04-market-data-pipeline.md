# 04 — The data layer everything else stands on

**You want:** prices and fundamentals you can trust, loaded unattended.
**You don't want:** a silent bad tick that quietly invalidates six weeks of
research.

**Import:** `04-market-data-pipeline.json`. Set `github_repo` to a repo you own
before you start — the workspace is where this project lives.

| | |
|---|---|
| Shape | **Deliver** — build it, prove it, stop |
| Teams | Data (`market-data-engineer` ×2), Platform (`engineer`) |
| Budget | $350/month, $3 per run |
| Needs | GitHub credential, market-data credential |

## Why it comes before the analysis

Every note, screen and backtest downstairs is only as true as this. The role
prompt makes the correctness rules the engineer's job whether or not a task
mentions them: proper adjustment, point-in-time labelling, survivorship, UTC
with the session recorded, idempotent loads, and validation on arrival.

## The plan you should expect

Stage them, and let the dependency graph refuse work that is out of order:

| | Requirement | Acceptance test |
|---|---|---|
| R-01 | Daily adjusted prices for the universe, back N years | `command` — row counts match the trading calendar, no gaps, no non-positive prices |
| R-02 | Delisted and merged symbols are in the store | `command` — a known delisting resolves |
| R-03 | Fundamentals carry the date each figure became known | `command` — `--assert-point-in-time` |
| R-04 | A re-run of any backfill produces the same table | `command` — run twice, diff empty |
| R-05 | Schema docs and a freshness contract per dataset, licence included | `observable` — a reader can tell what each covers and what they may do with it |

R-04 is the one people skip. A load that is not idempotent turns every retry
into a data incident.

## What to watch

- **Loud failure.** A load that finds bad rows must fail, not warn. Ask to see
  a deliberately broken load fail.
- **Licensing.** The role is told to check terms before pulling and to file a
  suggestion rather than scrape around a paywall or a rate limit. If a dataset
  cannot be had legitimately, that is a finding, not an obstacle.
- **The `drought_hours: 120` setting.** Infrastructure work produces little
  external evidence by nature. A 24-hour drought clock here would spend the
  project nagging.
