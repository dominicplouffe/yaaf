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
| 3 | The plan screen — stage board and dependency graph | **done** |
| 4 | Velocity, cost attribution, error load | **done** |
| 5 | Commitment, and a forecast that refuses things | **done** — migration 0003 |
| 6 | Deliverables, attestation, the sampled audit | **done** — migration 0004 |
| 7 | The status meeting, and six levers | **done** — migration 0005 |
| 8 | Explore / Deliver / Operate, and the learning loop | **done** — migration 0006 |
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

## Stage 3, as built

- `GET /observe/projects/{id}/plan` — **one response for both views**, so the
  stage board and the dependency graph cannot disagree with each other, and
  the critical path arrives computed server-side from the same arithmetic the
  feasibility refusals quote (bias multiplier included).
- The critical path is the longest chain of **unsettled** work: met and cut
  requirements conduct but weigh nothing, so the number beside the path is
  always the work still ahead, and cutting the heavy branch of a diamond
  visibly reroutes the path through the light one.
- `Plan.tsx` — Stages / Dependencies on a toggle. Stage board groups by stage
  with the open stage marked; the graph is layered SVG with arrows from
  blocker to blocked and the critical path accented.
- **Scope decisions happen on the screen**: accept, cut, and the observable
  verdict are buttons on the requirement they concern. Explore projects get no
  Plan tab at all — a tab that only ever explains its own absence is clutter.

## Stage 4, as built

- `yaaf/server/velocity.py` — **one producer** for the pace: requirements met
  per week over trailing 3 and 12 weeks, from `requirement_events`. The CEO's
  context pack and the chairman's Plan screen read the same dict, so they can
  never quote different speeds.
- **R-00 opening is not pace.** The gate lifting is excluded from the MET
  count, or every project would start with one free unit of velocity and the
  forecast would open optimistic by exactly one requirement.
- **Errors have a number and a name.** A run that dies — failed, killed,
  refusal-terminated, or reaped — charges `failure_load` on the requirement it
  was stamped against, from both death paths (`finalize` and the reaper). The
  velocity block names the single worst offender, so a falling pace reads
  "R-07 has eaten N runs", not "we got slower".
- **Attribution is measured, not assumed**: the window reports what share of
  runs (and dollars) were stamped against any requirement at all. In the audit
  62% of runs carried nothing; now overhead is a visible line instead of a
  smear.
- Plan screen grows **Pace** ("2.3/wk — 7 left, ~3 weeks at this pace") and
  **Waste** ("12% of runs died — R-07 worst at 9") tiles.
- Also this session, out of order: the **mode picker on New project** — stage
  3's Plan tab was unreachable from the UI because mode was API-only until
  stage 8's picker. A screen nobody can reach is not shipped; a compact
  three-card picker landed the day the screens behind it existed.

## Stage 5, as built

- `commitments` (migration 0003). The CEO proposes one — but the proposal has
  **no date field** (extra fields die at the schema), and the platform stamps
  its own projection as the proposed date, rounded to end-of-day because a
  promise is day-shaped. The chairman accepts with that date or his own,
  declines with a reason, or moves it later — his three verbs, none of them
  an agent's.
- **One producer for the projection**: `velocity.commitment_projection` —
  remaining ÷ trailing met-per-week (3w, falling back to 12w), and before any
  pace exists, the plan's own arithmetic (adjusted runs at the daily cap,
  labelled as the best case it is). The propose route quotes it, the janitor
  stamps it, the scope refusal shows it, the Plan screen draws it.
- **Acceptance freezes the ground**: `constitution_version` and
  `scope_snapshot` are written once and never updated. What the janitor last
  saw lives in `projection`, and re-validation is a **state diff** against it
  on three triggers — constitution version moved, scope keys changed, failure
  load crossed `commitment_failure_reforecast` (new constitution rule, default
  5, baseline advances only when it fires so a storm triggers once, not every
  tick). Never event hooks: amendments apply inline from three callers.
- The janitor walks the status itself — accepted / at_risk / breached / met —
  and every transition is a Notice to the CEO; at_risk and breached also email
  the chairman with the numbers. Those two notice kinds are urgent AND add a
  `commitment` wake reason, which is governance-classed — the re-forecast can
  spend the reserved slice on exactly the day ordinary work burned the cap.
- **The guarantee is a refusal**: while the projection lands past the date (or
  the date has passed), `POST /agent/requirements` is refused with the
  arithmetic — pace, projection, days late — in the message. Existing work
  continues; the freeze is on scope, never on finishing. The freeze computes
  live from the same producer, so it can never lag the janitor's last tick.
- Plan screen: proposal strip (accept with your own date / decline), slip
  banner with the move-date lever, a Commitment tile, and the **burn-up** —
  the met line against two markers, *committed* (his) and *projected* (the
  platform's), which is the whole conversation.
- 13 invariant tests: smuggle a date (dies at the schema), accept from a run
  token (no route), add scope past a breach (refused, arithmetic shown, task
  creation still allowed), amend the constitution out from under the freeze
  (re-validated and disclosed; the frozen version untouched).

## Stage 6, as built

- **`artifacts` is the receipt** (migration 0004): name, platform-incremented
  version, sha256, size, a real publish time, the author, and the requirement
  ids it claims to satisfy — validated against the project's scope, a claim
  and never a verdict. A run stamped with a requirement publishes against it
  by default. The Deliverables screen shows the hash and the "satisfies" keys.
- **`evidence.task_id` is a real FK.** The design said NOT NULL; what shipped
  is a refusal instead, because two taskless authors are legitimate — webhook
  connectors and Explore-mode agents. In Deliver/Operate, agent evidence
  naming no task (or a fake one) is refused with the reason spelled out.
- **Webhook secrets exist from birth.** `create_project` provisions both
  signing secrets and shows them exactly once, endpoint URL beside each (New
  project screen + a Settings card with regenerate). The `platform_attested`
  path itself was never the missing piece — provisioning was.
- **The sampled audit**: `audit_sample_rate` + `audit_response_hours` as
  constitution rules; the janitor rolls the dice server-side AFTER an
  observable close (command-checked closes are never sampled — the platform
  already ran those). Every considered close gets an `audit_samples` row,
  drawn or skipped, so nothing is re-rolled and the review's coverage line
  has a denominator. Pending samples land on the Work screen with two
  buttons; a failed one hits the task's audit fields and the CEO's next pack;
  expiry records "not reviewed" and blocks nothing. No agent route serves any
  of it — proven by a test that reads the audited claimant's own pack.
- **Anthropic tooling**: a memory-tool-shaped `memory` MCP tool (view/create/
  str_replace/rename/delete) over the governed store — same caps, same
  curator rules, because it IS the same store; `PreCompact` + `Stop` hooks
  that are record-only by construction (their return carries no decision key,
  so there is nothing for an agent's output to learn to pull); role-defined
  skills staged into a platform-owned `.claude/skills` with an explicit SDK
  allowlist — `[]` when none granted, so an agent-authored skill file stays
  dark; and `run_token_budget` handed to the SDK as an API-side task budget.

## Stage 7, as built

- **The janitor owns the meeting.** `reviews` (migration 0005): opened,
  numbered and emailed on `review_interval_days` with zero tokens — regardless
  of budget, an empty board included, every mode including Explore. One open
  at a time; an unattended one nags at 24h and escalates at 72h (louder email
  + audit row, never a brake).
- **The numbers are frozen at open**, from the same producers every screen
  reads: pace, the commitment projection, budget month-to-date, scope
  movement, audit coverage since the last review — so the record cannot drift
  as the week moves on.
- **The narrative rides the reserve.** An open review missing its narrative is
  a dispatcher wake of reason kind `review` — governance-classed, so it can
  spend the reserved slice on exactly the day the ordinary allowance is gone.
  `review_narrative` (new agent verb + tool) files it once; only the CEO;
  emailed to the chairman on arrival.
- **Six levers**, each acting through the same route it would outside the
  meeting and recorded on the review with the chairman's reason: cut; **add**
  (`POST /admin/projects/{id}/requirements` — chairman-authored scope,
  accepted at birth, est_runs required because the feasibility arithmetic
  does not care whose idea the work was); **reword** (words move, key /
  estimate / history / failure load do not — no laundering); move the date;
  raise the budget (the amendment rails, so it is recorded and disclosed like
  any rule change); accept the slip (an explicit decision on the record).
  Closing with zero decisions records "no change".
- **The next review remembers**: it opens with the prior review's decisions
  beside what they bought — requirements met since, pace then vs pace now.
- **The Review tab** is the meeting: frozen tiles, the narrative, prior
  decisions with their price, six buttons, close.

## Stage 8, as built

- **Explore carries a window**: `projects.deadline`, settable only there — a
  bare date is refused for Deliver (commitments) and Operate (cycles). When
  it passes, CEO notice + chairman email exactly once; nothing pauses itself,
  because an Explore project's worth was always his call. Overview counts it
  down; the New-project form takes it beside the mode picker.
- **Deliver completes through his gate.** The janitor detects "everything
  true" and emails once (`delivery_outstanding` is one function, so the email
  and the refusal can never disagree); the Overview button decides; the
  receipt's load-bearing row is **"failed once before"** — per requirement,
  est vs actual runs and the failure load it ate on the way to true.
  `COMPLETED` is terminal.
- **Operate runs on the constitution's rhythm**: `cycle_frequency` +
  `cycle_day` via `_rule()` (29th–31st refused at validation, never clamped),
  read in the one `timezone`. Cycles roll in the **janitor**; a roll closes
  the cycle (completed / missed / skipped), carries unsettled scope forward by
  `requirements.cycle_id`, and reopens R-00 — **each cycle plans fresh**, on
  a governance-classed `cycle_plan` wake. At most one open cycle ever: a
  three-week pause resumes into ONE (janitor never touches paused projects,
  and `start` opens exactly one). Two consecutive SKIPPED cycles pause the
  project and email him.
- **The learning loop**: closing a review on an Operate project writes the
  cycle's `narrative` (frozen numbers + the chairman's decisions + the CEO's
  words, uncapped); the next cycle's pack carries the last three **verbatim**
  — proven by the cycle-7-carries-4/5/6 test. Rolls without a review get an
  honest platform stub, never a silent gap. Cycle history with whole
  narratives is on the Plan tab.
- Scope became cycle-aware in ONE place (`policy.scope_requirements`), so the
  plan gate, feasibility, critical path, velocity-remaining and the Plan
  screen all read the open cycle for free; `estimate_bias` deliberately reads
  ALL cycles, or a CEO that lowballs every cycle would start each one
  forgiven.

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
