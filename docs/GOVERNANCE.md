# Governance — the constitution, rule by rule

Each project runs under a **constitution**: 62 numbers and switches the agents
cannot edit, argue with, or talk their way around. Every one is enforced
server-side in [`yaaf/server/policy.py`](../yaaf/server/policy.py) as an HTTP
refusal or an automatic state change. The schema itself lives in
[`yaaf/core/constitution.py`](../yaaf/core/constitution.py), and the settings
editor is *generated* from it — a rule added to the model is editable the
moment it exists, on the tab its group maps to in `GROUP_THEMES`.

**Constitution vs. configuration.** The constitution is what binds the agents
(caps, ratios, thresholds). Configuration is operational and changes freely
(runtime, heartbeat cadence, concurrency, integrations). Config changes via
`PATCH /admin/projects/{id}`; constitution changes only via an amendment.

---

## Changing a rule

**In the dashboard:** Settings → **Rules in force**. All 56 sit behind seven tabs
— Money, The bar, Pace & brakes, How work is run, Autonomy, Memory, Rhythm &
reporting —
one rule per row, with the group as a subhead. Edit anything on any tab; a tray
at the foot of the pane lists every pending change across all of them, takes
your reason, and shows you the before/after before it lands. Settings → **Money**
displays the budget read-only and links here; rules are changed in one place
only. Deep link with `?s=rules&t=<tab>`.

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
next context pack. The project still cannot move its own goalposts; you never
needed to be stopped from moving yours.

The one exception to "yours alone": the three **reporting** fields below are
instructions *to* the project about how to treat you, so the CEO may set them
itself from something you actually said in the boardroom — as a **directive**
that cites your message. Everything else is chairman-only.

---

## The rules

### Work discipline

| Rule | Default | What it does when broken |
|---|---|---|
| `queue_ceiling` | 8 | Top-level tasks open at once. At the cap, task creation is **409** — finish or kill something. |
| `subtask_ceiling` | 10 | Open subtasks under one parent. At the cap, **409**. |
| `max_teams` | 8 | Teams the project may run at once. Your CEO hires and stands roles down itself, from your role library, inside this number; the hire that would exceed it is **409**, and it is told to argue for a bigger roster with `capacity_request` rather than work around the cap. Hiring buys no runs — the daily ration and the monthly wall are project-level — so a bigger roster splits the same allowance thinner. What it does buy is wake-ups, which is why the ceiling is here at all. |
| `staffing_review_days` | 14 | Days a seat may spend runs while closing no work before the platform opens a `seat_idle` flag. The CEO must answer it: stand the role down, or say on the record why it stays. |
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
| `require_evidence_verification` | false | On, external evidence counts only after the platform fetches, checks, and snapshots it. Off by default — most projects verify with the data at their disposal; the "Show me proof" creation preset turns it on. |

### Autonomy

| Rule | Default | What it does |
|---|---|---|
| `approval_threshold_usd` | 250.0 | Spends at or above this — and anything legally ambiguous — wait for you. |
| `approval_timeout_days` | 7 | An unanswered approval decides itself here, so a vacationing chairman degrades the project instead of deadlocking it. |
| `approval_timeout_decision` | `"deny"` | What that expiry decides. Default-deny: silence is not consent. |
| `ask_window_hours_max` | 72.0 | The longest window your CEO may give you to answer a question. It names a shorter one when the work has a deadline. There is no setting for what an unanswered question decides — the CEO writes that down before it asks, and you see it on the desk. |
| `max_open_asks` | 3 | How many unanswered questions your CEO may have on your desk at once. Asking is cheap for it and expensive for you. |
| `plan_authority` | `"chairman"` | Who accepts proposed scope. `"ceo"`: the CEO accepts its own proposals — the plan gate runs the same arithmetic, every acceptance is audited and sent to you. |
| `scope_authority` | `"chairman"` | Who may cut, reopen, restage, reestimate, reword and unsequence. `"ceo"` hands the verbs to the CEO, on the record, reversibly. Your routes never close. |
| `commitment_authority` | `"chairman"` | Who signs the delivery date, and who resets one that has stopped holding. `"ceo"`: proposing adopts the platform's own projection immediately, and `commitment_reset` re-adopts it on a slip — the CEO still cannot write a date, only accept the arithmetic's. Your own Move is unrestricted, because you may promise what the arithmetic does not. |
| `verdict_policy` | `"chairman"` | Who judges unverifiable observable closes. `"ceo_judgment"`: the CEO judges and keeps going, and every self-judgment is disclosed to you in full — stronger than sampling. |
| `capability_authority` | `"chairman"` | Who scopes credentials to roles. `"ceo"`: the CEO re-scopes existing service credentials across its team itself, on the record, disclosed to you. Creating a credential is always yours — you hold the values. |

The five authority switches are the dial (docs/AUTONOMY.md): they decide *who
holds a verb*, never whether the record is honest.

**The same shape governs pace.** Thirteen rules decide how often the project
thinks, and they move together as a *tempo card* — `live` (~150 runs a day),
`working_hours` (~40) or `few_times_daily` (~12). A project that names none of
them is stamped with the card its mode implies: Explore and Deliver both end,
so they get `live`; an Operate project takes its cue from `cycle_frequency`,
and a weekly rhythm gets `few_times_daily`. Name even one pace rule and nothing
is stamped — that is a chairman choosing rule by rule, and it is never rounded
to a card, exactly as a hand-set authority dial is not. The Founder is held to
a stricter line than you are here: it must pick a card by name and may not
invent the numbers.

**A new project gets `"ceo"` on all five unless it says otherwise.** The
`"chairman"` defaults in the table are what an *omitted* rule means for a
constitution already stored; at creation, a spec (or a form, or a model client)
that mentions none of the five is stamped with the Autonomous preset instead.
Say the rules you want and they are used verbatim — including a dial you set
one rule at a time, which is never rounded to a preset. Every self-exercise runs
through the same machinery as your own buttons, lands in the audit log, is sent
to you out-of-band, and is listed under **Records → Decisions → what the CEO
decided**. The dial itself is chairman-only — a directive touching it is refused
like any other constitution grab.

**A granted authority always has a verb behind it.** With `plan_authority`,
`scope_authority` and `commitment_authority` all on `"ceo"`, an `owner_decision`
approval is **refused**, naming the verb to use instead: an approval blocks on a
human and carries a default-deny clock, so filing one for a decision you already
delegated is the one way an autonomous project can still stall on you. The four
kinds the dial cannot delegate — `spend`, `legal`, `tool_request`, `assist`, `capacity` —
still reach you at every setting.

### Budgets

| Rule | Default | What it does |
|---|---|---|
| `monthly_usd_cap` | 1000.0 | The unit under `api_key` billing. At 100% the project **pauses itself** and you're emailed. |
| `monthly_token_cap` | 200,000,000 | The unit under `subscription` billing. Same hard stop. |
| `hard_stop_multiple` | 3.0 | The wall, as a multiple of the budget. The budget is the plan you make before the work starts; this is where a runaway is actually stopped. They used to be one number, which is why every CEO lowballed: crossing your own plan was fatal, so the plan became a thing to stay well back from. 1.0 restores the old behaviour. |
| `budget_warn_ratio` | 0.8 | The CEO is told to cut burn here — before the platform pauses the project for it. Also warns on the external envelope. |
| `external_monthly_usd_cap` | 500.0 | The month's real-world spend wall (purchases, services) — separate from the model budget and enforced in **every** billing mode. A purchase that would cross it is refused with the arithmetic; no approval overrides it, only an amendment. 0 turns it off. |
| `max_usd_per_purchase` | 100.0 | The most one purchase may be without an approval, even below the approval threshold. 0 turns the line off. |

There is deliberately no `spend_authority` switch: the money dial **is** these
numbers plus `approval_threshold_usd` — a switch would only duplicate them.
Inside the envelope an autonomous CEO buys without asking; every purchase is
cleared before money moves (`purchase_begin`), booked with its receipt
(`purchase_finish`), and crash-retries can never buy twice. A spend made with
raw tooling is booked with `spend_book` against the same walls.

### The bar

| Rule | Default | What it does |
|---|---|---|
| `stakes` | `normal` | `low` / `normal` / `high`. Answered once, it buys two things. **Money:** how hard to spend when the budget turns out to be wrong — `high` gets real headroom before anyone worries and the CEO is told to spend what the work needs and report; `low` means going over is a stop-and-ask. **Craft:** at `high`, the seats whose output *is* the deliverable or whose job is to check it (roles filed `content`, `research`, `build`, `challenge`) are hired on the strongest model and the deepest reasoning instead of on whatever the shared library says — because raising it in the library raises it for every project on the install. A seat you gave an explicit model or effort keeps yours, and nothing is ever lowered. Stamped onto the roster at hire, so it is visible in an export rather than implied by a rule. |
| `require_search_record` | false | On, a task with `produces_external_evidence=true` cannot close without saying what it looked for, where, and what came back **empty**. The alternative floor — a count of cited sources — is satisfied by fetching three pages and citing them whether or not a claim derives from any, which buys a citation ritual that passes the gate and reads as rigour. A search record is unverifiable but not gameable in a way that resembles compliance: a thin one reads as thin. |
| `deliverable_review` | `none` | `independent_read` means the work is read by somebody who did not write it, and the platform refuses a roster that asks for it and hires no seat filed `challenge` — an editor, an adversary, a skeptic. `none` is what every project did before this rule existed: the only reader of a deliverable before you was the agent that wrote it, and 80% of those closes were never sampled. Off by default so an install that upgrades is unchanged. |

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
| `runs_per_project_per_day` | 150 | The daily activity **plan** across every agent — what the project should need, not the most it may have. |
| `runs_hard_stop_multiple` | 1.0 | The run wall, as a multiple of the daily plan. Between the plan and the wall the project keeps working and **owes an account** — a `run_overrun` flag it must raise with what the extra runs bought. Crossing the plan used to be silently fatal for the rest of the day, with no account, no flag and no way to ask, so the only safe move was to stay well under it. Same lesson as `hard_stop_multiple` for money, learned twice. **It defaults to 1.0** — plan and wall the same number, exactly as before this rule existed — because a default above 1 would have raised the daily run volume of every project on the install the moment it shipped, with no per-project opt-in and nothing to accept. Raise it on a desk whose run ration is what is actually pacing it. It does **not** touch `max_concurrent_runs`, which is what protects the provider rate limit the whole box shares — that is simultaneity rather than volume, and it still binds absolutely. |
| `runs_per_project_per_hour` | 15 | The pacing brake, on a trailing hour. Without it a project spends the day's allowance by lunch and sits dead until midnight. |
| `pair_breaker_messages` | 10 | Two agents this deep in one thread with nothing landing are talking, not working — the thread **freezes** until you unfreeze it. |
| `max_usd_per_run` | 2.0 | USD one run may spend. It is warned as it approaches and stopped at the line. 0 turns it off. |
| `refusal_cap_per_run` | 5 | Past this the run's token is revoked. Rephrasing a refused request is not a strategy. |
| `task_check_failures_before_block` | 3 | A task whose success test fails this often **blocks** and becomes a question for whoever planned it. Counts across re-claims; only a pass resets it. |
| `requirement_check_failures_before_escalation` | 3 | A requirement whose acceptance test keeps failing this often stops being retried silently and becomes a question — the requirement-level twin of the rule above. |
| `max_task_gate_hours` | 6 | The furthest ahead work may be scheduled with `not_before`. A gated task wakes nobody and shows on no screen as work in progress, so a project gated on one looks stopped. Gates beyond this are refused. |
| `stall_hours` | 3.0 | Hours of silence before you are told. A project that quietly stopped is the failure mode this catches. |
| `inbox_runs_per_project_per_day` | 40 | Reading the inbox has its own daily purse, so a busy mailbox cannot eat the allowance the work needs. |
| `inbox_runs_per_project_per_hour` | 8 | The trailing-hour twin, for the same reason as `runs_per_project_per_hour`. |
| `max_concurrent_runs` | 3 | Agents working simultaneously. Protects the provider rate limit every project on the box shares. |
| `reserved_governance_runs` | 3 | Runs held out of the daily ceiling so a review, a cycle plan or a re-forecast can always happen. Ordinary work stops at the cap minus this. 0 turns it off. |

### Sampled audit

Gates ask you at commitment, review and completion. Between them the platform
samples observable closes at a rate you set — and draws **after** the close, so
no agent knows which claim will be checked. Sampling never blocks.

| Rule | Default | What it does |
|---|---|---|
| `audit_sample_rate` | 0.2 | Chance an observable close lands on your desk. 0 turns sampling off; 1 samples everything. Command-checked closes are never sampled — the platform already ran those. |
| `audit_response_hours` | 48 | A sample you have not answered is recorded "not reviewed" and counted in the next review's coverage line. |

### CEO cadence

| Rule | Default | What it does |
|---|---|---|
| `ceo_heartbeat_hours` | 6.0 | Hours between planning wakes when nothing else has woken the CEO. Every wake is a full-price run. |
| `ceo_heartbeat_always` | false | Off, a planning wake is skipped with an empty board and no live bet — it would buy a run to conclude "nothing to do". |
| `ceo_daily_at` | none | `"09:00"` in `timezone`, or blank. Fires once per local day. The standup pattern. |

### Operate rhythm, review, commitment

| Rule | Default | What it does |
|---|---|---|
| `cycle_frequency` | `"weekly"` | Operate projects only: `daily`, `weekly`, `monthly` or `quarterly`. Each roll closes the cycle, carries unfinished scope forward and reopens the plan gate. |
| `cycle_day` | 0 | Weekly: 0=Monday … 6=Sunday. Monthly/quarterly: day 1–28 — the 29th to 31st are refused rather than quietly moved. |
| `review_interval_days` | 7 | The standing meeting. The janitor opens it on schedule regardless of budget or an empty board and emails you the numbers; the CEO files its variance narrative on a reserved governance run. |
| `commitment_failure_reforecast` | 5 | Dead runs against the plan before the platform re-projects the date and tells the CEO the ground moved. 0 turns this trigger off. |

### Reporting to you

A CEO run is stateless and cannot see its own last message, so "report every N
hours" was the one rule left to the model's memory — and a rule an agent must
remember is a suggestion. It's a refusal now.

| Rule | Default | What it does |
|---|---|---|
| `chairman_report_interval_hours` | 0.0 | Hours between unprompted status reports. **0 = you never asked for a cadence and nothing is refused.** |
| `chairman_report_window_local` | none | `"09:00-20:00"`, or blank for any hour. Must not wrap midnight. |
| `timezone` | `"UTC"` | IANA name, e.g. `America/New_York`. **One zone for the whole rulebook** — the reporting window, the daily CEO wake and every recurring rhythm are read in it. (Was `chairman_report_timezone`, and was also set a second time in project config; one place too many to get wrong.) |
| `brief_daily_at` | none | `"08:00"` in `timezone`, or blank for none. The daily **briefing** — what happened since the last one, composed by the platform from the record at **zero tokens**. |
| `mail_max_per_day` | 5 | How many emails **waiting on a decision from you** the platform may send in a rolling 24 hours. Past it the waits are read in the briefing instead. Alarms are never held. 0 = no limit. |

Replying to something you actually said is always allowed and is not a status.

The first three rules above govern what the *project* may say to you, and every
word of it costs a model call. `brief_daily_at` is the opposite direction: the
platform writing to you from rows it already holds. No run, no wake, no token
— which is why it is not gated on a budget or an empty board, and why it still
arrives on the morning the platform paused the project for an exhausted cap.
Every other letter you get from this system is an alarm, and a correspondent
who only ever brings bad news is one you stop reading.

`mail_max_per_day` governs the third direction: not what the project may say,
and not what the platform may compose, but how often either is allowed to
*interrupt* you. Every notice the platform raises names how much of you it is
asking for, and only two of the four kinds ever reach your inbox unbidden:

| Kind | Example | Reaches you |
|---|---|---|
| **alarm** | the project paused, a promise broken, a billing credential dead | always, uncapped |
| **action** | an approval to sign, a verdict only you can record, scope to accept | until the daily limit above |
| **brief** | the daily briefing itself | always |
| **fyi** | a deliverable published, a cycle closed, a decision the CEO took under authority you gave it | never — it is in the briefing |

The **fyi** row is the one that does the work. It is a third of the notice kinds
by count and much more than a third by volume, because those are the ones that
fire per event: every deliverable published, every cycle closed, every audit
sample drawn, every decision the CEO takes under a dial you set. They are not
dropped — the webhook still carries every one, the audit log still holds them,
and the briefing already reports them *batched*. A single briefing window in
this project's own record held "20 deliverables published" and "the CEO
exercised 26 decisions": two lines there, and forty-six separate emails before
this change. The inbox was the only surface in this system with no theory of whose
news is whose; the briefing has had one since it shipped, and it is the same
autonomy dial you set. A decision you delegated is not news, and the platform
should not have been waking you for it.

A held **action** notice loses you the interrupt, never the news. Everything in
that class is a *wait*, and the briefing renders every open wait live from the
record in its "waiting on you" section — so the next brief still names it, and
the audit log records both what was sent and what was held.

### Runs

| Rule | Default | What it does |
|---|---|---|
| `run_timeout_s` | 1800 | A run past this is reaped and its task claim released. The CEO's resident chat session is exempt — it dies only when its heartbeat stops. |
| `run_token_budget` | 0 | Handed to the model as a task budget so it paces itself and wraps up before the line instead of being cut off mid-thought. 0 turns it off. |

---

## What the janitor does on its own

Running every ~15s per project, without an agent asking:

- **Reaps stale runs** past `run_timeout_s` and **releases dead claims**, so a
  crashed run's task returns to the queue instead of being held forever.
- **Expires hypotheses** at their kill date.
- **Nags then expires approvals** per `approval_timeout_days` /
  `approval_timeout_decision`.
- **Answers, nags and lapses asks**: a question the CEO put to you is marked
  answered the moment you say anything in the boardroom after it, chased
  halfway through its window (proportional, because an ask's window can be
  hours where an approval's is days), and at `answer_by` becomes the default
  the CEO wrote down when it asked — which wakes it to do that and tell you.
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

- **Project feels stalled?** Raise `queue_ceiling` last, not first. Usually the
  real cause is a drought or the external floor, and the fix is a task that
  reaches outside.
- **Burn too high?** Lower `wakes_per_agent_per_hour` and
  `runs_per_project_per_day` before lowering `monthly_usd_cap` — the cap is a
  wall, the wake limits are a throttle.
- **Too many status reports?** Set `chairman_report_interval_hours` (with a
  window) rather than asking the CEO to be less chatty.
- **Memory refusals?** Raise the scope's cap *or* let the curator compact —
  both are legitimate; silently evicting is not, which is why yaaf won't.
