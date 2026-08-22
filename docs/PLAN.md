# The plan — where this is going, and what would kill it

This is the roadmap and the honesty page. It says what yaaf is betting on, what
would falsify the bet, what is built, and what is not. yaaf holds its own
projects to falsifiable claims with kill dates; it would be odd not to hold
itself to one.

---

## The thesis

Autonomous agents fail in ways better prompts do not fix. They mark their own
work done. They invent busywork and report it as progress. They talk to each
other in loops at full price. They spend a budget while nobody is watching, and
leave a record nobody can reconstruct.

yaaf's answer: **rules live in the server, not in the prompt.** Agents act only
through an authenticated API with short-lived per-run tokens, and every rule is
an HTTP refusal or an automatic state change. An agent cannot be argued out of
a rule it never held.

Layered on that: **the requirement is the root object.** A requirement is
something that must be true at the end. It carries an acceptance test the
platform runs. Tasks exist to make requirements true, and outside Explore mode
a task serving no requirement is refused — because work that makes nothing true
is how a company stays busy without finishing.

## The two questions

**1. Does the org structure beat one agent?** Nothing here proves a hierarchy
is worth its token cost. Hierarchy's main product is lossy summarization at
each boundary; CEO judgement — what to queue, kill, and prioritize — is the
actual product, and everything else is transport. So the repo ships a
controlled experiment rather than an assumption: see
[M2_PROTOCOL.md](M2_PROTOCOL.md) and `yaaf experiment`.

**2. Can the framework keep its own rules?** Every forcing function names its
enforcement mechanism. A notification is not an enforcement mechanism. The test
suite attacks each rule over HTTP with a real principal, and the chaos suite
attacks the recovery paths.

## Kill conditions

- **The org-vs-solo gate.** If the org arm does not beat the single-agent arm
  on the pre-registered rubric — blind-judged quality, verified external
  evidence, cost, wall-clock — then yaaf-as-an-org-framework is shelved. The
  salvage path is yaaf as a **governance harness for a single agent**: queues,
  requirements, verification, budgets, approvals and audit, one agent per
  project. That half stands on its own and is already usable (solo mode).
- **External evidence that yaaf works.** Self-use does not count. Either a yaaf
  project produces platform-attested external evidence that survives spot-audit,
  or an unaffiliated user runs yaaf.

The chairman may extend a kill date once, in writing, with the reason recorded
— the same rule the platform enforces on its own projects.

## What exists

The platform is built and tested: 515 offline tests, including chaos tests and
golden-transcript evals, green on SQLite and Postgres in CI.

| Layer | State |
|---|---|
| Policy engine — every rule as a refusal | Built. The invariant suite attacks each one over HTTP. |
| Requirements, stages, dependencies, the plan gate | Built. Chairman accepts scope; nothing but the platform writes `met`. |
| Commitments — a delivery date the platform owns and re-projects | Built. Re-validated on rule change, scope change, and failure load. |
| Cycles and the review — the Operate rhythm and the standing meeting | Built. The janitor opens reviews with zero tokens. |
| Autonomy dial — five authority switches, two presets | Built. See [AUTONOMY.md](AUTONOMY.md). |
| Spend envelope, purchases, revenue-by-webhook | Built. |
| Sandbox — container per run, scoped secrets, Chromium in the box | Built. |
| Evidence verification, webhooks, spot-audit, CEO-judgment metric | Built. |
| Dashboard — Desk, Overview, Boardroom, Plan, Work, Review, Deliverables, Records, Settings | Built. |
| Integrations — Slack (Socket Mode), GitHub, MCP data sources, S3, email | Built. |
| Experiment harness — `yaaf experiment init/status/export/judge/score/decide` | Built. |
| **The org-vs-solo experiment itself** | **Not run.** This is the current milestone. |

## What is next

1. **Run M2.** Freeze the protocol, run both arms on the agreed goal pairs,
   blind-judge, and apply the pre-registered decision rule. This is the gate;
   everything else waits behind whether the answer is yes.
2. **Act on the answer.** If the org loses, the org machinery is shelved and
   the governance harness continues for single agents. If it wins, the next
   work is making the CEO's planning better — the context pack and the eval
   suite are where that happens, not the prompt.

## Deliberately not built

- **Multi-tenant orgs and k8s.** One box with Docker Compose is the target.
  Agent runs are I/O-bound on somebody else's inference: account rate limits
  arrive long before worker capacity does, and `max_concurrent_runs` exists to
  serialize against exactly that.
- **A worker fleet.** Same reason. It is a deploy choice, not an architecture
  change, and it waits for demonstrated need.
- **Multi-provider models.** The runner interface leaves room; nothing more.
- **Teardown machinery.** Winding a project down is a decision, not a script.
  The account registry records what exists so you can settle it yourself.
- **Virtual-card issuance.** Recommended hardening if you bring an issuer,
  never a requirement — and a seam nothing uses is scaffolding.
- **Defeating security challenges.** Captchas and 2FA go to a human, always.

## Settled questions worth recording

- **Billing.** Anthropic's consumer terms do not permit running fleets on a
  personal subscription; products built on the Agent SDK use API-key auth.
  API-key billing is the required mode for yaaf agents. Subscription mode
  remains in the codebase for single-user personal experimentation, at the
  operator's own judgment.
- **The egress allowlist is gone.** Per-role domain whitelisting was removed:
  it solved one problem at the cost of invisible failures (a blocked request
  was an unlogged proxy 403 three layers from the agent that made it).
  Containment is the fresh container, the role-scoped secrets, the metered
  budget, and the audit — not a firewall ticket.
- **Roles never own MCP servers.** A server line frozen into a role template
  follows every project that pins the role. Data sources are project-owned and
  assigned to roles.
- **Amendments apply immediately.** A cooling-off period was exempted three
  times in two days before it was removed. What binds the agents is that they
  cannot move these values and are told when the chairman does — not a delay
  he could wait out.
- **Memory is curated, never auto-evicted.** Caps are refusals; `compact`
  (N→1) is the verb that makes room, and folded entries stay readable, so
  compaction is reversible.

## Related

- [FEATURES.md](FEATURES.md) — the complete feature reference.
- [GOVERNANCE.md](GOVERNANCE.md) — every rule and its default.
- [M2_PROTOCOL.md](M2_PROTOCOL.md) — the experiment that decides the gate.
