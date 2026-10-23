# 11 — Numbers you can defend

> **Why not just a Claude Skill?** A skill will analyse this data, and analyse
> it well. It can pin the file, write the script and state the sample — every
> good habit on this page is one you can ask for in a session. What it cannot do
> is **fail.** Nothing rejects the memo when a number in it does not match the
> data, because the thing producing the number and the thing vouching for it are
> the same thing, and there is no gate between them. Here every headline figure
> is recomputed from a pinned copy by a `command` test **the platform executes**
> — written by an agent that is refused permission to judge its own work, and
> scored by something no agent can write to. A memo that disagrees with the data
> is a failing build, not a footnote. The difference is not analysis. It is that
> the good habits stop being habits and become conditions of shipping.

**What you get:** a memo that answers one question, the scripts that produce
every number in it, and a provenance file pinning the exact data those numbers
came from — a bundle you could hand to someone who asks "how do you know?"

**What it teaches:** the difference between an answer and a *defensible* answer,
and the mechanism that enforces it — an acceptance test the analyst cannot
grade.

**What you need:** your Anthropic API key. The data is free, public, and needs
no account; the project fetches it itself.

**Cost:** capped at $60. Most of it goes on the analysis; the checks are cheap
and are the part you are actually paying for.

---

## Import it

**+ New project → Start from a setup file →** `11-numbers-you-can-defend.json`.

Two things before you click Create:

1. **Pick the credential that pays** on the money step.
2. **Change the question,** which is in capitals in the goal. It ships as *which
   college majors actually pay, and how sure can anyone honestly be* — a good
   default because the obvious answer is wrong for a reason you can see. Swap in
   your own question against the same data, or point the whole project at a
   different public CSV.

Everything else can stay. Supervised, two teams.

## The data, and why the project fetches it

Three files from FiveThirtyEight's college-majors dataset (CC BY 4.0), 173 rows
each, joinable on `Major_code`:

```
recent-grads.csv    graduates under 28: totals, employment, median salary, sample size
all-ages.csv        the same majors across every age — does the picture hold?
grad-students.csv   with and without a graduate degree
```

**Nothing ships in this repo and nothing is uploaded.** There is no way to put a
file into a project's workspace from the dashboard — the only file input in the
whole app parses setup bundles — so an example that assumed a CSV was already
sitting there would be an example you could not run. Instead, fetching the data
is **requirement one**, and the project records each file's URL, byte size, row
count and SHA-256 in `data/PROVENANCE.md` before computing anything. Every
number afterwards is a claim about those exact copies.

If a URL stops answering, that is the report for that task. The goal says so
explicitly, because this is a well-known dataset and the likeliest way this
project produces a confident wrong answer is an agent half-remembering it
instead of reading it.

## The org, and the rule between them

| Team | Role | Who | Job |
|---|---|---|---|
| Analysis | `data-analyst` | Nina (lead), Priya | Fetches, cleans, computes, writes the memo |
| Verification | `engineer` | Theo | Writes the checks that recompute the memo's numbers. **Never does the analysis.** |

The separation is the mechanism, and it is enforced rather than requested:
**yaaf refuses to let the agent that wrote a success test be the agent it
judges** — *"you wrote this task's success test, so you may not also be the one
it judges. Whoever asks for the work owns the check — that is the whole reason
the check means anything."* Nina cannot write the test that blesses Nina's
number.

`data-analyst` is new in the role library, and its charter is the other half:

- **A number you did not compute in code is a guess.** Ship the script beside
  the finding.
- **Sample size travels with the claim** — in the same sentence, every time.
- **Say what you cleaned and what it changed**, with the count of rows touched
  and the answer it would have been without the change.
- **Do not average an average.**
- **"This data cannot answer that" is a complete finding**, and the one it will
  be most tempted to pad.

## The plan

| | Requirement | Acceptance test |
|---|---|---|
| R-01 | The data is in the workspace and pinned | `command` — every SHA-256 in `data/PROVENANCE.md` matches the file on disk |
| R-02 | Cleaning decisions are written down with what each one moved | `observable` — `deliverables/cleaning.md` names every dropped or coerced row, its count, and the answer it changes |
| R-03 | **Every headline figure recomputes from the data** | `command` — `python3 -m unittest discover -s checks -q` |
| R-04 | No rank or rate appears without the count behind it | `observable` — a stranger can find the sample size in the same sentence as every ranked claim |
| R-05 | The memo says what this data cannot answer | `observable` — at least one question named as unanswerable, with how thin the evidence is |

## R-03 is the example

The memo publishes its headline figures machine-readably, and Verification
writes a check that re-derives each one straight from the CSV:

```python
# checks/test_findings.py — run by the platform, in a fresh container
CLAIMS = json.loads((ROOT / "deliverables/findings.json").read_text())
ROWS   = list(csv.DictReader((ROOT / "data/recent-grads.csv").open()))

def test_top_median_sample_size_is_published(self):
    top = max(ROWS, key=lambda r: num(r["Median"]))
    self.assertEqual(CLAIMS["top_median_sample_size"], int(top["Sample_size"]))
```

Inflate one number in the memo and the build goes red:

```
AssertionError: 400 != 36
FAILED (failures=1)
```

Three things make that more than a unit test:

- **The platform runs it, not the agent.** A `command` test executes in a fresh
  container on the persisted workspace. Its result is not something an agent
  reports; it is something that happens to an agent.
- **Nothing but the platform writes `met`.** There is no agent tool for it.
- **The author cannot be the judge.** The refusal quoted above.

Note what the check *does not* use: no pytest, no pip install. Every run gets a
fresh container — **the workspace persists, the environment does not** — so a
check may rely only on what the image ships (`python3` and the standard library,
`pandas`, `duckdb`, `node`, `jq`, `git`, `curl`) or on something committed to
the workspace. A package an earlier run installed is gone. This trips everyone
once, and it trips them here, because this is the example where the checks
matter most.

## The three traps in this data

They are real, they are in the file, and the goal names all three so you can
watch whether the project handles them or walks into them.

1. **Tiny samples at the top.** The second- and third-highest median salaries
   rest on samples of **7** and **3** graduates, and 32 of the 173 majors have
   fewer than 30 people behind their number. "Which major pays best?" has a
   clean-looking ranked answer sitting right there in the `Median` column, and
   taking it is the natural thing to do. A ranking that does not carry those
   counts is confidently wrong.
2. **A row with no totals.** `FOOD SCIENCE` is missing `Total`, `Men`, `Women`
   and `ShareWomen`. Every share, rate and denominator over the full set moves
   depending on what you do with it — and the honest memo says which choice it
   made and what the other choice would have given.
3. **The mean of medians.** Summarising `Major_category` by averaging the
   per-major medians is wrong. Go back to the counts and weight it, or say why
   you cannot.

If the memo lands all three, you have something you can defend. If it lands the
headline and skips the samples, you have exactly what a chat session gives you —
and now you can see the difference, which is the point of putting this one last.

## What to watch

- **The first failing R-03 run.** Activity → Runs shows the check output. This is
  the moment the example exists for: a number the analysis was confident about,
  refused by arithmetic.
- **A `PROVENANCE.md` written after the analysis.** Then it is decoration, not a
  pin. The order matters and R-01 is staged first for that reason.
- **Confident language over a thin cut.** "Clearly", "significantly", "by far" —
  check the count in that sentence. This is the failure mode the charter is
  written against, and the one worth failing on the spot-audit.
- **A check that cannot fail.** `assertTrue(True)`, a test over a file the
  analysis also writes, a comparison of a number to itself. Verification's job
  is to make the memo falsifiable; a green suite that proves nothing is worse
  than no suite, because you will believe it.

## Try this next

- **Point it at your own data.** The shape transfers to anything with rows: a
  public export you care about, a CSV your own tooling produces on a URL the
  project can reach. Keep R-01 and R-03 exactly as they are — the pin and the
  recompute are the parts doing the work.
- **Turn it into a standing report.** Change the shape to **Operate** with a
  weekly cycle and the same three teams, and the checks become a regression
  suite over a moving dataset: the day an upstream file changes shape, R-01
  fails instead of the number quietly drifting.
- **Add a red team.** Give [06](06-a-red-team.md)'s `adversary` the memo instead
  of a program, and let it attack the *reasoning* — the denominators, the
  excluded rows, the cut that was chosen after the answer was known.

That is the end of the set. What to build now is in
[writing your own](README.md#writing-your-own) — take the example closest in
*shape*, not in subject, and change the goal.
