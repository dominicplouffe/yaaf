# 02 — Settle an argument, with sources

> **Why not just a Claude Skill?** Again, you largely could — and again, do it
> here anyway, because this is where the first genuine difference shows up.
> Turn on `require_evidence_verification` (see the end of this page) and a cited
> URL counts for **nothing** until the platform has fetched, hashed and
> snapshotted it server-side. A dead link stops being a confident sentence and
> starts being a zero. No amount of prompting gets you that; it is a different
> process doing the checking.

**What you get:** a memo that answers one question you are stuck on, with
enough evidence that you could defend the answer to someone who disagrees.

**What it teaches:** the difference between an answer and a *sourced* answer —
and what yaaf does to agents that blur it.

**What you need:** your Anthropic API key. The research is done with the web
tools your key already pays for; there is no search subscription.

**Cost:** capped at $40; a well-scoped question lands well under it.

---

## Import it

**+ New project → Start from a setup file →** `02-settle-an-argument.json`,
then **replace the question in the goal**. It ships with *"should a small team
start on Postgres or SQLite?"* in capitals so you cannot miss it.

Good questions for this shape:

- *"Should we write this service in Go or keep it in Python?"*
- *"Is a four-day week actually working at the companies that tried it?"*
- *"Which of these three suppliers should we shortlist, and why not the others?"*
- *"Does our industry's certification actually get people hired?"*

Bad questions: anything where you already know the answer you want. The desk
will find it for you, and you will have paid to be agreed with.

## The org, and why it is two teams

| Team | Role | Who | Does |
|---|---|---|---|
| Research | `researcher` | Rey (lead), Sol | Finds and reads sources, reports findings with URLs |
| Editorial | `writer` | Ada | Turns findings into a memo a person will finish |

Two researchers, not one: the point of a second is that they can chase the two
sides in parallel and disagree. A single researcher building a case tends to
build the case it started with.

## What "done" looks like

The interesting acceptance tests here are the ones about *sourcing*, because
that is the thing an agent will quietly skip:

```json
{"type": "observable", "target": "deliverables/memo.md states the recommendation in its first two sentences"}
{"type": "observable", "target": "every factual claim in the memo carries a source URL that resolves, and claims without one are labelled as opinion"}
{"type": "observable", "target": "the memo contains the opposing case argued at its strongest, naming the conditions under which it wins"}
{"type": "observable", "target": "the memo ends with an observable that would change the recommendation"}
```

Note what is *not* there: nothing says "the memo is good". Prose like that is
refused at the API, and rightly — it is a test that cannot fail.

## The failure this example is really about

Ask an ordinary assistant a comparison question and you get a fluent answer
with plausible-sounding facts in it. Some are right. You cannot tell which.

yaaf's answer is not "the model tries harder". It is:

- The `researcher` role's charter is *"a claim without a source is an opinion
  and does not ship"*, and it is told to say which claims it verified and which
  it inferred.
- Requirements about sourcing are **accepted by you**, so the standard is set
  before the work starts rather than argued about after.
- When something closes on an `observable` test, it lands in your **spot-audit
  sample**. Open one source. If it doesn't say what the memo claims, fail it —
  the CEO is told its team's work is suspect.

That last one is thirty seconds and it is the whole system. Do it once.

## What to watch

- **A memo with no opposing case** means the desk fell in love. Cut the
  requirement, reword it harder, and say so in the Boardroom.
- **Sources that are all one publication** is a finding about the research, not
  about the question.
- **"It depends"** is a legitimate answer *if* it says what it depends on, in
  terms you could go and check. The goal asks for exactly that.

## Try this next

- Turn on **Show me proof** at creation (or `require_evidence_verification` in
  Settings → Rules). Now a cited URL counts for nothing until the platform has
  fetched, hashed and snapshotted it — and a dead link counts for nothing at
  all, however confident the sentence around it was.
- Then **[03 — A desk the world wakes up](03-a-desk-the-world-wakes.md)**, and
  everything after it, where a Claude session stops being an option at all.
