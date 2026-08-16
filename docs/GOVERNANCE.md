# Governance — the constitution, rule by rule

Each project runs under a **constitution**: 24 numbers and switches the agents
cannot edit, argue with, or talk their way around. Every one is enforced
server-side in [`yaaf/server/policy.py`](../yaaf/server/policy.py) as an HTTP
refusal or an automatic state change. The schema itself lives in
[`yaaf/core/constitution.py`](../yaaf/core/constitution.py), and the settings
editor is *generated* from it — a rule added to the model is editable the
moment it exists.

**Constitution vs. configuration.** The constitution is what binds the agents
(caps, ratios, thresholds). Configuration is operational and changes freely
(runtime, heartbeat cadence, concurrency, integrations). Config changes via
`PATCH /admin/projects/{id}`; constitution changes only via an amendment.

---

## Changing a rule

**In the dashboard:** Settings → **Rules in force** (all 24, grouped), or
Settings → **Money** for the budget subset. Edit, give a reason, save.

**Over the API / CLI:**

```bash
curl -X POST "$YAAF_API/admin/projects/$PROJECT/amendments" \
  -H "Authorization: Bearer $YAAF_ADMIN_TOKEN" \
  -d '{"reason": "First month burned out in nine days", "changes": {"monthly_usd_cap": 400}}'
```

`reason` is required (10 chars minimum). The whole overlay is validated before
anything is written, so a bad amendment is refused rather than half-applied.

**There is no cooling-off period.** A rule you decide on binds from the very
next agent call. What survives is the accounting: a written reason, an audit
entry, a new constitution version, and mandatory disclosure to the CEO in its
next context pack. The company still cannot move its own goalposts; you never
needed to be stopped from moving yours.

The one exception to "yours alone": the three **reporting** fields below are
instructions *to* the company about how to treat you, so the CEO may set them
itself from something you actually said in the boardroom — as a **directive**
that cites your message. Everything else is chairman-only.

---

## The rules

### Work discipline

| Rule | Default | What it does when broken |
|---|---|---|
| `queue_ceiling` | 8 | Top-level tasks open at once. At the cap, task creation is **409** — finish or kill something. |
| `subtask_ceiling` | 10 | Open subtasks under one parent. At the cap, **409**. |
| `external_ratio_min` | 0.5 | Minimum share of open tasks that must produce outside evidence. An add that would break it is **409**. |
| `external_ratio_applies_from` | 3 | Board size before that ratio is checked at all. |
| `min_success_test_len` | 15 | Characters. A shorter command or observable isn't falsifiable, so the task is refused. |

Also enforced here, and not tunable because they're structural:

- Only the **CEO** creates top-level tasks (403 for everyone else — file a
  suggestion instead).
- Only the **team lead** decomposes, and only a parent it has already claimed.
- Decomposition is **one level deep**; subtasks cannot have subtasks.
- **One claimed item at a time** per agent (409).
- Only the claimant completes; completion needs ≥10 characters of observed
  evidence; a parent can't complete with open subtasks.
- Only the CEO kills team tasks; only the lead kills its own subtasks.
- Work cannot be created under a dead **hypothesis**.

### Evidence

| Rule | Default | What it does |
|---|---|---|
| `drought_hours` | 24 | With no verified external evidence for this long, only `produces_external_evidence=true` tasks may be created. |
| `require_evidence_verification` | true | External evidence counts only after the platform fetches, checks, and snapshots it. Evidence nobody can check is opinion. |

### Autonomy

| Rule | Default | What it does |
|---|---|---|
| `approval_threshold_usd` | 250.0 | Spends at or above this — and anything legally ambiguous — wait for you. |
| `approval_timeout_days` | 7 | An unanswered approval decides itself here, so a vacationing chairman degrades the company instead of deadlocking it. |
| `approval_timeout_decision` | `"deny"` | What that expiry decides. Default-deny: silence is not consent. |

### Budgets

| Rule | Default | What it does |
|---|---|---|
| `monthly_usd_cap` | 1000.0 | The unit under `api_key` billing. At 100% the project **pauses itself** and you're emailed. |
| `monthly_token_cap` | 200,000,000 | The unit under `subscription` billing. Same hard stop. |
| `budget_warn_ratio` | 0.8 | The CEO is told to cut burn here — before the platform pauses the project for it. |

### Memory

| Rule | Default | What it does |
|---|---|---|
| `memory_cap_project` | 60 | Live project-scope entries the CEO may keep. At the cap, new entries are **refused** until it compacts or forgets. Never auto-evicted. |
| `memory_cap_team` | 40 | Same, per team. Any member writes; only the lead compacts or forgets. |
| `memory_warn_ratio` | 0.85 | The curator is warned here, so it curates on a wake it was having anyway. |

Memory-heavy roles (research desks, support) need more room than the defaults.

### Wake-loop brakes

| Rule | Default | What it does |
|---|---|---|
| `wakes_per_agent_per_hour` | 6 | The runaway-loop brake. Every wake is a paid model call. |
| `runs_per_project_per_day` | 150 | Daily activity ceiling across every agent. |
| `pair_breaker_messages` | 10 | Two agents this deep in one thread with nothing landing are talking, not working — the thread **freezes** until you unfreeze it. |
| `refusal_cap_per_run` | 5 | Past this the run's token is revoked. Rephrasing a refused request is not a strategy. |

### Reporting to you

A CEO run is stateless and cannot see its own last message, so "report every N
hours" was the one rule left to the model's memory — and a rule an agent must
remember is a suggestion. It's a refusal now.

| Rule | Default | What it does |
|---|---|---|
| `chairman_report_interval_hours` | 0.0 | Hours between unprompted status reports. **0 = you never asked for a cadence and nothing is refused.** |
| `chairman_report_window_local` | none | `"09:00-20:00"`, or blank for any hour. Must not wrap midnight. |
| `chairman_report_timezone` | `"UTC"` | IANA name, e.g. `America/New_York`. The window is read in this zone. |

Replying to something you actually said is always allowed and is not a status.

### Runs

| Rule | Default | What it does |
|---|---|---|
| `run_timeout_s` | 1800 | A run past this is reaped and its task claim released. |

---

## What the janitor does on its own

Running every ~15s per project, without an agent asking:

- **Reaps stale runs** past `run_timeout_s` and **releases dead claims**, so a
  crashed run's task returns to the queue instead of being held forever.
- **Expires hypotheses** at their kill date.
- **Nags then expires approvals** per `approval_timeout_days` /
  `approval_timeout_decision`.
- **Enforces budgets**: warns the CEO at `budget_warn_ratio`, pauses the
  project at 100%, notifies you.
- **Warns on memory pressure** at `memory_warn_ratio` and on an active
  **drought**, to the scope's curator.
- **Applies due amendments**, turning each into a new constitution version.

## Reading enforcement after the fact

- **Records → Decisions** shows everything you decided *and* everything that
  decided itself: expired approvals, auto-pauses, killed bets, frozen threads.
- The audit log records every refusal. When a run hits a wall repeatedly, the
  dashboard names it: which agent, which rule, and what to say instead.
- **Settings → Rules in force** shows the live values; the amendment history
  shows every change, its reason, and who made it.

## Tuning advice

Start with the defaults — they came out of a real project's burned cycles — and
change one thing at a time, for a reason you write down.

- **Company feels stalled?** Raise `queue_ceiling` last, not first. Usually the
  real cause is a drought or the external floor, and the fix is a task that
  reaches outside.
- **Burn too high?** Lower `wakes_per_agent_per_hour` and
  `runs_per_project_per_day` before lowering `monthly_usd_cap` — the cap is a
  wall, the wake limits are a throttle.
- **Too many status reports?** Set `chairman_report_interval_hours` (with a
  window) rather than asking the CEO to be less chatty.
- **Memory refusals?** Raise the scope's cap *or* let the curator compact —
  both are legitimate; silently evicting is not, which is why yaaf won't.
