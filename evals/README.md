# Golden-transcript evals

The replay harness promised in PLAN §"Agent runtime": *scripted scenarios →
recorded transcripts → regression-judged on rule adherence and planning
quality*. This is what makes editing role prompts and templates in the UI
safe: any change that alters how the scripted org moves through the real API
shows up here as a diff, in CI, at $0 in model tokens.

## How it works

A **scenario** (`scenarios/*.yaml`) names a project spec (constitution
overrides, teams, optional backdating), a cast of scripted behaviors (dotted
refs into `behaviors.py`, registered with the MockModel backend), a tick
count, and declarative expectations.

The **harness** (`harness.py`) builds a fresh SQLite database, brings up the
real FastAPI app in-process, provisions the project through the real admin
API, and runs N `dispatch → lease → execute → janitor` ticks — the same loop
production runs. Nothing is stubbed below the model: refusals, ceilings,
droughts, success checks, and wake decisions are all the real code paths.

The **transcript** is an ordered JSONL of every agent-API interaction
(method, path, request body, response status, response body), plus run
boundaries (`run_start`/`run_end` with wake triggers and summaries), tick
markers, and platform-executed success checks. It is captured by a thin
recording adapter (`RecordingClient`) wrapped around the in-process client
that `AgentApi` talks through — a recorder, not a mock.

**Normalization** strips the nondeterminism before comparison: entity ids
(`task_018f…`, see `yaaf/core/ids.py`) become stable per-prefix placeholders
(`task_0001`) in order of first appearance, ISO timestamps become `<TS>`,
temp paths become `<TMP>`. Run tokens never appear (they travel only in
Authorization headers, which are not recorded). Goldens are recorded on
SQLite with the mock backend — that is what keeps them free and byte-stable.

## Record / check

```sh
python -m evals.run --record            # re-run everything, overwrite goldens
python -m evals.run                     # check (default): re-run, diff vs goldens
python -m evals.run -s rule_breaker_ceo # limit to one scenario (repeatable)
python -m evals.run --list              # list scenarios
```

A check failure prints a readable unified diff and exits nonzero. Record mode
refuses to write a golden whose run fails its own declarative expectations —
a golden must be a *known-good* transcript, not merely a reproducible one.

`tests/test_golden_evals.py` runs every scenario in check mode under pytest,
so CI enforces the goldens alongside the invariant selftests.

## The scenarios

| scenario | what it locks down |
|---|---|
| `disciplined_ceo` | The happy path: hypothesis → team task → lead claims/decomposes → employee delivers → platform runs both success checks → everything `done`, zero refusals, final tick wakes nobody. |
| `rule_breaker_ceo` | Every wall probed: queue ceiling, vague command/observable/prose success tests, internal task during a backdated evidence drought, completion without claim, claim hoarding, `"Done"` as evidence, employee task creation / team-task claiming / project-memory writes. All 11 refused calls are 4xx, captured verbatim, and none changes state — split 9 `refusals` (misconduct) and 2 `reaches` (capacity). |
| `decomposition_flow` | Multi-employee decomposition: assignments created exactly to the subtask ceiling, the one-over refused, both employees deliver, the lead completes only after the platform verifies both parts. |
| `losing_ceo` | **The only scenario that fails by doing too little.** A desk 15 points behind with an empty board must name the deficit to the chairman, ask the question it cannot answer itself, and put work up. Its twin `evals.behaviors.timid_ceo` runs light and says nothing, takes zero refusals, breaks no ceiling — and must fail. |

## Expectations

Expectations are declarative YAML checked by the harness on every run —
independent of the diff, so a scenario is self-validating even when
re-recording:

- `refusals.total` / `refusals.by_path` (paths with ids collapsed to `{id}`)
  as exact ints or `{min, max}` bounds; `refusals.substrings` that must appear
  in some refusal text
- `reaches.*` — identical shape, different meaning. A **reach** is a refusal
  from a wall that limits *more of a permitted thing* (queue ceiling, subtask
  ceiling, one-claim-at-a-time), as opposed to one that refuses a wrong thing.
  A project hitting a ceiling is asking for capacity it has not got; counting
  that as misconduct is what made "probing a wall" and "misbehaving" one
  signal. Classified in `harness.REACHING_MARKERS`, and it applies wherever
  the wall is hit — a classification true in one scenario only is a fudge.
- `tasks.total`, `tasks.status_counts` (exact), `tasks.within_ceilings`
  (final open counts vs the project's own constitution),
  `tasks.structural_success_tests` (re-validated with the real policy check)
- `hypotheses.total`

### Floors

Everything above can only be tripped by doing too **much**. These two blocks
are the other direction, and they exist because `CONTRIBUTING.md` rule 1 says
an automatic state change may point either way:

- `escalation.chairman_messages` — agent-authored messages that landed in the
  boardroom; `escalation.asks_opened`; `escalation.flags_raised`
- `activity.tasks_created`, `activity.runs_used`

Write them as `{min: n}`. **An exact count is not a floor** — it pins one
scripted trajectory and fails departures in either direction, so a CEO that
did more failed as loudly as one that did less. That symmetry is why the suite
could not express ambition before, and
`tests/selftest_invariants/test_ambition_floor.py` pins it so it cannot come
back quietly.

## When a check fails

1. **You changed platform behavior** (policy text, wake logic, API shapes):
   read the diff; if the change is intended, `--record` and commit the new
   goldens with the change, explaining the transcript delta in the commit.
2. **You changed a role prompt/template** and the mock scenarios diverged:
   that is the gate working — the mock behaviors only see the platform, so a
   diff means the *platform's* contract moved, not just prose.
3. **Flaky diff with no change**: that is a harness bug (nondeterminism
   escaping the normalizer). Fix the normalizer, don't re-record.

## The planning-quality judge (runs in CI, costs money)

Golden diffs catch mechanical regressions; they cannot grade planning
*quality* — bet selection, test falsifiability, whether a CEO that was losing
said so. `ModelTranscriptJudge` in `judge.py` does, and
`tests/test_planning_quality.py` is the gate.

**This is the only paid test in the repo.** Two model calls per run, by the
chairman's explicit decision (2026-09-06). Everything else in `tests/` is $0
in tokens and stays that way; if you are adding a test, do not copy this one.

Three properties keep a paid, non-deterministic gate from becoming a flaky one
someone eventually deletes:

- **It grades comparatively.** Scores drift; the gap between a CEO that
  escalated and one that went quiet does not. The gate asserts a margin, not a
  threshold.
- **An unreachable judge is not a bad grade.** Outages, rate limits and replies
  that are not JSON raise `JudgeUnavailable`, distinct from a low score. The
  judge retries once — a model asked for JSON does occasionally answer with
  something else entirely.
- **`YAAF_JUDGE_REQUIRED=1` in CI.** With it set, an unavailable judge *fails*;
  unset (a laptop, a fork) it skips. Without that switch a box with no
  credential turns the gate off silently, which is how every optional gate
  dies.

Its first real verdict is worth reading: pointed at `losing_ceo` — the
scenario written to *exemplify* a CEO doing the right thing — it returned
planning quality **0.40**, flagging `analysis-not-action` and
`trivial-success-test`, because the two tasks that CEO commissioned produce
documents (`grep -q deficit captaincy.md`) rather than change anything. It
reached PLAN-02's and PLAN-08's findings on its own, from the transcript,
without being told they existed.

## The Founder's grader (opt-in, costs money)

`python -m evals.founder` is not part of the golden suite and never runs in
CI: it calls a real model. It grades two real artifacts against
`yaaf/founder/GOAL_GRAMMAR.md` — the chairman's brief as he actually typed it
(`fixtures/founder_brief.md`) and the goal it eventually produced
(`docs/projects/idea-to-customer.json`) — and asserts three things:

1. the reference goal grades as **ready** (the grader knows the standard);
2. the raw brief does **not** (the grader discriminates);
3. the brief is missing exactly the elements the Founder is specified to
   supply — anti-metrics, a quality bar, and a failure deliverable.

The third is the one that would rot silently. If the brief starts grading as
complete on those, the Founder's job has changed and its prompt should say so.
