# 07 — Tell me what changed (and nothing else)

**What you get:** a weekly brief on something you depend on, containing only
what actually changed and what it means for you.

**What it teaches:** per-agent **schedules**, **external evidence**, and the
**drought** rule — the mechanism that stops a company producing activity and
calling it progress.

**What you need:** your Anthropic API key. Everything it reads is public.

**Cost:** capped at $50/month.

---

## Import it

**+ New project → Start from a setup file →** `07-tell-me-what-changed.json`,
then **name what you actually depend on** and **set your timezone** — the two
watchers wake at 07:00 and 07:30 in it, and the cycle rolls Monday.

It ships watching Python releases and web frameworks. Better subjects are the
ones where missing a change costs you something:

- the libraries and services your product is built on
- a standard or spec you have to comply with
- a regulator's publications in your industry
- a competitor's changelog, pricing page and job ads
- a government dataset you rely on

## The rule that makes it worth reading

> **Report changes, not activity.**

Anything can produce a weekly list of things that happened. The value is in the
filtering, and the goal states it as a rule: a release that doesn't affect you
gets one line or no line; a deprecation that will break something you use gets
the version it lands in, what breaks, and what to do.

And the harder half: **say when nothing important changed.** A quiet week
reported as quiet is the most valuable brief this thing will send you. A quiet
week padded into three pages is how you learn to stop opening them.

## Schedules, and why they are staggered

```
Rey  07:00   reads the primary sources
Sol  07:30   reads the primary sources
CEO  06:30   plans the day before either of them starts
```

Per-agent daily wakes (`agents.daily_at`) are the CEO's standup, generalised.
They fire through the same heartbeat dedup and the same wake-rate brakes as
everything else — a schedule cannot become a loop.

The stagger is deliberate: the CEO sets up the day's work before the watchers
run, so they wake into a board rather than into a decision.

## Drought — the interesting rule here

`drought_hours` is 96 on this project. When the platform has seen no verified
external evidence for that long, **internal task creation is refused**. The
company may only create work that reaches outside.

For a watch desk this is exactly right: if a week goes by with no evidence
recorded from a primary source, the desk has stopped watching and started
writing. The refusal says so before you notice.

It is also the rule most worth tuning. Set it too tight and you nag a company
doing exactly the right thing over a holiday week; too loose and you find out
in a month.

## What "done" looks like, per cycle

```json
{"type": "observable", "target": "deliverables/briefs/YYYY-WW.md exists, and every item carries a date and a link to a primary source"}
{"type": "observable", "target": "no item in the brief appears in deliverables/already-reported.md from an earlier week"}
{"type": "observable", "target": "the brief states plainly when nothing important changed, rather than padding"}
```

The second one is the anti-repetition ratchet, and it is why the goal asks for
a running file of what has already been reported. Without it, week four
rediscovers week one.

## What to watch

- **Aggregators instead of sources.** A blog post *about* a release is a lead;
  the release notes are the evidence. The brief should link the latter.
- **Items with no "so what".** Every entry should say what it changes for you.
  If none of them do, the subject may be too broad — narrow it in the Brief.
- **The Review** every fortnight: this is where you say "too much noise" or
  "you missed X", recorded as a lever and shown back next time.

## Try this next

- Turn on **`require_evidence_verification`** (Settings → Rules). Now a cited
  link counts for nothing until the platform has fetched and snapshotted it —
  and a dead source stops the drought clock from resetting, which is precisely
  the honest behaviour.
- Then **[08 — A game you can play](08-a-game-you-can-play.md)**, for something
  completely different.
