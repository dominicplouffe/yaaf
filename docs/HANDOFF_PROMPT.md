# Handoff — paste everything below the line into a fresh session

---

You are continuing a rebuild of `yaaf` (a framework that runs a company of AI agents —
a CEO that plans, role-based employees that work, one human "chairman": Dominic,
dplouffe@gmail.com). Repo `/home/dplouffe/projects/yaaf`, branch **`rebuild`**. Read
`docs/REBUILD.md` first — it is the plan of record, with per-stage "as built" sections
and links to the full-plan artifact and the adversarial validation that preceded it.

## What this rebuild is

Nothing has shipped; everything is experimental and backward compatibility is
explicitly NOT a goal. On 18 Aug the chairman approved dropping the live database and
re-architecting his eight requests in rather than bolting them on. The one change under
everything: **the requirement is the root object**. A requirement is a thing that must
be true at the end; it carries an acceptance test the platform runs; tasks exist to
make requirements true; outside Explore mode a task serving no requirement is refused.

yaaf's thesis (README): **rules live in the server, never in the prompt.** Every rule is
an API `Refusal` (yaaf/server/policy.py) or an automatic state change. An agent cannot
be talked out of a rule it never held.

## Status: stages 0–4 are DONE, deployed, and verified live

Details in REBUILD.md's "as built" sections. In one line each:

- **0** — DB wiped and reseeded; every limit moved into `ConstitutionDoc` (settings UI
  generates from `_rule()`; `PATCH /admin/projects` refuses rule names as wiring);
  `projects.config` → `integrations`; migrations collapsed to `0001_baseline`;
  `reserved_governance_runs` (dispatcher holds back a slice of the daily cap for
  governance wakes — reason kinds `review`, `cycle_plan`, `commitment`).
- **1** — `requirements` / `requirement_events` / `requirement_edges`; CEO proposes,
  only chairman accepts; **no agent route writes `met`** (absent verb — janitor runs
  command acceptance tests via `RunKind.REQUIREMENT_CHECK`, observables go to the
  chairman's verdict route); dependency gate + cycle refusal that draws the loop;
  requester-owns-the-test claim rule (solo mode exempt, deliberately).
- **2** — **R-00**, the plan requirement, platform-created on start of a Deliver/Operate
  project; no task until the plan holds (accepted + staged + estimated + affordable);
  feasibility gate with **estimate bias**: settled est vs actual runs becomes a ×
  multiplier ≥ 1 on future plans (lowball once, priced in after); two sums — proposals
  count while planning, only accepted scope decides whether the plan HOLDS (else the
  CEO could freeze its own board by speculating); R-00 closes/reopens **in the
  janitor** (`check_plan`) because budgets move without anyone touching a requirement;
  stage gate (work may not jump the open stage); runs stamp `requirement_id`
  (planning runs stamp R-00).
- **3** — Plan tab (`web/src/pages/project/Plan.tsx`): Stages / Dependencies views on a
  toggle over ONE endpoint `GET /observe/projects/{id}/plan`; critical path computed
  server-side (`policy.critical_path`) from the same arithmetic the refusals quote;
  settled work conducts but weighs 0. Accept/cut/observable-verdict are buttons on the
  screen. Explore projects get no Plan tab. **Mode picker on New project** (3 cards)
  landed early because the tab was otherwise unreachable from the UI.
- **4** — `yaaf/server/velocity.py`, ONE producer read by both the CEO's context pack
  and the Plan screen: met/week trailing 3w+12w from `requirement_events` (R-00
  excluded), `weeks_left_at_pace`, waste (dead-run share of the window, worst offender
  named), attribution split (share of runs/dollars stamped to any requirement). Dead
  runs charge `Requirement.failure_load` from both death paths (`daemon.finalize` and
  `janitor.reap_stale_runs`) via `policy.charge_failure`. Pace + Waste tiles on Plan.

## Remaining stages (the full designs are in the REBUILD.md artifact)

- **5 — Commitment, and a forecast that refuses things.** `commitments` table:
  `deliver_by`, `accepted_at/by`, `constitution_version` frozen at acceptance,
  `scope_snapshot` (requirement keys), `status` proposed/accepted/at_risk/breached/met.
  Projection = remaining ÷ trailing velocity, recomputed by the **janitor**;
  `at_risk`/`breached` transitions disclosed to the CEO via `Notice` and emailed to the
  chairman (`yaaf/server/notify.py` has `chairman_notify`). Re-validation is
  **state-based** on three triggers: constitution version moved, scope changed, failure
  load crossed a threshold (never event hooks — amendments apply inline from three
  callers). **Refusals:** no new scope while projection > deliver_by (existing work
  continues; a new requirement needs the chairman); no agent can write a date (absent
  verb — the CEO proposes a commitment, the chairman accepts it; only the platform
  computes the projection). Burn-up/projection lands on the Plan screen (stage-board
  axis markers: commitment vs projected). New rules go in `ConstitutionDoc` via
  `_rule()` (e.g. a failure-load re-forecast threshold), never `projects.config`.
- **6 — Deliverables, attestation, sampled audit.** `artifacts` table (name, version,
  sha256, bytes, published_at/by, requirements satisfied); webhook secrets generated
  and shown at project creation (the `platform_attested` path in
  `yaaf/server/api/webhooks.py` EXISTS and has simply never been provisioned);
  `evidence.task_id` → real FK NOT NULL; **sampled audit**: `audit_sample_rate` +
  `audit_response_hours` as constitution rules, janitor draws samples of observable
  closes into an `audit_samples` table, chairman confirms on the Plan/Work screen,
  expiry recorded as "not reviewed" and NEVER blocks; memory-tool-shaped MCP tool over
  the governed store; `PreCompact` + record-only `Stop` hooks; `skills`.
- **7 — The status meeting.** Janitor opens/numbers/emails the review with zero tokens
  (this is why `reserved_governance_runs` exists — the CEO's variance narrative is a
  reserved governance run, reason kind `review`); the review covers goal + live bets +
  deadline; **six levers**: cut / **add** / **reword** / move date / raise budget /
  accept slip — each recorded with the chairman's reason, disclosed to the CEO, shown
  at the next review beside what it bought. A review with an empty board still fires.
- **8 — Explore/Deliver/Operate machinery.** Explore may carry an optional date (the
  chairman's own example: "one sale in 60 days" — open scope, bounded time; burn-up
  degrades to a countdown). Deliver: platform-run completion → `ProjectStatus.COMPLETED`
  (enum exists) + receipt whose load-bearing row is "failed once before". Operate:
  `RecurrenceRule` in a `cycles` table, 4 frequencies + IANA zone (constitution
  `timezone` rule), 29–31st refused not clamped, cycles roll in the **janitor**, at
  most one open cycle, two skips → pause + email. **The learning loop** (the half the
  old plan dropped): review writes `cycle.narrative` (uncapped), next cycle's context
  pack carries the last three verbatim; wake reason kind `cycle_plan`.
- **9 — Topology experiment.** `projects.topology`: solo / ceo_team /
  ceo_leads_workers; `experiments` + arms — one chairman-authored requirement set
  copied to every arm, identical constitutions, run concurrently; compared on **cost
  per requirement met**, wall-clock to first attested evidence, share of spend
  producing nothing, internal words per delivered byte; stop rule for an arm that
  breaches budget with nothing met. Known asymmetry to REPORT, not hide: solo is
  exempt from requester-owns-the-test, so solo self-grades observables.

## How to work (the chairman's standing instructions)

- Plain English, no jargon walls; tight updates; he is visual; **UI over CLI**; Apple-
  grade UX bar. Answer the question asked. He approved this whole plan — don't
  re-litigate settled decisions (they're in REBUILD.md "Decisions the chairman has
  made").
- Loop per stage: build → full test suite → commit (narrative commit messages, subject
  line a sentence about the *why*, end with `Co-Authored-By:` your model name
  `<noreply@anthropic.com>`) → `./update_single_server.sh --no-pull` (rebuilds BOTH
  images, keeps data) → verify on the live box (curl the API / query the DB) → update
  REBUILD.md status + "as built" → commit that too.
- Tests: `python3 -m pytest tests/ -q` from repo root. **3 failures in test_chat_lane
  and 4 errors in test_mcp_mount are pre-existing** (no `claude_agent_sdk` installed
  locally; it exists in the sandbox image). Everything else must pass. Current count:
  ~298 passing.
- Invariant tests live in `tests/selftest_invariants/`, run over HTTP with real minted
  run tokens, each trying to BREAK a rule. Reusable helpers:
  `tests/conftest.py`: `make_project(client, slug=, mode=, constitution=, start=)`,
  `amend(client, project_id, **changes)`, `live_token`, `agent_by_name`, `bearer`.
  `tests/selftest_invariants/test_requirements.py`: `deliver_project`, `propose`,
  `accept(..., project)` (runs the janitor tick), `make_task`, `tick`.

## Architecture rules (violating these is how previous plans failed validation)

- Enforcement is a `Refusal` with a message written FOR the agent, or an absent verb.
  Advisory-only anything is suspect.
- Governed knobs go in `ConstitutionDoc` via `_rule(group=, label=, help=)` — the
  settings UI generates itself, amendments are audited, the CEO is told via `Notice`.
- Disclosure rides `Notice` rows consumed exactly once by the next context pack.
- "Done" is never self-graded, at any level.
- Automatic state changes → janitor (15s tick, budget-independent); wake decisions →
  dispatcher (gated by run budgets). Anything that must happen regardless of budget
  CANNOT live behind the dispatcher's gates.
- **Migrations:** `0001_baseline` is `Base.metadata.create_all`, so every model change
  needs a guarded revision (`add_column_if_missing` / `create_table_if_missing` from
  `yaaf/core/migration_ops.py`). `tests/selftest_invariants/test_migration_chain.py`
  fails if models and the chain diverge — stage 1 shipped without its migration once
  and was caught on the live box, not in review. Keep that test's
  `STAGE_*` lists honest when adding schema. No PG enums: `String(n)` + `enum.StrEnum`.
  New id prefixes go in `yaaf/core/ids.py`.
- One producer per number: if two screens/consumers need the same figure, they read
  the same function (see `velocity.py`, `plan_arithmetic`, `critical_path`).
- Web: `web/src/` React SPA, dark cockpit theme, `npx tsc -b && npm run build` in
  `web/` before deploying; the api image serves the built bundle at `/app/`.

## The live box

Single-server Docker deploy (`deploy-db-1`, `deploy-api-1`, `deploy-runner-1`,
`deploy-proxy-1`). Admin token: `grep YAAF_ADMIN_TOKEN deploy/.env`. DB read access:
`docker exec deploy-db-1 psql -U yaaf -d yaaf`. Schema at `0002 (head)`. The database
was wiped 18 Aug (backup: `~/yaaf-backup/pre-rebuild-2026-08-18.sql.gz`; seed:
`~/yaaf-seed/`, restorable via `yaaf/core/seed.py`). On it now: 9 restored projects,
all **draft + explore mode**, plus 2 stopped smoke projects (deliver). No project is
running; nothing is spending. Old memories about the pre-wipe data (1,091 runs,
$220.37) refer to the backup only.

## Start here

Stage 5. Design it against the patterns above (the commitment is chairman-accepted
like a requirement; the projection is platform-computed like `met`; re-validation is
state-based in the janitor like `check_plan`; the scope refusal extends
`check_plan_affordable`'s style and shows its arithmetic). Migration `0003`, invariant
tests that try to fake a date, move scope past a breached commitment, and amend the
constitution out from under a frozen one. Then the burn-up on the Plan screen. Deploy,
verify live, update REBUILD.md, commit.
