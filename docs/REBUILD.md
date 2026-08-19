# The rebuild — plan of record

Nothing has shipped. The database is being dropped on purpose, so every
compromise that existed to keep old projects behaving exactly as they do is
gone. This is the shape yaaf would have had if the chairman's eight requests
had been the starting brief.

**Full plan (diagrams, screens, reasoning):**
https://claude.ai/code/artifact/b12f309d-bf0f-4b54-ab84-6cc9e720e0c8

**The adversarial validation that preceded it:**
https://claude.ai/code/artifact/f919fcaf-908e-48fb-bc93-b3f3840fc85c

This file is a table of contents, not a copy. Edit the artifact; keep this list
of statuses honest.

`docs/DELIVERY_LOOP.md` is the superseded plan. It is kept because its audit
findings are still the evidence base, not because its phases are still the plan.

## The one change under everything

The **requirement** becomes the root object. Today the board is the spine and
the CEO's standing instruction is "keep the queue fed but small" — a pipeline
with no terminus. A requirement is a thing that must be true at the end; it
carries an acceptance test the platform runs; tasks exist to make requirements
true, and a task that serves no requirement is a refusal rather than a smell.

## The chairman's eight, and the structure that carries each

A structure either exists in the schema or it does not, which is checkable
without reading anyone's intent. Four of these were substituted for something a
session preferred, twice over, which is why this table names objects.

| Request | Structure | Stage |
|---|---|---|
| Three project types | `projects.mode`, `Cycle` | 8 |
| The CEO's first task is the plan | Requirement `R-00`, refusal-gated | 2 |
| …built from budget, team, parameters | `Requirement.est_runs` + feasibility gate | 2 |
| Adaptable to plan changes | Review levers incl. **add** and **reword** | 7 |
| …parameter changes | `Commitment.constitution_version` | 5 |
| …errors | `Requirement.failure_load` | 4 |
| A visual plan, in the product | `RequirementEdge` + `Plan.tsx`, both views | 3 |
| A delivery date and a guarantee | `Commitment` + scope refusal | 5 |
| The six-step human loop | `Review`, owned by the janitor | 7 |
| Something is wrong with the topology | `Experiment` + `projects.topology` | 9 |
| Anthropic prebuilt tooling | memory MCP tool, hooks, skills | 6 |

## Status

| | Stage | Status |
|---|---|---|
| 0 | Fresh database, one baseline, one place for rules | **done** — reset run, seed restored |
| 1 | Requirements as the spine; done is true by construction | **done** — migration 0002 |
| 2 | The plan is the CEO's first task, and it has to add up | **done** |
| 3 | The plan screen — stage board and dependency graph | not started |
| 4 | Velocity, cost attribution, error load | not started |
| 5 | Commitment, and a forecast that refuses things | not started |
| 6 | Deliverables, attestation, the sampled audit | not started |
| 7 | The status meeting, and six levers | not started |
| 8 | Explore / Deliver / Operate, and the learning loop | not started |
| 9 | The topology experiment | not started |

Stage 3 is early on purpose. The visual was asked for twice, deferred to last
twice, and dropped both times.

## Decisions the chairman has made

- **Topology is an experiment parameter**, not an assumption. Three arms —
  solo, CEO+team, CEO+leads+workers — same goal, same constitution, run
  concurrently, compared on cost per requirement met.
- **What survives the wipe:** goals, constitutions and roles. Not the lesson
  memories, not the run history. Both are exported to `~/yaaf-seed/` anyway, so
  the decision stays reversible.
- **Both visuals**, one screen with a toggle.
- **Gates plus a sampled audit.** He is asked at commitment, review and
  completion; beyond that the platform samples observable closes at a rate he
  sets as a constitution rule. Sampling never blocks.

## Stage 0, as built

- Every limit moved from `projects.config` into `ConstitutionDoc`:
  `ceo_heartbeat_hours`, `ceo_heartbeat_always`, `ceo_daily_at`,
  `max_concurrent_runs`. `PATCH /admin/projects` now **refuses** a wiring key
  that is a rule name instead of storing a value nothing reads.
- The column is renamed `integrations`, because what is left is outward wiring:
  runtime, MCP servers, Slack, webhook secrets.
- `chairman_report_timezone` → `timezone`. The daily wake read a second copy
  out of config; one timezone now serves the reporting window, the daily wake
  and every future rhythm.
- **`reserved_governance_runs`** (new rule, default 3). Ordinary wakes stop at
  `cap − reserved`; a review, a cycle plan or a re-forecast may use the full
  cap. Clamped to half the ceiling. Without it, the day a company burns its
  allowance before lunch is also the day it cannot report that it did — which
  is the audit's sharpest finding, and the worst bug in the superseded plan.
- `ProjectStatus.COMPLETED`, so a delivered project stops looking abandoned.
- Migrations collapsed to a single `0001_baseline`. Free exactly once.
- `yaaf save-seed` / `yaaf seed`, with the logic in `yaaf/core/seed.py` so it is
  testable. Roles survive a wipe on their own; a page-long goal does not.

## Stage 1, as built

- `requirements`, `requirement_events`, `requirement_edges`. Tasks carry
  `requirement_id`, required outside Explore.
- **The CEO proposes, the chairman accepts.** No agent route writes scope.
- **No agent route writes `met`.** Command acceptance tests run through the
  same platform path as task success checks; observables go to the chairman.
  The strongest form of "done is never self-graded" is an absent verb.
- **Work is refused for a requirement with unmet dependencies**, which is what
  makes the graph a gate rather than a picture. An edge closing a loop is
  refused with the loop drawn out.
- **The requester owns the test:** a claimant may not claim work whose success
  test it wrote. Solo mode is exempt — with one agent there is nobody to hand
  it to. That is a real difference between topologies and stage 9 should
  measure it as a cost of solo, not paper over it.
- `projects.mode` lands here rather than at stage 8, because the requirement
  rules differ by mode. Stage 8 still owns the machinery: cycles, the picker,
  the completion receipt.

## Stage 2, as built

- **R-00**, created by the platform when a Deliver or Operate project starts.
  No task may be created until it closes. Its acceptance test is
  `{"type": "plan"}` — structural, evaluated in the server, and an agent
  proposing that kind is refused.
- **The feasibility gate is back.** The modes design deleted it — *"the
  feasibility check, replaced by a definition of done you set yourself"* —
  because per-phase budgets were a guess the CEO could satisfy by writing
  smaller numbers. The objection was right and it is answerable: settled
  requirements are compared against the runs they actually took, and the next
  plan is read at the CEO's own ratio. Lowball by 3× and you are planned for at
  3×. **Never below 1×**, or a CEO could buy headroom by over-estimating once.
- Refusals show their working — runs needed, runs affordable, the multiplier
  and where it came from — so the CEO can cut rather than guess.
- **Two sums, not one.** While planning, unaccepted proposals count; when the
  platform decides whether the plan *holds*, only accepted scope counts.
  Otherwise a CEO could freeze its own board by thinking out loud.
- R-00 closes and reopens **in the janitor**, because a plan stops being true
  when the chairman lowers a budget or the month's spend accumulates — nobody
  touches a requirement, and a gate that only re-checked when poked would let a
  project build all month against arithmetic that broke on the 3rd.
- **Stages are enforced**: work for a requirement whose stage is not open yet is
  refused, and the open stage advances by itself when the last one settles.
- Runs stamp the requirement they spent against, planning runs included — which
  is what makes estimate-vs-actual a comparison rather than a hope.
- `mode` is settable at creation (`explore` / `deliver` / `operate`). Stage 8
  still owns the picker, cycles and the completion receipt.

## The reset — done, 18 Aug

Reversible: `~/yaaf-backup/pre-rebuild-2026-08-18.sql.gz` (29 tables, verified),
seed at `~/yaaf-seed/` (15 roles, 9 projects).

```bash
./kill_single_server.sh --wipe --yes
./deploy_single_server.sh --skip-roles
# then restore roles + projects from ~/yaaf-seed over the admin API
```

Restored: 15 roles, 9 projects, all in **draft** — restoring a backup does not
start spending. The limits that used to be config came back as constitution
rules (ai-blog-writer kept its `America/Toronto` 09:00 daily wake).

## One thing this stage got wrong, on the record

Stage 1 was deployed and the new tables were not there. `0001` is `create_all`,
so a database that ran it before requirements existed reports head and quietly
lacks three tables and four columns — the API then answers against a schema
that cannot serve it. Caught by looking at the live database after the deploy,
not in review. `0002_requirements.py` fixes it, and
`tests/selftest_invariants/test_migration_chain.py` now fails if a model is
added without a revision: it builds the pre-stage-1 schema, stamps `0001`,
upgrades, and diffs against the models. Verified to fail when `0002` is
removed.
