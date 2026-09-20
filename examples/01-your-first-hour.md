# 01 — Your first hour

> **Why not just a Claude Skill?** For this one — you could, in a single
> message, and it would be a fine note. **That is the point of putting it
> first.** You are not here for the note; you are here to watch a plan get
> proposed, accept it yourself, and see the platform decide whether it was
> done. Nothing later in this set makes sense until you have.

**Do this one first.** It is deliberately the smallest complete thing yaaf can
do, because the point is not the deliverable — it is watching the machinery
work once, on something you can check in five minutes.

**What you get:** one briefing note, about 800 words, that explains a subject
you pick to someone smart who knows nothing about it.

**What it costs:** a few dollars at most. The budget is capped at $20 and it
will not come close.

**What you need:** your Anthropic API key. Nothing else. No accounts, no data
feeds, no repo.

---

## Import it

**+ New project → Start from a setup file →** `01-your-first-hour.json`.

The form fills itself in. Three things to do before you click Create:

1. **Paste your Anthropic key.** Without it the project is created but cannot
   start — its first run would die looking for one.
2. **Change the subject.** The goal says `RUNNING A MARATHON` in capitals.
   Replace it with whatever you actually want explained — a technology, a legal
   concept, a hobby, your own company's product. The capitals are there so you
   cannot miss it.
3. **Check your email address and timezone** at the top. The timezone drives
   the reporting window.

Leave everything else. Supervised, Balanced, one writer named Ada.

## What happens, roughly in order

**Minute 0 — the plan.** Nothing works yet, and that is correct. A Deliver
project starts owing a plan, so the first thing the CEO does is propose
**requirements**: things that must be *true* at the end. Go to the **Plan** tab
and you should see something like four of them, marked *proposed*:

| | Requirement | Acceptance test |
|---|---|---|
| R-01 | The note explains what the subject is and why anyone cares | `observable` — a reader who has never met the subject can say what it is after one read |
| R-02 | The three beginner traps are named, each with what to do instead | `observable` — three traps, each with a concrete alternative, not "be careful" |
| R-03 | A glossary covers the terms a newcomer will hit immediately | `observable` — every term used in the note before it is defined appears here |
| R-04 | Three next reads, each with one line on who it is for | `observable` — three links, each with an audience, not a description |

**Minute 1 — your only real job.** Read them. Are these the right four things?
Accept the ones you want; cut anything you don't. **Nothing can be created
until you do** — that is a refusal in the server, not a nudge, and it is the
single most important thing this example teaches you. Scope is yours.

**Minutes 2–20 — it works.** The **Work** tab fills with tasks, each carrying
the requirement it serves. Ada claims one at a time. You do nothing.

**Whenever it finishes — the deliverable.** The **Deliverables** tab has the
note. Read it. It is either good or it isn't, and you will know within a
paragraph.

## What to look at while you wait

- **Plan → the promise.** How much of the scope is true, and where the platform
  projects it landing. On a four-requirement project you will see this as
  numbers rather than a chart, deliberately: a line drawn through one or two
  points is the number said twice with a slope nobody can check. Once enough
  has settled over enough days it becomes the burn-up, and the same card
  answers the same question either way.
- **Records → Runs.** Every model call: which agent, why it woke, what it cost,
  and the full trace. Open one. This is where you learn what your agents are
  actually spending money on.
- **Work → check the agents' work.** When something closes, the platform deals
  you a sample: what the task had to prove, beside what the agent claimed.
  Two minutes. Say whether you believe it.

## The one thing to take away

You never created a task, never assigned anyone, never told anyone how. You
wrote a goal and accepted a plan. Everything else was the CEO's, and everything
it did is on the record.

## Try this next

- **Change the goal mid-flight.** Settings → Brief. Then say so in the
  Boardroom. Watch the CEO re-plan around it rather than carrying on.
- **Cut a requirement** on the Plan tab and give a reason. It shows up in
  Records → Decisions, and the CEO is told.
- Then go to **[02 — Settle an argument](02-settle-an-argument.md)**, where the
  answer has to survive a skeptic.
