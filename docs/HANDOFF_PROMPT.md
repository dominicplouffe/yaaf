# Handoff — paste everything below the line into a fresh session

---

You are working on `yaaf` (a framework that runs a company of AI agents — a CEO
that plans, role-based employees that work, one human "chairman": Dominic,
dplouffe@gmail.com). Repo `/home/dplouffe/projects/yaaf`, branch **`rebuild`**.
Read `docs/REBUILD.md` first — it is the plan of record, with per-stage
"as built" sections and links to the full-plan artifact and the adversarial
validation that preceded it.

## Status: THE REBUILD IS COMPLETE

All ten stages (0–9) are built, tested, deployed and verified live as of
19 Aug 2026. Schema at `0007 (head)`; ~374 tests passing. The per-stage
"as built" sections in REBUILD.md are the authoritative record — including
where the build deliberately deviated from the design (evidence NOT NULL
became a refusal; skills default dark; day-granular commitment dates).

The one change under everything: **the requirement is the root object**. A
requirement is a thing that must be true at the end; it carries an acceptance
test the platform runs; tasks exist to make requirements true; outside Explore
mode a task serving no requirement is refused. yaaf's thesis: **rules live in
the server, never in the prompt** — every rule is an API `Refusal`
(yaaf/server/policy.py) or an automatic state change.

## What exists, in one line per stage

- **0** — fresh DB, one baseline, every limit in `ConstitutionDoc` via
  `_rule()` (settings UI generates itself); `reserved_governance_runs`.
- **1** — requirements/events/edges; CEO proposes, chairman accepts; no agent
  route writes `met`; dependency+cycle gates; requester-owns-the-test.
- **2** — R-00 the plan gate; feasibility with estimate-bias multiplier ≥1;
  two sums (proposals count while planning, accepted scope decides HOLDS);
  stage gate; runs stamp `requirement_id`.
- **3** — Plan tab: stages/dependencies toggle over ONE endpoint;
  server-computed critical path; accept/cut/verdict buttons; mode picker.
- **4** — `velocity.py`, one producer: met/week 3w+12w, waste with the worst
  offender named, attribution share; `failure_load` charged from both death
  paths; Pace + Waste tiles.
- **5** — `commitments`: CEO proposes with NO date field (platform stamps its
  projection, end-of-day), chairman accepts/moves/declines; janitor walks
  accepted→at_risk→breached→met and re-validates on three state diffs
  (constitution version, scope keys, failure-load threshold rule); scope
  frozen past the wall (proposals refused with arithmetic; existing work
  continues); burn-up with committed + projected markers; governance-classed
  `commitment` wake.
- **6** — `artifacts` receipts (name/version/sha256/satisfies); evidence
  task_id real FK + refusal in Deliver/Operate; webhook secrets at project
  creation (shown once); `audit_samples` drawn server-side after observable
  closes at `audit_sample_rate`, expiry never blocks, invisible to run
  tokens; memory-tool-shaped MCP tool; record-only PreCompact/Stop hooks;
  role-defined skills with explicit allowlist ([] = dark); `run_token_budget`.
- **7** — `reviews`: janitor opens/numbers/emails with zero tokens (empty
  board still fires); frozen numbers; CEO narrative via `review_narrative` on
  a `review` governance wake, files once; SIX levers (cut/add/reword/
  move_date/raise_budget/accept_slip) each acting through its normal route
  and recorded with the chairman's reason; next review shows decisions beside
  what they bought; nag 24h → escalate 72h, never a brake; Review tab.
- **8** — Explore `projects.deadline` (refused elsewhere; passing tells both
  parties once, pauses nothing); Deliver completion gate (janitor detects,
  chairman's button decides, receipt's load-bearing row is "failed once
  before"; COMPLETED terminal); Operate cycles (`cycle_frequency`/`cycle_day`
  rules, 29–31 refused; rolls in janitor; at most one open cycle; carries
  unsettled scope by `requirements.cycle_id`; R-00 reopens per cycle on a
  `cycle_plan` governance wake; two skips → pause + email); the learning
  loop: review-close writes `cycle.narrative`, next pack carries last three
  VERBATIM. Scope is cycle-aware in ONE place (`policy.scope_requirements`);
  `estimate_bias` reads all cycles on purpose.
- **9** — `projects.topology` stamped at creation; `experiments` +
  `experiment_arms` freeze goal/scope/constitution; arms built through the
  normal machinery with chairman-authored scope accepted at birth; four
  metrics from one producer (`yaaf/server/experiments.py`), None on zero
  denominators; solo self-grading asymmetry printed on every report; stop
  rule halts a budget-breached arm with nothing met (watches PAUSED
  projects); Experiments page.

## How to work (the chairman's standing instructions)

- Plain English, no jargon walls; tight updates; he is visual; **UI over
  CLI**; Apple-grade UX bar. Answer the question asked. Don't re-litigate
  settled decisions (REBUILD.md "Decisions the chairman has made").
- Loop per change: build → full test suite → commit (narrative commit
  messages, subject line a sentence about the *why*, end with
  `Co-Authored-By:` your model name `<noreply@anthropic.com>`) →
  `./update_single_server.sh --no-pull` (rebuilds BOTH images, keeps data) →
  verify on the live box (curl the API / query the DB) → update REBUILD.md →
  commit that too.
- Tests: `python3 -m pytest tests/ -q` from repo root, ~374 passing.
  `claude_agent_sdk` IS installed locally now (the old note about it missing
  is stale). `test_ws.py::test_good_token_gets_stamp_and_changes_move_it` is
  flaky under full-suite load and passes in isolation.
- Invariant tests live in `tests/selftest_invariants/`, over HTTP with real
  minted run tokens, each trying to BREAK a rule. Helpers: `tests/conftest.py`
  (`make_project`, `amend`, `live_token`, `agent_by_name`, `bearer`,
  `seed_role`) and `tests/selftest_invariants/test_requirements.py`
  (`deliver_project`, `propose`, `accept(..., project)`, `make_task`, `tick`).

## Architecture rules (violating these is how previous plans failed validation)

- Enforcement is a `Refusal` with a message written FOR the agent, or an
  absent verb. Advisory-only anything is suspect.
- Governed knobs go in `ConstitutionDoc` via `_rule()`; disclosure rides
  `Notice` rows consumed exactly once by the next context pack.
- "Done" is never self-graded, at any level.
- Automatic state changes → janitor (15s, budget-independent); wake decisions
  → dispatcher (budget-gated). Governance wakes (`review`, `cycle_plan`,
  `commitment`) may spend the reserved slice.
- **Migrations:** `0001_baseline` is `create_all`, so every model change needs
  a guarded revision (`yaaf/core/migration_ops.py` helpers). Keep
  `test_migration_chain.py`'s `STAGE_*` lists honest. No PG enums. New id
  prefixes in `yaaf/core/ids.py`.
- One producer per number (see `velocity.py`, `plan_arithmetic`,
  `critical_path`, `commitment_projection`, `experiments.arm_metrics`).
- Web: `web/src/` React SPA, dark cockpit theme; `npx tsc -b && npm run build`
  in `web/` before deploying.

## The live box

Single-server Docker deploy (`deploy-db-1`, `deploy-api-1`, `deploy-runner-1`,
`deploy-proxy-1`). Admin token: `grep YAAF_ADMIN_TOKEN deploy/.env`. DB read:
`docker exec deploy-db-1 psql -U yaaf -d yaaf`. Schema at `0007 (head)`.
On it: 9 restored projects (draft, explore) + 2 stopped smoke projects. No
project is running; nothing is spending. Pre-wipe backup:
`~/yaaf-backup/pre-rebuild-2026-08-18.sql.gz`; seed: `~/yaaf-seed/`.

## What might come next (not started; the chairman decides)

- Run a real project end-to-end through the new machinery (needs a billing
  credential attached; nothing on the box is spending today).
- Run the first real topology experiment from the Experiments page.
- The open decisions from before the rebuild: squash-to-public, ToS stance,
  Proxmox migration.
