# YAAF — Product Experience Audit and Clean-Sheet Design

*An independent review of what it is like to use YAAF, from first encounter to month three, and a
from-scratch design for what it should be. Analysis only: no product code was changed.*

| Part | Question it answers |
|---|---|
| [**Part I — Audit of today's product**](#part-i--audit-of-todays-product) (sections A–K) | How do we make YAAF substantially easier to use? The baseline. |
| [**Part II — YAAF from a clean sheet**](#part-ii--yaaf-from-a-clean-sheet) (sections 1–9) | What would YAAF look like if we designed the best experience for people who just want to delegate and get excellent results? Part II covers results, automation and a from-scratch design. |
| [**Appendix — evidence**](#appendix--evidence) | Setup log, measured inventories, verbatim copy, CLI/MCP output, notification table, screenshots. |

**Bottom line.** Ship Part I's Phase 1 fixes now; they are defects under any design. Do not
start Part I's Phase 2 page rework. Validate Part II's design with the five-week test in Part II §8, then
build it on the existing engine.

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

# Part I — Audit of today's product

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

# Part II — YAAF from a clean sheet

*The second design exercise, after the baseline audit in Part I. That audit asked "how do we
make YAAF substantially easier to use?". This one asks:*

> **What would YAAF look like if we designed the best possible experience for people who
> simply want to delegate work and get excellent results — keeping every capability, and
> ignoring today's interface and workflows?**

It answers three gaps the review of the baseline named:

1. **Results.** Are deliverables useful, easy to consume, immediately actionable, and shaped to
   the task? (§1)
2. **Automation.** Remove at least another 50% of the interactions the baseline still left with
   the user. (§2)
3. **Radical simplification.** Design from scratch, and prove nothing is lost. (§3–§7)

Evidence labels are those of Part I: **[Measured]**, **[Code]**, **[Inferred]**,
**[Hypothesis]**. Interaction counts in §2 are **estimates from a stated scenario**, not
telemetry.

---

## Part II summary

**Today's YAAF is organised around running a project. The clean-sheet YAAF is organised around
receiving results.**

| | Today | Clean sheet |
|---|---|---|
| What you create | A project: shape, CEO, teams, constitution, presets | A **Job**: one paragraph that becomes a **Contract** you can read in 20 seconds |
| What you receive | Files on a shelf you visit; an FYI line that is never emailed | **Deliveries**: the answer itself, in your inbox, shaped to the task, with a proof label and the actions you can take |
| What you're asked | 10 kinds of decision; ~75 interactions (default) to ~100 (Supervised) across two 4-week jobs [Inferred, §2.1] | **Calls**: only consent, taste, or a broken promise. ~8 interactions in the same scenario [Inferred], about 80% fewer than the baseline's roadmap reaches (~45) |
| What you see of the machinery | Org chart, constitution, runs, wakes, droughts, levers | A receipt on every delivery ("checked by…, cost…, not verified…"). Everything else is *Under the hood* |
| Nouns you must learn | ~55 | **4: Job, Contract, Delivery, Call** |

**Recommendation.** Do not spend Phase 2 of the baseline roadmap rearranging today's 10 pages.
Ship the baseline's **Phase 1 truth fixes** (they are needed under any design), then build the
clean-sheet shell **on the existing engine**. The server's rules are the product and they stay;
the object model the user touches and the surfaces change. Validate it first with the
five-week test in §8.

---

## 1. The results audit: do users get what they came for?

The baseline audited how YAAF is *operated*. This section audits what YAAF *produces*, and
whether the user can consume it and act on it.

### 1.1 What we found

| # | Finding | Evidence |
|---|---|---|
| R1 | **Results don't come to you.** Publishing a deliverable is classed FYI, so it is never emailed. Its notice reads `"{filename} (v{n}, {bytes} bytes) is in the artifact store, satisfying R-03."` Only a Slack install receives the file itself. An email-only user gets nothing until the daily brief, which is **off by default**. | [Code] `yaaf/server/api/agent.py:3755`; `notify.py` FYI class |
| R2 | **Users have already complained about exactly this.** The model's own comment records it: *"where are my deliverables?", "where is the report??" and "can you push to me every morning instead of me going to look for a file"*. | [Code] `yaaf/core/models.py`, `Artifact.slack_ts` comment |
| R3 | **Deliverables are long, and the answer is buried.** The project's own data: markdown deliverables had a median of **12,199 characters**, three times everything else put together. One 11,540-character finding had a **two-sentence answer** (5,666 of 19,804 sellers have a phone; 81 may legally be dialled). The other 10,000 characters were provenance, hashes and funnel tables that had already shipped as separate files. | [Code/commit] `055bd0d` message |
| R4 | **The fix for R3 is a prompt, not a rule.** "Open with the answer…" was added to the context pack. YAAF's first house rule says a prompt is a suggestion and only a server refusal counts, yet deliverable *shape*, the thing the user actually consumes, is governed only by the prompt. | [Code] `yaaf/server/contextpack.py`; `CONTRIBUTING.md` rule 1 |
| R5 | **A deliverable is an opaque file.** The `Artifact` row has name, version, hash, size and claimed requirement ids. It has no summary, audience, "what changed since last time", actions or verification label. The platform knows a lot about the work and attaches none of it to the result. | [Code] `Artifact` model |
| R6 | **Working files and results share one shelf.** Deliverables lists "published" and "working" files together, filed by day, built for "thousands of files". One briefing window held "20 deliverables published". The user has to curate the output. | [Code/docs] `docs/DASHBOARD.md` Deliverables; `docs/FEATURES.md` §12 |
| R7 | **Proof is not on the result.** Whether a claim was fetched and verified lives in Activity → Proof, a different page from the file. | [Measured] web walkthrough |
| R8 | **Many results end in an action the user must do by hand, on a deadline, with no help.** FPL: *"You recommend; he executes"*, and a memo is due 24h before a deadline. Content engine: WordPress drafts "for you to approve". Brokerage: "every position arrives as a ticket". Nothing tracks whether the user acted, reminds them before the deadline, or offers to do it with consent. | [Docs] `docs/projects/fpl-manager.md`; examples 12; README |
| R9 | **Quality is graded by reality only where someone designed it in.** FPL scores each memo 48h after the gameweek. The Amazon desk is graded against Amazon's month-close reports. That is a superb pattern, but it is per-project prompt design, not a product feature, so most jobs have no track record. | [Docs] `fpl-manager.md` goal; README table |
| R10 | **The result type is never declared.** The 12 examples produce at least seven kinds of result (§1.2), all published the same way as "a file". | [Docs] `examples/*.md` "What you get" |

**Verdict.** YAAF is excellent at proving work *happened correctly*, and weak at making the
*result* useful at the moment the user receives it. The trust machinery stops at the artifact
store; the last mile (delivery, shape, action, follow-up) is left to prompts and the user.

### 1.2 Results by task type: what each should look like

The examples imply seven result types. Each needs a different presentation, different actions
and a different measure of success.

| Type | Examples today | What the user needs at delivery | Actions on the delivery | Checked by | Scored later by |
|---|---|---|---|---|---|
| **Answer** | 01 briefing note, 02 settle an argument, 10 filings note, 11 numbers you can defend | The answer in ≤ 3 sentences, then the 3–5 points that support it, then sources | Ask a follow-up · Share · "Wrong because…" | Sources fetched; numbers re-derived by a second agent | Whether a claim is later contradicted |
| **Recommendation** (you act) | FPL memo, brokerage ticket, PPC "what to change Monday" | **What to do, by when**, the expected gain, and the one thing that would make it wrong | **Done** · **Do it for me** (if connected and consented) · Skip (why?) · Remind me | Arithmetic shown; risk/compliance seat signed | Outcome vs expectation (points, P&L, savings) |
| **Change for approval** | 04 nightly PR, 12 WordPress drafts | A diff/preview, why, the risk, and the checks that passed | **Ship** · Request changes · Discard | CI/tests run by the platform | Merged/reverted; replies/engagement |
| **Artifact you use** | 08 game, 06 hardened tool, 05 lesson | Open it / run it; what it does; known limits | Open · Give feedback · Next version | Platform-run tests; red-team record | Usage; feedback |
| **Transaction** | Book the trip, buy data, place an order | The **confirmation**: record locator, amount charged, cancellation terms | Add to calendar · Cancel (if possible) | Platform-verified confirmation (email/webhook) | Did it happen as promised |
| **Digest / monitor** | 03 triage desk, 07 what changed, research dept., morning standup | **Only what changed**, ranked, with "nothing changed" as a valid one-liner | More like this · Less like this · Mute topic | Each item's source verified | Items the user acted on vs ignored |
| **Verdict** (explore) | 09 one idea, thirty days; "can we retire early?" | Yes / No / Not proven, the evidence, and what would change it | Accept · Keep going (cost) · Stop | External evidence verified; kill date | Whether the verdict held |

### 1.3 Design rules for results (each enforceable server-side)

In keeping with YAAF's house rule, each of these is a **refusal or an automatic state change**,
not an instruction to the agent.

1. **A delivery has a declared type** (§1.2), set in the Contract. Publishing an untyped file is
   a *working file*, never a delivery.
2. **A delivery has an answer-first `headline` (≤ 280 characters) and a `summary` (≤ 1,200
   characters).** Publish without them is refused. Supporting files attach *beneath* the
   delivery and are collapsed by default. This turns R3/R4 from a prompt into a rule.
3. **Recommendations and changes must carry `actions`**, each with a deadline where one exists.
   A recommendation with no "what to do, by when" is refused.
4. **Recurring deliveries must carry `changed_since_last`.** "Nothing changed" is a valid value
   and is delivered as one line.
5. **The platform, not the agent, writes the proof label**, from the evidence and tests linked to
   the delivery: *Verified* (platform-run test or fetched source) · *Second-agent checked* ·
   *Lead's judgment* · *Not checked*. Mixed deliveries show counts ("4 of 5 claims verified").
6. **Deliveries are pushed** to the user's channel (email, Slack or Claude) with the headline,
   summary and actions **in the message itself**. A link is for depth, never the only way to get
   the answer.
7. **Every delivery accrues a score when reality reports back**, if the type has a scoring source
   (§1.2). Scores roll up into the Job's **track record**.
8. **Feedback on a delivery is steering.** "Less like this", "wrong because…" and "always include
   X" become standing orders through the existing brief/standing-order mechanism, disclosed back
   ("Noted: no paywalled sources from now on").

### 1.4 What a delivery looks like

**Recommendation (FPL), in the inbox:**

```
Subject: GW12: sell Watkins → buy Isak (−4 hit) · captain Haaland · act by Fri 18:30

Do this by Friday 18:30 (deadline 19:30)
  1. Transfer: Watkins → Isak (−4 hit). Expected +6.1 pts over GW12–14.
  2. Captain Haaland, vice Saka.
  3. Bench order: Raya, Mykolenko, Gordon.
Would make it wrong: Isak is a late fitness test (70% to start, as of Thu 14:20).

[Done]   [Remind me Fri 17:00]   [Why this, not rolling?]

Checked: 14/14 figures from the FPL API (verified) · hit arithmetic re-derived by the
optimizer · team news 6h old.  Last 4 memos: +3, +11, −2, +7 pts vs rolling.
Cost this week: $3.40 · Full memo and working ▸
```

**Answer (PPC audit), first lines:**

```
You are wasting about $1,840/month on 3 campaigns. Pause "Generic – broad" first
(−$1,120/mo, no measurable sales lost in 90 days).
  ✓ All figures re-derived from your Ads export by a second agent (verified)
  ⚠ "No sales lost" assumes 14-day attribution; 7-day would show 2 sales.
[Open the deck]  [Ask why]  [Send to client…]
```

---

## 2. The automation pass: removing more than half of what's left

### 2.1 The interaction ledger

Scenario **[Inferred]**: one 4-week **Deliver** job (a research report with 6 scope items, ~25
judgement-checked closes, 2 spend approvals, 3 questions from the lead, 4 weekly reviews), plus
one 4-week **Operate** job (a weekly brief, 4 cycles). An interaction is one thing the user must
read-and-do, or decide.

| Interaction | Today: Supervised | Today: Autonomous (default) | After baseline roadmap | Clean sheet | How it disappears |
|---|---|---|---|---|---|
| **Creation decisions** (name, goal, shape, CEO, crew/roles, employees, key, model, autonomy, pace, quality, constitution, domains, start) | ~15 per job → 30 | 30 | 3 per job → 6 | **2 per job → 4** (paragraph, Start) | Contract inferred from the paragraph (§3.2) |
| **Keys / data / Slack setup** | 3–5 | 3–5 | 2 | **1** | One key; data sources asked just-in-time (2.2 A7) |
| **Accept scope** (per requirement) | 6 + 4 cycles × ~3 = 18 | 0 | 1 batched + 4 = 5 | **0** | The Contract *is* the scope consent (A1) |
| **Sign / move the date** | 1 | 0 | 1 | **0** | Due date is in the Contract; projection only interrupts on a broken promise (A2) |
| **Judge uncheckable closes (verdicts)** | ~5 | 0 | ~5 / 0 | **0** | Second-agent check + reality scoring + lead judgment, disclosed on the delivery's proof label (A3) |
| **Spend / tool / legal approvals** | 2 | 2 | 2 | **1** | Pre-authorised envelope in the Contract; precedent proposals (A4) |
| **Capacity requests ("more room")** | ~1 | ~1 | 1 | **0** | Pace auto-tunes inside the hard stop (A5) |
| **Budget overrun rulings** | ~1 | ~1 | 1 | **0** | Inside the hard stop, an overrun is a line in the receipt, not a ruling (A5) |
| **Questions from the lead (asks)** | 3 | 3 | 3 | **1** | Preference memory, the ask budget and "decide-and-tell" (A6) |
| **Flags ("Noted"), heads-ups (ack)** | ~5 | ~5 | ~3 | **0** | Reading is acknowledging; standing orders answer themselves (A8) |
| **Weekly reviews** (read, levers, close) | 4 × 2 = 8 | 8 | 4 | **0–1** | Auto-closed when green; a lever becomes a Call only when one is recommended (A9) |
| **Audit samples** | ~5 | ~5 | ~5 | **0** (optional "Does this look right?" on deliveries) | Feedback on deliveries replaces sampling (A10) |
| **Health "Got it", repin, unfreeze, extend bet, stand down/recall** | ~5 | ~5 | ~3 | **0** | Self-healing with notice (A11) |
| **Mark briefing read** | ~10 | ~10 | 0–10 | **0** | Delivery receipts mark read (A12) |
| **Complete the project** | 1 | 1 | 1 | **0** | Contract fulfilled → closes itself, with undo (A13) |
| **Broken promise** (late, over, blocked) | (inside reviews/flags) | | 1 | **1** | One Call with a recommended fix |
| **Act on recommendations** (outside YAAF) | uncounted | uncounted | uncounted | **reduced** by Do-it-for-me with consent | (A14) |
| **Total** | **~100** | **~75** | **~45** | **~8** | |

**Result: ~8 interactions vs ~45 after the baseline roadmap (−82%), and vs ~75 for today's
default (−89%).** The −50% target is met with margin even if every clean-sheet estimate is
doubled. Every removed interaction is moved to one of: *not needed by design*, *automated with
notice*, *automated with undo*, or *pre-decided once in the Contract*. None is hidden. All of it
appears in the weekly "Decided for you" digest (§2.3).

### 2.2 The automation mechanisms

Each is grounded in a capability the engine already has; the "Built on" column says which.

| # | Mechanism | What it replaces | Built on | Guardrail |
|---|---|---|---|---|
| **A1** | **Contract = consent to scope.** At Start you consent to *outcomes*, not requirements. The lead maps outcomes to requirements; scope changes that keep the Contract's promises (deliverable, date, ceiling) need no consent. | Accepting each requirement; per-cycle re-acceptance on Operate | `plan_authority`, plan gate arithmetic, `scope_authority` | A change that alters a promise is a Call. Every scope change is listed in "Decided for you". |
| **A2** | **Promise-only date interrupts.** The platform projects as today, and you hear about the date only if the projection breaks the Contract's promise, with a priced fix. | Signing and resetting dates | Commitments, projection, `commitment_reset` | The CEO still never writes a date; resets adopt the platform's projection, disclosed. |
| **A3** | **Layered checking instead of chairman verdicts.** Platform tests where possible; otherwise a second agent from a different role; otherwise lead judgment, shown as such on the proof label; reality scoring later. | Verdicts, much of spot-checking | `verdict_policy: ceo_judgment`, `deliverable_review: independent_read`, evidence verifier | Self-judged work is never labelled *verified*. Anyone can "Challenge this" on a delivery, which opens a Call. |
| **A4** | **Pre-authorised envelope + precedent proposals.** The Contract states "may spend up to $X on data/tools without asking, $Y per item". After you approve the same kind of request 3 times, YAAF offers: *"Pre-approve data purchases under $50 on this job?"* — one consent replaces future asks. | Most spend and tool approvals | `approval_threshold_usd`, `max_usd_per_purchase`, `external_monthly_usd_cap` | Pre-authorisation is **your** decision made in advance; the lead never gains spend authority. Legal and human-assist (captcha/2FA) are never pre-authorisable. |
| **A5** | **Pace auto-tunes inside the hard stop.** The Contract's ceiling is the only money number you set. Runs/day, concurrency and cadence adjust to the projection within it, and an overrun of the *plan* inside the ceiling is accounting, not a decision. | Capacity requests, overrun rulings, tempo presets | Capacity math (`capacity.py`), tempo rules, `hard_stop_multiple`, overrun accounting | The hard stop never moves without you. `max_concurrent_runs` stays a box-wide absolute. |
| **A6** | **Questions only for what only you know.** The lead must decide-and-tell on anything inferable from the Contract, standing orders or past answers. Questions about preferences are remembered as standing orders. The ask budget (`max_open_asks`) tightens to 1. | Most asks | Asks with defaults, standing orders, brief mechanism | Every decide-and-tell is listed; "you decided X, I'd have said Y" becomes a standing order. |
| **A7** | **Just-in-time capabilities.** Data sources, service credentials and Slack are requested when the work first needs them ("To read your Ads data I need access — connect?"), not at creation. | Up-front setup forms | Needs/assist approvals, `read_source`, data-source checks | Creating a credential is always yours; the request names exactly what it unlocks. |
| **A8** | **No acknowledgement verbs.** Flags and heads-ups are delivered as information; opening the message acknowledges it. A flag whose condition persists is folded into the job's status line. | "Noted", heads-up ack | Flags, heads-ups | Alarms remain alarms (always delivered). |
| **A9** | **Reviews run themselves.** The janitor still opens the standing review at zero tokens. If the platform's numbers are green and the lead recommends no lever, it auto-closes into the digest. If a lever is recommended, the review becomes one Call. | Weekly review meetings | Reviews, levers, variance narrative | The review record is unchanged; "open the review" stays available. |
| **A10** | **Feedback replaces sampling.** Each delivery carries an optional 👍 / 👎 / "wrong because…". That *is* a spot-check, done where you already are, and it steers. Platform sampling continues silently to the second-agent checker. | Audit sample queue | `audit_sample_rate`, audit records | If 👎 rates cross a threshold, the proof label degrades and a Call opens. |
| **A11** | **Self-healing with notice.** Auto-repin to a newer role version after a passing drill; auto-unfreeze a looping thread once with the lead's plan; auto-extend a bet once inside its kill rules; auto-create Slack rooms when first used; re-test data sources. | Health "Got it", repin, unfreeze, extend, stand down/recall | Health checks, drill, pair breaker, hypotheses, staffing | Each happens at most once per cause before becoming a Call, and each is listed and undoable. |
| **A12** | **Read receipts from delivery.** A delivery opened in email, Slack or Claude marks its window read. | "Mark as read" | Briefing seen-mark | The two-mark model (mail window vs read mark) is kept internally. |
| **A13** | **Self-closing jobs.** When every promise in the Contract is met, the job completes and sends the final delivery with its receipt; "Reopen" stays available for 14 days. | Complete | Ready-to-complete detection | Explore jobs end on their kill date with a Verdict delivery. |
| **A14** | **Do-it-for-me, per action type, with consent.** For recommendations that end in a click elsewhere (a transfer, a WordPress publish, a PR merge), the user can grant "do this kind of action for me" once, per job. Default off. | Manual execution of recommendations | Integrations, side-effect journal, idempotency keys, two-phase confirm | Irreversible external actions ask per type, are journaled, and can be revoked at any time. Money and legal actions still ask every time unless pre-authorised under A4. |

### 2.3 What is never automated

These stay human at every setting. They are the clean-sheet product's definition of a Call.

1. **Consent to the Contract**: what is delivered, by when, the ceiling, and what may be done
   without asking.
2. **Money beyond the envelope you set**, and any change to the hard stop.
3. **Irreversible external actions** of a type you have not delegated (publishing, buying,
   sending, merging).
4. **Legal weight** and **human-only steps** (captcha, 2FA, KYC). The platform never defeats a
   security challenge.
5. **Taste**: "is this what I wanted?", offered on deliveries, never demanded.
6. **A broken promise**: the date or quality cannot be met within the ceiling. You choose among
   priced options, with a recommendation.

**Transparency instead of interruption.** Everything automated lands in a weekly **"Decided
for you"** section of the digest: what was decided, why, under which Contract term, and
**Undo** where possible. That keeps Rule 7 of the original assignment ("never hide consequential
information") while removing the interruption.

---

## 3. The clean-sheet product

### 3.1 The mental model

> **You give YAAF a job. It proposes a contract. You say go. Results arrive where you are, each
> with its proof. It interrupts you only to ask for consent, for your taste, or when a promise
> breaks.**

Four nouns, and nothing else on the surface:

| Noun | What it is | Replaces (today) |
|---|---|---|
| **Job** | Something you delegated: a paragraph and its history | Project (+ shape, mode) |
| **Contract** | The promise: what you'll get, when, the hard stop, how it's checked, what it may do without asking | Goal + constitution + presets + autonomy dial + commitment + requirements (as consent) |
| **Delivery** | A result, typed, pushed to you, with a proof label, actions and later a score | Deliverables, briefing items, memo files, evidence views |
| **Call** | A decision only you can make, with a recommendation | Approvals (7 kinds), asks, scope/date/verdict, review levers, overruns, flags (when actionable) |

Everything else — CEO, teams, roles, runs, wakes, requirements, evidence, rules — still exists,
unchanged, **Under the hood**, for people who want to look.

### 3.2 The Contract

The Contract is the one place where the user's intent, YAAF's governance and the money meet. It
is generated from a paragraph and fits on a phone screen.

```
Contract · Morning AI brief                                        Repeats weekdays
──────────────────────────────────────────────────────────────────────────────────
You get        A digest of the 5 developments that matter, ranked, each sourced
When           Weekdays by 07:00 (your time, Toronto), by email
Hard stop      $60/month: it cannot spend past this. Typical: $25–40
Checked        Every source fetched by YAAF · each pick challenged by a skeptic
Without asking May use free sources and spend up to $10/item on articles (≤ $30/mo)
Will ask you   About paid subscriptions, changes of direction
Ends           When you stop it. Track record shown after 5 deliveries
──────────────────────────────────────────────────────────────────────────────────
[Start]  [Try one delivery first: ~$1.50, ~20 min]  [Edit]        Under the hood ▸
```

**How each line compiles to today's engine** (nothing new is enforced; this is a new face on the
same rules):

| Contract line | Compiles to |
|---|---|
| You get (+ type) | Goal; delivery type (§1.2); requirements proposed by the lead; Operate/Deliver/Explore inferred |
| When | `cycle_frequency`/`cycle_day`, `ceo_daily_at`, `timezone`, commitment (Deliver), Explore deadline |
| Hard stop | `monthly_usd_cap` × `hard_stop_multiple` presented as one number; `max_usd_per_run` derived |
| Checked | `require_evidence_verification`, `deliverable_review`, `stakes`, roster with a `challenge` seat |
| Without asking | `approval_threshold_usd`, `max_usd_per_purchase`, `external_monthly_usd_cap`, autonomy dial (CEO side), granted action types (A14) |
| Will ask you | Autonomy dial (chairman side) and undelegable kinds |
| Typical cost | Priced runs on this install × drill plan; shown as a range, never a point estimate |

**"Try one delivery first"** replaces the fire drill as the user-facing pre-flight check. The
drill still runs silently, and its findings become Contract warnings ("nobody checks the
writer"), but the user sees a *real* first result at a small, capped cost, and judges the
format before committing to a schedule. This answers the question the drill cannot: is the
output any good? [Hypothesis, see §8]

### 3.3 The interaction model: three moments

```
   1. ASK                     2. RECEIVE                        3. DECIDE (rarely)
   ─────────                  ──────────                        ─────────────────
   paragraph                  Delivery in your inbox/Slack/     Call: what happened ·
      ↓                       Claude: answer first, actions,    recommendation · options
   Contract (20 s read)       proof label, receipt              with consequences · if you
      ↓                          ↓                              ignore it · undo?
   [Start] or                 👍 / 👎 / "wrong because" →       one tap
   [Try one first]            steering (standing orders)
```

There is no fourth moment called "monitoring". Status is a sentence attached to deliveries and
to the job line; if you want to watch, it's Under the hood.

---

## 4. Surfaces from scratch

### 4.1 Where the user lives: their own channels

| Channel | Carries | Example |
|---|---|---|
| **Email** (default) | Deliveries (full headline + summary + actions in the body); Calls; alarms; one weekly digest with "Decided for you" | §1.4 |
| **Slack** | The same Delivery and Call cards with buttons; talking to the lead | `[Done] [Remind me] [Why?]` |
| **Claude (MCP)** | Natural-language ask, receive, decide (§4.4) | "What did I get this week?" |
| **Phone** | Calls only, as push notifications through email or Slack | "Decision: $29/mo source — recommended no" |

### 4.2 The web app: one page and a drawer

There is no dashboard to supervise. The web app is **Today**, plus a page per job.

```
Today                                                         Fri 24 Oct · Ask YAAF… ⌘K
───────────────────────────────────────────────────────────────────────────────────────
Calls for you (1)
  ◆ PPC audit · Late by 4 days at current pace.
    Recommended: drop "Glossary" → on time, saves $9.     [Drop it] [Move to 3 Nov] [Talk]

New deliveries (3)
  ✓ Morning AI brief — "OpenAI ships …; EU AI Act fines start …"        verified 5/5  07:02
  ✓ Fantasy desk — GW12: Watkins → Isak, captain Haaland · act by Fri 18:30  [Done]
  ◐ PPC audit — Draft findings: $1,840/mo wasted on 3 campaigns     4/5 checked   Thu

Your jobs
  Morning AI brief   on track · 15/15 on time · $27 of $60                 track record ↑
  Fantasy desk       on track · rank 212k → 148k · $41 of $80
  PPC audit          late 4 days (call above) · $148 of $400 hard stop

Decided for you this week (6) ▸            + New job
```

**Job page:**

```
PPC audit                                         Late by 4 days · 1 call · $148 / $400
───────────────────────────────────────────────────────────────────────────────────────
Contract   Audit deck for a non-technical owner · by 30 Oct · stop $400 · figures
           re-derived by a 2nd agent · may buy data ≤ $50          [Change the contract]
Deliveries
  Thu  Draft findings ……………………………… 4/5 checked · [Read] [👍] [👎]
  Mon  Data pull summary ……………………… verified
Track record   n/a until delivered
Ask the lead   ┌──────────────────────────────────────────────────────────┐  [Send]
Under the hood ▸  Plan · Work · Team · Money · Proof · History · Rules
```

"Under the hood" holds today's screens, kept intact for experts: Roadmap, Tasks, Team and
agent drawer, Spend, Reviews, Activity, Rules in force, Role library. They are reference views,
never the path to getting work done.

### 4.3 Creating a job

```
+ New job
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ Audit my Amazon ads every Monday and tell me what to change. Never touch  │
  │ anything. Under $150 a month.                                             │
  └──────────────────────────────────────────────────────────────────────────┘
  📎 Attach files or connect a source (optional)                    [Draft it]

→ Contract (as §3.2), with at most 2 questions YAAF couldn't infer:
    "Who reads the Monday memo — you, or your client?"  [Me] [My client]
    "Your Ads data: connect Amazon Ads, or upload exports weekly?"  [Connect] [Upload]
→ [Try one delivery first]  or  [Start]
```

**Two questions maximum before Start** (a rule for the drafter): everything else is a default
shown on the Contract and editable later. The goal grammar's ten elements are still graded; a
missing element becomes either a Contract default with an "Assumed:" line or one of the two
questions.

### 4.4 Conversation and MCP

**Six intents cover the product:**

| You say | YAAF does | MCP tool (new default set) |
|---|---|---|
| "I want…" / "Change it so…" | Drafts or amends a Contract, preview → confirm | `propose_job`, `change_contract` (two-phase) |
| "How's it going?" / "What did I miss?" | Status sentence per job + deliveries since a time | `jobs(since)` |
| "What do you need from me?" | Open Calls with recommendations | `calls()` |
| "Show me / why / prove it" | The delivery, its proof, the reasoning trail | `delivery(id, depth)` |
| "Yes / no / do it" | Decides a Call (two-phase for irreversible ones) | `decide(call, option)` |
| "Tell the lead…" | Message to the lead; may become a standing order | `tell(job, text)` |

**6 tools instead of 34** by default; the existing 34 remain as an "expert" profile. Every reply
leads with a prose `summary` and labels uncertainty ("4 of 5 verified; 1 is the lead's
judgment"). Agent-written text stays wrapped as untrusted; two-phase confirmation and `via
mcp:` attribution are unchanged.

### 4.5 The weekly digest

```
Subject: Your week: 12 deliveries, 1 call, $96 of $610 · all jobs on track but PPC audit

Needs you (1)    PPC audit is 4 days late → recommended: drop "Glossary"   [Decide]
Delivered (12)   Morning brief ×5 (5/5 on time) · Fantasy memo + review (+7 pts vs roll)
                 · PPC draft findings (4/5 checked)
Track record     Fantasy: memos beat "roll" 4 of 5 weeks · Brief: 3 items you starred
Decided for you  Bought 1 article ($6, within your $10/item) · added a 2nd source for
(6)              EU news · auto-closed weekly review (all green) · …          [Review]
Money            $96 this week · all jobs inside their hard stops
```

---

## 5. Capability preservation map

Every capability in today's product, and where it lives in the clean sheet. **Nothing is
removed from the engine.**

| Capability (today) | Clean sheet | Visibility |
|---|---|---|
| Rules enforced server-side; refusals; invariant attacks | Unchanged | Hidden; surfaced as receipts and proof labels |
| Requirements as root object; plan gate; stages; critical path | Unchanged; consent moves to the Contract | Under the hood → Plan |
| Explore / Deliver / Operate | Inferred from the job; labelled "Find out / One-off / Repeats" on the Contract | Contract label |
| Commitments; platform projection; reset to projection | Due date on the Contract; Call only on a broken promise | Contract + status sentence |
| Bets, kill dates, external-evidence floor | Explore jobs end with a Verdict delivery | Delivery type |
| Operate cycles, last three cycles handed verbatim | Unchanged; each cycle is a delivery with `changed_since_last` | Deliveries |
| Standing review (zero tokens), levers | Auto-closes when green; lever → Call | Digest / Call |
| Autonomy dial (5 switches) | "Without asking / Will ask you" lines | Contract |
| Undelegable approvals (spend, legal, tool, assist) | Calls, unless pre-authorised by you (legal and assist never) | Calls |
| Budget plan + wall; external envelope; per-purchase limit; per-run cap | One hard stop + envelope lines; plan derived | Contract |
| Evidence verification; drought; attestation; webhooks | Proof label on each delivery | Delivery |
| Sampled audit; CEO-judgment disclosure | Second-agent checks + delivery feedback; sampling continues | Proof label; Under the hood |
| Pair breaker, wake brakes, run caps, refusal caps | Unchanged; self-healing with notice | Hidden; "Decided for you" |
| Memory with curator; compaction | Unchanged; preferences you state become standing orders | Under the hood |
| Staffing by the CEO; role library; versions; repin | Unchanged; auto-repin after a passing drill | Under the hood → Team |
| Founder + goal grammar | The drafter behind "Draft it"; grammar graded silently; ≤ 2 questions | Contract |
| Fire drill | Runs silently; findings become Contract warnings; "Try one delivery first" is user-facing | Contract |
| Briefing (zero tokens), agenda | Weekly digest + status sentences; agenda Under the hood | Digest |
| Boardroom chat with the CEO; directives | "Ask the lead" box; Slack; Claude | Job page |
| Deliverables store, versions, hashes | Typed Deliveries; working files beneath | Delivery |
| Receipt (cost vs verified) | Footer on every delivery; track record on the job | Delivery / Job |
| MCP: scopes, attribution, two-phase, untrusted envelope, no secrets | Unchanged; 6-tool default profile | Claude |
| Slack, GitHub, data sources (MCP), S3, email, webhooks | Requested just-in-time | Calls (consent) |
| People: chairman, co-chairs, observers | Owner, co-owners, viewers | Job sharing |
| Multi-provider runtimes, solo mode | Unchanged; "Team: solo" is a Contract detail | Under the hood |
| Clone, export, save-seed | "Duplicate job", "Export" | Job menu |
| CLI | Ops and scripting: `yaaf up`, `doctor`, `jobs`, `calls`, `decide`, plus today's commands | Terminal |

---

## 6. Before and after: the same jobs, start to finish

Interaction counts in this section are **[Inferred]** from the project docs and the §2 scenario.

### 6.1 "Run my fantasy football team"

| Step | Today (Supervised, from the FPL project doc) | Clean sheet |
|---|---|---|
| Create | Import a 700-line project doc or fill a 4-step form: shape, 7 roles, constitution, memory caps, data-source supergateway | "Run my FPL team for the season, aim for top 100k, never make transfers yourself." → Contract → 1 question ("connect your FPL account?") → Start |
| Weekly | Accept the cycle's requirements; memo published as a file (FYI, not emailed); open Deliverables; read it; make the transfer in the FPL app by hand; scored review is another file | Memo arrives Thursday as a Recommendation (§1.4) with [Done]/[Remind me]; optional [Do it for me] if you grant it; review arrives Monday as a score on last week's delivery |
| Interactions / week | ~5–7 | ~1 (tap Done) |

### 6.2 "Audit my PPC account"

| Step | Today | Clean sheet |
|---|---|---|
| Create | Form, data source URL, credential, crew "Numbers you can defend", preset that refuses without a reviewer | Paragraph → Contract with "figures re-derived by a 2nd agent" already in "Checked" → JIT request to connect the Ads source |
| During | Accept 4–6 requirements, sign the date, answer asks, approve data purchase, judge closes, weekly review | One Call if late; data purchase pre-authorised ≤ $50 |
| Result | A 12k-character markdown file on the shelf | An Answer delivery: headline, 3 actions, proof label; deck attached |

### 6.3 "Book our February trip"

| Step | Today | Clean sheet |
|---|---|---|
| Result | A booking desk completes a task; confirmation arrives in your email from the airline; YAAF shows evidence rows | A Transaction delivery: record locator, amount charged, cancellation terms, verified against the airline's confirmation; [Add to calendar] |
| Decisions | Spend approval(s) via Inbox, ~48h-before-expiry email | One Call before purchase: "Book option B, $2,140, refundable until 1 Feb. Recommended. [Book] [Show the other 4]" |

---

## 7. What changes in the engine (and what doesn't)

The clean sheet is mostly a **new object model and new surfaces on the same engine**. Engine
work is limited to:

| Change | Why | Size [Inferred] |
|---|---|---|
| `Delivery` type, `headline`, `summary`, `actions`, `changed_since_last`, platform-computed proof label; refusal on publish without them | §1.3 rules 1–5 | M |
| Push deliveries and Calls on creation, with signed action links | §1.3 rule 6; baseline #1 | M |
| `Contract` as a stored, versioned view compiling to constitution + autonomy + goal | §3.2 | M–L |
| Unified `Call` producer over approvals, asks, scope/date, review levers, overruns | §2.3 | M |
| Precedent proposals and pre-authorised envelopes (per job, per kind) | A4 | M |
| Pace auto-tuning within the hard stop | A5 | M |
| Self-healing actions with an undo log and a "Decided for you" digest | A11–A13 | M |
| Reality scoring hooks per delivery type | §1.3 rule 7 | M–L |
| Do-it-for-me per action type, journaled, two-phase | A14 | L |
| 6-tool MCP profile | §4.4 | S–M |

**What does not change:** the policy engine, refusals, invariants, chaos tests, the dispatcher
and janitor, verification, budgets and walls, attribution, the MCP security model, runtimes and
providers.

---

## 8. Evolve or rebuild? A recommendation and a test

### 8.1 The comparison

| | Evolve (baseline roadmap Phases 2–3) | Clean-sheet shell on the same engine |
|---|---|---|
| User nouns | ~12 | 4 |
| Interactions in the §2 scenario | ~45 | ~8 |
| Results experience | Better labels and a Results page | Typed, pushed, actionable, scored deliveries |
| Engine risk | Low | Low–medium (additive objects; rules unchanged) |
| UI work | Rework 10 pages into 4 plus settings | New Today/Job/Delivery/Contract surfaces; existing pages move Under the hood untouched |
| Time to user value | Gradual | Arrives in a step, after validation |
| Risk | Months polishing an interaction model users may not want | The bet on "invisible org" and "contract" may not land, so validate first |

**Recommendation:**
1. **Ship the baseline's Phase 1 now.** The money copy, approval push, first-hour truth,
   creation traps and one key are defects under any design.
2. **Freeze baseline Phase 2 page-level work** (nav collapse, Settings dials, Overview redesign).
   The clean sheet replaces it.
3. **Run the five-week validation below.** It mostly uses the existing engine plus email.
4. If it passes, build the shell in this order: **Delivery (push + shape + proof) → Calls →
   Contract → Today → automation mechanisms A1–A14 → 6-tool MCP.** Delivery comes first because
   it is the biggest gap and needs no change to how users create jobs.

### 8.2 The five-week validation

| Week | Test | Pass if |
|---|---|---|
| 1 | **Delivery-only pilot.** For 5 users with running projects, hand-compose deliveries in the §1.4 format from real artifacts and email them (Wizard-of-Oz on the existing engine) | ≥ 4/5 prefer them to the shelf; time-to-answer < 30 s; ≥ 50% use an action |
| 2 | **Contract comprehension.** Show 10 people a Contract vs today's review step for the same job | After 20 s, ≥ 80% correctly state cost ceiling, schedule, what it asks; today's baseline measured alongside |
| 3 | **"Try one delivery first"** vs fire drill, on 8 new users | Users who saw a sample delivery adjust the Contract more often and abandon less after the first real week |
| 4 | **Automation trust.** Run 3 projects with A1/A2/A4/A9 simulated by an operator; send "Decided for you" weekly | ≤ 1 undo per user-week; no "I didn't know that happened" incidents; perceived control ≥ 4/5 |
| 5 | **Invisible org.** A/B: Contract with team listed vs "Team: 3 agents incl. a skeptic (details)" | No drop in trust; < 20% open the team details |

**Kill criteria** (in the spirit of `docs/PLAN.md`): if week 1 fails, the results thesis is
wrong and the baseline evolution is the right path. If week 4 fails, keep A1–A14 opt-in and the
Contract becomes a front end to today's Supervised mode.

---

## 9. Anti-goals: what this design refuses to do

- **No hidden money.** One hard stop, always shown, always true. Typical cost is a range,
  labelled as an estimate.
- **No fake certainty.** A proof label never upgrades lead judgment to "verified". Unchecked
  claims say so in the headline when they matter.
- **No silent automation.** Everything automated is in "Decided for you", with Undo where
  possible.
- **No agent-granted authority.** Every automation is a pre-decision the user made, which the
  lead can use but never widen.
- **No lost expert power.** Every current screen, rule, tool and command remains reachable
  Under the hood or in the expert MCP/CLI profiles.

---

# Appendix — evidence

Supporting material for Parts I and II. Everything here was measured on `cbb5bec`
(23 Oct 2026) in a local dev install (SQLite, `YAAF_SANDBOX_MODE=subprocess`, mock runtime) unless
marked **[Code]**. Screenshots referenced below are in [`screens/`](screens/).

---

## Appendix 1. Setup log (dev mode, per `docs/DEPLOYMENT.md`)

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

## Appendix 2. Measured web inventory (desktop 1440×900)

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

## Appendix 3. CLI

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

## Appendix 4. MCP

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

## Appendix 5. Notifications **[Code]** (one brief captured live)

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

## Appendix 6. Docs that disagree with the product

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

## Appendix 7. Screenshot index

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

## Appendix 8. What was not verified

Real model behaviour (CEO, Founder, `grade_goal`); real spend; a month of operation; reviews
with content; deliverables with files and verification; evidence audits; Slack and SMTP
delivery; claude.ai OAuth; Postgres; any real user. Populated screens came from
`evals.harness` + `evals.behaviors` run in-process against the dev database. Two fixture
artifacts appear in screenshots and are not product behaviour: "CEO Working: test" and a "$20,
model unknown, 1 run" ledger row.
