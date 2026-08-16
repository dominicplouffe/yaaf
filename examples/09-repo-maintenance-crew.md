# 09 — An overnight crew for a repo you already own

**You want:** to wake up to three clean PRs — the dependency bumps, the flaky
test finally pinned down, the deprecation warnings gone. **You don't want:** a
1,400-line "refactor" against your main branch, or a bot that opened the same
PR four times because a container died.

This is the most immediately useful example for a working developer, and the
one with the tightest definition of done: every task's success test is a
command the platform runs against your actual test suite.

## The org

| Team | Role | Members | Does |
|---|---|---|---|
| Maintenance | `engineer` | Ada (lead), Linus | Upgrades, flakes, warnings, small well-scoped fixes |

One team, two engineers. No writer — PR descriptions are the engineer's job and
your README is not the crew's business.

## The goal

> Keep the acme/api repository healthy without changing its behavior. Work
> only on: dependency upgrades that keep the full test suite green, fixing
> tests that fail intermittently, removing deprecation warnings, and closing
> issues explicitly labeled `good-first-issue`. Every change is a separate pull
> request against a branch, never a direct push to main, with the failing-then-
> passing evidence quoted in the description. Never change public API behavior;
> never bundle unrelated changes into one PR; never add tests, guards, or
> refactors nobody asked for. If a fix requires a design decision, open an
> issue describing the choice and stop.

Read that last clause again. It's the one that keeps the crew from turning into
an unsupervised architect at 3am. The `engineer` role already says "you decide
how, never what" — the goal makes it specific.

## Connect GitHub

Settings → **GitHub workspace**. The project gets a private repo as the agents'
shared workspace, cloned into the sandbox at lease time so work persists
between runs. For an existing repo you own, point the crew at it and grant a
token scoped to just that repo:

```bash
yaaf set-secret github --project api-maintenance --scopes engineer
# value prompted: a fine-grained PAT, contents+PR write on acme/api only
```

The secret's name becomes `GITHUB` in the engineer sandbox, and only there. Do
not hand it an org-wide classic token because it was faster.

**Branch protection is your real backstop.** Require a PR and a green CI check
on main. Then no amount of agent enthusiasm can push to it. Set this up before
you start; it takes two minutes and it is the difference between a maintenance
crew and an incident.

## Success tests — the good kind

This is the use case where `command` tests are natural, so use nothing else:

```json
{"type": "command", "command": "pip install -e '.[dev]' && pytest tests/ -q && ruff check . && mypy yaaf/"}
{"type": "command", "command": "pytest tests/test_scheduler.py::test_lease_expiry -q --count=50"}
{"type": "command", "command": "python -W error::DeprecationWarning -m pytest tests/ -q"}
```

The middle one is how you kill a flake: run it fifty times and require all
fifty. An agent cannot argue with that, and neither can the flake.

Put the standard in the goal so the CEO doesn't drift toward observables:

> Every success test must be a shell command this platform can run in the repo.

## The rule that prevents duplicate PRs

Before any external side effect — opening a PR, a deploy touching shared infra,
a message to an outside system — the engineer records it with a stable
idempotency key. If a run is retried after a crash, the ledger reports the prior
attempt completed and the PR does not fire again.

If you've ever had a bot open the same PR three times after a CI hiccup, this
is the mechanism that makes it structurally impossible rather than usually fine.

## Constitution settings that matter here

| Setting | Default | For this crew | Why |
|---|---|---|---|
| `monthly_usd_cap` | 1000 | 150 | Maintenance is cheap; if it isn't, something's looping |
| `queue_ceiling` | 8 | 4 | Four open PRs is already more than you want to review |
| `runs_per_project_per_day` | 150 | 40 | This is an overnight crew, not a swarm |
| `drought_hours` | 24 | 720 (30 days) | Internal maintenance produces no external evidence, ever |
| `approval_threshold_usd` | 250 | 10 | It should not be buying anything |
| `run_timeout_s` | 1800 | 3600 | A full matrix test run can legitimately exceed 30 minutes |

Then set the pace:

```bash
yaaf configure api-maintenance --ceo-heartbeat-hours 8 --max-concurrent-runs 2
```

## Watching the money

Maintenance work is the easiest place to quietly burn budget on retries, so
this is the example where the spend report earns its keep:

```bash
yaaf spend-report api-maintenance --days 7
```

It breaks spend down by team, agent, role, and run kind, and it counts the runs
that failed, were killed, or got reaped rather than quietly dropping them. That
waste column is a free environment check: when it's near zero the sandbox is
healthy, and when it spikes it tells you where to look. Note the report opens
the server's database directly, so run it on the host (it wants
`YAAF_DATABASE_URL`, not `YAAF_API`).

The other guardrail runs itself: if a run fails on a bad credential three times
in a row, the project pauses rather than burning money against the wall, and
you get an email.

## Your job

- **Review the PRs.** Same as any contributor's. The crew's output is only as
  good as your merge bar.
- **Say no to scope.** When a PR does more than the task said, close it with a
  one-line reason in the Boardroom. That correction lands in team memory and
  the next PR is tighter.
- **Feed it work through the CEO.** You can't create tasks, and you shouldn't
  want to. `yaaf say api-maintenance "Priority for this week: the flaky
  scheduler test in tests/test_scheduler.py — it fails maybe 1 in 30 on CI.
  Pin the cause before anything else."`
