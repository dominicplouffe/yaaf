# Working in this repo

## Running tests — pick the smallest tier that answers the question

The suite is ~1,350 integration tests that drive the real app over HTTP against
a real database. That is deliberate (see CONTRIBUTING house rule #1) and it means
the full run costs about a minute. A minute is cheap once. It is not cheap after
every edit, and running it after every edit is the single biggest drag on how
fast this project moves.

So: **match the tier to the question you are actually asking.**

| Tier | Command | When |
|---|---|---|
| 0 — iterating | `pytest --testmon -q` | after each edit. Runs only tests whose code you changed. Seconds. |
| 1 — this area works | `pytest tests/selftest_invariants/test_<area>.py -q` | before you claim a change works |
| 2 — ready to commit | `pytest -q -n auto` | **once**, at the end |
| 3 — Postgres | `YAAF_TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55432/postgres pytest -q -n auto` | only when you touched leasing, JSON columns, timestamps, or migrations |

**Do not run the full suite after every edit.** If you changed one function,
tier 0 or tier 1 is the honest check. Tier 2 belongs at the end, once, before a
commit — not between exploratory edits, and not before you and the human have
even decided the change is worth keeping.

`--testmon` needs one full run to build its map (`pytest -q` once, then
`--testmon` from then on). It is a development accelerator and **never a gate**:
CI always runs everything.

Always pass `-n auto`. The suite parallelises nearly linearly and there is no
reason to run it serially except to read one test's failure output.

## Keeping the suite fast

Four patterns carry the speed. `tests/selftest_invariants/test_suite_hygiene.py`
enforces the first three mechanically, because in this repo a rule is a refusal
rather than a comment:

1. **Never call `create_app()` in a test.** Use the `client` fixture. The app is
   built once per worker; building it per test cost ~28ms × 1,350 ≈ 38 seconds.
2. **Never build the schema in a test** (`create_all`, `reset_for_tests`). The
   `client` fixture hands you an empty database; the schema is a session cost.
3. **Never run a fire drill for a report you only read.** Use the `report`
   fixture in `test_rehearsal.py`. A drill is a whole project lifecycle, ~3
   seconds. Run your own only when asserting on a *side effect* of that drill.
4. **Prefer `make_project(client)` as-is.** It is ~28ms and not worth optimising
   around; keep tests readable.

When you add a test that is unavoidably slow, that is fine — say so in the test's
docstring so the next person knows it is deliberate.

## House rules

`CONTRIBUTING.md` holds the rules about what the product may and may not do.
Read it before adding a rule or an endpoint; it is short and it is the point of
the project.

## Adding a concept? Match its sibling's footprint

The most expensive mistake made in this repo is not a wrong design. It is a
right design wired into half the places it belongs. A new object gets its
model, its endpoint, its janitor pass and its tests — and then silently misses
the daily brief, the agenda, the change stamp, the MCP reader and the CLI,
because each of those is a different file and **not one of them fails**. The
suite stays green. The gap only shows up in production, as a screen that is
quietly stale or a section that claims completeness it no longer has.

So before calling a new domain concept done, find its nearest existing sibling
and diff the footprints:

```bash
git grep -l Approval -- yaaf web/src docs | sort > /tmp/sibling
git grep -l Ask      -- yaaf web/src docs | sort > /tmp/new
comm -23 /tmp/sibling /tmp/new
```

Every line that comes back is either a place your concept belongs, or a
sentence you owe about why it does not. Both answers are fine. Skipping the
question is what is not — it is how `Ask` shipped four times in a row missing a
surface `Approval` had held for months.

### Two red flags that mean you are already off the path

1. **You are editing an existing test's assertion so your change passes.**
   That assertion is a rule somebody wrote down, usually after being burned.
   Read what it was protecting before you widen it. Twice in one change it was
   protecting the thing that was actually wrong.
2. **You are writing a comment explaining why your addition is exempt from the
   stated rule of the container you just put it in.** It belongs in a different
   container. The comment is the tell, every time.
