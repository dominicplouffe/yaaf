# YAAF — Product Experience Audit

*An independent review of what it is like to use YAAF, from first encounter to month three.
Analysis only: no product code was changed. Evidence, raw measurements and verbatim copy are in
[APPENDIX.md](APPENDIX.md).*

> **Next: [CLEAN_SHEET.md](CLEAN_SHEET.md)** redesigns YAAF from scratch around results, removes
> ~80% of the interactions this audit's roadmap still leaves with the user, and recommends
> shipping only Phase 1 below before validating that design. Treat this audit as the baseline.

**Audited:** `main` at `cbb5bec` (23 Oct 2026) · **Surfaces:** README and docs, web app, CLI, MCP
server, email/webhook notifications, Founder, fire drill · **Method:** the docs; the
implementation wherever the docs and the product could disagree; and the product run locally in
dev mode on SQLite: web via Playwright (117 screenshots, 1440×900 and 390×844), CLI, and MCP
over raw JSON-RPC.

**Evidence labels**

| Label | Means |
|---|---|
| **[Measured]** | Counted or observed directly in the running product. |
| **[Code]** | Read from the implementation (file:line given), not exercised live. |
| **[Inferred]** | A reasoned conclusion from measured/code facts; not observed with a user. |
| **[Hypothesis]** | A design bet that needs the validation in §K before it is trusted. |

**Limits.** There was no real model key. Agents ran on the mock runtime (which on a fresh
install "reads its context and exits"); populated screens came from the eval harness's scripted
behaviours. Not observed: a real CEO's writing, a real month of operation, the Founder
conversation past its key gate, claude.ai OAuth, SMTP or Slack delivery, Postgres, and any real
user. The personas in this document are constructed.

---

## A. Executive assessment

**Score: 4.5 / 10 for the target user** (busy, technical, wants outcomes, will not study the
product). The governance engine is stronger than that score. The experience around it is weaker.

| | Verdict |
|---|---|
| **How good is it today?** | A rare, honest, deeply engineered product that **makes the user do the engineering**. To get value you have to understand about 55 internal concepts, 62 rules, 10 project pages, 34 MCP tools and 75 CLI commands. Screens explain themselves in paragraphs instead of being clear: creation alone is 1,925 words on one page. |
| **Greatest strength** | **Integrity you can feel.** Rules are enforced server-side. "Done" means the platform checked it. Every decision says what silence will do, and the approval card states why the decision is yours. The briefing costs zero tokens. The fire drill counts how often a project will stop for you *before* you spend anything. No comparable product states consequences this honestly. |
| **Greatest UX weakness** | **The architecture is the user's mental model.** You are cast as the "chairman" of an org with a CEO, teams, seats, a constitution, amendments, commitments, cycles, bets, droughts, levers and verdicts. The real job — *"get me this outcome; ask me only what you must"* — sits under governance vocabulary, and in places the vocabulary contradicts itself (§A findings 2 and 5). |
| **Three biggest opportunities** | **1. One decision queue that comes to you.** Push every decision when it is filed: today a spend approval sends nothing until ~48h before it auto-denies [Code]. Give all nine interruption types one card. **2. Outcome-first status.** One sentence per project, the same on every surface, and 10 project pages folded into Status · Chat · Results · Details. **3. Ten minutes to first value.** One key, one command, one paragraph, one proposal card, then a result. Today: Docker, an SSH tunnel, two keys, a token mint, and a form whose default preset refuses to create the project. |
| **What must fundamentally change** | Move from **"you chair an organisation"** to **"you own an outcome."** The org chart, the constitution and the run machinery become guarantees YAAF *shows* (receipts, checks, limits), not a system the user operates. Keep every rule; stop requiring people to read them to start, and make sure every sentence about them is true. |

### Scorecard summary (detail in §J)

| Dimension | Now | Target |
|---|---|---|
| First-time usability | 2.5 | 8 |
| Clarity | 3.5 | 8.5 |
| Simplicity | 2.5 | 7.5 |
| Decision-making | 5.5 | 9 |
| Outcome visibility | 5 | 8.5 |
| Trust & confidence | 6 | 9 |
| Cross-surface consistency | 4 | 8.5 |

### The six findings that matter most

1. **P0 — The most consequential requests are the quietest.** When an agent files a spend,
   legal, tool or owner approval, no notification is sent (`yaaf/server/api/agent.py:2273`, no
   `chairman_notify`) [Code]. The first email comes from `nag_approvals` 48h before the default
   7-day auto-deny (`yaaf/server/janitor.py:1895`). It says "Decide it on the Decisions screen",
   which is a history view with no buttons, and it carries no link. The daily brief that might
   catch it is **off by default**. A user who doesn't open the app learns of a spend request about
   five days late, and silence means "no".
2. **P0 — The product misstates when spending stops.** The Spend page says *"The budget is a hard
   limit… it warns, then pauses the project"* and *"Pauses the project at 100%"*
   (`web/src/pages/project/Spend.tsx:61`, `web/src/pages/Project.tsx:320`). The rules say the
   budget is "a PLAN, not a wall", with the wall at `hard_stop_multiple` = **3×** in every preset
   (`yaaf/core/constitution.py:760+`). A user who sets $1,000 is shown $1,000 as the stop; the
   real stop is $3,000. The review step of the creation form says "pauses itself at the cap"
   [Measured]. This is the one class of information the product must never get wrong.
3. **P0 — The default path to a first project is booby-trapped.** All four quality presets
   ("Full send", "Balanced", "Keep it cheap", "Show me proof") set
   `deliverable_review: independent_read` [Code]. "Balanced" is preselected, so a roster without
   a reviewer is **refused at Create**. "Short name — picked for you" is actually a required empty
   field. Hiring a role but leaving "Employees" blank silently creates **0 teams**. The imported
   first example then fails the web fire drill ("seed the role library first", though 38 roles
   were seeded) while the CLI drill passes it [Measured].
4. **P0 — The first tutorial contradicts the product.** `examples/01-your-first-hour.md` says
   *"Leave everything else. Supervised"* and *"Minute 1 — your only real job… Nothing can be
   created until you do."* The example's spec names no authority rule, so `default_autonomy()`
   creates it **Autonomous** and the CEO accepts its own plan (`yaaf/core/constitution.py:720`)
   [Code]. The page also says "Paste your Anthropic key" (the form now picks a saved credential)
   and points at "Plan" and "Work" tabs that no longer exist. Server-written notices still say
   "Decide it on the Plan tab" (`yaaf/server/dispatcher.py:1057`, `janitor.py:2678`).
5. **P1 — Two keys, and nine shapes of interruption.** The recommended creation path (the
   Founder) needs its own credential, separate from the one projects pay with. Over MCP,
   `grade_goal` returns `graded: false` without it [Measured]. Once running, the user can be
   asked things in nine shapes: approvals (7 kinds), asks, flags, heads-ups, scope, date,
   verdicts, review levers, audit samples. Each has its own verbs, clock and MCP tool. The
   Inbox unifies the *list* into five tabs (Decide, Acknowledge, Updates, Watching, Done), not
   the *decision*.
6. **P1 — The interface explains instead of simplifying.** Measured words of copy: creation
   step 3 is 1,056 words, the role picker 1,388, Rules in force 482 with 69 controls, the role
   library 1,361. Help blocks per screen (`className="help"`): NewProject 48, project Settings
   49, Overview 24 [Measured]. That is close to what the earlier UX study measured before the
   redesign, so the IA moved but the reading load did not. Docs and copy narrate the product's
   history ("used to…", "before this rule existed…"), which helps contributors and is noise to
   users.

---

## B. The ideal YAAF experience (north star)

*Told by a user who has never seen the architecture.*

> **Monday, 08:40.** I typed into Claude: *"I want a team that reads the AI news every morning
> and gives me the five things that actually matter, with sources. Don't spend more than $60 a
> month."*
>
> Claude came back with one card:
>
> ```
> Morning AI brief — proposal
>   Delivers   5 findings, sourced, every weekday by 07:00
>   Checked    every source fetched and saved by YAAF, not by the agents
>   Cost       est. $25–40/month · hard stop at $60 (it will not go past this)
>   Asks you   about once a week (a new paid source, a change of direction)
>   First one  in ~20 minutes, then tomorrow 07:00
>   [Start]  [Change something]  [Show the team and rules]
> ```
>
> I pressed Start. Twenty minutes later the first brief was in my inbox. Every claim had a
> source chip: four said *"source verified by YAAF"* and one said *"couldn't verify —
> paywalled"*.
>
> **Thursday.** One notification: *"Decision needed: add The Information ($29/mo)? Recommended:
> no — 4 of last week's 5 findings were covered by free sources. If you ignore this: no
> purchase. [Approve] [Decline]."* I declined from my phone in four seconds.
>
> **Three weeks later**, back from a trip, I asked: *"What did I miss?"* — *"15 briefs
> delivered, all on time. One source died and was replaced. Spend $27 of $60. One decision
> expired while you were away (a paid source); it defaulted to no. [Re-open] Nothing else needs
> you."*
>
> I never saw the words constitution, seat, drought or cycle. When I wanted to know *why* a
> finding made the brief, "Show the evidence" gave me the fetched page, the agent who found it,
> and the agent who challenged it.

**Principles behind that story**

1. **Outcomes first, machinery on request.** The first screen of anything says *what will exist,
   by when, at what cost, and how sure we are*. The org is one click down.
2. **Silence is safe, and you are told what silence does.** YAAF already gets this right.
3. **The product comes to you.** Decisions reach you where you are, with the recommendation and
   consequences on the card, and you can act on them there.
4. **One status, one decision shape, one vocabulary**, rendered by web, email, Slack, MCP and
   CLI from the same producer.
5. **Proof is a label on the result, not a page you visit.** Every output says how it was
   checked: verified by YAAF, judged by the lead, or unchecked.
6. **Guarantees are shown, not configured.** "Hard stop at $60", "nothing is marked done unless
   checked" and "asks you about once a week" are promises on a card. The 62 rules that enforce
   them live under Advanced, and every sentence about them is true.

---

## C. User journey audit

### C1. Discovery → first value

| Stage | Today | Friction | Ideal |
|---|---|---|---|
| **README** | 299 lines. Strong, concrete value ("the ask → who gets hired" table is excellent). Then chairman, CEO, desks, constitution, Explore/Deliver/Operate, 4 MCP scopes and 4 rules come before any install step. | "Is this for me?" takes ~5 min because "What you give up" is near the bottom. | One sentence, a screenshot of a *result*, a 3-line quick start, and the trade-offs near the top. |
| **Try free** | `yaaf rehearse examples/01…` from a checkout: 7 s, 64 lines [Measured]. | Needs venv + checkout. 2–5 lines of log noise first. Prints "not in an Explore project's shape" on a Deliver project (`yaaf/rehearsal/report.py:606`). Cost line is generic: two different examples both reported "3 requirements, 12 runs" because the crew is scripted. | A forecast card: cost *range* from priced runs, time to first result, interruptions per week. |
| **Install** | Docker install → group change → log out/in → clone → deploy script → SSH tunnel → one-time link → password. Then, on your laptop: install the CLI, `yaaf login`, `mcp-token mint`, `claude mcp add`. About 10 commands across 2 machines [Inferred from README]. Dev mode took **11 commands** [Measured]. | Dev mode fails silently off port 8080 (runner and bootstrap link hard-default to `:8080`; `YAAF_API_BASE_URL` undocumented). `seed-roles` before login prints 38 × `401` and **exits 0**. `seed-crews` is missing from the dev steps, so the crew picker is empty. Real runs need Docker even in "subprocess" mode (`yaaf/runner/sandbox.py:165`) [Measured]. | `yaaf up` (or hosted trial) ends with the browser open on "Paste one key → describe your goal". `yaaf doctor` for everything else. |
| **Keys** | LLM credential *and* a separate Founder credential, both under Settings. | Same key, two places, two names. | One key; the Founder uses it unless told otherwise. |
| **Create** | See C2. | Default preset refuses creation; hidden traps. | Paragraph → proposal card → Start. |
| **Start** | **Start** with no credential sets status "Running" with no warning. Home cards say "Running" while the Overview says "problem: no billing credential attached — agents cannot run" [Measured]. | The first status a user sees is false. | Start is disabled with one fix button: "Add a key to start". |

**Time to first value (TTFV), estimated:** 45–90 minutes for a technical user on a fresh server,
plus the first deliverable's run time [Inferred]. Target: **under 15 minutes.**

### C2. Project creation (measured)

**First Home** (52 words): *"You don't chair a project yet. A project is a CEO agent, a team, a
goal and a budget…"* → **Start a project** (315 words, 2 paths). *"Describe it, and yaaf drafts
it"* is badged *"needs a key first"*.

| Form step | Words | Fields | Buttons | Decisions asked |
|---|---|---|---|---|
| 1 — The job | 459 | 5 | 10 | name, goal, shape (frozen) |
| 2 — The org | 200 (+1,388 in the role picker, 36 roles, 40 controls) | 3 | 13 | CEO role, crew or roles, employees per team |
| 3 — Money & rules | **1,056** | 12 | 21 | credential, model, Autonomous/Supervised, 3 pace cards, 4 quality cards, 8 constitution fields |
| 4 — Review | 439 | 3 | 11 | name (again), start now?, fire drill |
| *All on one page* | **1,925** | 23 | — | — |

**Minimal path: 12 clicks, 2 typed fields, and three traps** [Measured]:
1. *"Short name — picked for you"* on step 1, but step 4's Name is required and empty. Create
   shows only the browser's "Please fill out this field."
2. The preselected **Balanced** card makes Create refuse: *"deliverable_review is
   'independent_read', but no team on this roster hires a reviewing seat… set
   deliverable_review to 'none'…"* (step 3 does warn, in a paragraph).
3. A role hired with "Employees" left blank yields a project with **0 teams** ("1 person across
   0 teams"); Review said only "nobody yet".

**After Create:** a 147-word page of webhook URLs, HMAC secrets and a curl/openssl recipe, which
is not what anyone needs at that moment.

**Import path:** `examples/01-your-first-hour.json` lands on step 4. The **web** fire drill says
*"This project would not start… Add a team on one of: none installed — seed the role library
first"* (38 roles were seeded) and then *"It never had to wait for you"* under that failure. The
**CLI** drill passes the same file. The project's own review reads *"Cap $20/mo, ≈ $26/mo at
this staffing"*, so the flagship example is over budget as shipped [Measured].

**What is right:** both paths end on the same review; nothing exists until you sign; "All on
one page"; a drill before money; presets fill visible rules; the Founder says what it assumed.

**What is structurally wrong**
- **Shape is a frozen, architecture-level, up-front choice.** Users know "weekly brief", "one
  audit" or "find an idea". YAAF can infer Operate, Deliver or Explore and show it as a label
  with a reason.
- **Four tuning questions before the first result** (autonomy, pace, quality, constitution).
  Nobody can choose "Live vs Working hours vs A few times a day" well without a cost next to
  each option.
- **Hiring roles is choosing an implementation.** The README's best example ("Book our February
  trip — I want a confirmation number") is an outcome; the form asks you to staff it.
- **The drill tests the configuration, not the project.** It says so ("no evidence at all about
  your *project*") but sits where a pre-flight check sits, so users will read it as one.

### C3. Day-to-day operation

A project shows itself through **10 project pages, 5 Inbox tabs, 7 Activity tabs, 7 rule tabs
(69 controls) and a 4-tab agent drawer** [Measured]. The populated Overview has 9 sections and
587 words; the other pages are lighter (Tasks 81, Reviews 80, Deliverables 84 words), which
argues for *fewer pages*, not shorter ones.

The user's real questions are three: *Is it OK? Does it need me? What has it produced?* The
answers exist but are spread across Overview tiles, Inbox (Decide / Acknowledge / Updates /
Watching), Deliverables and Activity → Proof.

**Status is not trustworthy at a glance** [Measured]:
- Home and project cards say **"Running"** for projects that cannot run (no credential).
- The Projects page header says "3 waiting on you" while its chip says "Needs you 2". It also
  shows both "Active 4" and "Running 3".
- On a Supervised project, two banners say *"your call — Waiting on you… Decide it on the Plan
  tab"* and *"…the chairman decides what is in scope, so this is waiting on him, not on you."*
  The user *is* the chairman, and each row is badged "Waiting for you".
- A health line shows a raw ISO time: *"first stopping at 2026-10-25T12:04+00:00"*.

### C4. Decisions

The measured spend-approval card [Measured]:

> **Buy the $199 premium projections feed — $199** · *Why it is yours:* Above your approval
> threshold, so it is never an agent's call. · *The case:* Our free projections lag… I recommend
> buying one month for $199. · *If you say no:* We keep using the free feed… · *If you do
> nothing:* It is denied in 7 days. Silence is a decision here.
> **[Approve $199 ▾]** · Discuss with the CEO

That is about 75 words, and **the structure is right**: why it is yours, the case, the
consequence of no, the consequence of silence. This is better than Linear Triage or Stripe's
review queue on the dimension that matters most here, which is the cost of not deciding.
Problems:
- **Deny is hidden inside the ▾ menu**, and **Approve is one click with no confirmation** for a
  $199 irreversible spend. The safer option takes more effort than the costlier one.
- The recommendation is agent prose ("I recommend…"), not a field. Nothing guarantees it is
  there, and no surface can show "Recommended: approve" as a label.
- Reversibility is never stated.

| Interaction | Object / verb | Structured recommendation? | Says what silence does? | Pushed when filed? |
|---|---|---|---|---|
| Spend / legal / tool / owner approval | `Approval` · `approval_decide` | No (prose; `disclosure` JSON optional) | Yes (`if_declined` required) | **No**: nag 48h before expiry [Code] |
| Capacity request | `Approval(capacity)` | Implicit | Yes | Yes |
| Human assist (captcha/2FA) | `Approval(assist)` | n/a | Yes | Yes |
| Brief change | `Approval(brief)` | Diff shown | Yes | Yes |
| CEO question | `Ask` (answered in chat) | **Yes**: options + recommendation | Yes (default action) | Yes |
| Heads-up | `HeadsUp` · `heads_up_ack` | n/a | n/a | Yes |
| Flag | `Flag` · `flag_ack` ("Noted") | No | No | Varies |
| Scope / date / verdict | `requirement_decide`, `commitment_decide` | Partial | Yes | Yes (supervised only) |
| Review | `review_decide` (6 levers) | CEO narrative | Re-nagged | Yes |
| Audit sample | `audit_decide` | No (by design) | Expires "not reviewed" | FYI only |

### C5. Ten scenarios

Counts are **[Measured]** in the dev install where possible, **[Inferred]** otherwise. "#n"
refers to the Top 20 in §E.

| # | Scenario | What the user wants | Today | Where it hurts | Ideal | Closed by |
|---|---|---|---|---|---|---|
| 1 | First install + first project | A result | ~10–11 shell commands, 2 key entries, form (12 clicks, 3 traps) or Founder (key-gated), drill, Create. 3 surfaces. TTFV 45–90 min | Docker/SSH, two keys, default preset refuses, stale tutorial, false "Running" | 1 command, 1 key, 1 paragraph, 1 card, Start. TTFV < 15 min | #4, #5, #8, #13 |
| 2 | Complex multi-team project, deadline + budget | Believe the date and cost | 4-step form: crews/roles, pace + quality + autonomy, 8 constitution fields; Supervised to accept scope and sign the date | Must translate a deadline into run caps; drill cost not spec-specific; budget copy misstates the stop | Proposal card with date *range*, cost *range*, the true hard stop, and "what I'll ask you" | #2, #8, #9 |
| 3 | Check 5 projects in 60 s | Who's in trouble? | Home headline is good (*"3 things need you across 2 projects. The soonest decides itself in 2 days…"*). Cards show status, runs, spend, needs-you | No per-project on-track/at-risk verdict; "Running" while broken; counts disagree | One row per project: verdict sentence, due vs projected, spend pace, needs-you | #6 |
| 4 | Unexpected approval | Decide well in < 30 s | Not told when filed. Later: Inbox → card (~75 words) → Approve (1 click) or ▾ → Deny | Late; Deny hidden; recommendation not a field; email names a screen with no buttons and has no link | Pushed now; one card shape; both options equal; one tap | #1, #3 |
| 5 | Behind schedule / over budget | Two honest options and their cost | Banner with Cut scope / Move date; Spend page; review levers (6) | Levers not priced; Spend page claims a 100% stop that isn't real | "4 days late. Cut *Glossary* → on time, −$9 (recommended). Or move to 3 Nov." | #2, #3, #6 |
| 6 | Is the work really correct? | Evidence, fast | Deliverables (files) and Activity → Proof (claims) are separate; audit samples on Tasks | Proof isn't on the result | Verification badge on each result; "Show the evidence" inline | #14 |
| 7 | Change the goal mid-flight | Redirect safely | Chat → CEO files `brief_propose` → BRIEF approval with diff. Or edit Settings → Brief directly. Two doors, different effects | The Settings door skips the impact analysis | One verb: "Change direction" → diff + impact → confirm | #15 |
| 8 | Back after 5 days | What did I miss? | Inbox → Updates (per-project briefing "since you last looked"); Done tab; approvals may have auto-denied | Per-project; expired decisions can't be re-opened from the list; MCP briefing takes no window | "While you were away" on Home with Re-open | #19, #12 |
| 9 | Failure and recovery | What broke, how to fix it | Alarm email (always sent, good); health rows with fix links; run traces. The first real run here crashed with `DockerException` and Home still showed nothing wrong; the project said "Ended early 1 — 1 failed outright" | Diagnosis spread over health, runs and flags; no "fix it" verb | Recovery card: cause, fix, cost to resume, [Resume] | #16 |
| 10 | Mostly via Claude/MCP | Ask in plain English | 34 tools, ~30k chars (≈7.5k tokens) of tool descriptions before the first call. "How are my projects doing?" = 1+N calls; "what needs me?" = 1+2N (the desk lacks flags, heads-ups, reviews); "since yesterday" impossible | Raw JSON, internal field names (`queue_ceiling`, `drought`, `ration`, `beat`) | `status`, `attention`, `changes(since)`, `explain` with a prose `summary` | #11 |

---

## D. Information and interaction audit

### D1. Concept inventory

About **55 named concepts** reach the user [Code/docs]:

| Group | Concepts |
|---|---|
| People & org | chairman, co-chair, observer, CEO, team, lead, seat, role, role version, repin, crew, aisle, stand down, recall, hire |
| Shape & plan | Explore, Deliver, Operate, requirement, stage, dependency, critical path, acceptance test, observable, plan gate, scope, cycle, bet/hypothesis, kill date |
| Work | task, subtask, run, wake, heartbeat, board, claim, refusal, frozen thread, directive |
| Truth | evidence, proof, attested, verified, drought, external ratio, controlled domains, spot-check / audit sample, verdict, CEO judgment |
| Time & money | commitment, projection, promise, budget (plan) vs wall, hard-stop multiple, external envelope, tempo/pace, stakes, the bar |
| Governance | constitution, rule, amendment, autonomy dial (5 switches), presets (3 families), review, lever, flag, ask, heads-up, approval (7 kinds), standing order |
| Plumbing | LLM credential, Founder credential, service credential, data source, harness, provider, runtime, memory, compaction |

**Proposal:** the *primary* experience uses about **12** words: **project, goal, result, check,
decision, lead, team, budget, hard stop, due date, plan, history**. Everything else appears only
in Details or Advanced, with a one-line definition on hover.

### D2. Surface by surface

| Surface | Today [Measured] | Value | Recommendation |
|---|---|---|---|
| **Home** | Headline sentence, decisions, project cards, while-you-were-away | High | **Simplify**: one verdict row per project; "while you were away" leads after an absence |
| **Inbox → Decide** | Ranked by time to consequence | Very high | **Keep; redesign the card** (§G3) |
| **Inbox → Acknowledge** | Flags, heads-ups | Medium | **Combine** into Decide as single-button FYI cards |
| **Inbox → Updates** | Per-project briefing | Medium | **Combine** into Home's "while you were away" and per-project History |
| **Inbox → Watching** | Clocks the CEO resolves | Low | **Hide**; surface only when within 24h of becoming a decision |
| **Inbox → Done** | Last 7 days; clipped at 1440px | Medium | **Keep**; add Re-open where reversible |
| **Projects** | Portfolio table; counts disagree with header | Medium | **Combine** with Home |
| **Overview** | 9 sections, 587 words, 13 buttons | High, overloaded | **Redesign** as Status (§G2) |
| **Chat** | 400 words; open asks beside the conversation | Very high | **Keep**; render decision cards inline |
| **Roadmap** | 68–119 words; Explore shows "No plan, on purpose" | Occasional | **Hide** under Details → Plan; its verdict goes on Status |
| **Tasks** | Read-only board, 81 words | Low (read-only by design) | **Hide** under Details → Work; audit samples → Decide |
| **Deliverables** | Files by day; reader | Very high | **Promote** to "Results" with a verification badge per item |
| **Team** | Org chart, drawer | Medium | **Hide** under Details → Team |
| **Spend** | 187 words; misstates the stop | Medium | **Fix the copy now**; headline on Status, rest in Details → Money |
| **Reviews** | Empty unless a review is open | Weekly | **Combine** into Decide: an open review *is* a decision with levers as options |
| **Activity** | 7 tabs | Forensic | **Hide** under Details → History; Proof also inline on Results |
| **Project Settings** | 11 sub-sections; Rules 7 tabs × 69 controls | Rare | **Simplify** to Money · Pace · Say-so + Connections; rules → Advanced |
| **Workspace Settings** | 6 sections incl. separate Founder key | Once | **Combine** the Founder key into LLM credentials |
| **Role library** | 1,361 words, 46 buttons | Power users | **Hide** under Workspace |
| **New project** | Fork + Founder + 4-step form + drill | Critical | **Redesign** (§G1) |
| **Mobile** | Horizontal overflow on Inbox (409px), Projects (396px), Spend (453px) | Approve-from-phone matters | **Fix**; the Decide card should be phone-first |

### D3. Project navigation: 10 → 4

```
Today (10)                                         Proposed (4)
Overview · Chat · Roadmap · Tasks · Deliverables   Status   ← verdict, decisions, latest results, ask box
Team · Spend · Reviews · Activity · Settings       Chat     ← the lead; decision cards inline
                                                   Results  ← deliverables + how each was checked
                                                   Details ▸ Plan · Work · Team · Money · History · Rules
```

---

## E. Top 20 UX improvements (ranked by expected impact)

Effort: **S** < 1 week · **M** 1–3 weeks · **L** > 3 weeks, for one engineer who knows the code
[Inferred].

### 1. Push every decision the moment it exists, with a link and a one-tap action — P0 · M
- **Problem.** Generic approvals are silent when filed. The first email arrives ~48h before
  auto-deny, says "Decide it on the Decisions screen" and has no link. The daily brief is off by
  default. No notice carries a link [Code].
- **Evidence.** `agent.py:2273` (no notify on file); `janitor.py:1895-1920`; notify copy.
- **Why it matters.** Spend, legal and tool approvals are the decisions the product promises
  will always reach you. Silence means deny, so a late notice is a silent veto.
- **Solution.** Send an ACTION notice when any approval is filed, with approvals ranked first
  within `mail_max_per_day`. Give every notice a signed deep link to its Inbox item, and Slack
  buttons that open a confirm page. Name the Inbox in the copy. Default `brief_daily_at` to 08:00
  local when mail is configured.
- **Example.** `Decision needed (5 days): Buy the projections feed, $199. Recommended: approve.
  If you ignore it: denied 30 Oct. [Approve] [Decline] [Open]`
- **Benefit.** Closes the largest silent-failure path in the product. **Risk.** More email;
  mitigated by the existing cap and batching in `notify.py`.

### 2. Tell one true story about money — P0 · S
- **Problem.** The Spend page and creation review say the project pauses at 100% of budget. The
  rules say 100% is a plan and the stop is 3× (every preset) [Code/Measured].
- **Why it matters.** Rule 7 of this audit: money is never simplified into something false. A
  user who budgets $1,000 can be charged $3,000 while the UI says $1,000 is the limit.
- **Solution.** Everywhere a budget appears, show both numbers: *"Budget $1,000 · hard stop
  $3,000 (it will not spend past this)"*. Make the creation card ask for the **hard stop** first
  (the number people mean by "budget") and derive the plan from it. Add a test asserting that
  the Spend copy is computed from `hard_stop_multiple`.
- **Benefit.** Removes a trust-breaking misstatement. **Risk.** None; this is a copy and data fix.

### 3. One Decision card for all nine interruption types — P0 · M
- **Problem.** Nine shapes, verbs and clocks. Only Asks have structured `options` +
  `recommendation`. Deny is hidden in a ▾ while Approve is one click. Reversibility is unstated
  [Measured/Code].
- **Solution.** One render contract everywhere: **What happened · Why it's yours ·
  Recommendation · Options (each with consequence) · If you do nothing · Can I undo? ·
  Evidence ▸**. Make `recommendation` and `reversible` required on approvals (agents already must
  write `if_declined`). Show both options as equal buttons, and confirm irreversible spends.
  Flags and heads-ups become FYI cards with one "Got it".
- **Example.** §G3. **Risk.** Weak agent recommendations; grade them in the golden evals, as
  `if_declined` is today.

### 4. Make the first hour true — P0 · S
- **Problem.** Example 01 teaches Supervised but imports Autonomous. Stale "Paste your Anthropic
  key", "Plan tab", "Work tab" and "Boardroom" in examples, docs **and server-generated
  notices**. `yaaf mcp-stdio` is documented but doesn't exist. Rule counts disagree (code 62;
  docs 52 and 60). The drill mislabels Deliver as Explore. The web drill fails the example the
  CLI passes. Example 01 is over budget at its own staffing [Measured/Code].
- **Solution.** Put the Supervised preset in example 01's spec. Run a "truth pass", and add CI
  greps for retired screen names in `docs/`, `examples/`, `web/src` and server strings, the way
  the repo already enforces suite hygiene.
- **Risk.** None.

### 5. One key, and a creation path that cannot trap you — P0 · S
- **Problem.** A separate Founder key. The default preset refuses creation. "Picked for you"
  names are required and empty. A blank "Employees" field silently gives 0 teams. Start without a
  credential shows "Running" [Measured].
- **Solution.** On first credential: *"☑ Also use this key to draft projects"*. Quality presets
  set `deliverable_review` only when the roster has a reviewer, or auto-add one with a visible
  line. Actually pick the name. Treat a hired role as 1 employee by default. Disable Start with
  "Add a key to start" when no credential is assigned.
- **Risk.** Founder spend lands on that key; say so on the checkbox.

### 6. One status sentence per project, everywhere, with a 60-second portfolio — P1 · M
- **Problem.** Health is spread over tiles, a delivery banner, Setup & health, Watching and
  flags. Cards say "Running" while broken, and counts disagree. The MCP `desk` has no
  per-project headline [Measured].
- **Solution.** A server `status` producer:
  `{verdict: on_track|at_risk|needs_you|blocked|paused|done, sentence, next, money, confidence}`
  feeding Home rows (sorted by risk, healthy ones collapsed), Status, email subjects, the Slack
  topic, MCP `status` and `yaaf status`. YAAF already uses a single producer behind several
  surfaces for attribution; apply the same pattern to status.
- **Example.** `At risk — projected 3 Nov, promised 30 Oct. Cut "Glossary" to make it. $148 of
  $400 (hard stop $1,200). 1 decision waiting.`

### 7. Collapse the project from 10 pages to Status · Chat · Results · Details — P1 · L
- Keep every view, under Details (§D3, §G2). Keep keyboard shortcuts (`g k` → Details → Work)
  and every old route.
- **Risk.** Power users lose a click; mitigate with a remembered Details tab and ⌘K.

### 8. Creation = paragraph → proposal card → Start — P1 · L
- **Problem.** A frozen shape, 3 preset families + autonomy, crew/role hiring, 1,925 words on
  one page; or a Founder that hands off to the same form.
- **Solution.** The Founder (or an MCP client) returns one **proposal card** (§G1): what will
  exist, by when, cost range, hard stop, how it's checked, how often it will ask you, and the team
  in one line. The shape is inferred and labelled with its reason. "Change something" opens the
  full form for experts. The drill runs automatically and feeds the card's "asks you ~N/week"
  and "fits budget" lines.
- **Risk.** An inferred shape may be wrong: show the reason and allow a flip before Start.

### 9. Settings: three dials and a promise list — P1 · M
- **Problem.** 62 rules in 7 tabs (69 controls); 3 preset families; autonomy stored defaults
  (`chairman`) differ from the creation default (`autonomous`), and GOVERNANCE.md shows the
  former.
- **Solution.** The primary view is **Money** (budget, hard stop, purchase limit), **Pace** (how
  often it works, with $/day), **Say-so** (what it must ask you), each naming its preset.
  Raw rules live under Advanced, still amended with a reason.

### 10. Labels instead of paragraphs — P1 · M
- **Problem.** 24–49 help blocks per main screen; 1,000+ word steps; history narration.
- **Solution.** House style: one line per element, with rationale behind "Why?". Move "used to…"
  out of user copy. Add a lint budget on `className="help"` per page and on words per step.

### 11. Outcome-level MCP tools and a smaller tool payload — P1 · M
- **Problem.** 34 tools, 1,742 description words, ~30k chars on `tools/list`. Natural questions
  take 1+N or 1+2N calls; "since yesterday" is impossible; replies are raw JSON with internal
  names [Measured].
- **Solution.** Add `status`, `attention` (including flags, heads-ups and open reviews),
  `changes(since)` and `explain(project, question)`. Every reply leads with a 1–3 sentence
  `summary`. Make an ~8-tool profile the default for read/steer clients; the full set is opt-in.

### 12. A daily brief worth opening, on by default — P1 · S
- **Problem.** Off by default. Subjects like *"briefing — You changed 1 rule (brief_daily_at)."*
  and *"Window: the window you asked for — … (0.06h)"* [Measured].
- **Solution.** On at 08:00 when mail works. One email across all projects, ordered decisions →
  risks → results → spend. The subject is the most important line.

### 13. One-command start and a doctor — P1 · M
- **Problem.** Docker + SSH tunnel; off-port dev failure; `seed-roles` exits 0 on 401; a refused
  connection prints a 224-line traceback; a missing file prints a 52-line one [Measured].
- **Solution.** `yaaf up` prints one URL and opens it. `yaaf doctor` checks API, runner, master
  key, credential, SMTP and seeded roles/crews, and prints a fix for each failure. Non-zero exits
  on failed seeding.

### 14. Verification as a label on every result — P1 · M
- **Problem.** Deliverables show published/working, version and hash. Whether the claims were
  checked lives in Activity → Proof.
- **Solution.** Each result shows **Verified by YAAF**, **Judged by the lead**, **Read by a second
  agent** or **Not checked**, with counts, and "Show the evidence" inline. Self-judged work is
  never labelled verified.

### 15. "Change direction" as one verb — P1 · M
- **Problem.** Two doors to change the goal (chat → BRIEF approval, or Settings → Brief), with
  different consequences.
- **Solution.** One button and one chat intent. The lead returns the diff *plus impact*
  (requirements added or cut, date and cost delta) as a Decision card. Settings → Brief becomes
  read-only with that button.

### 16. Recovery cards — P1 · M
- **Problem.** Alarms are emailed (good), but fixing one means reading health rows, run traces
  and settings. In the test, a crashed first run left Home looking healthy.
- **Solution.** Every alarm and every failed first run produces a card: the cause in one
  sentence, the fix, the cost to resume, and an action ([Resume], [Fix key], [Raise hard stop to
  $X]). The same card appears in email, Status and MCP.

### 17. Vocabulary cut — P2 · M
- Rename chairman → **you/owner**, constitution → **rules**, amendment → **rule change**,
  commitment → **due date**, drought → **"no outside proof for 24h"**, requirement → **must be
  true**, seat → **agent**, stand down → **pause team**, and Explore/Deliver/Operate → **Find
  out / Deliver once / Repeat** (inferred labels). Internal names stay in the API. Remove copy
  that refers to the user in the third person ("waiting on him").

### 18. A CLI that matches the product — P2 · S–M
- Group the help (Projects · Decide · Inspect · Setup · Admin). Add help text to the 18
  commands without it. Use one error printer with hints (today 62 commands print raw
  `{"detail": …}`). Add `yaaf status` (prose; `--json` for scripts). Let `create-project` accept
  the example bundles (today they fail with a 175-line validation dump) [Measured].

### 19. "While you were away" that can undo — P2 · M
- After 24h+ away, Home leads with results delivered, decisions that expired and what they
  defaulted to (with **Re-open** where still possible), spend, and alarms. MCP gets the same
  through `changes(since)`.

### 20. Activity vs achievement — P2 · M
- Show **cost per verified result** and a "busy but not progressing" flag (spend rising,
  nothing verified in N days) on Status and Home, built from data `receipt` and plan velocity
  already compute (`noop_share`, dead runs). This answers "which projects are wasting money?"

---

## F. Five bold product changes

### F1. Make the org invisible by default: "goal in, results out"
**Change.** Users never pick roles, crews, teams or a CEO; they state an outcome. YAAF staffs,
re-staffs, and explains the team on request ("Who's working on this?"). The role library becomes
an expert tool.
- **Benefits.** Removes the hardest and least informed creation decision, and the source of
  trap #3 in C2. Matches the README's own best pitches.
- **Downsides / risks.** The org is part of YAAF's identity and of its open research question
  (org vs solo, `docs/PLAN.md`). Hidden adversarial seats may look like padding when costs show.
- **Mitigation.** Show guarantees rather than roles: "Every number is re-derived by a second
  agent."
- **Validate.** A/B creation with roles visible vs hidden: task success, perceived control, and
  the rate of later roster edits. If few users edit rosters, hiding them is safe.

### F2. A decision budget the system must respect
**Change.** The user sets "ask me at most ~N times a week". The drill already counts how often a
project would stop for you; make that a live, enforced metric. Over budget, the lead must bundle
or justify. Undelegable kinds (spend, legal, assist) are exempt from the cap but still counted.
- **Benefits.** Turns "manage by exception" into a contract. **Risks.** Batching can delay an
  urgent decision. **Validate.** Interruptions/week, decision latency and satisfaction across
  autonomy settings.

### F3. One status protocol, many renderers
**Change.** **Status** and **Decision** become the product's public contract. Web, email,
Slack, MCP and CLI are thin renderers, and no surface composes its own summary.
- **Benefits.** Ends the drift measured here (Home vs Projects counts; desk vs briefing;
  "Running" vs "cannot run"). Short, consistent MCP answers. **Risks.** Refactoring cost; power
  views still need Details. **Validate.** 20 scripted states rendered on every surface; target
  zero disagreements.

### F4. Promises instead of a constitution UI
**Change.** Creation and Settings speak in **promises**: "Hard stop at $60/month", "Never marks
work done unless checked", "Asks before any purchase over $50", "Delivers by 30 Oct or tells you
3+ days ahead". Each promise compiles to the existing rules, shown under Advanced.
- **Benefits.** People configure what they care about; the rules remain intact and enforced, and
  the copy is generated from them so it cannot drift (cf. finding 2). **Risks.** Some rules have
  no natural promise (memory caps, wake brakes); leave them at defaults, out of sight.
  **Validate.** After 30 s, can users say what a project will and won't do: promises card vs
  rules tab?

### F5. A zero-install trial on a recorded real project
**Change.** A hosted (or `docker run`) read-only demo that replays a real month of a YAAF project
in five minutes: the plan, a decision, a slipped date and its cut, a verified result. Then "Start
your own" with one key.
- **Benefits.** Time to *understanding* drops from an install to a click, and it answers the
  README's own caveat (no project has been run for a month by anyone else) with a real record.
  **Risks.** Hosting and curation cost; the demo must not imply results it didn't get.
  **Validate.** Funnel from README → demo → first project; "what does YAAF do?" answers after
  the demo.

---

## G. New product experience blueprint

### Mental model

> **You give YAAF a goal. It shows a plan with a price, a hard stop and a date, does the work,
> proves what it can, and asks you only what it must.**

Four nouns on the surface: **Projects, Decisions, Results, Chat**. One status sentence per
project. Everything else is Details.

```
┌ Sidebar ─────────────┐
│ Home          (●2)   │  ← verdict rows · decisions · while-you-were-away
│ Decisions     (2)    │  ← the one queue: approvals, questions, scope, date, reviews, samples
│ ─ Projects ─         │
│  AI morning brief  ● │   ● on track  ▲ at risk  ◆ needs you  ■ stopped
│  PPC audit         ▲ │
│ + New project        │
│ ──────────────────── │
│ Workspace ▸ Keys · Roles · Slack · People
└──────────────────────┘
```

### G1. Creation

```
New project
─────────────────────────────────────────────────────────────────────
What do you want done?
┌────────────────────────────────────────────────────────────────────┐
│ Read the AI news every morning and give me the 5 things that        │
│ matter, with sources. Under $60/month.                              │
└────────────────────────────────────────────────────────────────────┘
                                                        [Draft it →]

Proposal · Repeats every weekday (why?)
─────────────────────────────────────────────────────────────────────
Delivers     5 sourced findings, weekdays by 07:00, by email
Checked by   YAAF fetches every source; a second agent challenges each pick
Cost         est. $25–40/month · hard stop $60 (from 214 priced runs)
Asks you     ~1×/week: paid sources, changes of direction
First result ~20 minutes after Start
Team         Researcher, Editor, Skeptic                    (details)
Assumed      English sources · no paywalled sites            [change]
Paid by      Anthropic key "personal"                        [change]

[Start]   [Change something]   [Run as a free test first]
```

### G2. Project Status

```
AI morning brief                                          On track ●
"Delivered 5 of 5 briefs this week, all on time. $11 of $60. Nothing needs you."
Next: tomorrow 07:00 brief                              [Pause] [Message lead]

Decisions for you (0)                     Latest results
  Nothing needs you.                        Thu brief  ✓ 5/5 sources verified
  May need you soon: paid-source review     Wed brief  ✓ 4/5 · 1 paywalled (unverified)
                                            Tue brief  ✓ 5/5
Ask the lead ─────────────────────────────────────────────────────────
│ Why was the OpenAI item ranked first?                           [Send] │
Details ▸ Plan · Work · Team · Money · History · Rules
```

### G3. Decision card (web, email, Slack, MCP)

```
Decision · Fantasy desk · Spend                                  7 days left
─────────────────────────────────────────────────────────────────────────
What happened    Free projections lag injury news by ~24h; we lost 9 pts on it.
Why it's yours   Above your approval threshold, so never an agent's call.
Recommendation   Approve — one month, $199, then review the gain.
Options          Approve  → premium feed from Saturday's deadline
                 Decline  → keep the free feed; expect late injury calls
If you ignore    Declines itself on 30 Oct, 17:00 your time
Undo             Not after purchase
Evidence ▸       points lost to stale data · feed comparison · agent's case
                                    [Approve $199]  [Decline]  [Ask the lead]
```

### G4. Surfaces and their jobs

| Surface | Its job | Not its job |
|---|---|---|
| **Email** | Decisions (immediately), alarms, one daily digest | FYI events (already held back, which is right) |
| **Slack** | The same decision cards with buttons; chat with the lead | Mirroring every team channel by default |
| **Web** | Decide, read results, look deeper | Teaching the architecture |
| **MCP / Claude** | Plain-language questions, drafting projects, steering with previews | Raw table dumps |
| **CLI** | Install, doctor, scripts, ops; `status` | Day-to-day reading |

### G5. Conversation examples

```
You:    How are my projects doing?
Claude: 3 projects. PPC audit is at risk (projected 3 Nov vs 30 Oct; cutting
        "Glossary" makes it). Morning brief and Tutor are on track. 1 decision
        waiting: $199 feed on Fantasy desk (recommended: approve, 7 days left).
        [1 call: status()]

You:    Fix the PPC date without spending more.
Claude: Within your hard stop the lead offers: cut "Glossary" (on time, −$9) or
        merge R-03 into R-02 (2 days late). Preview: cut Glossary → on time.
        Confirm?   [confirm token bound to that exact change, as today]
```

---

## H. Before-and-after examples (from observed behaviour)

| # | Area | Before (observed) | After |
|---|---|---|---|
| H1 | **Approval email** | `[yaaf/<slug>] approval expiring: <title>` · `…If you have not decided by <time>, the answer is no — that is what this project's rules say. Decide it on the Decisions screen.` Sent ~48h before expiry; no link. | Sent when filed: `Decision needed (7 days): Premium projections feed, $199. Recommended: approve. If you ignore it: declined 30 Oct. [Approve] [Decline] [Open]` |
| H2 | **Approval card** | `[Approve $199 ▾]` (Deny inside the ▾; Approve is one click, no confirm) · recommendation buried in "The case" | Equal `[Approve $199] [Decline]`, a "Recommendation" line, "Undo: not after purchase", confirm on irreversible spend |
| H3 | **Budget** | "The budget is a hard limit… it warns, then pauses the project" · "Pauses the project at 100%" (actual stop: 300%) | "Budget $1,000 · hard stop $3,000 · warns at $800" |
| H4 | **Daily brief** | Subject `briefing — You changed 1 rule (brief_daily_at).` · body `Window: the window you asked for — … (0.06h)`. Off by default | Subject `All 3 projects on track · nothing needs you`; decisions → risks → results → spend; on by default |
| H5 | **Keys** | "The Founder needs a key of its own… never handed to a project's agents" + a separate LLM credential list | "Paste your Anthropic key. ☑ Also use it to draft projects." |
| H6 | **Create** | 4 steps, 1,925 words; frozen shape; preselected preset refuses Create: "deliverable_review is 'independent_read', but no team on this roster hires a reviewing seat…" | Paragraph → proposal card → Start; reviewer added or not required |
| H7 | **First tutorial** | "Leave everything else. Supervised… Minute 1 — your only real job" (imports Autonomous) | Spec sets Supervised; page shows where proposed scope appears |
| H8 | **Status** | Card: "Running" · Overview: "problem: no billing credential attached — agents cannot run" | "Not started — needs a key. [Add key]" |
| H9 | **Supervised banner** | "…the chairman decides what is in scope, so this is waiting on him, not on you." · "Decide it on the Plan tab." | "2 scope items wait for you. [Review]" |
| H10 | **CLI connection error** | 224-line traceback ending `ConnectError: [Errno 111] Connection refused` | `Can't reach YAAF at http://localhost:8080. Is the server running? Try: yaaf doctor` |
| H11 | **`yaaf projects`** | Raw JSON with each full goal (~800 words for one project) | `PPC audit  at risk  due 30 Oct (proj. 3 Nov)  $148/$400 (stop $1,200)  1 decision` |
| H12 | **MCP "what needs me?"** | `desk` (3.2k chars JSON; no flags, heads-ups, reviews) + `project view=flags` + `view=heads_ups` per project | `attention()` → `summary: "2 things need you. Soonest: $199 feed on Fantasy desk, 7 days."` + cards |
| H13 | **Fire drill** | 64 lines, log noise first, generic cost, "not in an Explore project's shape" on Deliver; web and CLI disagree on the same file | Proposal-card lines: "Fits $20 at typical prices · asks you ~2× before the first result · gap: nobody checks the writer [Add a reviewer]" |
| H14 | **Overview** | 9 sections, 587 words, 4 tiles + Needs you + Team + Roadmap + From the CEO + Coming up + Money + Deliverables + Watching + Setup & health | Status (G2): one sentence, decisions, results, ask box |

---

## I. Implementation roadmap

### Phase 1 — Immediate simplification (2–4 weeks)
**Items #1, #2, #4, #5, #12, #18 (errors and help), the copy part of #10, and a minimal #3**
(`recommendation` required on approvals; equal Approve/Decline buttons).

Acceptance criteria:
- Every approval kind sends a notice within 1 minute of filing (invariant test).
- Every notice has a working deep link; CI fails on retired screen names in docs, examples,
  `web/src` and server strings.
- Budget copy is computed from `hard_stop_multiple` everywhere (test).
- Example 01 imports Supervised; web and CLI drills agree on it; it fits its own budget.
- One key runs the Founder and a project. The default form path creates a project with no
  refusal.
- No CLI command lacks help; no connection or file error prints a traceback.
- **Outcome:** TTFV −30%; zero "missed approval" reports; zero incorrect money statements.

### Phase 2 — Workflow redesign (1–3 months)
**Items #3 (full), #6, #7, #8, #9, #11, #14, #15, #16.**

Acceptance criteria:
- One `status` producer feeds Home, Status, email, MCP and CLI (an equality test).
- Project nav has 4 entries; every old route resolves.
- Paragraph → proposal card → Start in ≤ 3 user actions.
- Settings primary view ≤ 12 controls; all 62 rules reachable under Advanced.
- Each of the 10 natural questions in §C5 is answered over MCP in 1 call with a prose summary.
- **Outcome:** ≥ 80% of first-time test users create a project unaided; ≥ 90% correct on the
  60-second portfolio check.

### Phase 3 — World-class experience (3–6 months)
**Items #13, #17, #19, #20 and bold changes F1–F5 as validated.**

Acceptance criteria:
- Status and Decision are the documented public contract; Slack renders actionable cards.
- Interruptions/week is a visible, enforced project metric.
- Zero-install demo live, with README → first-project conversion measured.
- **Outcome:** TTFV < 15 min; ≥ 90% correct decisions at a median time to decision < 45 s.

---

## J. Product experience scorecard

Scores are judgements on a 10-point scale, anchored on the evidence column. They are not
measured user data; treat ±1 as noise.

| Dimension | Now | Target | Evidence |
|---|---|---|---|
| First-time usability | 2.5 | 8 | Docker + SSH + 2 keys + token mint; default preset refuses Create; 3 traps; tutorial contradicts default; dev mode fails off-port |
| Onboarding | 3 | 8 | Founder and drill are genuinely good ideas, behind setup; drill cost not spec-specific; web/CLI drills disagree |
| Clarity | 3.5 | 8.5 | ~55 concepts; 1,000+ word steps; budget, chairman and "Running" statements that contradict each other |
| Simplicity | 2.5 | 7.5 | 10 pages, 62 rules / 69 controls, 34 tools, 75 commands, 9 interruption types |
| Information architecture | 5 | 8.5 | The redesign gave one decision list and a familiar sidebar; Updates/Watching/Activity overlap; light pages argue for fewer pages |
| Decision-making | 5.5 | 9 | Time-to-consequence ranking, "why it's yours" and silence defaults are excellent; no push on filing; Deny hidden; recommendation unstructured |
| Workflow efficiency | 4 | 8 | MCP 1+N calls; CLI raw JSON; two doors to change direction; 12 clicks to create |
| Outcome visibility | 5 | 8.5 | Deliverables reader is strong; verification not on results; activity ≠ achievement not surfaced |
| Trust & confidence | 6 | 9 | Server-side rules, receipts, attribution, honest README caveats; undercut by false budget copy, false "Running" and silent approvals |
| Error recovery | 4.5 | 8 | Alarms always emailed; health card with fix links; no recovery verb; crashed first run invisible on Home; CLI tracebacks |
| Cross-surface consistency | 4 | 8.5 | Shared desk producer is good; MCP desk lacks flags/heads-ups; CLI vs MCP briefing windows differ; Projects counts disagree |
| Overall coherence | 4.5 | 8.5 | A coherent engine wrapped in an inconsistent surface vocabulary |

---

## K. Validation plan

No recommendation here has been tested with users. These tests decide which ones survive.

### K1. Moderated, task-based usability (two rounds of 5–8 participants)
Recruit technical leads or founders who use Claude Code or similar and have never used YAAF.
Sessions are 45 minutes. Round 1 runs on today's product as the baseline; round 2 runs on
Phase 1–2 prototypes (the clickable-mockup approach in `docs/ux-redesign/` is adequate).

| Task | Success criterion | Measures |
|---|---|---|
| Install and create a project from one paragraph | Running project, no help | TTFV, assists, errors |
| "In 60 s, which of these 5 projects needs attention and why?" | Correct project and reason | Accuracy, time |
| Decide a seeded spend approval | Matches the seeded right answer | Time to decision, correctness, confidence 1–5 |
| "What is the most this project can spend this month?" | States the hard stop | Correctness (today's copy predicts failure) |
| Was this deliverable's content checked? | Correct verification status | Time, clicks |
| Bring a late project back on time without more budget | Chooses the cut | Correctness, steps |
| Change the goal mid-flight | Uses the intended path | Steps, errors |
| The same five questions via Claude/MCP | Correct answers | Tool calls, tokens, correctness |

### K2. Product metrics (local, privacy-safe)
- **Time to first value**: install → first published deliverable.
- **Task completion**: share of created projects that reach a first verified result.
- **Decision latency** by kind; **expired-undecided rate** (target < 5% for spend and legal).
- **Correct-decision proxy**: decisions reversed within 72 h.
- **Interruptions per project-week** vs the drill's forecast.
- **Unnecessary interventions**: kinds where the user accepts the recommendation > 95% of the
  time are candidates for delegation.
- **Comprehension**: a one-question check on the proposal card ("What happens if you ignore
  this project for a week?").

### K3. Hypotheses to test, not assume
- **H1** Users prefer a hidden org (F1) *without* losing trust; measure perceived control.
- **H2** A proposal card with ranges is trusted more than the drill's walk; check calibration
  against real costs after two weeks.
- **H3** Push-on-filing raises decision rates without raising annoyance; track opt-outs and
  ignored emails.
- **H4** Four project pages cover 90% of visits; check with Details click-through telemetry.

---

## Competitive notes: principles worth borrowing (not interfaces)

| Product | Pattern | Evidence | For YAAF |
|---|---|---|---|
| **Linear Triage** | An intake inbox with single-key Accept / Decline / Duplicate / Snooze; snoozed items return on new activity ([triage](https://linear.app/docs/triage), [snooze](https://linear.app/docs/snooze)) | Verified in docs | Decision queue keys; "snooze until 24h before it decides itself" |
| **Stripe Radar reviews** | A prioritised review queue; each action states its effect; irreversibility labelled ("a completed refund is permanent") ([reviews](https://docs.stripe.com/radar/reviews)) | Verified in docs | **Undo: possible / not possible** on every Decision card |
| **GitHub Copilot coding agent** | Assign an issue like a teammate; work arrives as a draft PR with session logs one click away; review requested at the end ([docs](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-on-github)) | Verified in docs | Result first, trace on demand; the outcome object is the hub |
| **Devin** | Plan approval is one setting; the Slack thread is the work surface ([release notes](https://docs.devin.ai/release-notes/2024.md), [Slack](https://docs.devin.ai/integrations/slack)) | Verified in docs | Autonomy as one plain toggle; Slack decision cards |
| **Paperclip** | Familiar SaaS nouns, ambient liveness, triage inbox (studied in `docs/ux-redesign/`) | From its repository | Already adopted; YAAF's time-to-consequence ranking is better |
| **Vercel** | Every deployment is one status (Ready / Error / Building) with logs one click away | General product knowledge, not re-verified here | One verdict per project, details on demand |

---

*Appendix: [APPENDIX.md](APPENDIX.md): setup log, measured inventories, verbatim copy, CLI/MCP
outputs, notification table, and screenshot index.*
