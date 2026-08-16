# 06 — One agent, all the guardrails

**You want:** an agent working unattended on a real goal, with budgets that
actually stop, a definition of done it can't self-grade, and a record you can
read afterward. **You don't buy** the premise that a simulated org chart makes
agents smarter — and honestly, neither does this repo yet
([docs/M2_PROTOCOL.md](../docs/M2_PROTOCOL.md) is the experiment that will
settle it).

Good news: the governance layer stands on its own. **Solo mode** gives you one
agent that plans and executes, under byte-identical policy to a full org. No
CEO-to-lead-to-employee relay, no channels, no coordination tax.

## What solo mode actually is

One agent with CEO-class permissions, placed as the sole member — and therefore
the derived lead — of the only team. It creates its own tasks against the same
policy engine that would refuse a CEO. It writes its own success tests, and the
platform runs them. It claims, works, completes, and gets refused exactly as
often as an org would.

What you keep:

- Success tests the platform executes, not self-assessment
- Evidence verification against uncontrolled domains
- Hard budget stops, warn at 80%, self-pause at 100%
- A fresh container per run, egress allowlisted per role, scoped secrets
- Leases, reaping, and a side-effect ledger so a crash can't double-fire a PR
- Every message, transition, run, dollar, and refusal in Postgres

What you drop: the org.

## The spec

Solo mode requires exactly one team with no employees. From the dashboard
wizard it's a checkbox; here it is as a spec file:

```json
{
  "slug": "solo-migration",
  "name": "Postgres 14 → 16 migration",
  "goal": "Migrate the analytics service from Postgres 14 to 16 with zero data loss and no more than 5 minutes of write downtime. Deliverables: a rehearsed migration runbook, a tested rollback path, and the migration performed against staging with the full test suite green afterward. Do not touch production; the chairman runs the production cutover using the runbook. Done by August 29th.",
  "chairman_email": "you@example.com",
  "billing_mode": "api_key",
  "controlled_domains": ["github:your-account"],
  "solo": true,
  "teams": [
    { "role_slug": "engineer", "name": "Solo", "employees": [] }
  ],
  "constitution": {
    "monthly_usd_cap": 120,
    "queue_ceiling": 5,
    "drought_hours": 336,
    "approval_threshold_usd": 20,
    "runs_per_project_per_day": 60
  }
}
```

```bash
yaaf create-project examples/solo-migration.json
yaaf set-secret anthropic --project solo-migration --kind billing
yaaf configure solo-migration --billing-secret-id secr_…
yaaf start solo-migration
```

Note `"employees": []` — that's what makes it solo. A non-empty list with
`solo: true` is refused at creation, on purpose.

## Why it's still worth having a platform for one agent

The obvious question: why not just run a coding agent in a terminal?

Because the terminal version has you in it. The moment you close the laptop,
you lose the four things that make unattended work safe:

| | Agent in a terminal | Solo mode |
|---|---|---|
| Lifespan | While you watch | Days — wakes on schedule, works one unit, sleeps |
| "Done" | Whatever it says | A command the platform ran, or an observable a stranger can check |
| Spend | You notice eventually | Metered per run; pauses itself at the cap; you get an email |
| After a crash | You re-run it and hope | Lease reaped, work released, side effects not re-fired |
| The record | Scrollback | Every run, dollar, refusal, and transition, queryable |

Solo mode is for the overnight and the over-the-weekend. It's not trying to be
a better pair-programming session — it's for when nobody's pairing.

## Success tests when there's nobody to check the checker

Solo mode's one real weakness: the agent that writes the test is the agent that
passes it. There's no lead reviewing the decomposition. Compensate by putting
the standard in the goal, and by preferring `command` tests over `observable`
ones — a command the platform executes doesn't care who wrote it.

```json
{"type": "command", "command": "docker compose -f test/pg16.yml up -d && ./scripts/migrate.sh --dry-run && pytest tests/ -q"}
{"type": "command", "command": "./scripts/rollback.sh --verify && psql -c 'select count(*) from events' | diff - fixtures/expected_count.txt"}
```

Add the standard to the goal text itself:

> Every task's success test must be a command this platform can run. Where an
> observable is unavoidable, it must name a file, URL, or metric a stranger
> could check without asking you.

## What the day looks like

Quiet. One agent, one unit of work per run, sleeping in between. `yaaf runs
solo-migration --limit 20` shows you what happened. `yaaf spend-report
solo-migration --days 7` shows where the money went, broken out by run kind, so
you can see if heartbeat wakes are eating budget that should go to work.

If you want it more or less industrious:

```bash
yaaf configure solo-migration --ceo-heartbeat-hours 6 --max-concurrent-runs 1
```

## What will actually go wrong

**Scope creep with nobody to refuse it.** In an org, an employee who wants to
refactor gets told no by a lead. Solo, the same brain writes the ticket and
picks it up. The queue ceiling is your only structural brake — keep it low (5),
and read the task list weekly for work you didn't ask for.

**Self-graded observables.** If the agent leans on `observable` tests, it is
grading itself with extra steps. Watch for it in the Tasks tab; it's the single
highest-signal thing to check in solo mode.

**It's slower than it looks.** One agent, one item per run, is genuinely serial.
For work that parallelizes — three independent services, ten files to migrate —
an org arm will finish sooner, coordination tax and all. That's part of what
[07](07-run-the-experiment.md) is designed to measure.
