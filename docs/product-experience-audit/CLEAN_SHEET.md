# YAAF from a clean sheet

*The second design exercise, after the [baseline audit](README.md). That audit asked "how do we
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

Evidence labels are those of the baseline: **[Measured]**, **[Code]**, **[Inferred]**,
**[Hypothesis]**. Interaction counts in §2 are **estimates from a stated scenario**, not
telemetry.

---

## Summary

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
**Undo** where possible. That keeps Rule 7 of the baseline brief ("never hide consequential
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
