# 02 — Pre-open market brief

**You want:** one brief on your screen before the open, every trading day, with
a source under every claim. **You don't want:** a headline feed you still have
to read.

**Import:** `02-daily-market-brief.json`. Set your timezone on the form — every
schedule in this bundle is read in it.

| | |
|---|---|
| Shape | **Operate** — a daily cycle |
| Teams | Tape (`market-news-analyst` ×2), Macro (`macro-analyst`) |
| Budget | $250/month, 60 runs/day |
| Wakes | CIO 05:00 · Nadia 05:30 · Piet 06:00 · Rosa 06:15 |

## Why this shape

The deliverable repeats and should get better each time. That is Operate: the
cycle rolls daily, unfinished scope carries forward, the plan gate reopens, and
the CEO is handed the last three cycles' narratives **verbatim**. The learning
loop is a platform step, not a good intention.

## The rhythm

Per-agent daily wakes do the pacing inside a cycle. The staggered times are the
point: the tape is read before the macro is interpreted, and the CIO's 05:00
wake sets up the day's work before either.

Two consecutive cycles with nothing accepted **pause the project**. A rhythm
nobody dances to is a bill, not a company.

## What "done" looks like each day

```json
{"type": "observable", "target": "deliverables/briefs/YYYY-MM-DD.md exists before 08:00 ET, and every item carries a timestamp and a primary-source URL"}
{"type": "observable", "target": "every scheduled release in the brief names the consensus going in and the actual"}
```

The second one is what makes this worth paying for. A number that lands on
consensus is not news however dramatic the headline, and a desk that cannot
tell you the expectation is reporting, not analysing.

## What to watch

- **Rumours.** The role prompt says label them and never build on them. Check
  that it does. One unlabelled "people familiar" is a fail on spot-audit.
- **Primary sources.** An aggregator's summary of a filing is a lead, not
  evidence. The brief should link the filing.
- **Staleness.** Every brief ends with the timestamp of the last item checked,
  so you know how old the newest thing in it is.

## Cost note

A daily deliverable is the most expensive shape in this folder. $250/month at
60 runs/day is roughly a dollar a brief. If it drifts, look at
`runs_per_project_per_day` before you look at the budget — the cap is a wall,
the run ceiling is a throttle.
