# Appendix — evidence for the product experience audit

Supporting material for [README.md](README.md). Everything here was measured on `cbb5bec`
(23 Oct 2026) in a local dev install (SQLite, `YAAF_SANDBOX_MODE=subprocess`, mock runtime) unless
marked **[Code]**. Screenshots referenced below are in [`screens/`](screens/).

---

## 1. Setup log (dev mode, per `docs/DEPLOYMENT.md`)

| # | Step | Result |
|---|---|---|
| 1 | `python3 -m venv .venv && pip install -e ".[dev]"` | 57 s |
| 2 | `cd web && npm install && npm run build` | 29 s; "5 vulnerabilities (3 moderate, 2 high)"; Vite warns of an 886 kB chunk; **rewrites tracked `web/tsconfig.tsbuildinfo`** |
| 3 | `export` DB URL / sandbox / API / master key | — |
| 4 | `create_all` one-liner | ok |
| 5 | `yaaf-server &` | Warns on every start: "YAAF_PUBLIC_BASE_URL is unset, so OAuth discovery advertises http://localhost:8080…" (not in dev docs) |
| 6 | `yaaf-runner &` | Off port 8080 it calls `YAAF_API_BASE_URL` (default `:8080`, undocumented) → repeated `401`s against whatever is on 8080 |
| 7 | `yaaf users bootstrap` | Link hard-coded to `:8080` |
| 8 | Set password in browser | ok (12-char minimum stated up front, good) |
| 9 | `yaaf login` | With piped stdin: "GetPassWarning: Can not control echo… Password input may be echoed." |
| 10 | `yaaf seed-roles roles/` | Run before login: 38 × `401 {"detail":"sign in first"}` and **exit 0** |
| 11 | `yaaf seed-crews crews/` | **Not in the dev steps.** Without it the hiring modal reads "Nobody hired yet — pick a crew above, or tick roles." with no crews above |
| — | First non-mock run | `backend crashed: DockerException("Error while fetching server API version…")`. `run_agent_container` always calls `docker.from_env()` (`yaaf/runner/sandbox.py:165`), so "subprocess" mode still needs Docker for real agent runs |

## 2. Measured web inventory (desktop 1440×900)

### 2.1 Creation

| Screen | Words | Fields | Buttons |
|---|---|---|---|
| Login | 50 | 2 | 1 |
| Set password | 56 | 2 | 1 |
| First Home (no projects) | 52 | 0 | 1 |
| Start a project (fork) | 315 | 0 | 2 + 1 link |
| Form step 1 — job | 459 | 5 | 10 |
| Form step 2 — org | 200 | 3 | 13 |
| ↳ role picker modal | 1,388 | — | 40 controls, 36 roles |
| Form step 3 — money & rules | 1,056 | 12 | 21 |
| Form step 4 — review | 439 | 3 | 11 |
| All on one page | 1,925 | 23 | — |
| After create (webhooks page) | 147 | — | — |

Minimal successful path: **12 clicks, 2 typed fields**, after hitting:
1. "Short name — picked for you" (step 1) vs a required, empty Name on step 4 (native
   "Please fill out this field.").
2. Create refused with the preselected "Balanced" card: *"deliverable_review is
   'independent_read', but no team on this roster hires a reviewing seat… set
   deliverable_review to 'none'…"* All four quality presets set it
   (`yaaf/core/constitution.py:760–800`) **[Code]**.
3. A role hired with "Employees" blank → project with 0 teams ("1 person across 0 teams").

Screens: `screens/10_start_project.png`, `screens/13_form_step3.png`, `screens/20_create_result.png`.

### 2.2 Project pages (populated "Fantasy desk", Explore; Deliver figures where different)

| Page | Words | Buttons/links | Sections | Primary action |
|---|---|---|---|---|
| Overview | 587 | 13/16 | 9 | Approve $199 / Answer |
| Chat | 400 | 8/2 | rail of 3 | Send |
| Roadmap | 68 (Explore: "No plan, on purpose") · 119 (Deliver) | 0 · 9 | 1 | — · Accept… / Drop it |
| Tasks | 81 | 2/1 | 5 columns | Ask CEO for a change |
| Deliverables | 84 | 0 | 0 | — |
| Team | 136 | 6/2 | org chart | Message CEO / Add a team |
| Spend | 187 | 4/2 | 2 | Change the budget… |
| Reviews | 80 | 0 | 0 | — |
| Activity | 92 (Decisions tab 356) | 3/8 | 7 tabs | — |
| Settings | 135 | 9 | 11 sub-sections | Save the brief |
| Rules in force | 482 | 17 | 7 tabs, **69 controls** | Amend |
| Workspace Settings | 145 | — | 6 sections | — |
| Role library | 1,361 | 46 | — | + New role (≈19 more controls) |

Help blocks per source file (`grep -c 'className="help"'`): project `Settings.tsx` 49,
`NewProject.tsx` 48, `Overview.tsx` 24, `Decisions.tsx` 24, `Plan.tsx` 21, `Inbox.tsx` 15,
`Deliverables.tsx` 14, `Review.tsx` 12.

Mobile (390×844): horizontal overflow on Inbox (409 px), Projects (396 px), Spend (453 px); the
other 7 project pages fit. `screens/91_m_inbox.png`.

### 2.3 Verbatim copy

**Home headline (good):** "3 things need you across 2 projects. The soonest decides itself in 2
days. 2 runs are in flight across 3 running projects." (`screens/30_home.png`)

**Spend approval card** (`screens/50_inbox_item1.png`, `51_approve_click.png`):
"Buy the $199 premium projections feed — $199 · Why it is yours: Above your approval threshold,
so it is never an agent's call. · The case: Our free projections lag… I recommend buying one month
for $199. · If you say no: We keep using the free feed… · Amount $199 · If you do nothing: It is
denied in 7 days. Silence is a decision here." Buttons: **[Approve $199 ▾]** (Deny in the menu),
"Discuss with the CEO". Approve applied on one click with no confirmation.

**Question card:** "Do we spend into the deficit or accept the season? … If you say nothing: If
you say nothing I will spend into the deficit…" (the doubled phrase is the agent's text; the
chat preview shows a raw "_Answer by…" underscore).

**Contradictions**

| Where | Says |
|---|---|
| Spend page lede (`web/src/pages/Project.tsx:320`) | "The budget is a hard limit no agent can talk its way past: it warns, then pauses the project." |
| Spend policy (`web/src/pages/project/Spend.tsx:61`) | "Pauses the project at **100%**" |
| Rules in force | "This is a PLAN, not a wall: crossing it does not stop the project… Hard stop… default 3" |
| Form review | "the project pauses itself at the cap" |
| Form step 3 header | "Constitution (frozen at creation)", next to "editable later in Settings" |
| Supervised requirement banners | "your call — Waiting on you: … Decide it on the Plan tab." / "…the chairman decides what is in scope, so this is waiting on him, not on you." (rows: "Waiting for you") |
| Supervised health suggestion | "…on an autonomous project, accepted it too" |
| Health line | "first stopping at 2026-10-25T12:04+00:00" (raw ISO) |
| Project cards vs Overview | "Running" vs "problem: no billing credential attached — agents cannot run" (`screens/42_p2_spend.png` shows "Healthy", "Running · 2 need you" and "No payment credential connected — real runs will fail" together) |
| Projects page | header "3 waiting on you" vs chip "Needs you 2"; "Active 4" and "Running 3" side by side (`screens/32_projects.png`) |
| Form copy | "lana-derived defaults" |

Server-written strings naming retired screens **[Code]**: `yaaf/server/dispatcher.py:1057`
("Decide it on the Plan tab."), `yaaf/server/janitor.py:2678` ("Open the Plan tab…"),
`yaaf/server/janitor.py:1920` ("Decide it on the Decisions screen.").

**Empty and error states**
- Inbox: "No project is running right now — which is not the same as all clear." (good)
- Roles before seeding: "No role matches that. Clear the search, or write a new one." with no search entered.
- Reviews: "No review is open. The platform opens one on the schedule you set…"
- Bad project URL: "unknown project". Unknown routes redirect to Home silently.

**Import of `examples/01-your-first-hour.json`:** lands on step 4. Web fire drill: "This project
would not start… Add a team on one of: none installed — seed the role library first" (38 roles
seeded), followed by "It never had to wait for you". Review: "Cap $20/mo, ≈ $26/mo at this
staffing". (`screens/83_form_fire_drill.png`)

**Start with no credential:** status becomes "Running" with no confirmation; first run crashes
(see §1); Home shows nothing wrong; the project shows "Ended early 1 — 1 failed outright".
(`screens/89e_home_after_crash.png`)

## 3. CLI

- **75 runnable commands**: 60 top-level, of which 4 are groups (`mcp-token` 3, `members` 5,
  `people` 6, `users` 5). `yaaf --help` is 88 lines, in source order, not grouped.
- **18 commands with no help text:** whoami, start, pause, stop, projects, dashboard, board, org,
  channels, tail, runs, ledger, evidence, hypotheses, approvals, decide, unfreeze, audit-evidence.
- **Docs vs code:** `ack-heads-up` and `asks` exist but are undocumented; `yaaf mcp-stdio` is
  documented (`docs/MCP_SERVER.md`) but does not exist; `yaaf projects` is documented as
  "headline numbers" and prints raw JSON including each full goal (~800 words for content-engine).
- **Error handling:** 62 commands print errors through `_show` (raw JSON, no hint); 6 use
  `_json_or_die` (adds a hint).

| Mistake | Output |
|---|---|
| Not logged in (`projects`, `briefing`, `whoami`, `mcp-token list`) | `{"detail": "sign in first"}`, exit 1, no hint |
| Not logged in (`agenda`, `receipt`, `say`) | `API error 401: sign in first` + `Hint: run \`yaaf login\`…` |
| Missing argument | `Missing argument 'project'.`, exit 2 (clean) |
| Server not running | **224-line traceback** ending `ConnectError: [Errno 111] Connection refused` |
| `YAAF_API=…/api` | `{"detail": "Not Found"}` |
| `rehearse` missing file | **52-line traceback**, `FileNotFoundError`, exit 1 (docs say 2) |
| `rehearse` bad JSON | `rehearsal: …/bad.json is not valid JSON: Expecting ',' delimiter: line 2 column 1 (char 25)`, exit 2 (good) |
| `rehearse` spec with no goal | `rehearsal: the spec has no goal; that is the one thing the CEO plans from and the drill cannot invent it`, exit 2 (good) |
| `create-project examples/…json` | 175-line validation dump, `"loc": ["body","slug"], "msg": "Field required"` (examples are bundles; the CLI wants a bare spec) |
| `mcp-token mint --scopes admin` | `{"detail": "unknown scope(s) admin; valid: read, steer, create, configure"}` (good) |

**Fire drill** (`yaaf rehearse examples/01-your-first-hour.json`): exit 0, 7.3 s, 64 lines / 832
words. It opens with stderr noise:

```
YAAF_PUBLIC_BASE_URL is unset, so OAuth discovery advertises http://localhost:8080 — …
constitution amended: project=first-hour changes={'wakes_per_agent_per_hour': 999, …}
refusal 409 on POST /agent/tasks (run=run_…, 4 left): R-02 is in stage 2 and stage 1 is still open…
Fire drill — Your First Hour (deliver)
…
  – Finished work was read by somebody else — not in an Explore project's shape
…
What it will cost
  this box has never priced a run, so it cannot tell you the total
  it fits your $20 cap if a run costs under $1.67 — check that against a bill you have seen
What it still needs before it can really run
  ! no billing credential attached — agents cannot run
  ! no seat on this project exists to disagree with the plan. … Hiring one `challenge` role is the whole fix.
```

The "Explore" label on a Deliver project is hard-coded at `yaaf/rehearsal/report.py:606`.
Examples 01 and 12 both report "3 requirement(s), 12 estimated run(s)", because the drill's crew
is scripted. The Operate example reports "Walls it hit: 2× /agent/commitments", which comes from
the scripted CEO, not the user's spec.

**Output sizes** on a young project: `dashboard` 350 lines, `activity` 169, `org` 101, `briefing`
(all projects) 50, `receipt` 14, `agenda` 10. Only `briefing`, `agenda` and `receipt` are prose.

## 4. MCP

- **34 tools**: read 14, steer 9, create 6, configure 5. Plus 4 prompts (`brief`,
  `why-at-risk`, `spot-audit`, `design`) and resources (desk, goal grammar, design doctrine,
  and per-project briefing, plan, goal and constitution).
- **`tools/list` is 30,131 characters** (≈7.5k tokens) and tool descriptions total **1,742
  words**. Longest: `create_project` 206, `catalog` 95, `receipt` 92, `lifecycle` 86, `inbox`
  80, `flag_ack` 78. Shortest: `spend` 21, `commitment_decide` 22.
- **Good:** unauthenticated → `401` with resource metadata; a personal token → "a personal
  token is not an MCP principal. Mint a scoped client token instead…"; a bad project → "no
  project 'nope' is reachable by this client. It can see: first-hour, content-engine"; the
  confirm token is bound to the arguments, single-use, 300 s, and a replay says "that
  confirmation was already spent; preview again".

| Call | Size |
|---|---|
| `desk` (2 projects) | 3,183 chars / 416 words (the truncated goal is ~650 chars per row) |
| `desk` full | 6,864 / 1,070 |
| `project` overview | 7,654 / 740 |
| `org` | 4,985 |
| `work` | 4,744 |
| `project` agenda | 1,883 |
| `plan` | 1,274 |
| `project` briefing | 1,201 (the only reply with a human `headline`) |
| `receipt` | 968 |
| `rehearse` | 8,037 / 1,130 (summary mode cuts the walk to 5 steps + "… 11 more, not shown") |

**Jargon frequency across the captured replies:** cap 42, wake 33, evidence 23, chairman 22,
beat 17, heartbeat 16, audit 17, cycle 15, requirement 11, commitment 9, queue_ceiling 8,
drought 7, verdict 7, boardroom 7, harness 6, ceo_judgment 6, kill_date 5, ration 4, frozen 4.
Also seen: `governance_reserve`, `coalescing`, `wake_ceiling`, `open_scope_no_board`,
`planless`, `noop_share`, `attributed_share`, `standing_orders`, `external_ratio_min`.

**Natural questions → calls** (N = number of projects)

| Question | Calls | Gap |
|---|---|---|
| How are my projects doing? | `desk` (+ `project view=briefing` × N for prose) = 1+N | No per-project verdict |
| What needs my attention? | `desk` + `project view=flags` × N + `view=heads_ups` × N = 1+2N | The desk omits flags, heads-ups and open reviews |
| What changed since yesterday? | `project view=briefing` × N | **No window argument** over MCP (the CLI has `--hours`, `--seen`) |
| What's going to miss its deadline? | `plan` × N | The desk has no commitment projection |
| Which project is wasting money? | `desk` + `receipt`/`plan` × N | Waste lives in `plan.velocity.runs_3w` and `receipt` |

**Founder flow over MCP (`design` prompt):** get prompt → read 2 resources (**~6,500 words**:
goal grammar 2,573 + design doctrine 3,968) → `grade_goal` → `catalog` × 1–2 → `rehearse` →
`catalog credentials` → `create_project` preview → `create_project` confirm. That is at least
**~9 calls** before any interview round. Without a Founder credential:
`{"graded": false, "why": "no platform credential is configured…", … "note": "Read that resource and grade the goal against all ten elements yourself…"}`.
The preview said `authority: autonomous (the default — this spec did not say)` and, with no
billing, still warned "bill the credential named above".

The doctrine's design tree has 14 decisions: 5 roots (finish line, beneficiary + standard,
shape, who pays, imposed date), 5 mid-level (stakes, anti-metrics, human role, roster, tempo)
and 4 leaves (authority dial, failure deliverable, resource posture, provider per seat). The
goal grammar's ten elements are finish_line, beneficiary, standard, anti_metrics,
method_constraints, stated_bias, quality_bar, human_role, resource_posture and
failure_deliverable.

## 5. Notifications **[Code]** (one brief captured live)

Transport: `yaaf/server/notify.py:chairman_notify`, by email (SMTP) and/or a JSON webhook
(Slack-compatible `text`). Subject `[yaaf/<slug>] <subject>`; plain-text body; **no links**.
There are 40 call sites: ALARM 8 (always emailed), ACTION 16 (emailed, capped by
`mail_max_per_day` = 5 per rolling 24 h), BRIEF 1, FYI 15 (never emailed; webhook only). Slack
otherwise mirrors boardroom and team channels; about 17 platform lines carry emoji prefixes
(📋 NEED, 🤝 ASSIST NEEDED, 📈 MORE ROOM, 👤 HIRED, 🚪 STOOD DOWN, 🔔 heads-up).

| Type | Trigger | Class | Subject |
|---|---|---|---|
| Daily brief | `brief_daily_at` (**default off**) | BRIEF | `briefing — {headline}` |
| Auto-pause: credential | 3 auth failures | ALARM | "project auto-paused: billing credential invalid" |
| Hard stop | budget wall | ALARM | "project auto-paused: hard stop reached" |
| Commitment breached | date passed | ALARM | "commitment BREACHED" |
| Two skipped cycles | Operate | ALARM | "project paused: two skipped cycles" |
| Stall | no runs | ALARM | "the project has gone quiet" |
| Explore window | deadline | ALARM | "explore window closed" |
| Success test | killed 2 tasks | ALARM | "a success test has now killed two tasks" |
| External spend | envelope crossed | ALARM | "Real-world spend crossed the envelope" |
| Commitment at risk | projection slips | ACTION | "commitment at risk" |
| Scope / date waiting | proposed (supervised) | ACTION | "{n} requirement(s) waiting on you to accept" / "a delivery date is waiting on your signature: …" |
| Review | open; nagged twice | ACTION | "review #N is open" / "…is waiting on you" / "…has been open N days" |
| Ask | CEO question | ACTION | "your CEO is waiting on you: {title}" |
| Need / assist / capacity / brief / data write | agent request | ACTION | "The project needs: …" / "Human needed: …" / "Your CEO asks for more room: …" / "Your CEO asks you to confirm the brief" / "Approve a change to your {source} account?" |
| Heads-up | standing order | ACTION | "Heads-up: {title}" |
| Ready to complete | all met | ACTION | "ready to complete" |
| **Generic approval (spend/legal/tool/owner)** | **filed** | **— none —** | `yaaf/server/api/agent.py:2273` sends nothing |
| Approval expiring | 48 h before expiry | ACTION | "approval expiring: {title}" … "Decide it on the Decisions screen." (`janitor.py:1914`) |
| FYI (15 kinds) | hires, stand-downs, CEO decisions, date resets, review narrative, deliverable published, cycle closed, commitment met, approval expired, audit sample | FYI | e.g. "CEO decision: …", "deliverable published: f v2" |

**Daily brief captured live (webhook):**

```
Subject: [yaaf/content-engine] briefing — You changed 1 rule (brief_daily_at).
You changed 1 rule (brief_daily_at).

Window: the window you asked for — 2026-10-23 11:57 to 2026-10-23 12:01 UTC (0.06h)

What moved
  · you changed 1 rule (brief_daily_at)

The CEO handled this itself
  · 6 runs in all

Money: $0.00 this window; $0.00 of $150 this month.

Waiting on you: nothing.

Next: CEO — planning wake — 2026-10-23 17:57 UTC

This briefing was composed from the record — no agent wrote it and no run was spent on it.
```

Only one of two projects' briefs arrived within ~3 minutes; not investigated.

## 6. Docs that disagree with the product

| Doc | Says | Product |
|---|---|---|
| `examples/01-your-first-hour.md` | "Leave everything else. Supervised"; "Minute 1 — your only real job… Nothing can be created until you do" | The spec names no authority rule → created Autonomous (`yaaf/core/constitution.py:720`, `yaaf/server/api/admin.py:1271`) |
| `examples/01…md:30`, `examples/11…md:37` | "Paste your Anthropic key" | The form picks a saved LLM credential |
| `examples/01…md:45,60`, `docs/CREATING_A_PROJECT.md:376,388,427` | "Plan tab", "Work tab", "Boardroom" | Roadmap, Tasks, Chat |
| `docs/README.md` | "all 60 rules" | 62 constitution fields; `docs/DASHBOARD.md` says 52 |
| `docs/DASHBOARD.md` | Inbox has four tabs | Five: Decide, Acknowledge, Updates, Watching, Done |
| `docs/GOVERNANCE.md` | Autonomy rule defaults `"chairman"` | New projects get `ceo` when silent (explained in `docs/AUTONOMY.md`, not in GOVERNANCE) |
| `docs/MCP_SERVER.md` | `yaaf mcp-stdio` | Not a command |
| `docs/CLI.md` | `yaaf projects` shows headline numbers | Raw JSON |
| `docs/DEPLOYMENT.md` dev mode | 4 numbered steps | Also needs `seed-crews`, `YAAF_API_BASE_URL` off-port, and Docker for real runs |
| `README.md` vs `CLAUDE.md` | 1,813 tests | ~1,350 tests |

## 7. Screenshot index

| File | Shows |
|---|---|
| `screens/10_start_project.png` | The two-way fork, "needs a key first" |
| `screens/13_form_step3.png` | Step 3: credential, model, autonomy, pace, quality and constitution (1,056 words) |
| `screens/20_create_result.png` | Create refused by the default quality preset |
| `screens/30_home.png` | Home headline and decisions |
| `screens/32_projects.png` | Counts that disagree |
| `screens/42_p2_overview.png` | Populated Overview (9 sections) |
| `screens/42_p2_spend.png` | "Pauses the project at 100%", "Healthy" and "no payment credential" together |
| `screens/50_inbox_item1.png` | Inbox: Decide tab and a question card |
| `screens/51_approve_click.png` | Spend approval applied on one click |
| `screens/61_rules_Pace_brakes.png` | Rules in force, the densest tab |
| `screens/72_ws_Founder.png` | The separate Founder credential |
| `screens/83_form_fire_drill.png` | Web drill failing example 01 ("seed the role library first") |
| `screens/89e_home_after_crash.png` | Home after a crashed first run |
| `screens/91_m_inbox.png` | Inbox at 390 px (horizontal overflow) |

## 8. What was not verified

Real model behaviour (CEO, Founder, `grade_goal`); real spend; a month of operation; reviews
with content; deliverables with files and verification; evidence audits; Slack and SMTP
delivery; claude.ai OAuth; Postgres; any real user. Populated screens came from
`evals.harness` + `evals.behaviors` run in-process against the dev database. Two fixture
artifacts appear in screenshots and are not product behaviour: "CEO Working: test" and a "$20,
model unknown, 1 run" ledger row.
