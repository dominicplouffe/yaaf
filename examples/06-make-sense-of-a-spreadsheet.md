# 06 — Make sense of a spreadsheet

**What you get:** an analysis of a messy CSV that tells you what is actually
happening in it — with the arithmetic shown, as a script you can re-run.

**What it teaches:** how yaaf handles bulk data (in files, with code — never by
reading rows into a model's context), and how it handles the question a
dataset *cannot* answer.

**What you need:** your Anthropic API key. **The data ships with this repo** —
420 rows of support tickets at
[`examples/data/support-tickets.csv`](data/support-tickets.csv). Bring your own
later; start with this one so nothing is in your way.

**Cost:** capped at $40.

---

## Set it up

**+ New project → Start from a setup file →** `06-make-sense-of-a-spreadsheet.json`.

Then get the data into the workspace. Either:

- **attach a repo** (Settings → Integrations → GitHub) with the CSV committed
  at `data/support-tickets.csv` — the runner clones it in at lease time; or
- **say so in the Boardroom**: *"the CSV is at this URL, fetch it into
  `data/support-tickets.csv` as the first task"* and paste the raw GitHub link.
  The agents have web access; this works fine.

To use your own data instead, change the path in the goal and describe what the
columns mean. Agents guess column meanings about as well as you'd expect.

## The data is messy on purpose

The sample has, deliberately:

- **two date formats** in one column (`2026-05-27` and `30/06/2026`)
- **inconsistent product names** (`Checkout` and `checkout` are the same thing)
- **blank response times** scattered through
- **a duplicated row**
- and one real finding buried under all of it

Every one of those changes an answer if it is ignored, which is the point. A
clean dataset teaches you nothing about what an analyst is for.

There **is** something real in there to find. Not saying what.

## How the bulk-data rule works

The `researcher` role holds `data_processing`, and its charter is explicit:
fetch once into the workspace, aggregate with `python3`, report the aggregate.
The platform backs that up:

- **`data_fetch`** writes a source's whole result to a workspace file and
  returns only a path, a size, a digest and a 2 KB preview.
- A data source marked **bulk** is not mounted as tools at all, so its rows
  *cannot* reach a context by accident.
- `pandas` and `duckdb` are baked into the sandbox image, so there is no setup
  step and no excuse.

Why it matters: 420 rows read into a context is a small waste. Four hundred
thousand is a large one, on every turn after — and a model that "counts" rows
by reading them gets it wrong. **A number computed in code is a fact; a number
eyeballed is a guess.**

## What "done" looks like

```json
{"type": "command",    "command": "cd /workspace && python3 analysis/run.py --check"}
{"type": "observable", "target": "deliverables/findings.md states the sample size behind every claim"}
{"type": "observable", "target": "deliverables/findings.md lists what was cleaned and how many rows each fix touched"}
{"type": "observable", "target": "deliverables/findings.md names at least one question this data cannot answer, and why"}
```

That last one is the one to keep. The failure mode of an eager analyst is not
being wrong — it is being confident about something 420 rows cannot support.
Making "say what you can't know" a requirement is how you get it said.

## What to watch

- **Re-run the script yourself.** It is committed for exactly this. If your
  number differs from the memo's, the memo is wrong.
- **Correlation stated as cause.** "Response time drives satisfaction" and
  "slow tickets are also hard tickets" look identical in this data.
- **A cleaning step with no count.** "Removed duplicates" is not a finding;
  "removed 1 duplicate row (0.2%)" tells you whether it mattered.
- **Averages hiding shapes.** Ask for the distribution in the Boardroom if the
  memo only gives you means.

## Try this next

- Point it at **your own export** — a CSV from your CRM, your bank, your
  analytics, a government open-data file. This is the example that most often
  turns into something people keep running.
- Then **[07 — Tell me what changed](07-tell-me-what-changed.md)**, which is
  the same discipline applied to a moving target.
