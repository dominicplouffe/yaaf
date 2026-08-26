# FPL Gameweek Desk — the weekly in-season company

**Upload file:** [`fpl-gameweek-desk.json`](fpl-gameweek-desk.json) — a `yaaf_bundle`
setup file carrying the project *and* its eight roles. Drop it on **+ New project →
load a setup file** and the roles are created for you in the same action.

This is the **in-season** companion to [`fpl-manager.md`](fpl-manager.md). That project
picks an opening squad and stops. This one starts the morning after Gameweek 1 and runs
for the remaining thirty-seven, answering the same six questions every week and getting
scored on its own answers.

Two things it does differently, and both are deliberate:

- **No MCP server, no data source, no account.** The desk runs on the public FPL API
  directly. See §3.
- **It is built to be cheap and mostly idle.** Most weeks cost four agent runs, not
  thirty. See §7.

> The agents **recommend only**. They have no write access to the team. Every transfer,
> chip, captain and bench swap is executed by you, in the FPL app, before the deadline.

---

## 1. What you get, every gameweek

One deliverable — **the gameweek memo**, in the boardroom at least 24 hours before the
deadline. It answers six questions, in this order, with names and numbers, and it is
forbidden from hedging into "consider X or Y":

| # | Question | What the memo must contain |
|---|---|---|
| 1 | **Transfer** | Make one or roll. If make: who out, who in, sell value and buy price, bank after, expected-points gain across the next five gameweeks. A −4 only when the arithmetic shows it clearing four expected points **across the horizon**, not in one week. |
| 2 | **Chip** | Wildcard, Free Hit, Bench Boost, Triple Captain — or **none**. When none: the gameweek each unplayed chip is currently aimed at, and the condition that would move it. |
| 3 | **Captain** | One name, with expected points, effective ownership and the fixture. |
| 4 | **Vice-captain** | One name, chosen kickoff-time aware — a vice whose match has already finished cannot cover a captain benched at 15:00. Kickoff times are in `/fixtures/`, so this is checkable rather than a matter of taste. |
| 5 | **XI and bench** | The eleven with the formation named, then the four bench players in auto-sub order (sub GK in slot 12), and **explicitly which players move between XI and bench versus last week**. |
| 6 | **What would make this wrong** | The single most likely way the memo loses points, and what to do if it happens before the deadline. |

When nothing needs to change, the memo opens with **NO CHANGES THIS WEEK** and still
answers 3, 4 and 6. A quiet week is a real finding; churn is the most expensive habit in
this game and the desk is told so in several places.

Then, **within 48 hours of the gameweek ending** — 48 because bonus and defensive
contributions settle at 09:00 UK the day after the last match, which `/event-status/`
confirms — the **scored review**: points scored, points left on the bench, rank movement,
what the captain returned against the alternatives, which calls were right, which were
wrong, and the one thing that changes as a result. It scores the *decision*, not the
outcome: a captain pick that was right on expected points and blanked was still right, and
pretending otherwise is how a desk starts chasing last week's score.

---

## 2. Your weekly job: paste the brief

The desk cannot see your team — that is the one thing the public API will not serve, because
a live squad needs your login. Once a week, paste this into the **Boardroom**:

```text
GW<N> BRIEF
Free transfers: <n>
Chips remaining: <wildcard 1st half / free hit / bench boost / triple captain — whichever are left>
Overall points: <n>
Overall rank: <n> of <total players>
Last gameweek points: <n>

Current team:
<paste the JSON from https://fantasy.premierleague.com/api/entry/<TEAM_ID>/event/<N>/picks/>
```

Your `<TEAM_ID>` is the number in the URL when you view your own points page on the FPL
site.

### Worked example (the GW1 brief that produced this project)

```text
GW2 BRIEF
Free transfers: 1
Chips remaining: all eight — nothing played
Overall points: 51
Overall rank: 4,080,064 of 9,706,614
Last gameweek points: 51

Current team:
{"active_chip": null, "automatic_subs": [], "entry_history": {"event": 1, "points": 51, ... }, "picks": [ ... ]}
```

### Why the decoding rules are written into every prompt

That JSON is almost entirely codes, and a desk that guesses at them produces confident
nonsense. All eight roles carry the same decoding block:

- `picks[].element` is an **FPL player id** and means nothing until it is resolved against
  `elements[].id` in `bootstrap-static`. Ids are reassigned between seasons, so the prompts
  forbid guessing a name from an id or carrying one forward from an old note.
- `element_type`: 1 = GK, 2 = DEF, 3 = MID, 4 = FWD.
- `position` 1–11 is the XI; 12–15 is the bench **in auto-sub order**, and 12 is always the
  substitute goalkeeper.
- `multiplier`: 0 = benched, 1 = starting, 2 = captain, 3 = triple captain.
- `entry_history.bank` and `.value` are in **tenths of a million** — `bank: 0` is £0.0m,
  `value: 1000` is £100.0m.
- The formation is read off the XI by counting `element_type` in positions 1–11, never
  assumed.

The GW1 brief above decodes to a **1-5-3-2**: five defenders started, four points sat on
the bench, £0.0m in the bank, and the vice-captaincy was on a goalkeeper — which is the
exact class of free mistake question 4 exists to catch, since a keeper covering a benched
captain caps your downside protection at a handful of points.

---

## 3. The data layer: the public API, and one snapshot

There is no MCP server, no data source to connect, and no account. Every endpoint the desk
uses is public, unauthenticated JSON — the same ones the FPL website's own front end runs
on. Nothing to install, nothing to keep running in a terminal, nothing to expire mid-season.

| Endpoint (under `https://fantasy.premierleague.com/api`) | What it carries |
|---|---|
| `/bootstrap-static/` | The spine, ~3 MB. Every player with Opta `expected_goals`, `expected_assists`, `expected_goal_involvements`, `expected_goals_conceded` (each with a `_per_90`), `clearances_blocks_interceptions`, `recoveries`, `tackles`, `defensive_contribution`, `starts`, `bps`, `selected_by_percent`, `status`, `news`, `ep_next`. Plus `teams`, `events` (deadlines in UTC), and `game_settings` — the real rulebook. |
| `/fixtures/` | Every fixture with `kickoff_time`, both FDR ratings, and a post-match `stats` block. `event: null` means unscheduled. |
| `/element-summary/<id>/` | **Per-fixture** history for one player — the same xG/DefCon fields, one row per match. The distribution, not just the mean. |
| `/event/<GW>/live/` | Per-player points and bps for a gameweek in flight or just finished. |
| `/event-status/` | Whether `bonus_added` is true and the gameweek has finalised. |
| `/entry/<TEAM_ID>/history/` | Per-gameweek points, rank, bank, value, bench points — and the `chips` array of **every chip already played**. |
| `/entry/<TEAM_ID>/transfers/` | Every transfer with prices, which makes sell value computable rather than guessable. |
| `/team/set-piece-notes/` | FPL's own penalty, free-kick and corner notes per club. |

**This is a better data layer than the MCP wrapper, not just a simpler one.** FPL's own API
carries Opta expected-goals and defensive-contribution data natively, and
`/element-summary/` carries it *per fixture* — which is what "clears 10 in 7 of his last 10
starts" is computed from. The Performance analyst is told explicitly that understat and
fbref are a cross-check on a figure that looks wrong, not the starting point.

### One snapshot, eight readers

The workspace is **per-project and shared by every role**, so the architecture follows:

- The **Data** team pulls the API once per cycle into `/workspace/data/` as tidy CSVs, plus
  a `MANIFEST.md` recording every URL, fetch time in UTC, byte size, row count and SHA-256.
  It also computes the fixtures-per-team-per-event table, so **blanks and doubles fall out
  arithmetically** instead of being read off a rumour.
- **Everyone else reads those files.** `bootstrap-static` alone is ~3 MB of JSON; pulling it
  into seven agents' contexts every week is money spent to get arithmetic wrong by eye. The
  prompts say: read the file, aggregate with code, never paste a raw API response into a
  deliverable, and name the snapshot time on every figure.
- Every role gets `data_processing: true` (the house convention — the Bash tool under a
  narrower charter, so a role aggregates bulk data with code rather than by reading rows).
  Only **Data** and **Optimizer** get full `bash`.

The pull is a committed `pull.py` that runs with no arguments against what the sandbox
already ships — python3, pandas, duckdb, pulp. After week one, the Data role's job is to run
it and report what moved, not to rewrite it.

---

## 4. The org

Seven one-person teams and a strategist. A sole member is its own lead, so there is no
coordination overhead and no meeting simulation.

```
                     the public FPL API
                             │
                         ▼ (once)
                    Data ──► /workspace/data/*.csv + MANIFEST.md
                             │ (everyone reads)
   ┌────────────┬────────────┼──────────────┬────────────┬──────────────────┐
   ▼            ▼            ▼              ▼            ▼                  ▼
Performance  Fixtures  Value & Ownership  Optimizer  Team News   Chips & Captaincy
   └────────────┴────────────┴──────────────┴────────────┴──────────────────┘
                                     ▼
                          Chief Strategist (CEO)
                                     ▼
                              you, weekly
```

| Team | Role slug | Model / effort | What it owns |
|---|---|---|---|
| *(CEO)* | `fpl-gw-chief-strategist` | `claude-opus-5` / high | The memo, the review, the risk posture, the chip calendar, **and what not to commission** |
| Data | `fpl-gw-data` | `claude-sonnet-5` / high | The only fetch. CSVs, the manifest, the blank/double table |
| Performance | `fpl-gw-performance` | `claude-sonnet-5` / high | xGI vs actual returns, DefCon rates and hit frequencies, BPS under the 2026/27 weights, **minutes** |
| Fixtures | `fpl-gw-fixtures` | `claude-sonnet-5` / high | Six-GW runs on separate attacking and defensive axes, swings, blanks and doubles |
| Value & Ownership | `fpl-gw-value-ownership` | `claude-sonnet-5` / high | Sell values, price moves, bench cost, effective ownership, the template |
| Optimizer | `fpl-gw-optimizer` | `claude-sonnet-5` / xhigh | The `pulp` integer program: roll vs transfer vs hit, chip deltas, XI and bench order |
| Team News | `fpl-gw-team-news` | `claude-sonnet-5` / high | Who actually starts. Runs last, timestamps everything |
| Chips & Captaincy | `fpl-gw-chip-strategist` | `claude-sonnet-5` / high | The eight-chip calendar and its expiry clock, the captaincy shortlist, **the counter-case** |

### The role built for the thing you asked about

**Chips & Captaincy** exists because chip timing is where seasons are lost, and because
folding it into the strategist is exactly how four chips reach January unplayed. Filed under
`challenge` on purpose. Its rules:

- **Every hold is two numbers, never a feeling.** What does this chip earn *this* gameweek,
  and what is the expected value of holding it for the best week still plausibly ahead? Play
  it when the first exceeds the second, or when the expiry clock has made the second a
  fiction. "Hold" with no number attached is banned.
- **Bench Boost has a lead time.** It is worth exactly what your four bench players score,
  so it is worth nothing without a bench that starts. The role must name a target gameweek
  four to six weeks out and tell Value to build toward it. A Bench Boost decided on the
  Thursday is a Bench Boost worth eight points.
- **Triple Captain wants the highest ceiling, not the highest mean** — and ceiling and floor
  come from the per-fixture history on disk, not from a feeling.
- **Wildcard is for a fixture swing or four-plus needed moves**, priced as the unconstrained
  rebuild's horizon score minus the current squad's, minus the hits it saves.
- **Chips remaining are reconciled from two places every week**: your brief, and the `chips`
  array in `/entry/<TEAM_ID>/history/` — which is the endpoint that actually carries the
  record.
- **From GW14 the memo carries the expiry countdown in every issue.** The first set of four
  is **lost, not carried**, at the GW19 deadline — 13:30 GMT, Saturday 2 January 2027.
- **It writes the counter-case.** Every week, the strongest argument *against* whatever the
  desk is drifting toward, in three sentences with a number in it.

---

## 5. The goal

Verbatim from the file. It is the single most important field — the strategist plans
everything from it.

> Run the chairman's Fantasy Premier League team through the rest of the 2026/27 season and
> maximise the **total points** it scores across all 38 gameweeks. Overall rank is the
> public scoreboard that measures it: after GW1 the team sits on 51 points and roughly
> 4,080,000 of 9,706,614, so the standing target is to finish inside the **top 500,000** by
> GW38, with the **top 100,000** as the stretch. Thirty-seven gameweeks remain and the
> compounding thing in this game is process, not a hot week.

The target is deliberately a rank band and not a points number: points are not comparable
across seasons, and rank is the one figure on the public API that cannot be argued with.
From the 42nd percentile after one gameweek, top 500k is a real recovery and top 100k is a
genuine stretch — a target you would beat by accident teaches the desk nothing.

Two clauses are load-bearing and worth not editing:

- *"A recommendation without expected points, effective ownership and a stated downside is
  not a recommendation."* FPL analysis fails in exactly one way — a confident pick with no
  traceable reasoning — and this makes that failure visible in the deliverable itself.
- *"Real figures only, never illustrative or estimated ones, and every figure states the
  gameweek range it covers."* In August the samples are tiny, and the honest thing is to say
  so rather than to launder a 3-match average into a season projection.

---

## 6. The operating rhythm

The gameweek **is** the cycle. `cycle_frequency: weekly`, `cycle_day: 1` — the platform
rolls it every **Tuesday**, when the last gameweek has finalised and the next deadline is
three to four days out. Each roll closes the cycle, carries unfinished scope forward,
reopens the plan gate, and hands the strategist the last three cycles' narratives verbatim.

| When | Who | Output |
|---|---|---|
| Cycle roll, Tuesday | Data | The snapshot. Nothing else starts until `MANIFEST.md` is written — six roles reasoning from six different snapshots is six answers you cannot reconcile |
| Deadline − 5 to 4 days | Fixtures, Performance | *(full cycle only)* Six-GW ticker + swings; underlying numbers on the fifteen and the watchlist |
| Deadline − 3 days | Value; Chips & Captaincy | *(full cycle only)* Sell values, EO, template; chip calendar + captaincy shortlist |
| Deadline − 2 days | Optimizer | *(full cycle only)* Roll vs transfer vs hit, scored alternatives, chip deltas, XI + bench order |
| Deadline − 36 to 24h | Team News → Strategist | Availability, then **the memo** |
| GW end + 48h | Strategist | The scored review, once `/event-status/` says `bonus_added` |

> **A note on `max_task_gate_hours`.** That rhythm schedules work with `not_before` up to
> five days out, and the platform's default refuses any gate more than **6 hours** ahead —
> which would have silently rejected almost every task this design creates. The file sets it
> to **120**. If you rebuild this project by hand, that is the one setting you must not
> leave at its default.

---

## 7. Cost: what it actually runs, and the brakes

You are experimenting, so the file is set up to be **mostly idle**, and the strategist is
told in as many words that not commissioning work is part of its job.

### The light cycle is the default

Most weeks cost **four agent runs**: Data refreshes the snapshot, Team News lands
availability, the strategist writes the memo, and after the gameweek it writes the review.
That is enough to answer all six questions in a week where nothing changed, because last
cycle's fixture ticker, underlying numbers, EO table and chip calendar are still on disk and
still true. A six-gameweek fixture run does not move in seven days.

The strategist escalates to a **full cycle** — all seven teams, ~9 runs — only when one of
these is true, and it must name which one in the memo:

- a fixture swing lands inside the next six gameweeks, or a blank or double is newly confirmed;
- a chip is in play this week or next, or the GW19 expiry is inside six gameweeks;
- one of your fifteen is out, newly benched, or has lost a set-piece role;
- two or more free transfers are banked, so the move is a multi-transfer problem;
- a wildcard is on the table;
- or four gameweeks have passed since the last full cycle.

**A full cycle every third or fourth gameweek is the intended rate**, so a typical month is
roughly 4 + 4 + 9 + 4 ≈ 21 runs, most of them Sonnet.

### The brakes, and what each one stops

| Setting | Value | What it prevents |
|---|---|---|
| `monthly_usd_cap` | **40** | The hard stop. The project **pauses itself** at 100% and warns you at 70%. This is the number to change if you want more or less headroom — it is monthly, not weekly. |
| `max_usd_per_run` | 1.25 | A single run that spends this much has stopped working and started looping. It is warned as it approaches and stopped at the line. |
| `runs_per_project_per_day` | 12 | The daily ceiling across every agent, against ~4–9 runs in a normal *week*. |
| `runs_per_project_per_hour` | 4 | The pacing brake, so a day's allowance cannot be spent in one burst. |
| `wakes_per_agent_per_hour` | 2 | The runaway-loop brake on any single agent. |
| `max_concurrent_runs` | 2 | Two agents working at once, never seven. |
| `ceo_heartbeat_hours` | 24 | The strategist plans once a day at most when nothing else wakes it — and `ceo_heartbeat_always` is off, so **an empty board costs nothing**. |

Worst case, every brake bites at once and the month still stops at $40. Realistically it
will be well under. **Measure it after the first two gameweeks** on the project's spend
card — light weeks and full weeks look very different, and one full cycle early on is the
only honest way to price the rest.

### Gates deliberately turned off, so you are not chasing them

You said you did not want agents grinding against limits that are hard to clear. These are
loosened on purpose for an experiment:

| Setting | Value | Why |
|---|---|---|
| `external_ratio_min` | **0.0** | The default forces a third of open tasks to produce outside-world evidence. This desk's evidence is a weekly rank check; enforcing a ratio on a 3-task board just generates refusals. |
| `drought_hours` | **720** | A month. An international break is a fortnight with no new match evidence, and the default would spend it nagging a company doing exactly the right thing. |
| `audit_sample_rate` | **0.0** | Sampling never blocks work, but it does put items on your desk. Off while you are evaluating; turn it up once you care about spot-checks. |
| `plan_authority`, `scope_authority`, `commitment_authority` | `ceo` | An Operate project reopens the plan gate every cycle. With a hard deadline every week, a plan waiting on your click is a memo that misses it. Every acceptance still lands in your Decisions record, and all of it is reversible. |
| `verdict_policy` | `ceo_judgment` | "The memo was delivered and named a captain" is an observable close. Parking one on your desk weekly is friction with no return. |
| `stall_hours` | 24 | The desk is *supposed* to be idle midweek. The default would report that as a stall. |

If you later want the discipline back, all of these are Settings → Rules in force, and an
amendment is a written reason and one click.

### Other constitution values

| Setting | Value | Why |
|---|---|---|
| `mode` | `operate` | A Deliver that starts again on a rhythm. The gameweek is the rhythm. |
| `cycle_frequency` / `cycle_day` | `weekly` / `1` | Tuesday roll: the last GW has settled, the next deadline is days out. |
| `chairman_report_interval_hours` | 12 | Room for a Thursday memo *and* a Saturday-morning amendment when team news breaks late. |
| `chairman_report_window_local` | `05:00-22:00` | The earliest Saturday deadline — 90 minutes before a 12:30 UK kickoff — is 11:00 UK, which is **06:00 in Toronto** in both BST and GMT. A window opening at 06:00 opens on the deadline itself, so it starts at 05:00 to leave room for a pre-deadline amendment. The wizard's `09:00` default would miss that deadline entirely. |
| `timezone` | `America/Toronto` | One zone serves the reporting window, the daily wake and the cycle roll. **Change it if it isn't yours** — IANA names only. |
| `memory_cap_project` / `_team` | 120 / 80 | The chip calendar, the set-piece hierarchy and per-player judgments accumulate for 37 more weeks. |
| `controlled_domains` | *(empty)* | You control no football data source, so every source the agents cite is legitimately external. |

---

## 8. Importing it

### The dashboard

1. **+ New project → load a setup file →** `docs/projects/fpl-gameweek-desk.json`. All eight
   roles are POSTed to `/admin/roles` at submit time; the form lands you on the review step.
2. **Set these by hand — the wizard's own fields win over the file:**
   - **Who runs it? → Autonomous.** The authority preset overrides whatever the file says,
     so picking *Supervised* silently reverts the plan/scope/commitment/verdict settings in
     §7. (Autonomous also hands `capability_authority` to the CEO; if you would rather keep
     credential-scoping yourself, set it back in Settings → Rules in force afterwards.)
   - **Hours between reports → 12**, **window → `05:00-22:00`**, **timezone → yours.**
   - **Do not click a "How does it run?" preset after loading the file.** Those presets
     overwrite exactly the four knobs that matter here — queue ceiling, drought, monthly cap
     and approval threshold — and *Balanced* would put your cap back to $1000.
3. **Paste your Anthropic API key.** Without one the project is created but cannot start.
4. **There is no step 4.** No data source to connect, no gateway to keep running. The API is
   public.
5. **Overview →** clear anything the Setup & health card names, then start it.

### The CLI

`yaaf create-project` takes the bare project body and does not seed roles, so split the
bundle first — and note it honors the **whole** constitution as written, including the
brakes and `max_task_gate_hours`, which the wizard partly overrides:

```bash
export YAAF_API=http://localhost:8080
export YAAF_ADMIN_TOKEN=$(grep YAAF_ADMIN_TOKEN deploy/.env | cut -d= -f2-)

python3 - <<'PY'
import json, pathlib
b = json.loads(pathlib.Path("docs/projects/fpl-gameweek-desk.json").read_text())
pathlib.Path("/tmp/fpl-gw").mkdir(exist_ok=True)
for r in b["roles"]:
    pathlib.Path(f"/tmp/fpl-gw/{r['slug']}.json").write_text(json.dumps(r, indent=2))
pathlib.Path("/tmp/fpl-gw-project.json").write_text(json.dumps(b["project"], indent=2))
PY

yaaf seed-roles /tmp/fpl-gw/
yaaf create-user dplouffe@gmail.com "Your Name"
yaaf create-project /tmp/fpl-gw-project.json
yaaf start fpl-gameweek-desk
```

---

## 9. The first message to send it

```text
The season is one gameweek old: 51 points, overall rank 4,080,064 of 9,706,614. I have 1
free transfer, £0.0m in the bank, and all eight chips unplayed. I execute every move myself
— give me the memo by Thursday night. I care about total points over the full season, and I
want expert help on chip timing above everything else.

I'm experimenting and the budget is small, so run light cycles by default and tell me in the
memo when you escalate and why. Ask me here if you're blocked.
```

Then, before you accept the first memo, ask it for one thing the file cannot give it: the
squad decoded. *"Resolve the fifteen element ids in my brief against bootstrap-static and
confirm the names, prices and sell values back to me before you recommend anything."* If it
gets that wrong, everything downstream is wrong, and it is a four-minute check.

---

## 10. Your job, which is small on purpose

- **Paste the brief every week.** It is the one input the desk cannot get for itself.
- **Execute the moves.** The agents never touch your team. A memo you don't act on does
  nothing.
- **Check the spend card after two gameweeks.** One light week and one full week is enough
  to price the season, and the cap is one field.
- **Spot-check once a month.** Recompute one expected-points figure by hand against
  `/workspace/data/`. Four minutes, and it is the difference between advice you can trust
  and advice that sounds good.
- **Answer the real questions.** Around November the strategist will ask something like
  *"your rank says gamble, your chip calendar says hold — which do you actually want?"* That
  question is why you are in the loop.

Refusals in the run log are the system working — the queue ceiling, the per-run spend line,
the reporting cadence. The strategist is told why, and re-plans.

---

## Sources

Rules, scoring and the 2026/27 changes are set out in full in
[`fpl-manager.md` §1](fpl-manager.md), which this project's prompts restate — and then tell
every role to verify against `game_settings` in `bootstrap-static`, which is the rulebook as
the game actually enforces it.
