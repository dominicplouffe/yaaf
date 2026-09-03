# 12 — A content engine that is graded by its readers

> **Why not just a Claude Skill?** A skill writes you a blog post, and it will
> be a decent one. What it cannot do is come back next week knowing which of
> the last nine posts earned the one reply that arrived, keep a bar it did not
> set, or refuse to publish something the person who wrote it was happy with.
> This desk has a seat whose only job is to read the post before you do, and a
> finish line the desk cannot write into its own status report.

**What you get:** posts published as WordPress drafts for you to approve, each
answering one question a real person asks — and an honest account of whether
anybody outside responded.

**What it teaches:** the difference between *published* and *read*, and what it
takes to stop a desk grading itself on the first one.

**What you need:** your Anthropic API key, and a WordPress site with an
application password. Without the WordPress credential the desk still runs —
the posts land in the workspace as drafts and nothing reaches the world.

**Cost:** capped at $150/month on purpose, and the goal says why. The drill
reports it fits if a run costs under $12.50 — check that against a bill you
have seen.

---

## Import it

**+ New project → Start from a setup file →** `12-a-content-engine.json`, then
**replace the two things it shouts at you in capitals**:

- **the subject.** One you actually know something about, so you can tell a
  good post from a plausible one. That judgement is the only thing here the
  platform cannot supply.
- **the standard.** Name the publication you would be compared to. This is the
  line that lets the desk cover something you did not think to ask for — leave
  it and your brief silently becomes the ceiling.

Also set `controlled_domains` to your site. That one is load-bearing: evidence
from a domain the project controls does not count as outside response, and it
is what stops "we published it" being filed as "somebody read it".

## The org, and why it is four teams

| Team | Role | Who | Does |
|---|---|---|---|
| Research | `researcher` | Rey | Finds the question and the sources, and writes down what came back empty |
| Editorial | `blog-writer` | Ada | Turns the research into a post, and publishes it to WordPress as a draft |
| Review | `editor` | Juno | Reads it first, and finds at most three things it fails to say |
| Growth | `marketer` | Sol | Predicts what response would count, then records what actually arrived |

**Review is the seat that makes this different from a skill.** Every other
writing crew in the library used to stop at Editorial, which meant the only
person who read a post before the chairman was the person who wrote it. Juno
finds at most *three* things, ranked, each one naming content that is absent
and which of the goal's standard or beneficiary it fails. The cap is
deliberate: an uncapped reviewer produces a list of everything that could be
better, and a writer who complies with all of it hands back something longer,
more hedged, and no deeper.

Juno never rewrites. Finding and fixing are different seats, and a post Juno
edited is a post nobody independent has read.

## What "done" looks like

The interesting acceptance tests are the ones about *response*, because that is
the thing a content desk will quietly substitute:

```json
{"type": "observable", "target": "every published post carries a source URL for each factual claim, and the editor's findings on it are each fixed or answered in writing"}
{"type": "observable", "target": "each post contains at least one thing not available on the first page of search results for its question, named explicitly in the post's completion evidence"}
{"type": "observable", "target": "a link to a post from a domain not in controlled_domains, or a reply from a named person who is not the chairman"}
```

The third one is the finish line, and it is the only one the desk cannot
satisfy by working harder. That is the point of it.

## The failure this example is really about

A content desk that is measured on output will produce output. Posts published,
words written, keyword rankings, an "SEO score" — every one of those can climb
for a month while nobody outside has read anything, and every one of them is
easier to move than a reader is.

So the goal rules them out by name. `WHAT DOES NOT COUNT` is not decoration:
without it, a desk that cannot reach the finish line quietly re-aims at the
nearest thing it can reach and reports that instead, and the status report
looks identical either way.

The second failure is subtler and it is why the quality bar is written as a
consequence rather than a preference. Competent filler is not a step on the way
to a good post — it costs the same to produce, teaches you nothing about
whether the subject has readers, and gets no second visit, so you never find
out whether they refused the argument or refused the writing.

## What to watch

- **The first cycle's editor findings.** They tell you more about the desk than
  the posts do. Three specific absences means Review is working; three notes
  about tone means the goal did not give it a standard to grade against, and
  that is your line to fix, not Juno's.
- **The search record in the research file.** What came back *empty* is the
  half that proves a question was actually investigated rather than summarised.
- **What Growth predicted versus what arrived.** A prediction written before
  publishing is a test. One written afterwards is a story.
- **The three-cycle clause.** If nothing outside responds, the goal says the
  deliverable is an honest account rather than another post. Watch whether the
  CEO honours that or keeps the queue moving — it is the most informative thing
  this example will show you.

## Try this next

- Turn on the **Show me proof** preset, or set `require_evidence_verification`
  yourself, and a cited URL counts for nothing until the platform has fetched,
  hashed and snapshotted it. This bundle ships with it on.
- Drop Review from the roster for one cycle and read the posts side by side.
  It is the cheapest way to see what the seat is actually buying you.
- [11 — Numbers you can defend](11-numbers-you-can-defend.md) is the same idea
  in a domain where the check is arithmetic rather than judgement.
