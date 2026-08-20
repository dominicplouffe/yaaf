# Validation prompt — hand this to a fresh context window

Paste everything below the line into a new session with the yaaf repo open. Its job is to
**adversarially validate** the plan, not to admire it. It should assume the plan is
incomplete and try to prove it.

---

You are auditing an implementation plan for `yaaf` (a framework that runs a company of AI
agents — a CEO that plans, role-based employee agents that work, and one human "chairman").
Repo: `/home/dplouffe/projects/yaaf`. Branch: `delivery-loop`. Read `docs/DELIVERY_LOOP.md`
first, then the three artifacts it links.

**Your task: find what the plan misses.** The chairman's explicit instruction was that
every request be *"embedded in the architecture and patterns for yaaf, not only cherry
picked what is easiest to please me."* A previous session substituted four of his requests
for things it preferred and only caught it when challenged. Assume more remain.

## Part 1 — every request the chairman made, in his own framing

Verify each is genuinely in the plan, architected rather than gestured at. For each,
answer: **present / partial / absent**, name the phase, and quote the plan text that
covers it.

1. **Three project types.** (a) Open-ended, where agents decide what to do from the goal
   and parameters — *"find an idea, start a company, get at least one sale in the next 60
   days."* (b) One-time, which may involve multiple teams, complex goals and a complex
   deliverable, then ends — *"an audit for example."* (c) Recurring — daily, weekly,
   bi-weekly, monthly — *"create a weekly blog post, track traffic and the audience, learn
   from findings and go back to create a blog post."*
2. **For (b) and (c), the CEO's first task is a waterfall-type plan** built from *"the
   budget, team, and all the arguments that have been set by the user (i.e. number of
   daily runs, budget, number of open tasks allowed, and so on)."*
3. **The CEO must be adaptable** to *"plan changes, parameter changes, errors, and the
   like."* Parameter changes means the chairman amending the constitution mid-flight.
4. **A visual plan** — *"either a waterfall type schedule or some workflow diagram."* He
   states he is visual. This must exist **in the product**, not only in a design document.
5. **A delivery date and a guarantee.** *"We have great gates so we don't overspend, but
   this means it's impossible to get a delivery date or anything of substance that can
   offer me some sort of guarantee."*
6. **The human-team loop, all six steps:** goal → hypothesis → requirements → delivery date
   → **status meetings** (to discuss the goal and hypothesis, alter requirements, and
   judge whether the deadline will be met) → **react**: modify, cut features.
7. **His suspicion about the topology.** *"Something is fundamentally wrong with the
   current CEO to agent to agent design. We burn through tokens, runs, budget so fast yet
   we don't seem to do anything useful."* Was this measured and turned into work, or only
   quoted?
8. **Anthropic prebuilt tooling** — specifically the memory tool — *"if it makes sense…
   to make things better and more effective."* An honest verdict on what is reachable is
   an acceptable answer; silently skipping it is not.

## Part 2 — does it follow yaaf's own architecture?

yaaf's thesis, from its README: **the rules live in the server, not in the prompt.** Agents
act only through an authenticated API with short-lived per-run tokens; every rule is an API
refusal or an automatic state change; an agent cannot be talked out of a rule it never
held. Check the plan against the patterns that actually exist in the codebase:

- **Enforcement is a `Refusal`, never prose.** See `yaaf/server/policy.py`. Anything in
  the plan that is advisory-only is suspect — flag it. Is the *forecast* enforced?
- **Governed knobs live in `ConstitutionDoc`** (`yaaf/core/constitution.py`) declared via
  `_rule()` with group/label/help, so the settings UI generates itself, amendments are
  audited, and the CEO is told. Does every new limit in the plan go there rather than into
  `projects.config`?
- **Disclosure rides `Notice` rows** consumed exactly once by the next context pack, with
  `disclosed_at` as the receipt. Does every new governance event use it?
- **"Done" is never self-graded.** Success tests are structurally validated
  (`policy.validate_success_test`) and command tests are executed by the platform
  (`daemon._run_success_check`). Does anything in the plan let an agent grade itself?
- **Automatic state changes belong in the janitor** (`yaaf/server/janitor.py`, 15s tick);
  wake decisions belong in the dispatcher (`yaaf/server/dispatcher.py`, 2s poll). Is
  anything placed in the wrong loop? Note specifically that anything which must happen
  regardless of run budget cannot live behind the dispatcher's gates.
- **Migrations use `add_column_if_missing` / `create_table_if_missing`** because
  `0001_initial` is `Base.metadata.create_all`. No native PG enums — `String(n)` plus
  `enum.StrEnum`.
- **Tests are invariant tests** in `tests/selftest_invariants/`, over HTTP, with real
  minted run tokens, each trying to *break* a rule.
- **Backward compatibility:** every existing project must behave exactly as today unless
  the chairman opts in.

## Part 3 — verify the factual claims

The plan rests on an audit of 1,091 runs. **Do not trust it. Re-derive.** The live database
is read-only reachable at `docker exec deploy-db-1 psql -U yaaf -d yaaf` — SELECT only,
this is production. A previous analysis reported `trigger.reason` as unpopulated; it looked
for a singular key when the column carries `reasons` as an array, and that false finding
made it into a plan and nearly into a day's work.

Spot-check at least: the 20 side-effects figure; 61.7% of agent runs moving no task; 62% of
runs carrying no `task_id`; the talk:work ratio of 1.28; median claimed→done of 5.5
minutes; that all evidence rows are `agent_claimed` with zero `platform_attested`; and that
`decisions` is genuinely never read back (grep the whole tree).

Also verify against the code, not the plan's description: that `ClaudeAgentOptions` really
has no field for Anthropic-provided tools or `context_management`; that `task_events` can
support a velocity query with no schema change; and that `max_budget_usd`, `task_budget`,
`hooks` and `skills` exist on the options object.

## Part 4 — the things already settled

Do not re-litigate these; flag only if you find them false.

- The agents are **not** chattering (1.28 messages per transition) and are **not** slow
  (5.5 min median). Both were assumed by the chairman and by a previous session, and both
  are false.
- **Bet discipline works** — 24 hypotheses, 100% with real tasks, 75% producing verified
  external evidence. Do not "fix" it.
- **The gates are the best-working part of the system.** Every failure happened where a
  gate was *missing* or *decorative*, not where one was too tight. The chairman's instinct
  that safety costs him the delivery date is backwards.
- **Managed Agents is the wrong fit** — it would hand the loop and the sandbox to
  Anthropic, and the containment model is the product.
- **The memory tool itself is unreachable** from the Claude Agent SDK. The plan adopts the
  pattern via an MCP tool, not the tool.
- **Phases-as-schedule was the wrong axis.** Scope ÷ velocity forecasts; a schedule does
  not. The plan keeps sequencing as *stages over chairman-owned requirements* instead.

## Part 5 — deliver

1. A table: every item from Part 1, with present / partial / absent, the phase, and
   evidence.
2. Every architectural violation from Part 2, with the file and pattern it breaks.
3. Every factual claim you could not reproduce, with the query you ran.
4. **Anything the chairman asked for that appears nowhere.** This is the most important
   output. Be specific and quote him.
5. Anything in the plan that is *not* traceable to a request or a measured finding —
   scope that crept in.
6. Your judgement on ordering: the plan claims the order is forced (make "done" true →
   measure it → commit to it → review it → repeat it). Is it, or is that rationalisation?

Be adversarial. A previous session repeatedly found its own errors only when challenged; do
not assume this one is clean.
