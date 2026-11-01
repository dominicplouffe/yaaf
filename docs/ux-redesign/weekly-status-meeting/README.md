# The weekly status meeting — spec and mockup

**Open the mockup:** [`mockup.html`](mockup.html) (one self-contained file; download and open it in
a browser). The **Deliver / Operate / Explore** switch at the top shows the same meeting for each
project shape. **Design notes** pins the reasoning to each section.

> **Status: steps 1–3 of §8 are built.** Step 1: the record
> (`yaaf/server/review_record.py`, frozen onto `reviews.record` at open), the headline, the
> Explore tiles, the fold on the numbers. Step 2: the structured filing (`POST /review_report`,
> tool `review_report`, `reviews.report`), the CEO's pack carrying the record with "explain it, do
> not restate it", the refusals of §4.2, and the screen's CEO sections. Step 3: plan versus
> actual — `review_record.plan_marks` freezes the previous `next` list with the platform's mark
> per item into `numbers.prior.plan` at open; the filing's `last_plan` must answer for every item
> and `done` against an open requirement is refused naming the key; the table is on the screen.
> Filing a report also renders `narrative`/`recommended`, so the green rule (§4.3) already reads
> `proceed` as "recommended nothing" without a change of its own. `review_narrative` is kept
> for one release. No eval golden moved: no scenario opens a review. Steps 4–5 are still proposal. The decisions in §7 were taken as recommended: one meeting on
> Operate (at the roll), two replies per meeting, old filings rendered the old way, "Status
> meeting" as the name.
>
> This proposal changes what the review *contains* and what the CEO is made to file; it keeps every
> mechanism the review already has — opened by the janitor at zero tokens, numbers frozen at
> open, six levers through the same routes they use anywhere else, the green auto-close, the nag
> and the escalation.

---

## 1. The ask

The chairman, reading review #1 on a project he had never worked in day to day:

1. Call it a weekly status meeting.
2. The numbers mean nothing unless you live in the project. Change that.
3. A status meeting starts with *what was done and how that moved the goal*, then *what will be
   done next and how that will move the goal* — per project shape, because the goal means something
   different in each.
4. Without that, everything is abstract.
5. Then: the CEO confirms we are good to proceed, or the two of you work out what changes.
6. If something changes, it is applied: orders, brief, the team.

All six hold. Two need sharpening, and the sharpening is what this document is for:

- **"Acknowledgement from the CEO" points the wrong way.** The CEO reports; the chairman
  acknowledges. Under the fully autonomous dial the CEO already *holds* the decisions, but the
  meeting is still the chairman reading and closing. What the chairman actually wants from point 5
  is for the CEO to say, in one line, *"proceed as planned"* or *"I want to change X"*. That line
  is **the ask**, and it becomes a required part of the filing (§3.5).
- **"What was done" must come from the record, not from the CEO.** The one thing today's review
  gets right is that the platform freezes the numbers and the CEO explains them
  (`yaaf/server/api/agent.py`, `file_review_narrative`: "the CEO explains the numbers, it does not
  produce them"). A free-text "here is what we did" paragraph would be an agent grading its own
  homework, which the whole product exists to prevent. So the *list* of what was done is composed
  by the platform from things it already wrote down under its own rules; the CEO writes the
  *meaning* on top of it (§3.2).

## 2. What a review is today, and why it reads as noise

`Review` (`yaaf/core/models.py`) is opened by `open_reviews` in `yaaf/server/janitor.py` every
`review_interval_days` (default 7, `yaaf/core/constitution.py`), with `review_numbers` frozen onto
the row. The CEO is woken on the governance reserve (`dispatcher.py`, reason 4c) and files a
*variance narrative* through the `review_narrative` tool (`sandbox_entry.py`): "what moved
against the plan, the SINGLE largest cause, what you recommend cutting or moving". The chairman
pulls levers (`admin.py`, `pull_review_lever`) and closes (`close_review` → `finish_review`), or the
janitor closes a green one after 24h (`_review_needs_nobody`). `web/src/pages/project/Review.tsx`
renders it: six tiles, the recommendation, the narrative behind a fold, the levers.

What a reader who does not live in the project sees:

| On the screen | What it actually is | Why it does not land |
|---|---|---|
| **Requirements settled 11 a week** · 4 open · 0.4 weeks | `velocity.per_week_3w`, a forecasting input | A rate is for the projection. The reader wants "what got done". |
| **Met since last 12** · 11 met · 14 cut · 0 proposed | Counts of `RequirementEvent` transitions | Counts without the things counted. *Which* 11? *Why* were 14 cut? |
| **Commitment At risk** · promised Oct 31 · now Nov 2 | `live_commitment` + projection | The one tile that says something. It should be the headline. |
| **Budget 114%** | month-to-date vs cap | Fine, but it is October's number on a meeting about a week. |
| **Audit coverage 0** | samples drawn since last review | Means "you spot-checked nothing". Reads as a score of zero. |
| **Worst offender R-29** · 9 dead runs | `velocity.worst_failure` | A key. The reader has to go find out what R-29 is. |
| **What your CEO says it will do** | `recommended` | Good, and the only part written for a reader. |
| **Why, in its own words — 1,915 characters** | `narrative` | A variance report assumes the reader knows the plan it varied from. |

Three structural gaps underneath:

1. **"What was done" is not on the page.** Only a count of it.
2. **"What happens next" is not on the page at all**, and is never recorded, so the next meeting
   cannot hold the CEO to it. The levers are accountable ("shown beside what they bought"); the
   CEO's plan is not.
3. **The tiles are Deliver-shaped.** On an Explore project there are no requirements, so the first
   tile reads "—", the commitment tile is absent, and the bets in `numbers.hypotheses` are frozen
   onto the row and never rendered. The meeting the chairman most needs on an Explore project is
   "which bets are alive, what did we learn", and the screen cannot say it.

## 3. The meeting, section by section

Order is the chairman's: what happened → what it bought → what went wrong → what is next → the ask.
Numbers last. Every section is either *from the record* (platform, zero tokens, cannot be argued
with) or *from the CEO* (on the reserve run), and the screen says which.

### 3.1 The headline — from the record

One line the platform composes from the frozen numbers, in words, mode-aware:

- Deliver: *"Behind the promise by two days (Oct 31 → Nov 2). 11 of 15 requirements true. October
  spend is 114% of plan."*
- Operate: *"Cycle 7 closed with 8 of 10 met and 2 carried. Pace is holding at 9 a week."*
- Explore: *"Two bets alive, one killed on schedule this week. 14 pieces of evidence verified,
  3 refused."*

Same state words every other screen uses (`COMMITMENT_PLAIN` in `Review.tsx`, the strip's
`deliveryVerdict`). This replaces the six tiles as the first thing read.

### 3.2 Last period — what was done, and what it bought

**From the record**, for the window *previous meeting opened → this meeting opened*:

| Deliver | Operate | Explore |
|---|---|---|
| Requirements made true (key + statement, and *how* it was checked: verified / judged) | The cycle's scorecard: accepted, met, carried, with keys | Bets added, killed, expired; evidence verified / refused per bet |
| Requirements cut, with the reason on the event | Same, for the cycle's scope | Decisions recorded (`Decision` rows) |
| Deliverables published (name, version, which requirements they claim) | Deliverables published | Deliverables published |
| Spot-checks: drawn, passed, failed, expired unanswered | same | same |
| Runs: total and dead, spend for the window | same | same |

This is **the briefing's window, re-cut**. `yaaf/server/briefing.py` already composes "what
happened while you were away" from exactly these producers at zero tokens, and already holds R-00
and standing requirements out of "settled true" on the same grounds velocity does. The review
should call the same producers over its own window rather than grow a second composer that
drifts from the first. Frozen into `numbers.record` at open.

**From the CEO** — `done`: one paragraph on what the period *bought*. Meaning, not list; the list is
above it and the CEO is told not to restate it.

**Plan versus actual.** If the previous meeting recorded a `next` list (§3.4), it is shown here,
item by item, with the platform's mark first and the CEO's note second:

| What it said it would do | Served | Platform says | CEO says |
|---|---|---|---|
| Ship the cold-email sequence v2 | R-21 | **true** (verified Oct 29) | done |
| Get the Ads account unblocked | R-29 | **open** · 9 dead runs | not done — account suspended Oct 29, see §3.3 |
| Book the Nov 9 newsletter slot | R-31 | **true** (judged, by you) | done |

The platform's column is derived: an item that `serves` a requirement key reads that
requirement's status; a bet id reads the hypothesis; `cycle` reads the scorecard. The CEO's column
is its `last_plan` filing, and **a CEO that says "done" about an item whose requirement the
platform does not hold as met is refused, naming the key** — the same rule as "nothing but the
platform writes `met`", applied to the meeting.

This is the mechanism the levers already have ("shown at the next review beside what they
bought"), pointed at the CEO's own plan. It is the part of the proposal that makes the rest
accountable.

### 3.3 What did not go to plan, and why — from the CEO

Today's variance narrative, demoted from "the narrative" to one section and held to its original
brief: what moved against the plan, and *the single largest cause named by what it is*, not by its
key. The worst-offender tile becomes a sentence here: *"The Google Ads account was suspended on
Oct 29; nine runs died retrying it."*

### 3.4 Next period — what it will do, and how that moves the goal — from the CEO

A **list**, not a paragraph, because a list can be checked next week:

```
next: [
  {what: "Send the cold sequence to the 140-name list on the first legal day", serves: "R-33", by: "2026-11-07"},
  {what: "Publish the Nov 9 newsletter issue",                                   serves: "R-31", by: "2026-11-09"},
  {what: "Leave the Ads budget unspent unless the account clears by Nov 3",      serves: "R-29", by: "2026-11-03"},
]
goal_effect: "Takes scope to 15 of 15 true and puts the first real customer contact on Nov 7."
```

`serves` must name something the platform holds open: a requirement key in Deliver/Operate, a
live bet in Explore, or `cycle` in Operate. An item that serves nothing is refused, which is the
same rule as "a task that serves no requirement is refused" (`CONTRIBUTING.md`, house rule 1,
and `FEATURES.md` §5). `goal_effect` is one sentence and is mode-specific by construction: in
Deliver it is scope and date; in Operate it is the cycle; in Explore it is which bets continue
and what question they answer.

Recorded on the review row. The next meeting opens with it (§3.2).

### 3.5 The ask — from the CEO, answered by the chairman

One of two:

- `proceed` — *"Nothing needs you; I am proceeding as above."*
- `change` — one or more of the six levers, named, with the reason: *"Accept the slip to Nov 2:
  every open item is a channel test and we have no proof from any channel yet."*

This is where point 5 of the ask lives and where the levers move to. The six buttons stay exactly
as they are mechanically (`pull_review_lever`, same routes, same record); they are rendered under
the ask, with the CEO's recommended lever first and pre-filled from its `detail`, so agreeing is
one press with the reason already written. The other five sit under "Something else".

**Say something back.** A bounded thread on the meeting: the chairman writes, the CEO answers on
the governance reserve, capped by a new constitution rule `review_replies` (default 2 per
meeting; a reply past the cap is refused with the number). It reuses the boardroom's threads
(`message_post` already takes `thread_id`); the review carries the thread id and the Chat tab
shows the same messages. This is point 5's "collaborate" without opening an unbounded chat on a
reserve meant for one run.

Under the fully autonomous dial the section is headed *"What your CEO decided"* instead of *"The
ask"*, the levers fold under *"Overrule"*, and the thread and Close remain. The dial already
governs the page's intro sentence (`fullyAutonomous` in `Review.tsx`); it should govern this
section the same way.

### 3.6 Applying the changes — unchanged, made visible

Point 6 is already built: each lever acts through the route it would use outside a meeting
(`cut_requirement`, `add_requirement`, `reword_requirement`, `move_commitment`,
`amend_constitution`), is recorded with the reason, and reaches the CEO as a notice;
`finish_review` sends one closing summary and, on Operate, writes the cycle narrative. What is
missing is the loop being *seen to close*. Two additions:

- **"Decided this meeting"** stays, and gains one line per decision saying what it changed on the
  record (*"R-29 cut; commitment re-projected Nov 2 → Oct 31; 1 task cancelled"*), from the
  route's own result.
- The closing notice to the CEO says the `next` list is on the record and the next meeting opens
  with it. Nothing new to enforce; the enforcement is §3.2's refusal.

### 3.7 The numbers — from the record, collapsed

The six tiles, unchanged, under a fold labelled *"The numbers behind this"*. Three fixes while
they are there: the Explore tiles show bets (alive / killed / expired this period) in place of
requirements and commitment; "Audit coverage 0" reads *"0 of N closes spot-checked"*; the worst
offender names the requirement's statement, not only its key.

## 4. The data

### 4.1 `Review` row

| Field | Today | Proposed |
|---|---|---|
| `numbers` | frozen snapshot | + `record` (§3.2 table, from the briefing's producers over the window) and `prior.plan` (the previous `report.next` with the platform's mark per item) |
| `narrative`, `recommended` | the CEO's filing | kept, read-only, for reviews filed before this change; the UI renders those the old way |
| `report` *(new, JSON)* | — | the structured filing below |
| `thread_id` *(new)* | — | the boardroom thread for "say something back" |
| `decisions` | `[{kind, reason, detail, at}]` | + `effect` per decision, from the route's result |

### 4.2 The filing — `POST /agent/review_report`

```json
{
  "done":        "what the period bought us — meaning, not the list (≤ 1,200 chars)",
  "last_plan":   [{"item": 0, "outcome": "done|partly|not_done|dropped", "note": "…"}],
  "variance":    {"what": "…", "largest_cause": "named by what it is (≤ 600 chars)"},
  "next":        [{"what": "…", "serves": "R-33 | H-2 | cycle", "by": "2026-11-07"}],
  "goal_effect": "one sentence",
  "ask":         {"kind": "proceed|change", "levers": [{"kind": "accept_slip", "detail": "…"}], "why": "…"}
}
```

Refusals, each a rule in the house sense (server-side, with an attack in
`tests/selftest_invariants/`):

| Rule | Refusal |
|---|---|
| Only the CEO files; only into an open review; once | as today (403 / 409 / 409) |
| Every section present; `next` has ≥ 1 item | 422 naming the missing section |
| `next[].serves` names an open requirement, a live bet, or `cycle` on Operate | 422 naming the item and what it named |
| `last_plan` covers every item of the prior `next` | 422 naming the uncovered item |
| `last_plan[].outcome == done` for an item serving a requirement the platform does not hold as met | 422 naming the key — done is never self-graded |
| `ask.kind == change` carries ≥ 1 lever from the six; `proceed` carries none | 422 |
| `review_narrative` after this ships | 410 pointing at `review_report`, after one release of accepting both |

### 4.3 The green auto-close

`_review_needs_nobody` keeps its four conditions (filed; date holds; no failed spot-check; month
inside plan) and swaps "recommended nothing" for `ask.kind == "proceed"`. Under this rule the
meeting in the screenshot would not have auto-closed: the CEO asked to accept a slip, which is a
`change`. That is correct; it is a meeting worth having.

### 4.4 Plan versus actual, at open

`open_reviews` reads `prior.report.next`, marks each item from the record, and freezes the result
into `numbers.prior.plan`. Zero tokens, like everything else the janitor does here. The CEO's pack
(`contextpack.py`, the `review` block) carries it, with the instruction: *"For each item say
done, partly, not done or dropped, in one line. The platform's mark is beside it; where you
disagree, say why, and know that 'done' against an open requirement is refused."*

## 5. Where it touches — the footprint

The sibling rule in `CLAUDE.md`: a concept wired into half the places it belongs is the expensive
mistake. `git grep -l '\bReview\b\|review_narrative' -- yaaf web/src docs` today:

| File | What changes |
|---|---|
| `yaaf/core/models.py` | `Review.report`, `Review.thread_id`; migration |
| `yaaf/core/constitution.py` | label *"Days between status meetings"*; `review_replies` |
| `yaaf/server/janitor.py` | `review_numbers` + `record`, `prior.plan`; `open_reviews` email body in the new order; `_review_needs_nobody` on `ask` |
| `yaaf/server/briefing.py` | expose the window producers for reuse; `_meeting_lines` says "filed its report" |
| `yaaf/server/api/agent.py` | `review_report`; `review_narrative` deprecated |
| `yaaf/server/api/admin.py` | `effect` on decisions; thread post; close notice text |
| `yaaf/server/api/observe.py` | `report`, `record`, `thread` on the view |
| `yaaf/server/contextpack.py` | the `review` block: record, prior plan, new instruction |
| `yaaf/server/dispatcher.py` | 4c on `report is None`; new reason `review_reply` (governance-classed, capped) |
| `yaaf/server/agenda.py` | "status meeting" wording |
| `yaaf/server/receipt.py` | the receipt's meeting lines read `report.ask` |
| `yaaf/server/mcp/tools_read.py`, `tools_steer.py` | reader shows the new sections; steer gains the thread post |
| `yaaf/runner/sandbox_entry.py` | tool `review_report` with the schema above; `review_narrative` removed from the CEO's list after one release |
| `yaaf/server/authority.py` | the thread post needs STEER |
| `web/src/api.ts`, `web/src/pages/project/Review.tsx` | the screen in §3; old reviews render the old way |
| `web/src/desk.tsx`, `pages/project/Details.tsx`, `Settings.tsx`, `Founding.tsx`, `NewProject.tsx` | banner and help text: "status meeting" |
| `docs/DASHBOARD.md`, `FEATURES.md` §4, `AGENT_API.md`, `CLI.md`, `CREATING_A_PROJECT.md`, `GOVERNANCE.md` | the new shape |
| `evals/` | golden re-record: the CEO's pack and tool text change (house rule 3) |
| `tests/selftest_invariants/` | one attack per refusal in §4.2; the green rule; plan-vs-actual marks; the reply cap; the Explore record; old-filing rendering |

Before calling it done, run the diff in `CLAUDE.md` with `Approval` as the sibling and account for
every line.

## 6. Naming

- The screen and every chairman-facing sentence say **status meeting**. The tab is **Status**.
  "Weekly" is not in the name: it is `review_interval_days`, and the page says *"every 7 days"*
  beside the meeting number. On a 14-day interval "weekly status meeting" would be a lie the
  product tells on its own front page.
- The CEO's filing is a **status report**; the tool is `review_report`.
- Model, table, routes and ids keep `review`. Renaming a table buys the reader nothing and costs a
  migration, an audit-log rename and every integration that reads `review.*` events.

## 7. Decisions needed

1. **Operate: one meeting or two?** An Operate project has a cycle roll (writes the scorecard,
   reopens the plan gate, hands the CEO the last three narratives) and a review on its own clock
   (writes the cycle's narrative at close). On a weekly cycle with a 7-day review they land within
   a day of each other and are the same conversation. **Recommendation:** on Operate, when
   `cycle_frequency` is weekly or longer, the status meeting opens *at the roll* and
   `review_interval_days` is ignored with a note in Settings; daily cycles keep the interval.
   Nothing else changes: the roll already does the zero-token work, and `finish_review` already
   writes the narrative the next cycle reads.
2. **The thread cap.** `review_replies` default 2. Each reply is a governance-reserve run; the
   reserve is 3 a day by default. Two replies leaves one run for a re-forecast or a cycle plan on
   the same day.
3. **Old filings.** Keep rendering `narrative`/`recommended` for reviews filed before the change,
   or backfill a `report` from them. **Recommendation:** render old ones the old way under a
   "filed before status reports" line; never synthesise a `next` list the CEO did not write.
4. **The fully autonomous dial.** The proposal keeps the meeting open until the chairman closes it
   or the green rule closes it. The product-experience audit (A9) goes further: a green meeting
   folds into the digest without a page at all. Compatible; this is the page that exists when a
   meeting is *not* green.

## 8. Rollout, in the order it pays

1. **The record and the headline** — `numbers.record`, the Explore tiles, §3.1 and §3.2's
   from-the-record half, the fold on the numbers. No agent-visible change, no golden diff; the
   page already reads better.
2. **The report** — `review_report`, the pack, the refusals, the screen's CEO sections, evals
   re-recorded. `review_narrative` still accepted.
3. **Plan versus actual** — `prior.plan` at open, the `last_plan` refusal, the table in §3.2.
   This is the one that needs two meetings to see, so it ships before anyone looks.
4. **The ask, the levers under it, the thread, the green rule on `ask`.**
5. **Naming and docs**, and the Operate alignment if decision 1 is taken.

Each is a PR a reviewer can hold in their head. The suite tiers in `CLAUDE.md` apply; step 1 and
step 3 touch JSON columns and timestamps and get the Postgres run.
