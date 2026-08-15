# yaaf (Yet Another Agent Framework) — a hardened, generalized multi-agent company framework

## Context

`dominicplouffe/lana-project` runs a 3-agent AI company (CEO, engineering, GTM) as cron-fired `claude -p` cycles on one box, with the human as chairman. Its value is **discipline encoded in code, not prose**: work is pulled never invented (empty queue = $0 exit before a token is spent), forcing functions are refusals not guidelines, external evidence is the only progress, everything is recorded, and operational scar tissue (claim reaping, cost booked on killed runs, kill switches) is built in.

yaaf generalizes this into a multi-project platform: projects with an LLM CEO, teams of role-based agents (role library, N employees per role with a lead), a human chairman, strict hierarchical comms, a UI, scoped memory, optional Slack, budgets, and audit — **without letting generalization corrode the discipline being generalized**, and **without building six milestones of plumbing before testing whether the org structure beats one agent**.

**Stack decisions (settled with the user):**
| Decision | Choice |
|---|---|
| Backend | Python 3.12+ / FastAPI, SQLAlchemy + Alembic, Postgres only (queue + NOTIFY + FTS; no Redis) |
| Agent runtime | Claude Agent SDK (Python); billing per project: API key, or subscription auth **pending a ToS check** (below) |
| Frontend | React + Vite SPA (TypeScript), WebSockets |
| Execution | Event-driven wakeups + heartbeats; dispatcher does the cheap-exit check in SQL before any tokens |
| Auth | Multi-user, single org; per-project: chairman / observer |
| Sandboxing | Container-per-run; role-scoped tools + secrets; **egress proxy ships with the sandbox, not later** |
| Comms | Boardroom (chairman↔CEO) / leadership (CEO+leads) / team rooms, enforced server-side; chairman reads all, writes only boardroom |
| Slack | Optional per-project bridge; Postgres bus always canonical |
| Workspaces | Private GitHub repos per project + S3-compatible artifact store |
| Deployment | Docker Compose on one box is the primary target (rate limits, not compute, are the bottleneck); worker-fleet/Proxmox is a deploy option, not a milestone gate |

---

## The two questions this plan must answer first

**1. Does the org structure beat one agent?** Nothing in lana proves the hierarchy is worth its token cost; hierarchy's main product is lossy summarization at each boundary, and CEO judgment — what to queue, kill, prioritize — is the actual product, with everything else being transport. So the first deliverable is not a platform; it is a **controlled experiment** (M2) comparing CEO+team against a single agent under the same governance, goal, and budget.

**2. Can the framework keep its own rules?** Every forcing function below states its enforcement mechanism. "Governance event" (a notification) is not an enforcement mechanism.

## Kill condition for yaaf itself

- **Baseline experiment concluded by 2026-09-30.** If the org arm does not beat the single-agent arm on the predefined rubric (blind-judged quality, verified external evidence, cost, wall-clock), yaaf-as-org-framework is killed. Salvage path: yaaf as a **governance harness for single agents** (queues, evidence verification, budgets, approvals, audit for one agent per project) — independently valuable.
- The chairman (Dominic) may extend this date **once**, with a written reason, recorded in the plan repo.
- **External evidence that yaaf works** (self-use doesn't count): by 2026-12-31, a yaaf project produces platform-attested external evidence that survives spot-audit, or an unaffiliated user runs yaaf.

---

## Constitution vs. configuration

A forcing function you can edit in a settings page is a default, not a forcing function. Policy splits into two classes:

**Constitution — frozen at project start.** Changeable only by the chairman, via a formal amendment with: a written reason, a **72-hour cooling-off delay** before taking effect, an audit entry, and automatic disclosure to the CEO agent in its next context pack. Contains: queue ceiling, external-evidence floor, drought rule, kill-date semantics, approval thresholds and approval-timeout default, budget caps, wake-loop limits, evidence-verification rules.

**Configuration — tunable anytime.** Heartbeat cadences, model tiers per role, concurrency limits, Slack mappings, team names/sizes (within budget).

## Forcing functions and their enforcement (each one a refusal or automatic state change)

| Rule | Enforcement |
|---|---|
| Only CEO creates tasks; success test must name a `command` or checkable `observable`; evidence required to complete | API refusal (403/422) against per-run scoped agent tokens — an agent cannot be talked out of rules it never held |
| Queue ceiling | API refusal (409) |
| External-evidence floor | API refusal on task creation that would break the ratio |
| Drought (no verified external evidence for N hours) | API **refuses** creation of `produces_external_evidence: false` tasks while active; CEO also notified |
| Hypothesis kill date | **Automatic expiry** — platform expires the hypothesis at the date; linked open tasks auto-move to `blocked(hypothesis_expired)`; CEO must pivot or kill; only the **chairman** may extend, **once**, with reason |
| Budget caps | 80% → warning event; 100% → project auto-paused, chairman notified. Caps are in the billing mode's native unit (below) |
| Memory cap | `add` refuses at cap; explicit supersede/forget by the scope's curator (CEO for project scope, lead for team scope); the janitor never auto-evicts |
| Approval timeout | Approvals carry an expiry (constitution default: 7 days, decision on timeout = **deny**); chairman is nagged via email/Slack before expiry; on timeout the CEO is woken with the denial and must re-plan — a vacationing chairman degrades the project, never deadlocks it |
| Refusal storms | An agent that hits N policy refusals in one run (default 5) has the run terminated and logged — refuse-rephrase-retry loops die fast |

## External evidence: verification, because the API can't enforce truth

The agent that does the work also writes the evidence row — so `external` is the most gameable field in the system. Teeth:

- **Two provenance classes.** `platform_attested`: evidence arriving via platform connectors the agent cannot forge (inbound email webhook, Stripe webhook, analytics pull, GitHub events from repos the project doesn't own). `agent_claimed`: written by an agent, and **unverified until the platform checks it**.
- **Platform verifier at completion time** (server-side, never the agent): source URL must resolve; content fetched and hashed by the control plane, snapshot stored in the artifact store; the domain must not be in the project's registered controlled-domain list (project's own GitHub repos, sites, accounts — registered at creation, auditable); the evidenced event must postdate task creation.
- Drought and the external-ratio computations count **verified/attested evidence only**.
- **Chairman spot-audit**: the UI surfaces a random sample of recent evidence for periodic human audit; failed audits mark the agent's evidence stream suspect and notify the CEO.
- Prefer building connectors so most external evidence is attested rather than claimed.

## Completion truth: success tests that can actually fail

Evidence-truth has a sibling gap: the CEO writes the success test, the employee asserts completion, the lead reviews — all LLMs, and LLM reviewers approve nearly everything put in front of them. So:

- **`success_test` is structured, not prose.** Either a `command` — which the **platform executes** in the task's sandbox at completion and records pass/fail + output (the agent never self-reports a command result) — or an `observable`, which must name a checkable artifact, URL, or metric. Task creation is **refused** if the test names neither (a structural check, not an LLM judgment).
- **Completions join the chairman spot-audit sample** alongside evidence rows: the UI surfaces random recently-completed tasks with their test, claimed result, and artifacts.
- **Per-CEO judgment metric**: the fraction of tasks a CEO created that ever produced verified/attested external evidence, surfaced in the UI. This is the CEO-quality signal — and a primary input to the M2 rubric.

## Ledger: tokens always, dollars only when dollars move

Booking subscription runs "at list price" produces a number that is neither cash nor opportunity cost and distorts every budget decision. So: every run books **tokens** (and rate-limit consumption); **USD rows exist only for real money** (API-key billing, infra, external spend via approvals). Budget caps operate in the project's billing-native unit: token budget for subscription mode, USD for API mode.

**Open verification item (before M1 ends):** confirm Anthropic ToS/usage policy permits running multiple autonomous agents on subscription auth. Until confirmed, API-key billing is the default for anything beyond single-project personal use.

## Wake-loop prevention (the #1 way these systems burn money)

Dispatcher-enforced hard limits (constitution class), not just debouncing: per-agent wakes/hour cap; per-project daily run ceiling; conversation-pair circuit breaker — N messages between the same two agents with **no task state change** freezes the thread and flags the CEO; refusal cap per run (above). All trips are audit-logged and visible in the UI.

## Idempotent side effects (reap-and-retry that doesn't double-fire)

Releasing a claimed task back to `ready` is correct for a dead worker and wrong for one that already opened a PR, posted to Slack, or spent money. Every external side-effect tool call writes a `side_effects` row (run, task, kind, idempotency key, status, result) **before** performing the effect; effects are keyed idempotently where the target API allows; on retry, the new run's context pack includes the completed side-effect ledger for that task. The janitor reaps leases; it never erases side-effect history.

---

## Architecture

```
┌────────────── control plane (docker compose, one box) ──────────────┐
│ FastAPI API+WS │ React SPA │ Dispatcher+Janitor │ Slack bridge      │
│ Postgres: state, run_queue (SKIP LOCKED + leases), FTS, audit       │
│ Egress proxy (per-role allowlist) │ MinIO (artifacts, snapshots)    │
└─────────────────────────────────────────────────────────────────────┘
        ▲ agent-scoped tokens                ▲ job lease
   runner daemon(s) — same box initially; extra worker CTs/VMs are a
   deploy choice (join token), not an architecture change
```

- **Postgres only**: job queue via `FOR UPDATE SKIP LOCKED` with lease heartbeats; `LISTEN/NOTIFY` is strictly a **latency hint over a polling floor** (listeners drop messages across reconnects — polling is the source of truth). `run_queue` (hot) is separate from `runs` (history); the janitor moves rows on completion.
- **Agents talk to the platform only through the API** (exposed as MCP tools via the Agent SDK) with short-lived per-run tokens scoped to one agent. lana's `plan.py`/`memory.py`/`slack.py` become endpoints; every rule above is server-side.
- **Runner**: per run — Docker container (no-privilege, resource caps), project workspace cloned from GitHub, role-scoped secrets + run token injected, **all egress through the allowlist proxy** (untrusted content + secrets + open network is the standard lethal combination; the proxy ships in M1, before web fetch does), trace streamed to control plane, tokens metered even on timeout/kill, stop/pause honored mid-run.

## Domain model (indicative)

`users`, `projects` (status, chairman, billing config, controlled-domains registry, GitHub repo, Slack ref) · `constitutions` (frozen policy + amendments log) · `configs` · `role_library` (versioned templates: prompt, model tier, tool permissions, secret scopes; seeded with CEO/PM, engineer, marketer, GTM, sales, support, researcher, writer) · `teams` (pinned role version, `lead_agent_id` nullable — **effective lead is derived**: explicit lead or the sole employee; `agents.kind` is only `ceo|employee`) · `channels`/`messages` (threaded, `slack_ts`) · `tasks` (success_test required, external flag, `not_before`, lease, `parent_task_id` for lead→employee decomposition, evidence required on complete) · `hypotheses` (kill_date, auto-expiry) · `evidence` (provenance class, verifier result, snapshot hash) · `decisions` · `memories` (scope, cap, supersede chain, FTS) · `ledger` (tokens + usd columns) · `run_queue`/`runs` · `side_effects` · `approvals` (expiry, default-deny) · `secrets` (envelope-encrypted, role-scoped) · `audit_log`.

## Agent runtime

Stateless runs, durable record: context pack = pinned role prompt + generated project constitution + roster + triggering input + side-effect ledger for the task + memory *pointers* (query, don't recite). External content is wrapped as untrusted. Directives honored only from the chairman via boardroom. **CEO judgment is the product**: the CEO context pack and planning prompts get first-class design attention and a **golden-transcript replay eval** harness (scripted scenarios → recorded transcripts → regression-judged on rule adherence and planning quality). Role templates become user-editable in the UI only after the eval harness exists to catch regressions.

---

## Milestones (inverted: experiment before platform)

**M1 — Minimal harness (the experiment rig).** Docker Compose, single box. Trimmed schema; API with the constitution refusals (tasks, evidence, messages, memory); dispatcher with wake-loop breakers; runner with Docker sandbox + **egress proxy**; Agent SDK integration (API-key billing); context packs; evidence verifier v1; MockModel runtime + the invariant selftest suite (every rule gets a test that tries to break it through the API, including forged tokens); read-only observation UI or CLI. *No approvals UI, no Slack, no GitHub provisioning yet — just enough to run agents under the rules.*

**M2 — The baseline experiment (the gate).** Same goal, same token budget, same tools, same governance API for both arms — isolating the org variable, not the discipline variable. Arm A: single agent + task list. Arm B: CEO + lead + 2 employees. 2–3 goal pairs, chosen so external evidence is **well-defined and platform-verifiable** (e.g., publish a small open-source artifact and obtain N organic interactions from unaffiliated accounts within a window — not "write a comparison report", where external evidence is undefined). Measured: blind-judged deliverable quality (rubric; judge model + human), verified external evidence count, per-CEO task→verified-evidence conversion rate, tokens, wall-clock. **Decision gate = the kill condition above.**

**M3 — Full org mechanics** *(conditional on M2)*: multi-employee decomposition, scoped/capped memory, hypotheses auto-expiry, approvals inbox with timeout-deny, ledger + budget gates, full dashboard/boardroom/channels/agents UI, golden-transcript evals.

**M4 — Integrations**: GitHub workspace provisioning + artifact store; Slack bridge (bus canonical); inbound evidence connectors (email, webhooks).

**M5 — Hardening + pilot**: chaos tests (kill worker mid-run → reap without duplicate side effects; lease expiry; Postgres restart), rate-limit scheduler tuning, secrets audit, spot-audit UI; then a real pilot project with real external-evidence semantics.

**M6 (optional, on demonstrated need)** — worker fleet on Proxmox (Terraform + cloud-init + Ansible, join tokens), monitoring CT (Prometheus/Grafana/Loki), backup automation. Demoted because agent runs are I/O-bound on someone else's inference: account-level rate limits arrive long before worker capacity, and `max_concurrent_runs` exists to serialize against exactly that.

## Repo layout

```
yaaf/
  server/    # FastAPI: api/, models/, policy/, dispatcher/, verifier/, slack/
  runner/    # worker daemon + sandbox harness + egress proxy config
  web/       # React + Vite SPA
  roles/     # seed role-library definitions
  evals/     # golden transcripts + replay harness
  deploy/    # docker-compose.yml (primary); proxmox/ (optional M6)
  tests/     # pytest: selftest_invariants/, chaos/, mockmodel/
```

## Verification

- CI: MockModel end-to-end (planning → decomposition → claim → complete → evidence verify → governance trips) at $0; invariant selftests; chaos suite.
- M2 experiment protocol and rubric written down **before** the runs, results recorded as decisions.
- Live checks: budget cap trips pause a project; kill-date expiry blocks tasks; approval timeout denies and wakes the CEO; circuit breaker freezes a ping-pong thread; a reaped run's retry sees its predecessor's side effects.

## Out of scope for v1

Migrating the live lana company; multi-tenant orgs; k8s; multi-provider models (runner interface leaves room); embeddings memory search (FTS first); auto-scaling workers.

---

## Status appendix (2026-08-15 — maintained, unlike the frozen plan above)

**M1 — the experiment rig: DONE.** Schema, policy engine (all forcing functions
as refusals), agent/admin/observe APIs, dispatcher (cheap-exit wakes, wake caps,
pair circuit-breaker), janitor (reaping, expiries, budget auto-pause,
amendments), evidence verifier, MockModel + Agent SDK runners, Docker sandbox
behind the squid egress allowlist, seed role library, CLI, invariant selftest
suite (34 tests, green on SQLite and Postgres). Validated with real model runs.

**Pulled forward from M3 (chairman's call, 2026-08-14): the web UI — DONE.**
Login, project wizard, role library editor, per-project cockpit (overview,
boardroom, board, channels, runs, approvals inbox, evidence + spot audit,
settings), credentials manager with per-role grants.

**Built beyond the plan (driven by the first production project):** daily
wall-clock wakes (`ceo_daily_at` + timezone), role-scoped secret injection,
failed-run retries that return their triggers, add-team-to-existing-project,
WordPress draft-first publishing in the blog-writer role, subscription-mode
token-only ledger.

**M2 — the baseline experiment: HARNESS BUILT, RUNS NOT STARTED.** Protocol
and preregistered rubric drafted (docs/M2_PROTOCOL.md), spec with three
candidate goal pairs (experiments/m2.spec.json), and the full harness:
`yaaf experiment init/status/export/judge/score/decide` — paired-arm
provisioning (org arm vs a **solo-mode** single agent: the CEO as sole member
and derived lead of its one team, byte-identical policy), sealed-label blind
bundling, judge-model + human rubric passes, composite scoring, and the
decision writer. **Blocked on the chairman:** goal-pair selection and protocol
freeze. The gate still decides by **2026-09-30** (one extension allowed).

**M3 remainder (2026-08-15): DONE.** Per-CEO judgment metric (API + Overview
tile), approval nags via a chairman notifier (SMTP/webhook, best-effort),
constitution amendments listable/cancellable during cooling-off, completed-task
spot-audit verdicts (failed audits wake the CEO), Postgres FTS memory search,
and the golden-transcript eval harness (evals/, goldens enforced in CI) — the
gate on role editing is now real.

**M4 (2026-08-15): DONE, built ahead of the M2 gate by chairman instruction.**
Inbound evidence connectors (`/webhooks/github` + HMAC generic, producing
platform-attested evidence; chairman-provisioned secrets agents never hold),
Slack bridge (bus canonical, best-effort mirror, signed inbound
chairman→boardroom), GitHub workspace provisioning (private repo per project,
credential-scrubbed clones), artifact store (local default, S3/MinIO optional,
snapshots + traces + agent `artifact_put` deliverables), and per-role egress
scoping (squid ports generated from the role library, `yaaf egress sync`).

**M5 (2026-08-15): hardening assets DONE; live pilot pending.** Chaos suite
(worker races on SKIP LOCKED — held on real Postgres; reap/retry side-effect
integrity; DB-restart recovery), CI (SQLite + Postgres + lint + web build),
runner integrity fixes (usage checkpointing so hard kills are metered,
revocation stops the container, traces stashed and served). The *pilot with
real external-evidence semantics* is M2 itself — by design it comes next.

**Also (2026-08-15):** WebSocket live updates layered over polling (latency
hint, never source of truth), record-of-build note: the chairman authorized
building M3+ machinery ahead of the M2 result; the kill condition is
unchanged — if the org arm loses, the org-framework parts are shelved and the
salvage path (governance harness for single agents) proceeds.

**M6 (Proxmox fleet): NOT STARTED** — still demoted, on demonstrated need.

**Open items:** golden-transcript evals before role editing counts as safe; M2
goal selection needs chairman approval (draft protocol: docs/M2_PROTOCOL.md).

**Resolved 2026-08-15 — subscription-auth ToS verification: NOT permitted for
multi-agent use.** Anthropic's consumer terms prohibit automated access except
via API key; the Claude Code legal docs state OAuth/subscription credentials
are for ordinary personal use of Anthropic's own apps, and that products built
on the Agent SDK must use API-key auth. Verdict: API-key billing is the
required mode for yaaf agents (subscription mode remains in the codebase for
single-user personal experimentation only, at the operator's own judgment).
Sources: anthropic.com/legal/consumer-terms, code.claude.com/docs/en/legal-and-compliance,
support.claude.com article 15036540.
