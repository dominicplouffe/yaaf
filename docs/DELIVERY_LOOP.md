# The delivery loop — superseded

**This plan is superseded by [REBUILD.md](REBUILD.md).** Nothing had shipped, so the
database is being dropped and the requests are being architected in rather than added
beside — which removes most of the compromises below. This file is kept because its
audit findings are still the evidence base, not because its phases are still the plan.
Validating it turned up six numbers that do not reproduce; see REBUILD.md for the link.

Why the chairman cannot get a delivery date out of yaaf, and the work that fixes it.
Derived from an audit of 1,091 runs across 9 projects and $220.37 of real spend.

**Full plan (diagrams, screens, reasoning):**
https://claude.ai/code/artifact/ec39b8ee-04fd-4fff-b6c4-6203a632f79c

**The audit it follows from:**
https://claude.ai/code/artifact/e1dcf448-4316-4b33-86b7-f659d991390f

**The three project types (Explore / Deliver / Operate), designed in full:**
https://claude.ai/code/artifact/249df2ee-c2f1-4642-86f9-344b8d01c585

This file is a table of contents, not a copy — it exists so a session working on `main`
can see what is planned before duplicating or contradicting it. Edit the artifact; keep
this list of statuses honest.

## The diagnosis, in one line

Work gets planned, claimed, done, evidenced, memorised and decided — and almost none of
it becomes a thing that exists outside the database. 126 tasks completed, 20 side
effects, all time. The gates are the best-working part of the system; every failure
happened where a gate was missing or decorative.

## Status

| | Phase | Status |
|---|---|---|
| 0 | Evidence verification back on | **done** — fpl-manager, constitution v9 |
| 1 | Reaped-run metering · suggestion survival · org chart in the pack · `max_usd_per_run` | **done** — branch `delivery-loop` |
| A | Pace the daily run budget to the money | approved, not started |
| B | Stop the restart waste (six audits, two never ran) | not started |
| 2 | Deliverables become real; the requester owns the acceptance test | not started |
| 3 | Velocity measured and shown; run→task cost attribution | not started |
| 4 | Requirements, commitment, burn-up, `ProjectStatus.COMPLETED` | not started |
| 5 | The status meeting | not started |
| 6 | Explore / Deliver / Operate | not started — needs 2 and 4 |
| C | Memory as a namespace (the memory tool's shape, your governed store) | not started |
| D | `task_budget`, `PreCompact` and `Stop` hooks | needs a live experiment |
| E | Does the employee layer earn its keep? (measure the topology) | not started |

Order is forced, not preferred: make "done" true (2), then measure it (3), then commit to
it (4), then review it (5), then repeat it (6). A and B are independent of all of it.

## Things already established, so they are not re-litigated

- **`trigger.reason` is not broken.** `runs.trigger` carries `reasons` as a populated
  array on all 945 agent runs. An earlier analysis looked for a singular key and was
  wrong; it was listed as a bug and is not one.
- **The agents do not chatter and are not slow.** 1.28 messages per task transition;
  median claimed→done is 5.5 minutes. Both were assumed and both are false.
- **Bet discipline works.** 24 hypotheses, 100% with real tasks attached, 75% producing
  verified external evidence. Do not "fix" it.
- **The memory tool itself is unreachable.** `ClaudeAgentOptions` has no field for
  Anthropic-provided tools, no `context_management`, and `betas` accepts one literal.
  Phase C adopts the pattern, not the tool.
- **Managed Agents is the wrong fit.** It would hand the loop and the sandbox to
  Anthropic; the containment model is the product.

## Validating this plan

`docs/PLAN_VALIDATION_PROMPT.md` is a self-contained brief for a fresh context window to
adversarially check this plan against every request the chairman actually made, against
yaaf's own architectural patterns, and against the live database. Use it before starting a
phase, not after.

## Branches

- `delivery-loop` — this work. Branched from `main`.
- `project-modes` — parked. Its step-0 commit is already cherry-picked onto
  `delivery-loop`; the rest is a design record for Phase 6.
