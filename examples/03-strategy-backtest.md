# 03 — Backtest one claim, honestly

**You want:** to know whether an edge is real. **You don't want:** a beautiful
equity curve that only exists in-sample.

**Import:** `03-strategy-backtest.json`. Edit the goal to name *your* claim —
one claim, stated as something that could be false.

| | |
|---|---|
| Shape | **Deliver** — one study, then stop |
| Teams | Quant (`quant-researcher` ×2), Data (`market-data-engineer`) |
| Budget | $300/month, $3 per run |
| Needs | A repo (set `github_repo`) and a market-data credential |

## Why this is the example that shows the platform off

Backtesting is the discipline where self-graded work fails most expensively,
and this is the one project type where "done" can be an actual command:

```json
{"type": "command", "command": "python -m study.run --walk-forward && python -m pytest study/tests -q"}
{"type": "command", "command": "python -m study.checks --assert-point-in-time --assert-no-survivorship"}
```

The platform runs those, in a sandbox, **not** the agent that wants them to
pass. An agent cannot report a green test it never ran.

## The plan you should expect

| | Requirement | Why it is separate |
|---|---|---|
| R-01 | The hypothesis and falsification test are written down, before results | If this is not its own requirement it happens after the fact, which is how edges get manufactured |
| R-02 | A point-in-time, survivorship-free universe exists | Every result downstream is worthless without it |
| R-03 | The study runs end to end and is reproducible from a clean checkout | `command` — someone else's machine, same number |
| R-04 | Results reported in-sample and out-of-sample, separately, with costs | The line most studies quietly cross |
| R-05 | The write-up states how many specifications were tried | A t-stat of 2.5 after 40 tries is noise, and this is the only way you would know |

## What to watch

- **A null result is a delivered result.** If the study says there is no edge,
  the requirement is met and the project is a success. Accept it as such — a
  desk that learns "no" is punished for it will stop reporting "no".
- **Multiple testing.** Ask how many variants were run. The answer should be in
  the write-up without your asking.
- **Costs.** An edge that dies at realistic spread and borrow is a dead edge.

## The line

The quant researcher never places an order and never holds a brokerage
credential. This project tests ideas; you decide what to do with the answer.
