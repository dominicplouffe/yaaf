# 10 — Try the whole thing for $0

**You want:** to see what this actually does — the queue, the refusals, the
evidence rules, the dashboard — before you attach an API key or trust it with
anything real. **You don't want:** to learn what `queue_ceiling: 8` means by
watching $40 disappear.

yaaf ships a mock runtime: scripted agents that drive the **real** API, get the
**real** refusals, and move through the **real** state machine. Nothing below
the model is stubbed. You get the full experience of the platform with zero
tokens spent.

## Five minutes, no Docker, no key

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/          # the whole suite runs offline

export YAAF_DATABASE_URL=sqlite:////tmp/yaaf.db \
       YAAF_ADMIN_TOKEN=dev-admin-token \
       YAAF_SANDBOX_MODE=subprocess
.venv/bin/python -c "from yaaf.core.db import engine; \
  from yaaf.core.models import Base; Base.metadata.create_all(engine())"
.venv/bin/yaaf-server &                    # API + dispatcher + janitor
export YAAF_API=http://localhost:8080
.venv/bin/yaaf seed-roles roles/
```

Open **http://localhost:8080/app** and log in with `dev-admin-token`.

## Create a project that will never cost a cent

The switch is one config key: `"runtime": "mock"`.

```json
{
  "slug": "dry-run",
  "name": "Dry run",
  "goal": "A goal long enough to pass validation, used only to watch the machinery move: build a small thing, verify it with a real test, and record evidence for it.",
  "chairman_email": "you@example.com",
  "config": { "runtime": "mock" },
  "teams": [
    { "role_slug": "engineer", "name": "Build", "employees": ["Ada", "Grace"], "lead": "Ada" }
  ],
  "constitution": { "queue_ceiling": 3, "monthly_usd_cap": 5, "drought_hours": 2 }
}
```

```bash
yaaf create-user you@example.com "You"
yaaf create-project dry-run.json
yaaf start dry-run
```

No billing credential needed — the mock backend never calls a model. Watch it:

```bash
yaaf dashboard dry-run
yaaf board dry-run
yaaf runs dry-run --limit 20
yaaf channels dry-run && yaaf tail chan_… --limit 30
```

## What to actually do with the dry run

Not "confirm it starts." Use it to answer the questions you'd otherwise answer
with real money.

**1. Find out what your constitution feels like.** Set `queue_ceiling: 3` and
watch the fourth task creation get refused with a message written for the
agent to read: *"finish something or kill something. A refusal here is the
system working."* Now you know whether 3 is disciplined or strangling, before a
real CEO is living under it.

**2. Watch a drought bite.** Set `drought_hours: 2` and backdate nothing —
within a couple of hours of no verified external evidence, internal task
creation starts getting refused. This is the rule that surprises people most in
production. Meet it here.

**3. Watch every rule get attacked.** The agent API takes a short-lived
per-run token, not your admin token — you can't poke it by hand, and neither
can anything else. What you can do is run the attacks that are already written:

```bash
.venv/bin/python -m pytest tests/selftest_invariants -v
```

Twenty-odd files, each one a rule being attacked over real HTTP by a real
principal: completing a task you didn't claim, posting in another team's room,
creating a task as a non-CEO, granting a role a billing secret, writing
project-scope memory as an employee. Read
[tests/selftest_invariants/test_task_rules.py](../tests/selftest_invariants/test_task_rules.py)
and [test_governance.py](../tests/selftest_invariants/test_governance.py) — ten
minutes there teaches you more about the security model than the README does.

**4. See what a success test rejection looks like.** The rule-breaker cast
submits `{"type": "observable", "target": "done"}` for you; the refusal reads
*"success_test.target must name a checkable artifact, URL, or metric."* To try
your own variations, add a behavior function to `evals/behaviors.py` — it's
`(api, job) -> summary`, and `api` is bound to that run's own token.

## Rehearse your real project's rules

The genuinely useful move: create your intended project *in mock mode first*,
with the exact constitution you plan to run. Same ceilings, same drought hours,
same budget, same team shape. Run it for a day. You'll find out whether your
numbers produce a company that works or one that spends its life being refused —
and it costs nothing to find out.

Then create it for real with the numbers you learned. Remember the constitution
freezes at creation: budgets, reporting cadence and memory caps you can change
immediately, but behavioral knobs take a 72-hour cooling-off amendment. Getting
them right the first time is worth an afternoon of rehearsal.

## The scripted casts you can borrow

[evals/behaviors.py](../evals/behaviors.py) has three casts already written:

| Cast | What it does |
|---|---|
| `disciplined_ceo` / `_lead` / `_employee` | Plays by the rules — the happy path, end to end |
| `rule_breaker_ceo` / `_lead` / `_employee` | Deliberately attacks every rule so you can watch each refusal fire |
| `decomposition_ceo` / `_lead` / `_employee` | Exercises the CEO → lead → employee breakdown path |

The rule-breaker cast is the one to watch. It's the guided tour of the
governance layer, and it runs in seconds.

## The eval harness, which you'll want later

```sh
python -m evals.run              # check: re-run scenarios, diff against goldens
python -m evals.run --record     # re-record goldens after an intentional change
```

Each scenario builds a fresh SQLite database, brings the real FastAPI app up
in-process, provisions a project through the real admin API, and runs
`dispatch → lease → execute → janitor` ticks — the production loop. Every agent
API interaction is recorded as a normalized transcript and diffed against a
golden.

Why you'll care: it's what makes editing role prompts safe. Change a system
prompt in `roles/`, run the evals, and see exactly how the scripted org's path
through the API changed — in CI, at $0. Without it, "I improved the engineer
prompt" is a claim nobody can check.

## When you're ready for the real thing

```bash
./deploy_single_server.sh
```

That generates both secrets, builds both images, migrates, seeds the roles and
prints the token you log in with. Details in
[docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md).

Postgres, the API and dashboard, the runner, and the egress proxy. Migrations
apply themselves. Attach an Anthropic API key per project in Settings, and flip
the runtime:

```bash
yaaf configure my-project --runtime agent_sdk
```

That one flag is the whole difference between the rehearsal and the show.
