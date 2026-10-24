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
is how a project stays busy without finishing.

## The two questions

**1. Does the org structure beat one agent?** Nothing here proves a hierarchy
is worth its token cost. Hierarchy's main product is lossy summarization at
each boundary; CEO judgement — what to queue, kill, and prioritize — is the
actual product, and everything else is transport. This is still open, and
nothing in the repo settles it. What the repo does give you is a fair
comparison to run yourself: solo mode puts one agent under byte-identical
governance, so the same goal can be handed to a hierarchy and to a single
brain and the bills compared.

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

The platform is built and tested: 1,813 offline tests, including chaos tests and
golden-transcript evals — SQLite between edits, Postgres in CI.

| Layer | State |
|---|---|
| Policy engine — every rule as a refusal or a floor | Built. The invariant suite attacks each one over HTTP. A rule may fire when a project is *under*-reaching as well as refuse when it over-reaches; the floors are far younger and far fewer. |
| Requirements, stages, dependencies, the plan gate | Built. Chairman accepts scope; nothing but the platform writes `met`. |
| Commitments — a delivery date the platform owns and re-projects | Built. Re-validated on rule change, scope change, and failure load. |
| Cycles and the review — the Operate rhythm and the standing meeting | Built. The janitor opens reviews with zero tokens. |
| Autonomy dial — five authority switches, two presets | Built. See [AUTONOMY.md](AUTONOMY.md). |
| Spend envelope, purchases, revenue-by-webhook | Built. |
| Sandbox — container per run, scoped secrets, Chromium in the box | Built. |
| Evidence verification, webhooks, spot-audit, CEO-judgment metric | Built. |
| Dashboard — Home, Inbox, and per project Team, Chat, Results and Details (Roadmap, Tasks, Deliverables, Spend, Reviews, Activity, Settings) | Built. See [ux-redesign](ux-redesign/README.md) for why it is laid out this way. |
| The chairman's MCP server — 32 tools across four scopes | Built, M0 through M5. Claude gets its own credential, never its person's own; nothing irreversible lands in one call; agent text arrives wrapped and labelled untrusted; no secret is readable at any scope. See [MCP_SERVER.md](MCP_SERVER.md). |
| People — accounts, sessions, personal tokens; chairman, co-chairs, observers | Built. One box, several people, each project with one chairman who alone decides who decides; co-chairs decide as peers and are recorded as themselves. Every human route declares its authority and a test refuses one that does not. The static admin token is retired. See [AUTH.md](AUTH.md). |
| Integrations — Slack (Socket Mode), GitHub, MCP data sources, S3, email | Built. |
| Staffing — the CEO hires and stands roles down; the plan carries each seat's end | Built. Bounded by `max_teams`, a refusal to orphan open work, and a refusal to remove its own last critic. A declared `stand_down_when` is fired by the janitor, not by a reminder. |
| Runtimes — a seat on any harness and provider, under the same rules | Built through M7: roles name a class of model and run on whichever provider pays the project; yaaf's own loop speaks every provider's own API — Anthropic, OpenAI and Azure, Gemini and Vertex, Bedrock, and the OpenAI chat shape of DeepSeek, Mistral, xAI, Groq, Together and the rest, local servers included — beside the Agent SDK, the Claude, Codex and Gemini CLIs and OpenCode. Prices import from a feed with their provenance; search is a platform capability on every provider. A live seat on each provider is the line only a deployment can tick, with `yaaf smoke`. Every seat is metered against its project's one cap; a seat that cannot be paid, priced, modelled or given what its role grants is refused at the door. See [RUNTIMES.md](RUNTIMES.md) and [PROVIDERS.md](PROVIDERS.md). |
| Attribution — who spent which runs, and on what | Built. One producer behind the chairman's screen, the CLI, the chairman's MCP and the CEO's own reader, so the two sides of the boardroom cannot hold different numbers. |

## What is next

1. **Make the CEO's planning better.** The context pack and the eval suite are
   where that happens, not the prompt.
2. **Keep the solo path first-class.** Whatever the org layer turns out to be
   worth, a single agent under the same governance has to stay a real way to
   run yaaf — it is the control the first question is judged against.
3. **Prove the MCP server against claude.ai.** The surface is built and the
   local clients work. The one line that cannot be ticked from a laptop is a
   real connection from claude.ai through the consent screen, against a public
   deployment — which is also the first time the security model is tested by
   someone who did not write it. Tracked in [MCP_SERVER.md](MCP_SERVER.md).

## Deliberately not built

- **Multi-tenant orgs and k8s.** One box with Docker Compose is the target.
  Agent runs are I/O-bound on somebody else's inference: account rate limits
  arrive long before worker capacity does, and `max_concurrent_runs` exists to
  serialize against exactly that.
- **A worker fleet.** Same reason. It is a deploy choice, not an architecture
  change, and it waits for demonstrated need.
- **A model router.** Seats run on many providers ([RUNTIMES.md](RUNTIMES.md)),
  but a seat runs on what it was hired on: no automatic fallback from one
  provider to another when a call fails, and no exchange rate between a meter
  in dollars and a meter in tokens.
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
- [MCP_SERVER.md](MCP_SERVER.md) — chairing yaaf from a model client: design and milestones.
- [RUNTIMES.md](RUNTIMES.md) — any harness, any provider, the same rules: design and milestones.
- [PROVIDERS.md](PROVIDERS.md) — any model from the first screen: classes, one loop on every wire, prices, search.
