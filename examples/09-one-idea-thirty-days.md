# 09 — One idea, one agent, thirty days

> **Why not just a Claude Skill?** Because the deliverable is *thirty days of
> not fooling yourself*. A bet that dies on a date you cannot quietly move,
> evidence the platform fetches and snapshots rather than takes your word for,
> and a rule that refuses to let the company write another framework document
> when it has gone two days without finding anything real outside.

**What you get:** one small product idea with an argument that survives attack —
or an honest "there is nothing here", which is the same deliverable.

**What it teaches:** **Explore mode** (no plan, no fixed end), **solo mode**
(one agent under every rule), **bets with kill dates**, and **controlled
domains** — the field that makes external evidence mean anything.

**What you need:** your Anthropic API key.

**Cost:** capped at $60 over the window.

---

## Import it

**+ New project → Start from a setup file →** `09-one-idea-thirty-days.json`.

Two things to change before you create it:

1. **Set the deadline.** Explore is the one shape that takes a bare date —
   open scope, bounded time. Thirty days out is what this goal assumes.
2. **Edit `controlled_domains`.** It ships with placeholders. Put in your own
   site, your own `github:your-account`, anything you control. Read the next
   section for why this is the most important field on the form.

## Controlled domains, and why they are the point

Anything on that list **cannot count as external evidence**. Ever.

Without it, "proof someone wants this" degrades into an agent citing its own
reasoning back to you in a confident voice. With it, plus
`require_evidence_verification: true` (already on in this bundle), the platform:

- fetches every cited source itself, server-side
- hashes and snapshots it, so you can read what it read
- **refuses to count anything from a domain you control**
- and refuses to count anything whose URL does not resolve

So when the Work tab shows you three pieces of verified external evidence, they
are three strangers, in public, saying something. That is a different object
from three paragraphs of plausible argument, and telling them apart is most of
what this example is for.

## Solo mode, and Explore

**Solo** puts the CEO as the sole member of the only team: one brain plans
*and* executes, under **byte-identical policy**. Every rule still binds — the
queue ceiling, the evidence rules, the budget, the audit, the spot-check. What
disappears is the hierarchy, not the discipline.

**Explore** is the shape for a question you cannot write requirements for yet.
There is no plan gate and no Plan tab — instead the agent states falsifiable
bets with kill dates, and the platform enforces them:

> **H1:** Small agencies pay for a thing that reconciles two systems by hand
> every month. **Kill if** by day 20 I cannot find five people describing this
> problem in public in their own words.

At the kill date the bet dies unless **you** extend it — once, in writing, with
a reason. That rule exists so a dead idea cannot be kept warm, including by
you.

## Watch this rule fire

`drought_hours` is 48 here, deliberately short. Go two days with no verified
external evidence and internal task creation is **refused** — the company may
only create work that reaches outside.

That is the anti-navel-gazing rule, and on an idea-hunting project it is the
one that earns its keep. An agent's instinct when it cannot find proof is to
write another framework document. This refuses.

## The honest outcome

If thirty days produce nothing defensible, the deliverable is the note that
says so, and it is a **successful project**. Read that sentence twice: a desk
punished for reporting "nothing here" learns to find something.

## What to watch

- **Evidence that is really opinion.** A blog post arguing the market exists is
  not someone with the problem. A forum thread where somebody describes the
  problem unprompted is.
- **Verification failures.** In the Work tab's proof table, `failed` means the
  platform fetched the URL and got a 404 or a mismatch. Those count for
  nothing, and the drought clock keeps running — which is exactly why a busy
  week can still be in drought.
- **Scope drift into building.** This project finds and proves. It does not
  build. If tasks start looking like implementation, cut them.

## Try this next

- Run it **twice on the same question** — once solo like this, once with a
  three-person org — and compare cost and output. That is the question yaaf
  itself is unsure about, and you can settle it for your own work.
- Then **[10 — Read the filings](10-read-the-filings.md)**, for the strictest
  sourcing rules in the set.
