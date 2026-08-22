# 01 — Earnings coverage desk

**You want:** initiation notes on five companies, built from filings, that a
skeptic could attack. **You don't want:** five summaries of what management said
about itself.

**Import:** `01-earnings-coverage-desk.json` → **New project → load a setup
file**. Then paste your API key, put your five tickers in the goal, and create.

| | |
|---|---|
| Shape | **Deliver** — it ships one pack and finishes |
| Teams | Research (`equity-analyst` ×2), Data (`market-data-engineer`), Compliance (`compliance-officer`) |
| Budget | $400/month, approvals over $50 |
| Needs | A market-data credential, or nothing but the web |

## Why this shape

A coverage pack has a definition of done, so it is a Deliver project: the CEO
owes you a plan before any work starts, and the platform proves each
requirement rather than taking a note's word for it.

## The plan you should expect

The CIO proposes; you accept on the **Plan** tab. A good plan looks roughly
like this, in three stages:

| | Requirement | Acceptance test |
|---|---|---|
| R-01 | The five names have point-in-time fundamentals back eight quarters | `command` — the store check passes with as-of dates on every row |
| R-02 | A comparable table exists, every figure sourced | `observable` — each figure in `comps.csv` resolves to a filing URL |
| R-03 | Five notes, each with a bear case and a named falsifier | `observable` — five notes, each carrying a sourced numbers section |
| R-04 | Every note has passed compliance with no open FIX items | `observable` — a dated PASS review per note |
| R-05 | One page on where framing and statements disagree | `observable` — names the specific adjustments, with page references |

Refuse anything vaguer. `{"type": "observable", "target": "the notes are good"}`
is refused at the API, and that refusal is the feature.

## What to watch

- **The bear case is the tell.** If a note's bear case is a sentence, the
  analyst has not done the work. Fail it on the spot-audit.
- **Sourcing.** Pick one figure a week and open the filing yourself. Four
  minutes; it is the difference between research and plausible prose.
- **The drought.** Set to 48 hours here: filings work is not continuous, and a
  24-hour drought clock would nag a desk doing exactly the right thing.

## The line this desk does not cross

Every role in this bundle is research-only. Nothing places an order, nothing
touches a brokerage credential, and nothing written here is advice for anyone
but you. If a task would cross that, the CIO's charter says refuse it and tell
you — and the compliance reviewer is the second pair of eyes.
