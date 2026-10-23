# YAAF — Product Experience Audit

*An independent review of what it is like to use YAAF, from first encounter to month three.
Analysis only: no product code was changed. Evidence and raw measurements are in
[APPENDIX.md](APPENDIX.md).*

**Audited:** `main` at `cbb5bec` (23 Oct 2026) · **Surfaces:** README and docs, web app, CLI, MCP
server, email/webhook notifications, Founder, fire drill · **Method:** the docs; the
implementation where the docs and the product could disagree; and the product run locally in dev
mode on SQLite (web via Playwright at 1440×900 and 390×844, CLI, and MCP over raw JSON-RPC).

**How to read the evidence labels**

| Label | Means |
|---|---|
| **[Measured]** | Counted or observed directly in the running product or its code. |
| **[Code]** | Read from the implementation, not exercised live. |
| **[Inferred]** | A reasoned conclusion from measured/code facts; not observed with a user. |
| **[Hypothesis]** | A design bet that needs the validation in §K before it is trusted. |

**Limits.** There was no real model key. Agents ran on the mock runtime, which in a fresh
install "reads its context and exits", and populated screens came from scripted fixtures.
Nothing here observes a real CEO's writing quality, a real month of operation, the claude.ai
OAuth connection, SMTP or Slack delivery, or a single real user. Every "user" in this
document is a constructed persona.

---

## A. Executive assessment

**Score: 4.5 / 10 for the target user** (busy, technical, wants outcomes, will not study the
product). The governance engine is stronger than that score. The experience around it is weaker.

| | Verdict |
|---|---|
| **How good is it today?** | A rare, honest, deeply engineered product that **makes the user do the engineering**. To get value you have to understand about 55 internal concepts, 62 rules, 10 project pages, 34 MCP tools and 75 CLI commands. Almost every screen explains itself in paragraphs instead of just being clear. |
| **Greatest strength** | **Integrity you can feel.** Rules are enforced server-side. "Done" means the platform checked it. Budgets stop by themselves. Every unanswered decision says what silence will do. The briefing is composed at zero tokens. The fire drill counts how often a project will stop and wait for you *before* you spend anything. No competitor I know of states consequences this honestly. |
| **Greatest UX weakness** | **The product's architecture is the user's mental model.** You are asked to be the "chairman" of an org with a CEO, teams, seats, a constitution, amendments, commitments, cycles, bets, droughts, levers and verdicts. The real job — *"get me this outcome, ask me only what you must"* — is buried under governance vocabulary and self-justifying prose. |
| **Three biggest opportunities** | **1. One decision queue that comes to you.** Merge nine kinds of interruption into one Decision card with a recommendation, and push it the moment it is filed. Today a spend approval sends nothing until ~48h before it auto-denies [Code]. **2. Outcome-first status.** One sentence per project (on track / at risk / waiting on you / stopped, plus why and what happens next), the same on every surface, with 10 project pages folded into Status · Chat · Results · Details. **3. Ten minutes to first value.** One key, one command, one paragraph, one forecast card, then a result — instead of Docker, an SSH tunnel, two separate keys, a token mint and a four-step form full of presets. |
| **What must fundamentally change** | Move from **"you chair an organisation"** to **"you own an outcome."** The org chart, the constitution and the run machinery should become guarantees YAAF *shows* (receipts, checks, limits), not a system the user operates. Keep every rule; stop making people read them to get started. |

### Scorecard summary (detail in §J)

| Dimension | Now | Target |
|---|---|---|
| First-time usability | 2.5 | 8 |
| Clarity | 4 | 8.5 |
| Simplicity | 2.5 | 7.5 |
| Decision-making | 5.5 | 9 |
| Outcome visibility | 5 | 8.5 |
| Trust & confidence | 6.5 | 9 |
| Cross-surface consistency | 4.5 | 8.5 |

### The five most important findings

1. **P0 — The most consequential requests are the quietest.** When an agent files a spend, legal,
   tool or owner approval (`POST /agent/approvals`), no notification is sent
   (`yaaf/server/api/agent.py:2273`) [Code]. The first email arrives from `nag_approvals` 48h
   before the default 7-day auto-deny (`yaaf/server/janitor.py:1895`). It points to "the
   Decisions screen", which is a history view with no decide buttons, and it carries no link.
   The daily brief that might have caught it is **off by default** (`brief_daily_at: none`). A
   user who does not open the app finds out about a spend request ~5 days late, and silence
   means "no".
2. **P0 — The first tutorial contradicts the product.** `examples/01-your-first-hour.md` says
   *"Leave everything else. Supervised"* and *"Minute 1 — your only real job… Nothing can be
   created until you do."* But the example file names no authority rules, so
   `default_autonomy()` creates it **Autonomous** (`yaaf/core/constitution.py:720`) and the CEO
   accepts its own plan [Code]. The lesson the first hour exists to teach does not happen. The
   same page says "Paste your Anthropic key" (the form no longer takes a key) and points at
   "Plan" and "Work" tabs that no longer exist.
3. **P0 — Two keys before anything happens.** Projects pay with an "LLM credential". The
   Founder, the recommended way to create a project, needs a *separate* "Founder model
   credential", "deliberately not one of the LLM credentials above"
   (`docs/DASHBOARD.md`). Without one, `grade_goal` over MCP returns `graded: false`
   [Measured]. A first-time user with one Anthropic key enters it twice, in two places, under
   two names.
4. **P1 — Interruptions come in nine shapes.** Approvals (7 kinds), asks, flags, heads-ups,
   scope to accept, a date to sign, verdicts to judge, review levers and audit samples each
   have their own object, verbs, MCP tool or view, and clock [Code]. The Inbox unifies the
   *list*, not the *decision*: only Asks carry a structured recommendation and options.
   Approvals carry free text plus "What happens without it".
5. **P1 — The interface explains instead of simplifying.** Help paragraphs per screen: New
   project 48, project Settings 49, Overview 24, Decisions 24, Plan 21
   (`className="help"` count) [Measured]. That is essentially what the earlier UX study
   measured before the redesign (Overview 24, Settings 55). The IA moved; the reading load
   did not. Docs and in-product copy keep narrating the product's history ("used to…",
   "before this rule existed…"), which is useful to contributors and noise to users.

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
>   Checked    every source link fetched and saved by YAAF, not the agents
>   Cost       ~$1.40/day (est. $31/mo) · hard stop at $60
>   Asks you   about once a week (new paid source, or a change of direction)
>   First one  in ~20 minutes, then tomorrow 07:00
>   [Start]  [Change something]  [Show the team and rules]
> ```
>
> I pressed Start. Twenty minutes later the first brief was in my inbox. Every claim had a
> source chip; two said *"source verified by YAAF"* and one said *"couldn't verify — paywalled"*.
>
> **Thursday.** One notification: *"Decision needed: add The Information ($29/mo)? Recommended:
> no — 4 of last week's 5 findings were covered by free sources. If you ignore this: no
> purchase. [Approve] [Decline]."* I declined from my phone in four seconds.
>
> **Three weeks later**, back from a trip, I asked: *"What did I miss?"* — *"15 briefs
> delivered, all on time. One source died and was replaced automatically. Spend $27 of $60. One
> decision expired while you were away (a paid source); it defaulted to no. Nothing needs you."*
>
> I never saw the words constitution, seat, drought or cycle. When I wanted to know *why* a
> finding was in the brief, "Show the evidence" gave me the fetched page, the agent who found
> it and the agent who challenged it.

**The principles behind that story**

1. **Outcomes first, machinery on request.** The first screen of anything is *what will exist,
   by when, at what cost, how sure*. The org is one click down.
2. **Silence is safe, and you are told what silence does.** (YAAF already gets this right.)
3. **The product comes to you.** Decisions are pushed to where you are, with the recommendation
   and the consequences on the card, and can be acted on there.
4. **One status, one decision shape, one vocabulary**, rendered by web, email, Slack, MCP and
   CLI from the same producer.
5. **Proof is a label on the result, not a page you visit.** Every output says how it was
   checked: verified by YAAF, judged by the lead, or unchecked.
6. **Guarantees are shown, not configured.** "Hard stop at $60", "nothing is marked done
   unless checked" and "asks you about once a week" are promises on a card. The 62 rules that
   enforce them live under Advanced.

---

## C. User journey audit

Step counts are **[Measured]** in the dev install unless marked. Full logs are in the Appendix.

### C1. Discovery → first value

| Stage | Today | Friction | Ideal |
|---|---|---|---|
| Read the README | 299 lines. The value proposition is strong and concrete (the "ask → who gets hired" table is excellent). Then "chairman", "CEO", "desks", "constitution", "Explore/Deliver/Operate", 4 MCP scopes and 4 rules come before any install step. | The 30-second test half passes: "delegate a multi-week job with governance" is clear. "Should I use this?" takes ~5 minutes because the honest caveats ("What you give up") sit at the bottom. | Lead with one sentence, one screenshot of a *result*, a 3-line quick start, then the trade-offs table near the top. |
| Try without paying | `yaaf rehearse examples/01…` works from a checkout in 7s [Measured]. Its 64-line report has the walk, cost and gaps, but opens with 2–5 lines of log noise and labels a Deliver step "not in an Explore project's shape" (`yaaf/rehearsal/report.py:606`). | It needs a Python venv and checkout first. Its cost line is generic ("fits your cap if a run costs under $1.67") because the mock crew is scripted: two different examples gave the same "3 requirements, 12 runs". | A forecast that is honest about being a forecast: a cost *range* from real priced runs, time to first result, interruptions per week. |
| Install | **Docker install → group change → log out/in → clone → deploy script → SSH tunnel → open one-time link → set password**, then the CLI on your laptop (`yaaf login`, `mcp-token mint`, `claude mcp add`). That is about 10 commands and 2 terminals before the UI. Dev mode is 11 commands and **fails silently on any port but 8080**: the runner and the bootstrap link hard-default to `:8080`, and `YAAF_API_BASE_URL` is not documented [Measured]. | Infrastructure work before value. The SSH tunnel is a sound security default but a hard first-run step. | `yaaf up` (or a hosted trial) that ends with the browser open at "Paste one key → describe your goal". |
| Keys | LLM credential (Settings) **and** Founder credential (Settings) are separate. | Two entries of the same key for most users. | One key. The Founder uses the first credential unless told otherwise. |
| Create (Founder) | Fork screen → describe → Founder rounds → "Review & create" → 4-step form, prefilled → fire drill → Create. | See C2. | Paragraph → one proposal card → Start. |
| First result | In the Autonomous default, minutes. In Supervised, after you accept requirements and sign the date. | You do not know which you are in unless you read the header badge. | The proposal card says "First result in ~20 min". |

**Estimated time to first value (TTFV) today:** 45–90 minutes for a technical user on a fresh
server, including Docker and SSH, plus the run time of the first deliverable [Inferred]. The
target should be **under 15 minutes**.

### C2. Project creation

| | Form path ("Fill it in myself") | Founder path |
|---|---|---|
| Decisions put to the user | Name; goal; **shape** (frozen); CEO role; teams (14 crews or 32 roles in 6 aisles); LLM credential; **3 preset families** (autonomy ×2, tempo ×3, bar ×4); constitution fields (editable); controlled domains; chairman email; timezone; reporting cadence; service credentials; data sources; start now? | A paragraph, then the Founder's questions. Its doctrine defines a 14-node design tree (5 roots, 5 mid, 4 leaves) [Code]. |
| Concepts you must understand to choose well | Explore/Deliver/Operate, requirement, plan gate, commitment, autonomy verbs, tempo (runs/day), stakes, deliverable review, external evidence, controlled domains | Fewer up front; all of them reappear on the form you land on. |
| Explanatory copy | 48 help blocks on NewProject [Measured]. | The doctrine plus grammar is **~6,500 words** a model client is told to read [Measured]. |

**What is right:** both paths end on the same review; nothing is created until you sign; the
form's "All on one page" toggle; a fire drill before money; presets that fill visible rules;
the Founder telling you what it assumed.

**What is wrong**
- **Shape is a frozen, up-front, architecture-level choice.** A user knows "weekly brief",
  "one audit" or "find an idea". YAAF can infer Operate, Deliver or Explore and show it as
  a label with a reason.
- **Three preset families plus an autonomy dial make four tuning questions before the
  first result.** Most people cannot answer "Live vs Working hours vs A few times a day"
  or "Full send vs Balanced vs Keep it cheap vs Show me proof" without seeing a cost
  forecast next to each one.
- **Choosing a crew is choosing an implementation.** The README's own best example
  ("Book our February trip — I want a confirmation number") is an *outcome*. The form asks
  you to hire roles.
- **The fire drill reports the configuration, not the project.** It says so honestly ("a
  clean drill is evidence about your configuration and no evidence at all about your
  project"), but it is presented as the pre-flight check, so users will read it as the
  latter.

### C3. Day-to-day operation

A project shows itself through **10 project pages, 4 Inbox tabs, 6 Activity views, 6
Settings rule tabs and an agent drawer with 4 tabs** [Code: `web/src/nav.ts`, DASHBOARD.md].
The Overview alone has 4 KPI tiles, Needs-you, Team-right-now, Roadmap, From-the-CEO, Coming-up
(with rhythms and events), Money-by-team, Latest deliverables, Watching and Setup & health.

The user's real questions are three: *Is it OK? Does it need me? What has it produced?* The
answers exist but are spread across Overview tiles, the Inbox (Needs you / Updates / Watching),
Deliverables and Activity → Proof. "Watching" ("clocks the CEO resolves") is a whole tab and an
Overview section for things the user is explicitly told need no action.

### C4. Decisions

| Interaction | Object / verb | Has a recommendation? | Says what silence does? | Pushed when filed? |
|---|---|---|---|---|
| Spend / legal / tool / owner approval | `Approval` + `approval_decide` | Free text only | Yes ("What happens without it", required) | **No**: nag 48h before expiry [Code] |
| Capacity request | `Approval(kind=capacity)` | Implicit | Yes | Yes (ACTION email) |
| Human assist (captcha/2FA) | `Approval(kind=assist)` | n/a | Yes | Yes |
| Brief change | `Approval(kind=brief)` | Diff shown | Yes | Yes |
| CEO question | `Ask` (answer in chat) | **Yes: options + recommendation** | Yes (default action) | Yes |
| Heads-up | `HeadsUp` + `heads_up_ack` | n/a | n/a | Yes |
| Flag | `Flag` + `flag_ack` ("Noted") | No | No | Varies |
| Scope / date / verdict | `requirement_decide`, `commitment_decide` | Partial | Yes | Yes, but supervised only |
| Review | `review_decide` (6 levers) | CEO narrative | Re-nagged | Yes |
| Audit sample | `audit_decide` | No (by design) | Expires "not reviewed" | FYI only |

**The core design is right.** Ranking by time to consequence, the silence default,
default-deny, "only what only you can do", the mandatory `if_declined`, and asks that must
state *why* and may carry a recommendation are all better than Linear's Triage or Stripe's
review queue on the dimension that matters most here: the cost of not deciding.

**What is missing** is one card shape, a mandatory recommendation on every approval,
reversibility stated on the card, and delivery to where the user is.

### C5–C10 are covered by the scenarios in §C-S below.

### C-S. Ten scenarios

Counts are steps / screens / context switches. "Today" counts are **[Measured]** in the dev
install where possible and **[Inferred]** from the implementation otherwise.

| # | Scenario | What the user wants | Today | Where it hurts | Ideal | Close the gap with |
|---|---|---|---|---|---|---|
| 1 | First-time install + first project | A result | ~10 shell commands + 2 key entries + 4-step form or Founder + drill + Create. 3 surfaces (terminal, browser, Claude). TTFV 45–90 min | Docker/SSH, two keys, shape/preset choices, stale tutorial | 1 command, 1 key, 1 paragraph, 1 card, Start. TTFV < 15 min | #3, #4, #7, #12 |
| 2 | Complex multi-team project with deadline and budget | Confidence the date and cost are real | Form: crew/roles, tempo + bar + autonomy, constitution, controlled domains; drill; Supervised to accept requirements and sign date | Must translate the deadline into tempo/run caps; drill cost not tied to the spec | Proposal card with date *range*, cost *range*, the levers, and "what I'll ask you" | #7, #8 |
| 3 | Check 5 projects in 60 s | Who's in trouble? | Home: needs-you sentence + project cards (status, runs, spend vs cap, needs-you count). No single "on track / at risk" verdict per project; delivery risk lives on each Overview | 5 clicks to compare delivery risk | One row per project: verdict sentence, due vs projected, spend pace, waste flag, needs-you | #5, #18 |
| 4 | Unexpected approval | Decide right in < 30 s | Not told when filed (generic approvals). Later: Inbox row → right pane with body + "What happens without it" + Approve/Deny | Late, no recommendation field, email says the wrong screen and has no link | Pushed now; card with recommendation, options, silence, reversibility; one tap | #1, #2 |
| 5 | Behind schedule / over budget | Two honest options and their cost | Banner "projection lands N days past promise" with Cut scope / Move date; Spend page; review levers (6) | Levers are named but not priced: what does cutting R-03 buy? | "Behind by 4 days. Cut *Glossary* → on time, −$9. Or move to 3 Nov. Recommended: cut." | #2, #5 |
| 6 | Is the work really correct? | Evidence, fast | Deliverables (files) and Activity → Proof (claims) are separate; audit samples under Tasks | Proof isn't on the result | Verification badge on each deliverable; "Show the evidence" opens checks inline | #13 |
| 7 | Change the goal mid-flight | Redirect without breaking things | Chat to CEO → CEO files `brief_propose` → BRIEF approval (diff) → confirm. Or edit Brief in Settings. Two doors, different effects | Settings edit bypasses the CEO's impact analysis | One verb: "Change direction" → diff + impact on date/cost/scope → confirm | #14 |
| 8 | Back after 5 days | What happened, what did I miss? | Inbox → Updates (per-project briefing since "last looked"); Done tab (7 days); approvals may have auto-denied | Briefing is per project; expired decisions listed but no "undo/redo"; MCP briefing has no window arg | Home: "While you were away" — top changes, expired decisions + re-open, results | #19, #11 |
| 9 | Project fails and needs recovery | What broke, how to fix it | Auto-pause alarm email (good: always sent). Health card rows with fix links. Run traces in Activity → Runs | Diagnosis is spread across health, runs, flags; no "fix it" verb | Recovery card: cause in one sentence, the fix, cost to resume, [Resume] | #15 |
| 10 | Run YAAF from Claude only | Ask in plain English | 34 tools, ~7.5k tokens of tool descriptions before the first call. "How are my projects doing?" = 1+N calls; "what needs me?" = 1+2N for completeness; "what changed since yesterday?" cannot be asked (no window arg) | Low-level JSON; jargon (`drought`, `queue_ceiling`, `ration`, `beat`) | `status`, `attention`, `changes(since)`, `explain` returning a prose `summary` plus data | #10 |

---

## D. Information and interaction audit

### D1. Concept inventory: what a user meets today

About **55 named concepts** reach the user [Code/docs]. Grouped:

| Group | Concepts |
|---|---|
| People & org | chairman, co-chair, observer, CEO, team, lead, seat, role, role version, repin, crew, aisle, stand down, recall, hire |
| Shape & plan | Explore, Deliver, Operate, requirement, stage, dependency, critical path, acceptance test, observable, plan gate, R-00, scope, cycle, bet/hypothesis, kill date |
| Work | task, subtask, run, wake, heartbeat, board, claim, refusal, pair breaker / frozen thread, directive |
| Truth | evidence, proof, attested, verified, drought, external ratio, controlled domains, spot-check / audit sample, verdict, ceo_judgment |
| Time & money | commitment, projection, promise, budget (plan) vs wall, hard_stop_multiple, external envelope, tempo, stakes, the bar |
| Governance | constitution, rule, amendment, autonomy dial (5 switches), presets (3 families), review, lever, flag, ask, heads-up, approval (7 kinds), standing order |
| Plumbing | LLM credential, Founder credential, service credential, data source (MCP), harness, provider, runtime, memory (scopes, compaction) |

**Proposal.** The *primary* experience uses about **12** words: **project, goal, result,
check, decision, lead (CEO), team, budget, due date, plan, rules, history**. Everything else
appears only in Details or Advanced, with a one-line definition on hover.

### D2. Surface-by-surface recommendations

| Surface | Today | Value | Recommendation |
|---|---|---|---|
| **Home** | Needs-you sentence, first 5 decisions, project cards, "While you were away" | High | **Simplify**: one verdict row per project; "while you were away" leads after an absence |
| **Inbox: Needs you** | Ranked by time to consequence, one rule | Very high | **Keep + Redesign the card** (§G3) |
| **Inbox: Updates** | Per-project briefing + CEO flags | Medium | **Combine** into Home's "while you were away" and per-project History |
| **Inbox: Watching** | Clocks the CEO resolves | Low for the user | **Hide**: show only when a clock is within 24h of becoming a decision, as a "may need you soon" line |
| **Inbox: Done** | Decided last 7 days | Medium | **Keep**, add "re-open" where reversible |
| **Projects** | Portfolio table | Medium | **Combine** with Home (same rows) |
| **Overview** | 4 tiles + ~10 sections | High but overloaded | **Redesign** as Status (§G2) |
| **Chat** | Resident CEO chat, starters, open asks beside | Very high | **Keep**; make decision cards render inline |
| **Roadmap** | Commitment banner, numbers, burn-up, stages/graph, per-row verbs | High for Deliver, needed occasionally | **Hide** under Details → Plan; surface its verdict on Status |
| **Tasks** | Read-only board + audit samples + list & bets | Low for the user (read-only by design) | **Hide** under Details → Work; move audit samples into Decisions |
| **Deliverables** | Files by day, reader, published/working | Very high | **Promote** to "Results", with a verification badge per item |
| **Team** | Org chart, drawer (Now/Work/Runs/About) | Medium (curiosity, debugging) | **Hide** under Details → Team |
| **Spend** | Budget, today, by team, 30-day detail | Medium | **Combine**: the headline on Status, the rest in Details → Money |
| **Reviews** | Standing meeting, 6 levers | Medium (weekly) | **Combine** into Decisions: an open review *is* a decision card with levers as options |
| **Activity** | What happened / Runs / Decisions / Proof / Messages / Memory | Low daily, high forensic | **Hide** under Details → History; Proof also inline on Results |
| **Settings (project)** | Brief, Money, Data, Integrations, 62 rules in 6 tabs, Duplicate, Advanced | Necessary, rarely | **Simplify** to 3 dials + "What I must ask you" + Connections; rules → Advanced |
| **Settings (app)** | LLM credentials (rich lifecycle), Founder credential, Slack | Necessary once | **Combine** the Founder credential into LLM credentials |
| **Role library** | 38 roles, versions, blast radius, CSV | Power users | **Hide** from the primary nav (Workspace → Roles) |
| **New project** | Fork, Founder, 4-step form, drill | Critical | **Redesign** (§G1) |

### D3. Proposed project navigation (10 → 4)

```
Today (10)                                         Proposed (4)
Overview · Chat · Roadmap · Tasks · Deliverables   Status   ← verdict, decisions, latest results, ask box
Team · Spend · Reviews · Activity · Settings       Chat     ← the lead; decision cards inline
                                                   Results  ← deliverables + how each was checked
                                                   Details ▸ Plan · Work · Team · Money · History · Rules
```

---

## E. Top 20 UX improvements (ranked by expected impact)

Effort: S < 1 week · M 1–3 weeks · L > 3 weeks (one engineer familiar with the code)
[Inferred].

### 1. Push every decision the moment it exists, with a deep link and one-tap action — **P0 · M**
- **Problem.** Generic approvals are silent when filed. The first email comes ~48h before
  auto-deny, says "Decide it on the Decisions screen" and has no link. The daily brief is off by
  default. Emails never carry links ("Plan tab", "Decisions screen") [Code].
- **Why it matters.** Spend, legal and tool approvals are the undelegable decisions, the ones
  the product promises always reach you. Silence = deny, so a late notice is a silent veto.
- **Solution.** Send an ACTION notice on filing for every approval kind (respecting
  `mail_max_per_day` but with approvals ranked first). Every notice carries a signed deep link
  to the Inbox item; Slack gets interactive buttons that open a confirm page. Fix the copy to
  name the Inbox. Default `brief_daily_at` to 08:00 local when mail is configured.
- **Example.** `Decision needed (5d left): Buy the Semrush API, $129/mo. Recommended: approve.
  If you ignore it: denied on 30 Oct, and the keyword study uses free data. [Open]`
- **Benefit.** Removes the largest silent-failure path in the product. **Risk.** More email:
  mitigate with the cap and the batching already in `notify.py`.

### 2. One Decision card for all nine interruption types — **P0 · M**
- **Problem.** Approvals (7 kinds), asks, flags, heads-ups, scope, date, verdict, review and
  audit sample differ in shape, verbs and clocks. Only Asks carry `options` +
  `recommendation`. `Approval.disclosure` (change set, effect, reversibility, case against)
  exists in the model but is optional [Code].
- **Why.** Time to decision depends on not having to reconstruct the situation.
- **Solution.** One render contract everywhere: **What happened · Why it matters ·
  Recommendation · Options (each with its consequence) · If you do nothing · Can I undo? ·
  Evidence ▸**. Make `recommendation` and `reversible` required on approvals. The agent
  already has to write `if_declined`, so this is two more fields. Flags and heads-ups become
  "FYI" cards with a single "Got it".
- **Example.** See §H1.
- **Benefit.** Faster, more correct decisions; one keyboard and one muscle memory. **Risk.**
  Agents may write weak recommendations: grade them in evals as `if_declined` is today.

### 3. Make the first hour true — **P0 · S**
- **Problem.** Example 01 teaches Supervised but runs Autonomous. "Paste your Anthropic key",
  "Plan tab", "Work tab", "Boardroom" are stale (8 occurrences in examples/docs). `yaaf
  mcp-stdio` is documented but does not exist. 18 CLI commands have no help text. Rule counts
  disagree (code 62; docs say 52 and 60). The drill prints "not in an Explore project's shape"
  for Deliver projects [Measured/Code].
- **Solution.** Put `plan_authority: chairman` (or the Supervised preset) into example 01's
  spec, or rewrite the page for Autonomous. Run a "docs truth" pass, and add a CI check that
  greps docs for retired screen names, as the repo already does for its own invariants.
- **Benefit.** The first experience stops contradicting itself. **Risk.** None.

### 4. One key to start — **P0 · S**
- **Problem.** The Founder needs its own credential, separate from the ones projects pay with.
- **Solution.** On first credential creation, offer "Also use this key for drafting projects
  (recommended)". Keep the separation as an advanced option for people who want the Founder
  billed elsewhere.
- **Benefit.** Removes a whole setup step and the most confusing concept on day one. **Risk.**
  Founder usage is billed to that key: state it on the card.

### 5. One status sentence per project, everywhere — **P1 · M**
- **Problem.** Health is spread across 4 tiles, a delivery banner, Setup & health, Watching
  and flags. The MCP `desk` has no per-project headline; only the briefing has one
  [Measured].
- **Solution.** A server-side `status` producer returning
  `{verdict: on_track|at_risk|needs_you|blocked|paused|done, sentence, next, money, confidence}`,
  shared by the Home row, Status page, email subject, Slack topic, MCP `status` and
  `yaaf status`. YAAF already holds a single producer behind several surfaces (attribution);
  apply that to status.
- **Example.** `At risk — projected 3 Nov, promised 30 Oct. Cut "Glossary" to make it. $148
  of $400. 1 decision waiting.`

### 6. Collapse the project from 10 pages to Status · Chat · Results · Details — **P1 · L**
- See D3 and §G2. Keep every view, but under Details. Keyboard shortcuts keep working
  (`g k` → Details → Work).
- **Risk.** Power users lose one click. Mitigate with remembered Details tab and ⌘K.

### 7. Creation = paragraph → proposal card → Start — **P1 · L**
- **Problem.** Four steps, frozen shape, 3 preset families + autonomy, crews/roles, 48 help
  blocks; or a Founder that hands off to the same form.
- **Solution.** The Founder (or MCP client) returns one **proposal card** (§G1): what will
  exist, by when, cost range, how it's checked, how often it will ask you, and the team in one
  line. Shape is inferred and shown as a label. "Change something" opens the form for experts.
  The drill runs automatically and feeds the card's "asks you ~N/week" and "fits budget" lines.
- **Risk.** Inferred shape may be wrong: show it with its reason and let the user flip it
  *before* Start.

### 8. Settings: three dials and a promise list — **P1 · M**
- **Problem.** 62 rules in 6 tabs, each with help text; 3 preset families; the autonomy dial
  as 5 switches whose stored defaults (`chairman`) contradict the creation default
  (`autonomous`) in GOVERNANCE.md.
- **Solution.** The primary Settings shows **Money** (monthly budget, hard stop, purchase
  limit), **Pace** (how often it works, with $/day), **Say-so** (what it must ask you:
  spending over $X, scope, date, judging uncheckable work) and the preset each one is on. Raw
  rules move to Advanced, unchanged, still amended with a reason.

### 9. Replace explanatory paragraphs with labels and a "Why?" — **P1 · M**
- **Problem.** 24–49 help blocks per main screen; docs and UI justify past decisions.
- **Solution.** House style: one line per element. Rationale moves to a "Why?" disclosure or
  the docs. Remove history narration ("used to…") from user-facing copy and keep it in
  commit messages. Budget it: a lint that counts `className="help"` per page.

### 10. Outcome-level MCP tools and smaller tool payload — **P1 · M**
- **Problem.** 34 tools, 1,742 description words, ~30k chars on `tools/list` [Measured].
  Natural questions take 1+N or 1+2N calls; "since yesterday" is impossible; responses are raw
  JSON with internal field names.
- **Solution.** Add `status` (portfolio verdicts), `attention` (everything that needs you,
  including flags, heads-ups and open reviews), `changes(since)` and `explain(project,
  question)`. Every response leads with a 1–3 sentence `summary`. Offer a "chairman-lite"
  tool profile (≈8 tools) as the default for the `read` and `steer` scopes.

### 11. A better daily brief, on by default — **P1 · S**
- **Problem.** Off by default. Headlines like "You changed 1 rule (brief_daily_at)" and "the
  window you asked for" are self-referential [Measured].
- **Solution.** Default on at 08:00 when mail is configured. One email across all projects,
  ordered: decisions → at-risk → results delivered → spend → nothing else. Subject = the most
  important line.

### 12. One-command start and a doctor — **P1 · M**
- **Problem.** The README path needs Docker, a re-login, a clone, a script and an SSH tunnel.
  Dev mode silently fails off-port. A refused connection prints a 224-line traceback
  [Measured].
- **Solution.** `yaaf up` prints one URL and opens it. `yaaf doctor` checks API reachability,
  runner, master key, credential and SMTP, and prints the fix for each failure. Friendly errors
  for connection refused and wrong base URL.

### 13. Verification as a label on every result — **P1 · M**
- **Problem.** Deliverables show published/working, version and hash. Whether the *claims*
  were checked lives in Activity → Proof.
- **Solution.** Each result shows **Verified by YAAF** (test ran / source fetched) · **Judged
  by the lead** (ceo_judgment) · **Reviewed by a second agent** · **Not checked**, with
  counts, and "Show the evidence" inline. Never show "verified" for self-judged work.

### 14. "Change direction" as one verb — **P1 · M**
- **Problem.** Changing the goal can happen in Chat (CEO drafts a BRIEF approval with a
  diff) or by editing Settings → Brief directly, with different consequences.
- **Solution.** One button and one chat intent. The CEO returns the diff *plus impact*
  (requirements added/cut, date and cost delta) as a Decision card. Settings → Brief becomes
  read-only with that button.

### 15. Recovery cards — **P1 · M**
- **Problem.** Alarms (auto-pause, stall, hard stop, dead credential) are emailed, which is
  good, but the fix requires reading health rows, run traces and settings.
- **Solution.** Every alarm produces a recovery card: the cause in one sentence, the fix, the
  cost to resume and a [Resume]/[Fix credential]/[Raise cap to $X] action. Same card in
  email, Status and MCP.

### 16. Vocabulary cut and rename — **P2 · M**
- chairman → **you / owner**; constitution → **rules**; amendment → **rule change**;
  commitment → **due date**; drought → **"no outside proof for 24h"**; requirement →
  **"must be true"** (in plan) / **check**; seat → **agent**; stand down → **pause team**;
  Explore/Deliver/Operate → **Find out / Deliver once / Repeat** (labels, inferred). Keep the
  internal names in the API.

### 17. CLI that matches the product — **P2 · S–M**
- Grouped help (Projects · Decide · Inspect · Setup · Admin). Help text on all 75 commands.
  One error printer with hints (today 62 commands print raw `{"detail": …}`). `yaaf status`
  with prose by default and `--json` for scripts. `create-project` accepts the example bundles
  (today they fail with a 175-line pydantic dump) [Measured].

### 18. A 60-second portfolio — **P2 · S**
- Home rows sorted by risk: verdict sentence, due vs projected, spend pace (on/over), cost per
  verified result, needs-you. Hide healthy rows behind "4 on track".

### 19. "While you were away" that can undo — **P2 · M**
- After more than 24h of absence, Home leads with: results delivered, decisions that expired
  and what they defaulted to (with **Re-open** where the action is still possible), spend and
  any alarms. Same in MCP `changes(since)`.

### 20. Activity vs achievement metric — **P2 · M**
- **Problem.** "Runs" and "spend" are visible; "verified outcomes per dollar" is only in
  `receipt` and plan velocity (`noop_share`, dead runs).
- **Solution.** Show **cost per verified result** and a "busy but not progressing" flag
  (spend rising, nothing verified in N days) on Status and Home. It directly answers "which
  projects are wasting money?"

---

## F. Five bold product changes

### F1. Make the org invisible by default ("Goal in, results out")
**Change.** Users never pick roles, crews, teams or a CEO. They state an outcome. YAAF staffs,
re-staffs and explains the team on request ("Who's working on this?"). The role library
becomes an expert tool.
- **Benefits.** Removes the hardest and least informed decision in creation. Matches the
  README's own best pitch ("I want a confirmation number").
- **Downsides / risks.** The org is part of YAAF's identity and of its open question (org vs
  solo). Hidden adversarial seats (risk, red team) could look like padding when costs are
  shown.
- **Mitigation.** Show *guarantees*, not roles: "Every number is re-derived by a second agent."
- **Validate.** A/B the creation flow (roles visible vs hidden) on task success, perceived
  control and the rate of post-creation roster edits. If few users change rosters, hiding them
  is safe.

### F2. A decision budget the system must respect
**Change.** The user sets "ask me at most ~N times a week". YAAF already counts how often a
project would stop for you (the drill). Make that a live, enforced metric: interruptions per
week are shown, batched and, when over budget, the CEO must bundle or justify.
- **Benefits.** Turns "manage by exception" into a contract. **Risks.** Batching delays urgent
  decisions. Undelegable kinds (spend, legal, assist) are exempt from the cap but still counted.
- **Validate.** Track interruptions/week, decision latency and satisfaction across autonomy
  settings.

### F3. One status protocol, many renderers
**Change.** Define two objects, **Status** and **Decision**, as the product's public
contract. Web, email, Slack, MCP and CLI are thin renderers of the same objects; no surface
composes its own summary.
- **Benefits.** It ends drift (today: Home vs Overview vs Inbox vs desk vs briefing). MCP
  answers become short and consistent. **Risks.** Refactoring cost; dense power views must
  still exist under Details.
- **Validate.** Compare answers across surfaces for 20 scripted states; target zero
  disagreements.

### F4. Replace the constitution-as-UI with promises
**Change.** Creation and Settings speak in **promises**: "Hard stop at $60/month", "Never
marks work done unless checked", "Asks before any purchase over $50", "Delivers by 30 Oct or
tells you 3+ days ahead". Each promise compiles to the existing rules, which are shown under
Advanced.
- **Benefits.** Users configure what they care about; the rules stay intact and enforced.
  **Risks.** Some rules have no natural promise (memory caps, wake brakes); leave them at the
  defaults and out of sight.
- **Validate.** Comprehension test: can users say what a project will and won't do after 30s
  on the promises card vs the rules tab?

### F5. Zero-install trial on a recorded real project
**Change.** A hosted (or `docker run`) read-only demo that replays a real month of a YAAF
project in five minutes: the plan, a decision, a slipped date and its cut, a verified result.
Then "Start your own" with one key.
- **Benefits.** Time to *understanding* drops from an install to a click. It answers "has
  anyone run this for a month?" with a real record. **Risks.** Hosting and data curation cost;
  the demo must not imply results it didn't get.
- **Validate.** Funnel conversion from README → demo → first real project, and qualitative
  "what does YAAF do?" answers after the demo.

---

## G. New product experience blueprint

### Mental model

> **You give YAAF a goal. It shows you a plan with a price and a date, does the work, proves
> what it can, and asks you only what it must.**

Four nouns on the surface: **Projects**, **Decisions**, **Results**, **Chat**. One status
sentence per project. Everything else is Details.

```
┌ Sidebar ────────────┐
│ Home        (●2)    │  ← verdict rows + decisions + while-you-were-away
│ Decisions   (2)     │  ← the one queue (today's Inbox "Needs you" + reviews + samples)
│ ─ Projects ─        │
│  AI morning brief ● │
│  PPC audit       ▲  │  ▲ at risk  ● on track  ◆ needs you  ■ paused
│ + New project       │
│ ─────────────────── │
│ Workspace ▸ Keys · Roles · Slack · People
└─────────────────────┘
```

### G1. Creation

```
New project
───────────────────────────────────────────────────────────────────
What do you want done?
┌──────────────────────────────────────────────────────────────────┐
│ Read the AI news every morning and give me the 5 things that      │
│ matter, with sources. Under $60/month.                            │
└──────────────────────────────────────────────────────────────────┘
                                                      [Draft it →]

Proposal  ·  Repeats every weekday  (why?)
───────────────────────────────────────────────────────────────────
Delivers     5 sourced findings, weekdays by 07:00, by email
Checked by   YAAF fetches every source; a second agent challenges each pick
Cost         est. $25–40/month   ·  hard stop $60  (from 214 priced runs)
Asks you     ~1×/week: new paid sources, changes of direction
First result ~20 minutes after Start
Team         Researcher, Editor, Skeptic   (details)
Assumed      English sources only · no paywalled sites       [change]

[Start]   [Change something]   [Run as a test first — free]
```

### G2. Project Status page

```
AI morning brief                                  On track  ●
"Delivered 5 of 5 briefs this week, all on time. $11 of $60. Nothing needs you."
Next: tomorrow 07:00 brief                                  [Pause]  [Message lead]

Decisions for you (0)                    Latest results
  Nothing needs you.                       Thu brief   ✓ 5/5 sources verified
  Next possible ask: paid source review    Wed brief   ✓ 4/5 · 1 paywalled (unverified)
                                           Tue brief   ✓ 5/5
Ask the lead ───────────────────────────────────────────────────────
│ Why was the OpenAI item ranked first?                          [Send]│
Details ▸ Plan · Work · Team · Money · History · Rules
```

### G3. Decision card (everywhere)

```
Decision · PPC audit · Buy data                              5 days left
───────────────────────────────────────────────────────────────────────
What happened   The analyst needs 90 days of search-term data; the free
                export stops at 60.
Recommendation  Approve — $129 one-off. The 30-day gap holds 38% of spend.
Options         Approve   → full analysis, date unchanged
                Decline   → analysis on 60 days; findings flagged "partial"
If you ignore   Declines itself on 30 Oct at 17:00 (your time)
Undo            Purchase can't be undone once made
Evidence ▸      cost calc · data gap · agent's case
                                              [Approve]  [Decline]  [Ask…]
```

### G4. Surfaces and their jobs

| Surface | Job | Not its job |
|---|---|---|
| **Email** | Decisions (immediately), alarms, one daily digest | FYI events (already held back, good) |
| **Slack** | The same Decision cards with buttons; chat with the lead | Mirroring every team channel by default |
| **Web** | Decide, read results, look deeper | Teaching the architecture |
| **MCP / Claude** | Plain-language questions, drafting projects, steering with previews | Raw table dumps |
| **CLI** | Install, doctor, scripting, ops | Day-to-day reading (keep `status`) |

### G5. Conversational examples

```
You:    How are my projects doing?
Claude: 3 projects. PPC audit is at risk (projected 3 Nov vs 30 Oct; cut "Glossary"
        to make it). Morning brief and Tutor are on track. 1 decision waiting:
        $129 data purchase on PPC audit (recommended: approve, 5 days left).
        → status() 1 call

You:    Fix the PPC date without spending more.
Claude: The lead's options within budget: cut "Glossary" (on time, −$9) or
        merge R-03 into R-02 (2 days late). Preview: cut Glossary → on time.
        Confirm?  [token bound to that exact change]
```

---

## H. Before-and-after examples (from observed behaviour)

| # | Area | Before (observed) | After |
|---|---|---|---|
| H1 | **Approval email** | `[yaaf/ppc] approval expiring: <title>` · `<body> What happens without it: <…> If you have not decided by <time>, the answer is no — that is what this project's rules say. Decide it on the Decisions screen.` (sent ~48h before expiry; no link) | Sent when filed: `Decision needed (5 days): Buy 90-day search data, $129. Recommended: approve. If you ignore it: declined 30 Oct. [Approve] [Decline] [Open]` |
| H2 | **Daily brief** | Subject `briefing — You changed 1 rule (brief_daily_at).` Body opens `Window: the window you asked for — … (0.06h)`. Off by default. | Subject `All 3 projects on track · nothing needs you`. Body: decisions → risks → results → spend. On by default. |
| H3 | **Onboarding: keys** | "The Founder needs a key of its own… It pays for product-wide conversations… never handed to a project's agents" + a separate LLM credential list | "Paste your Anthropic key. ☑ Also use it to draft projects." |
| H4 | **Project creation** | Fork → describe or 4-step form; shape frozen; 3 preset families; 48 help blocks | Paragraph → proposal card (G1) → Start |
| H5 | **First tutorial** | "Leave everything else. Supervised… Minute 1 — your only real job…" while the import runs Autonomous | Spec sets Supervised explicitly; the page names where you'll see the plan |
| H6 | **CLI connection error** | 224-line rich traceback ending in `ConnectError: [Errno 111]` | `Can't reach YAAF at http://localhost:8080. Is the server running? Try: yaaf doctor` |
| H7 | **`yaaf projects`** | Raw JSON with each full goal (~800 words for one project) | `PPC audit   at risk   due 30 Oct (proj. 3 Nov)   $148/$400   1 decision` |
| H8 | **MCP "what needs me?"** | `desk` (3.2k chars JSON; no flags, heads-ups or reviews) + `project view=flags` + `view=heads_ups` per project | `attention()` → `summary: "2 things need you. Soonest: $129 data purchase on PPC audit, 5 days."` + cards |
| H9 | **Fire drill** | 64 lines; stderr noise first; generic cost; "not in an Explore project's shape" on a Deliver | Proposal card lines: "Fits your $20 budget at typical prices · asks you ~2× before the first result · 1 gap: nobody checks the writer's work [Add a reviewer]" |
| H10 | **Overview** | 4 tiles + Needs you + Team + Roadmap + From the CEO + Coming up + Money by team + Latest deliverables + Watching + Setup & health (24 help blocks) | Status page (G2): one sentence, decisions, results, ask box |
| H11 | **Settings → Rules** | 62 rules in 6 tabs, each with help text | Money · Pace · Say-so dials with their $ effect; "Advanced: 62 rules" |
| H12 | **Behind schedule** | Banner: projection N days past promise; "Cut scope" / "Move date" | "4 days late. Cut *Glossary* → on time, saves $9 (recommended). Or move to 3 Nov. [Cut] [Move]" |

---

## I. Implementation roadmap

### Phase 1 — Immediate simplification (2–4 weeks)
Items **#1, #3, #4, #11, #17 (errors + help)**, the copy part of **#9**, and a minimal
**#2**: `recommendation` required on approvals, rendered in Inbox and email.

**Acceptance criteria**
- Every approval kind triggers a notice within 1 minute of filing (invariant test).
- Every notice carries a working deep link; no copy names a screen that does not exist (CI grep).
- Example 01 imports Supervised; a fresh user following it sees proposed requirements.
- One key is enough to use the Founder and run a project.
- No CLI command lacks help; no CLI error prints a traceback for connection/file errors.
- **Outcome:** time to first value on a fresh box −30%; zero "missed approval" reports.

### Phase 2 — Workflow redesign (1–3 months)
Items **#2 (full), #5, #6, #7, #8, #10, #13, #14, #15**.

**Acceptance criteria**
- One `status` producer feeds Home, Status, email, MCP and CLI (a test asserts equality).
- The project nav has 4 entries; every old route still resolves.
- New project from a paragraph → proposal card → Start in ≤ 3 user actions.
- Settings primary view ≤ 12 controls; all 62 rules still reachable under Advanced.
- MCP: each of the 10 natural questions in §C-S answered in 1 call with a prose summary.
- **Outcome:** in the §K usability test, ≥ 80% of first-time users create a project unaided;
  60-second portfolio check ≥ 90% correct.

### Phase 3 — World-class experience (3–6 months)
Items **#16, #18, #19, #20** and bold changes **F1–F5** as validated.

**Acceptance criteria**
- Decision and Status are the documented public contract; Slack renders actionable cards.
- Interruptions/week is a visible, enforced project metric.
- Zero-install demo live; README → first project conversion is measured.
- **Outcome:** TTFV < 15 min; correct-decision rate ≥ 90% at median time to decision < 45 s.

---

## J. Product experience scorecard

Scores are judgement on a 10-point scale, anchored on the evidence column. They are not
measured user data; treat ±1 as noise.

| Dimension | Now | Target | Evidence |
|---|---|---|---|
| First-time usability | 2.5 | 8 | Docker + SSH + 2 keys + token mint; tutorial contradicts default; dev mode silently fails off-port |
| Onboarding | 3 | 8 | Founder + drill are genuinely good ideas; buried behind setup; drill cost not spec-specific |
| Clarity | 4 | 8.5 | ~55 concepts; 24–49 help blocks per screen; terminology mostly stable after the redesign |
| Simplicity | 2.5 | 7.5 | 10 pages, 62 rules, 34 tools, 75 commands, 9 interruption types |
| Information architecture | 5 | 8.5 | The redesign gave one Inbox definition and a familiar sidebar; Watching/Updates/Activity overlap |
| Decision-making | 5.5 | 9 | Time-to-consequence ranking and silence defaults are excellent; no push on filing; recommendation optional |
| Workflow efficiency | 4 | 8 | MCP 1+N calls; CLI raw JSON; changing direction has two doors |
| Outcome visibility | 5 | 8.5 | Deliverables reader is strong; verification not on results; activity ≠ achievement not surfaced |
| Trust & confidence | 6.5 | 9 | Server-side rules, receipts, attribution, honest caveats; undercut by stale docs and silent approvals |
| Error recovery | 4.5 | 8 | Alarms always emailed, health card with fix links; no recovery verb; CLI tracebacks |
| Cross-surface consistency | 4.5 | 8.5 | Shared desk producer is good; MCP desk lacks flags/heads-ups; briefing window differs between CLI and MCP |
| Overall coherence | 4.5 | 8.5 | A coherent engine, an incoherent surface vocabulary |

---

## K. Validation plan

None of the recommendations has been tested with users. These tests decide which ones survive.

### K1. Moderated task-based usability (5–8 participants per round, two rounds)
Participants: technical leads/founders who already use Claude Code or similar, have not used
YAAF, and get 45 minutes.

| Task | Success criterion | Measures |
|---|---|---|
| Install and create a project from one paragraph | Project running without help | TTFV, assists needed, errors |
| "In 60 seconds, which of these 5 projects needs attention and why?" | Correct project and reason | Accuracy, time |
| Decide a seeded spend approval | Decision matches the seeded "right" answer | Time to decision, correctness, confidence (1–5) |
| Find out if a deliverable's claims were checked | Correct verification status | Time, clicks |
| Bring a late project back on time without raising budget | Chooses the cut | Correctness, steps |
| Change the goal mid-flight | Change applied via the intended path | Steps, errors |
| Same five questions via Claude/MCP | Correct answers | Tool calls, tokens, correctness |

Run round 1 on today's product (baseline) and round 2 on Phase-1/2 prototypes (the mockup
approach already used in `docs/ux-redesign/` is adequate).

### K2. Product metrics to instrument (privacy-safe, local)
- **Time to first value**: install → first published deliverable.
- **Task completion**: projects created that reach a first verified result.
- **Decision latency** per kind; **expired-undecided rate** (target < 5% for spend/legal).
- **Correct-decision proxy**: decisions reversed within 72h.
- **Interruptions per project-week** vs the drill's forecast.
- **Unnecessary interventions**: approvals/asks the user answered with the recommendation
  unchanged > 95% of the time → candidates for delegation.
- **Comprehension**: one-question check ("What will this project do if you ignore it?") on
  the proposal card.

### K3. Hypotheses that must be checked, not assumed
- H1: users prefer a hidden org (F1) *without* losing trust. Test perceived control.
- H2: a proposal card with ranges is trusted more than the drill's walk. Test calibration
  against real costs after 2 weeks.
- H3: push-on-filing increases decision rate without raising annoyance. Test opt-outs and the
  email-ignored rate.
- H4: 4 project pages are enough for 90% of visits. Test with Details click-through telemetry.

---

## Competitive notes (patterns worth borrowing, not interfaces)

| Product | Pattern | Evidence status | Translation to YAAF |
|---|---|---|---|
| **Linear Triage** | A dedicated intake inbox with single-key Accept / Decline / Duplicate / Snooze; snoozed items return on new activity ([docs](https://linear.app/docs/triage), [snooze](https://linear.app/docs/snooze)) | Verified in docs | Decision queue keys; "snooze until it's 24h from deciding itself" |
| **Stripe Radar reviews** | A prioritised review queue; each action states its effect; irreversible actions labelled ("a completed refund is permanent") ([docs](https://docs.stripe.com/radar/reviews)) | Verified in docs | Put **Undo: possible / not possible** on every Decision card |
| **GitHub Copilot coding agent** | Assign an issue like a teammate; work arrives as a draft PR with session logs one click away; review requested at the end ([docs](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-on-github)) | Verified in docs | Result first, trace on demand; the outcome object (PR ≈ deliverable) is the hub |
| **Devin** | Plan approval is a *setting* (wait or not); Slack thread is the work surface ([release notes](https://docs.devin.ai/release-notes/2024.md), [Slack](https://docs.devin.ai/integrations/slack)) | Verified in docs | Autonomy as one plain toggle; Slack decision cards |
| **Paperclip** | Familiar SaaS nouns, ambient liveness, triage inbox (studied in `docs/ux-redesign/`) | From its repo | Already adopted. YAAF's time-to-consequence ranking is better |
| **Vercel** | Every deployment is one status (Ready / Error / Building) with logs one click away | General product knowledge, not re-verified | One verdict per project, details on demand |

---

*Appendix: [APPENDIX.md](APPENDIX.md): measured inventories, verbatim copy, CLI/MCP outputs,
notification table, screenshots index and setup log.*
