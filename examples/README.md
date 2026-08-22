# Examples — ten desks you can import and run

Every example is a pair: a short page explaining **why it is shaped the way it
is**, and a `.json` setup file you import to create the project. They are
markets- and research-focused, and they span all three project shapes, solo
mode, and the $0 mock runtime.

New to yaaf? Import **[10 — the $0 dry run](10-dry-run-dollar-zero.md)** first.
It costs nothing and it teaches you more about your own constitution than
reading it does.

| # | Example | Shape | What it teaches |
|---|---|---|---|
| 01 | [Earnings coverage desk](01-earnings-coverage-desk.md) | Deliver | Requirements with tests that can fail, primary-source discipline, a compliance gate |
| 02 | [Pre-open market brief](02-daily-market-brief.md) | Operate (daily) | Cycles, per-agent wake schedules, expectation vs actual |
| 03 | [Backtest one claim](03-strategy-backtest.md) | Deliver | `command` tests the *platform* runs, point-in-time data, honest nulls |
| 04 | [Market data pipeline](04-market-data-pipeline.md) | Deliver | Stages and dependencies, idempotent loads, licence care |
| 05 | [Portfolio review desk](05-portfolio-review-desk.md) | Operate (weekly) | The review and its six levers, risk as a gate, recommend-only |
| 06 | [Solo idea hunt](06-solo-idea-hunt.md) | Explore, solo | One agent under every rule; bets with kill dates; forced verification |
| 07 | [Macro watch](07-macro-watch.md) | Operate (weekly) | A forecast scorecard, data vintages, curated memory |
| 08 | [Market newsletter](08-market-newsletter.md) | Operate (weekly) | Controlled domains, verified external evidence, compliance review |
| 09 | [ETF & fund screen](09-etf-screen.md) | Deliver | Bulk data in files not context, exclusions as a deliverable |
| 10 | [The $0 dry run](10-dry-run-dollar-zero.md) | Deliver (mock) | Rehearse your constitution before spending a cent |

## Importing one

**Prerequisite (once per deployment):** the role library must be loaded, since
these designs hire from it. `./deploy_single_server.sh` does it for you;
otherwise run `yaaf seed-roles roles/`.

**In the dashboard** — the intended path:

1. **+ New project → load a setup file**, pick the `.json`.
2. The form fills in: goal, shape, teams, constitution, and the credentials the
   design expects (as blank rows for you to fill).
3. Paste your Anthropic API key, edit the goal to name *your* tickers, universe
   or claim, and create.

Two things the form owns rather than the file, so check them before you submit:

- **The knobs on the form win.** Queue ceiling, drought hours, budget and
  approval threshold prefill from the file — but the reporting cadence, the
  reporting window, the timezone and "require verification" come from the
  form's own fields. Set them there.
- **The autonomy preset wins.** Supervised or Autonomous is a choice you make
  on the form; the file cannot make it for you. See
  [docs/AUTONOMY.md](../docs/AUTONOMY.md).

**From the CLI** — this path honors the whole constitution as written:

```bash
mkdir -p seed/projects seed/roles
cp roles/*.json seed/roles/
cp examples/01-earnings-coverage-desk.json seed/projects/

export YAAF_API=http://localhost:8080 YAAF_ADMIN_TOKEN=…
yaaf create-user you@example.com "Your Name"
yaaf seed seed --chairman you@example.com        # created in draft, never started
yaaf set-secret anthropic-api-key --project earnings-coverage-desk --kind billing
yaaf configure earnings-coverage-desk --billing-secret-id secr_…
yaaf start earnings-coverage-desk
```

Then watch it: `yaaf dashboard <slug>`, `yaaf board <slug>`, `yaaf runs <slug>`,
`yaaf evidence <slug>`, `yaaf spend-report <slug> --days 7`. Talk to your CEO
with `yaaf say <slug> "…"`, and spot-check with `yaaf audit <slug> --n 5`.

## The three things every example has in common

**The goal is the product.** The CEO plans from it and nothing else. Copy the
*shape*: what exists at the end, who it is for, the deadline, where the inputs
come from, and what would count as working — stated before anyone starts.
"Analyze the data" produces a company that putters at $8/hour.

**Done is defined before work starts.** Every requirement and every task carries
a test, and there are exactly two kinds:

```json
{"type": "command",    "command": "python -m study.checks --assert-point-in-time"}
{"type": "observable", "target":  "deliverables/comps.csv has 5 rows, every figure resolving to a filing URL"}
```

A `command` is run by the **platform**, not the agent that wants it to pass. An
`observable` names something a stranger could check, and lands in your
spot-audit sample. Prose ("verify it works well") is refused at the API.

**These desks recommend; they never trade.** Every role in this folder is
research-only: no agent places, modifies or cancels an order, and none holds a
brokerage credential. Only public information enters the company, data licences
are checked before data is pulled, and nothing produced here is personalised
investment advice. Several bundles set `external_monthly_usd_cap: 0` so the
arithmetic backs the prompt. Keep that line where it is.

## The roles they hire

From [`roles/`](../roles/). The markets family:

| Slug | Role | Good at |
|---|---|---|
| `cio` | Chief Investment Officer (CEO) | Plans, sizes the research agenda, kills what evidence kills. The only agent you talk to |
| `equity-analyst` | Equity Research Analyst | Filings, financials, unit economics, the bear case |
| `quant-researcher` | Quantitative Researcher | Backtests that survive costs, walk-forward and multiple testing |
| `market-data-engineer` | Market Data Engineer | Point-in-time stores, corporate actions, validation on arrival |
| `market-news-analyst` | Market News Analyst | Filings and releases as they land; news versus surprise |
| `macro-analyst` | Macro Analyst | Rates, inflation, curves — and a scored forecast record |
| `technical-analyst` | Technical / Market Structure | Price and liquidity, with measured base rates, never chart-reading |
| `risk-manager` | Risk Manager | Limits, concentration, drawdown. Expected to say no |
| `portfolio-manager` | Portfolio Manager | Turns research into a recommended book with invalidations |
| `compliance-officer` | Compliance Reviewer | Advice-vs-research, claims, licensing, conduct. Escalates real legal questions |
| `finance-writer` | Market Commentary Writer | Prose a reader finishes, every number traced to a research file |

The general-purpose roles (`ceo`, `engineer`, `researcher`, `writer`,
`blog-writer`, `marketer`, `gtm`, `sales`, `support`) and a mobile-game family
ship alongside them.

Roles are **versioned**. Edit the JSON, re-seed, and existing teams keep running
their pinned version until you adopt the new one — so a prompt edit cannot
silently change a project that is mid-flight.
