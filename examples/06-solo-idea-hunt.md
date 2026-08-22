# 06 — One analyst, all the guardrails

**You want:** one investment idea with an argument that survives attack.
**You don't want:** to pay for an org chart to get it.

**Import:** `06-solo-idea-hunt.json`. Set the Explore **deadline** on the form —
30 days out is the shape this goal assumes.

| | |
|---|---|
| Shape | **Explore** — open scope, bounded time |
| Teams | One. Solo mode: the CEO *is* the team |
| Budget | $150/month |
| Notable | `require_evidence_verification: true` |

## Solo mode

Solo places the CEO as the sole member of the only team, so one brain plans and
executes under **byte-identical policy**. Every rule still binds: the queue
ceiling, the evidence rules, the budget, the audit, the spot-check. What
disappears is the hierarchy, not the discipline.

This is also the single-agent arm of the org-vs-solo experiment
([docs/M2_PROTOCOL.md](../docs/M2_PROTOCOL.md)) — and a completely legitimate
way to run yaaf if you just want the guardrails.

## Why Explore, not Deliver

You do not know what the idea is yet, so you cannot write requirements for it.
Explore is the shape for that: no plan gate, no Plan tab, a bounded window, and
bets with kill dates doing the work instead. Expect the analyst to state
something like:

> **H1:** The market is mispricing X because it is reading the segment change
> as a demand problem. **Kill if** the next 10-Q shows the same trend in
> volumes, or by day 30 if I cannot find the disclosure that would settle it.

You may extend an expired bet exactly **once**, in writing. That is the rule
that keeps a dead idea from being kept warm.

## `require_evidence_verification: true`

Turned on deliberately. In this project an unverified claim counts for nothing
at all — the platform fetches the source, hashes it and snapshots it, and a
404'd citation is worth zero toward the drought and the external floor. One
analyst with nobody to check it is exactly where you want that switch on.

## The honest outcome

If 30 days produce nothing defensible, that is the deliverable, and the note
that says so is the successful one. A desk punished for saying "nothing here"
learns to find something.
