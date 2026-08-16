# 04 — A standing research desk

**You want:** to ask a hard question on Sunday night and have a sourced answer
waiting Monday morning. **You don't want:** four confident paragraphs of
synthesis with no citations, where two of the claims are inventions and you
can't tell which two.

Research is where agents are at their strongest, and yaaf is what makes that
strength bankable: a claim without a source doesn't ship, findings compound in
curated memory instead of evaporating, and you get dealt random samples of the
work so the quality bar is yours, not the model's.

## The org

| Team | Role | Members | Does |
|---|---|---|---|
| Desk | `researcher` | Ana (lead), Bo, Cy | Answers questions with sources; says what's verified vs. inferred |
| Editorial | `writer` | Dev | Turns findings into briefs a busy person reads in five minutes |

Three researchers is the one place a real team beats a soloist: three
questions genuinely run in parallel, and the lead can decompose a fat question
into three narrow ones.

## The goal

> Run a standing research desk for our product team. Answer the questions the
> chairman posts, each with a written brief: the direct answer first, then the
> evidence, with a source URL for every factual claim and an explicit split
> between what was verified and what is inference. When no question is
> outstanding, maintain a live competitive landscape file for the four vendors
> in our space — pricing, positioning, shipped features, public complaints —
> and flag material changes in the boardroom within 24 hours of noticing them.
> A brief with an uncited claim is not delivered.

The last sentence is the one that matters. It gives the CEO a standard to
reject work against, and you'll see it invoked.

## How you actually feed it questions

You never create tasks — that's the CEO's job and the API enforces it. You ask
in the Boardroom, and the CEO turns questions into tasks:

```bash
yaaf say research-desk "Question, priority high: has anyone shipped per-seat-to-usage pricing migration tooling for B2B SaaS? I want to know if this is a product or a feature. Answer by Thursday."
```

Or type it in the Boardroom tab. The CEO keeps a resident chat session, so it
answers in seconds — the typing indicator is real — and the background research
runs through the governed queue. Chatting never eats your run caps.

Ask like you'd ask a good analyst: what decision the answer feeds, the deadline,
and what would change your mind.

## Success tests for questions

```json
{"type": "observable", "target": "briefs/2026-08-19-usage-pricing.md answers whether migration tooling exists as a standalone product, with 8+ sources, each claim linked, verified/inferred marked per section"}
{"type": "observable", "target": "Competitive file lists current public pricing for all four vendors with the URL and retrieval date for each figure"}
```

The retrieval date is not pedantry. Pricing pages change, and a six-week-old
number presented as current is the most common way a research brief becomes
wrong without anyone lying.

## Memory is the feature that makes this compound

A research desk that forgets is a search engine with extra steps. The
constitution caps memory — 60 project-scope entries, 40 per team — and the caps
are refusals, not silent evictions. When the team hits 40, it must *curate*:
supersede a stale entry or explicitly forget it. Nothing gets auto-dropped
behind your back.

That's the mechanism that keeps "we already looked at this in June, here's what
we found and what's changed since" possible after four months. Check what it
knows in the **Memory** tab; if the entries read like a junk drawer, tell the
CEO to curate and it will.

## Constitution settings that matter here

| Setting | Default | For this desk | Why |
|---|---|---|---|
| `monthly_usd_cap` | 1000 | 250 | Reading a lot of web is the cost driver |
| `queue_ceiling` | 8 | 8 | Keep the default; questions genuinely parallelize |
| `drought_hours` | 24 | 336 (14 days) | Internal research produces almost no external evidence — see below |
| `memory_cap_team` | 40 | 80 | This is a memory-heavy job; give it room, keep curation mandatory |
| `wakes_per_agent_per_hour` | 6 | 6 | Default is fine; three researchers at 6 wakes is plenty of throughput |

**Tuning note:** an internal desk delivers to you, not to the world, so its
output is internal evidence by design. Set `drought_hours` to 14 days and the
evidence machinery does bookkeeping here instead of enforcement — the job it
does in [01](01-open-source-tool.md) or [08](08-first-paying-customers.md),
where the outside world is the point. Your quality gate on this project is the
spot audit below, and it's a strong one.

## Your job — and here it's the whole job

`yaaf audit research-desk --n 5` deals you a random sample of recent completed
work and evidence claims. For a research desk, do this **weekly, without
exception**, and check the one thing that matters:

> Open three cited URLs. Do they say what the brief claims they say?

Not "does the source exist" — whether it supports the claim. That's where
research work fails, and no automated check catches it. When one doesn't hold
up:

```bash
yaaf audit-evidence evid_… --bad --note "Source 3 is a vendor blog post from 2023; brief presents it as current independent pricing"
```

The CEO is told its team's work is suspect and adjusts. You are the one
reviewer in this system that doesn't approve everything put in front of it —
AI reviewers approve nearly everything, which is exactly why the sample exists.
