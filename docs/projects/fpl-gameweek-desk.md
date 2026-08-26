# FPL Gameweek Desk — the weekly in-season company

**Upload file:** [`fpl-gameweek-desk.json`](fpl-gameweek-desk.json) — a `yaaf_bundle`
setup file carrying the project *and* its seven roles. Drop it on **+ New project →
load a setup file** and the roles are created for you in the same action.

This is the **in-season** companion to [`fpl-manager.md`](fpl-manager.md). That project
picks an opening squad and stops. This one starts the morning after Gameweek 1 and runs
for the remaining thirty-seven, answering the same six questions every week and getting
scored on its own answers.

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
| 4 | **Vice-captain** | One name, chosen kickoff-time aware — a vice whose match has already finished cannot cover a captain benched at 15:00. |
| 5 | **XI and bench** | The eleven with the formation named, then the four bench players in auto-sub order (sub GK in slot 12), and **explicitly which players move between XI and bench versus last week**. |
| 6 | **What would make this wrong** | The single most likely way the memo loses points, and what to do if it happens before the deadline. |

When nothing needs to change, the memo opens with **NO CHANGES THIS WEEK** and still
answers 3, 4 and 6. A quiet week is a real finding; churn is the most expensive habit in
this game and the desk is told so in four separate prompts.

Then, **within 48 hours of the gameweek ending** — 48 because bonus and defensive
contributions settle at 09:00 UK the day after the last match — the **scored review**:
points scored, points left on the bench, rank movement, what the captain returned against
the alternatives, which calls were right, which were wrong, and the one thing that changes
as a result. It scores the *decision*, not the outcome: a captain pick that was right on
expected points and blanked was still right, and pretending otherwise is how a desk starts
chasing last week's score.

---

## 2. Your weekly job: paste the brief

The desk cannot see your team. Once a week — Tuesday or Wednesday is ideal, the cycle rolls
Tuesday — paste this into the **Boardroom**:

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
site. The endpoint is public — no login needed.

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
nonsense. Every one of the seven roles carries the same decoding block:

- `picks[].element` is an **FPL player id** and means nothing until it is resolved against
  `elements[].id` in <https://fantasy.premierleague.com/api/bootstrap-static/>. Ids are
  reassigned between seasons, so the prompts forbid guessing a name from an id or carrying
  one forward from an old note.
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

## 3. The org

Six one-person teams and a strategist. A sole member is its own lead, so there is no
coordination overhead and no meeting simulation.

```
                    fpl data source (MCP) + the public FPL API
                                     │
   ┌────────────┬────────────┬───────┴──────┬────────────┬──────────────────┐
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
| *(CEO)* | `fpl-gw-chief-strategist` | `claude-opus-5` / high | The memo, the review, the risk posture, the chip calendar |
| Performance | `fpl-gw-performance` | `claude-sonnet-5` / high | xGI vs actual returns, DefCon rates, BPS under the 2026/27 weights, **minutes** |
| Fixtures | `fpl-gw-fixtures` | `claude-sonnet-5` / high | Six-GW runs on separate attacking and defensive axes, swings, blanks and doubles |
| Value & Ownership | `fpl-gw-value-ownership` | `claude-sonnet-5` / high | Sell values, price moves, bench cost, effective ownership, the template |
| Optimizer | `fpl-gw-optimizer` | `claude-sonnet-5` / xhigh | The integer program: roll vs transfer vs hit, chip scenario deltas, XI and bench order |
| Team News | `fpl-gw-team-news` | `claude-sonnet-5` / high | Who actually starts. Runs last, closest to the deadline, timestamps everything |
| Chips & Captaincy | `fpl-gw-chip-strategist` | `claude-sonnet-5` / high | The eight-chip calendar and its expiry clock, the captaincy shortlist, **the counter-case** |

**Only the Optimizer gets `bash`.** It is the one role that runs real code — a `pulp`
integer program over the actual constraints, because optimisation by intuition is how a
squad ends up £0.3m short of the player it needed and how a bench gets ordered by vibe.

### The two roles this project adds over `fpl-manager.md`

**Chips & Captaincy** exists because you said chip timing is where you want expert help,
and because folding it into the strategist is exactly how four chips reach January
unplayed. It is filed under `challenge` on purpose. Its rules:

- **Every hold is two numbers, never a feeling.** What does this chip earn *this* gameweek,
  and what is the expected value of holding it for the best week still plausibly ahead?
  Play it when the first exceeds the second, or when the expiry clock has made the second a
  fiction. "Hold" with no number attached is banned.
- **Bench Boost has a lead time.** It is worth exactly what your four bench players score,
  so it is worth nothing without a bench that starts. The role must name a target gameweek
  four to six weeks out and tell Value to build toward it. A Bench Boost decided on the
  Thursday is a Bench Boost worth eight points.
- **Triple Captain wants the highest ceiling, not the highest mean.**
- **Wildcard is for a fixture swing or four-plus needed moves**, priced as the
  unconstrained rebuild's horizon score minus the current squad's, minus the hits it saves.
  A wildcard played to fix two problems is a wildcard wasted.
- **From GW14 the memo carries the expiry countdown in every issue.** The first set of four
  chips is **lost, not carried**, at the GW19 deadline — 13:30 GMT, Saturday 2 January 2027.
- **It writes the counter-case.** Every week, the strongest argument *against* whatever the
  desk is drifting toward, in three sentences with a number in it.

**The Optimizer's brief is in-season transfer planning**, not squad construction, and it is
required to return the **roll option scored as a genuine alternative** — "roll, then make
the best two-transfer move next week, which currently looks like this" — alongside the best
single transfer, the best −4 with the arithmetic explicit, and three to five near-neighbours
priced in expected points rather than adjectives.

---

## 4. The goal

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

## 5. The operating rhythm

The gameweek **is** the cycle. `cycle_frequency: weekly`, `cycle_day: 1` — the platform
rolls it every **Tuesday**, when the last gameweek has finalised and the next deadline is
three to four days out. Each roll closes the cycle, carries unfinished scope forward,
reopens the plan gate, and hands the strategist the last three cycles' narratives verbatim,
which is exactly the scored-review loop this project wants. A daily 08:00 wake steps the
rhythm inside a cycle; the strategist sets `not_before` on the tasks it creates.

| When | Who | Output |
|---|---|---|
| Deadline − 5 to 4 days | Fixtures, Performance | Six-GW ticker + swings; underlying numbers on the fifteen and the watchlist |
| Deadline − 3 days | Value; Chips & Captaincy | Sell values, ownership, EO, template; chip calendar + captaincy shortlist |
| Deadline − 2 days | Optimizer | Roll vs transfer vs hit, scored alternatives, chip deltas, XI + bench order |
| Deadline − 36 to 24h | Team News → Strategist | Availability, then **the memo** |
| GW end + 48h | Strategist | The scored review |

Between deadlines the analysts are cheap to leave idle and expensive to leave busy. The
strategist is told, in as many words, that a gameweek with no fixture swing, no injury and a
rolled transfer does not need six reports.

---

## 6. The constitution, and why

| Setting | Value | Why |
|---|---|---|
| `mode` | `operate` | A Deliver that starts again on a rhythm. The gameweek is the rhythm. |
| `cycle_frequency` / `cycle_day` | `weekly` / `1` | Tuesday roll: the last GW has settled, the next deadline is days out. |
| `monthly_usd_cap` | 90 | Seven agents on a weekly cadence. It is a hobby — price it deliberately. Six worker teams cost more than the five in `fpl-manager.md`; drop Chips & Captaincy back to £75 if you want the smaller desk. |
| `queue_ceiling` | 7 | Six teams plus the review. Fanning out wider on a weekly rhythm just spends money. |
| `drought_hours` | 336 | An international break is a fortnight with no new match evidence. At 24h the platform spends every break nagging a company doing exactly the right thing. |
| `external_ratio_min` | 0.34 | Most gameweek work is analysis; the external scoreboard only lands weekly. |
| `chairman_report_interval_hours` | 12 | Room for a Thursday memo *and* a Saturday-morning amendment when team news breaks late. |
| `chairman_report_window_local` | `06:00-22:00` | A Saturday 11:30 UK deadline is 06:30 in Toronto. The window has to open before it. |
| `timezone` | `America/Toronto` | One zone serves the reporting window, the daily wake and the cycle roll. **Change it if it isn't yours** — IANA names only. |
| `memory_cap_project` / `_team` | 120 / 80 | The chip calendar, the set-piece hierarchy and per-player judgments accumulate for 37 more weeks. |
| `approval_threshold_usd` | 10 | Nothing here should need to buy anything. |
| `plan_authority`, `scope_authority`, `commitment_authority` | `ceo` | An Operate project reopens the plan gate every cycle. With a hard deadline every week, a plan waiting on your click is a memo that misses it. Every acceptance still lands in your Decisions record and every one is reversible. |
| `verdict_policy` | `ceo_judgment` | "The memo was delivered and named a captain" is an observable close. Parking one on your desk weekly is friction with no return. |
| `controlled_domains` | *(empty)* | You control no football data source, so every source the agents cite is legitimately external evidence. |

---

## 7. Importing it

### The dashboard (what you asked for)

1. **+ New project → load a setup file →** `docs/projects/fpl-gameweek-desk.json`. All seven
   roles are POSTed to `/admin/roles` at submit time; the form lands you on the review step.
2. **Set these four by hand — the wizard's own fields win over the file:**
   - **Who runs it? → Autonomous.** The authority preset overrides whatever the file says,
     so picking *Supervised* silently reverts the plan/scope/commitment/verdict settings in
     §6. (Autonomous also hands `capability_authority` to the CEO; if you would rather keep
     credential-scoping yourself, set it back in Settings → Rules in force afterwards.)
   - **Hours between reports → 12**, **window → `06:00-22:00`**, **timezone → yours.** The
     file's cadence is not read by the wizard.
   - **Do not click a "How does it run?" preset after loading the file** — the four knobs it
     prefilled (queue ceiling, drought, cap, approval threshold) are exactly what those
     presets overwrite.
3. **Paste your Anthropic API key.** Without one the project is created but cannot start.
4. **Settings → Data sources → `fpl`.** Same MCP server as `fpl-manager.md`
   ([`rishijatia/fantasy-pl-mcp`](https://github.com/rishijatia/fantasy-pl-mcp/)), granted to
   all seven roles:
   ```bash
   npx -y supergateway --stdio "python -m fpl_mcp" \
     --outputTransport streamableHttp --streamableHttpPath /mcp \
     --healthEndpoint /healthz --port 8931
   ```
   `--outputTransport streamableHttp` is required; supergateway defaults to sse.
   **This is optional.** Every figure the desk needs is on the public, unauthenticated FPL
   API, and the prompts name those endpoints directly — the MCP source is a convenience, and
   the one tool that needs your login (`get_my_team`) is the one the weekly brief replaces.
5. **Overview →** clear anything the Setup & health card names, then start it.

### The CLI

`yaaf create-project` takes the bare project body, and does not seed roles — so split the
bundle first:

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
yaaf create-project /tmp/fpl-gw-project.json     # honors the whole constitution as written
yaaf start fpl-gameweek-desk
```

---

## 8. The first message to send it

```text
The season is one gameweek old: 51 points, overall rank 4,080,064 of 9,706,614. I have 1
free transfer, £0.0m in the bank, and all eight chips unplayed. I execute every move myself
— give me the memo by Thursday night. I care about total points over the full season, and I
want expert help on chip timing above everything else. Ask me here if you're blocked.
```

Then, before you accept the first memo, ask it for one thing the file cannot give it: the
squad decoded. *"Resolve the fifteen element ids in my brief against bootstrap-static and
confirm the names, prices and sell values back to me before you recommend anything."* If it
gets that wrong, everything downstream is wrong, and it is a four-minute check.

---

## 9. Your job, which is small on purpose

- **Paste the brief every week.** It is the one input the desk cannot get for itself.
- **Execute the moves.** The agents never touch your team. A memo you don't act on does
  nothing.
- **Spot-check once a month.** Recompute one expected-points figure by hand against the
  source. Four minutes, and it is the difference between advice you can trust and advice
  that sounds good.
- **Answer the real questions.** Around November the strategist will ask something like
  *"your rank says gamble, your chip calendar says hold — which do you actually want?"* That
  question is why you are in the loop.
- **Watch the money.** The budget meter warns at 80% and the project pauses itself at 100%.

Refusals in the run log are the system working — the queue ceiling, the drought warning, the
reporting cadence. The strategist is told why, and re-plans.

---

## Sources

Rules, scoring and the 2026/27 changes are set out in full in
[`fpl-manager.md` §1](fpl-manager.md), which this project's prompts restate verbatim;
check them against the [official rules](https://fantasy.premierleague.com/en/help/rules)
once before you start, and again if FPL announces a mid-season change.
