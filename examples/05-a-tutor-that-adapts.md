# 05 — A tutor that adapts to what you got wrong

> **Why not just a Claude Skill?** Because it has to wake up next Sunday. And
> the Sunday after that, for months — reading what you got wrong in week three
> when it plans week four. Here that is not a promise the model makes; the
> platform hands it the last three cycles' narratives **verbatim** and reopens
> the plan gate every time the cycle rolls.

**What you get:** one lesson a week on a subject you choose — explanation,
worked example, exercises, answer key — and a curriculum that *changes* based on
the answers you leave for it.

**What it teaches:** **Operate mode**. Cycles, scope that carries forward, and
the learning loop the platform makes a project take rather than merely
recommending.

**What you need:** your Anthropic API key, and about twenty minutes a week.

**Cost:** capped at $40/month. A lesson is a handful of runs.

---

## Import it

**+ New project → Start from a setup file →** `05-a-tutor-that-adapts.json`,
then **replace the subject** (it ships teaching SQL) and **set your timezone**
— the cycle rolls Sunday in it.

This works for anything with a doing-it component: a language, an instrument's
theory, statistics, a framework, cooking technique, your own company's domain
for a new hire.

## Why Operate, and what that actually means

A course is not one deliverable; it is the same deliverable, repeatedly, each
one better informed than the last. That is exactly what Operate is for:

- **The cycle rolls weekly.** Closing it carries unfinished scope forward and
  **reopens the plan gate** — so every week the tutor has to say what next
  week's lesson must make true, and you accept it.
- **The last three cycles' narratives are handed to the CEO verbatim.** Not
  summarised. This is the mechanism behind "adapts": what you got wrong in week
  three is *in the context* when week four is planned, because the platform put
  it there.
- **Two consecutive empty cycles pause the project.** A rhythm nobody dances to
  is a bill, not a project — including when the person who stopped showing up
  is you.

## Your half of the loop

This is the one example in the set that asks something of you every week, and
it does not work if you skip it:

1. Read the lesson in **Deliverables**.
2. Do the exercises. Put your answers in the workspace as a file — anything,
   `week-03-answers.md` is fine.
3. Next cycle, the tutor reads them, marks them, and **re-teaches what you got
   wrong before moving on**.

If you leave nothing, you get a competent generic course. The adaptation is
bought with your twenty minutes.

## What "done" looks like, per cycle

```json
{"type": "observable", "target": "deliverables/lesson-NN.md exists with an explanation, one worked example, and five exercises"}
{"type": "observable", "target": "deliverables/lesson-NN-answers.md exists as a separate file, with reasoning shown, not just answers"}
{"type": "observable", "target": "if answers were left in the workspace since the last cycle, the lesson names what was got wrong and re-teaches it before new material"}
```

The answer key is a *separate file* on purpose — a key in the same document is
a key you have already read.

## What to watch

- **The Review tab.** Every two weeks the platform opens a standing meeting
  with its own numbers and the CEO's explanation of them. This is where you say
  "too fast", "too much theory", "I want more exercises" — as a recorded lever,
  which is shown back at the next review beside what it bought.
- **Flattery.** The role is told never to flatter and to say when something is
  hard. If lesson four tells you that you are doing great when you got three of
  five wrong, that is a real defect. Say so in the Boardroom.
- **Padding.** A subject "traditionally contains" a lot of material that will
  not help you do the thing. The curriculum is built backwards from what you
  should be able to DO; hold it to that.

## Try this next

- **Cut a lesson** you don't care about on the Roadmap, with a reason. Watch
  the arc re-sequence around it.
- Then **[06 — A red team that can block](06-a-red-team.md)**, where a second
  agent gets a veto over the first.
