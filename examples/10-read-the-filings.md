# 10 — Read the filings

> **Why not just a Claude Skill?** A skill will read a 10-K and write you a
> note. What it will not give you is the thing you actually need when somebody
> asks *"how do you know?"* — every claim fetched and snapshotted by the
> platform, every computed figure shipped with the script that computed it,
> every decision and dollar logged, and a sample of the work dealt back to you
> to check. The note is the easy half. The record is the deliverable.

**What you get:** an initiation note on one public company, built only from its
own filings, that a skeptic could attack.

**What it teaches:** a **review gate in the pipeline** — nothing ships until a
different agent, with a different job, has signed it off — and the strictest
sourcing discipline in the set. It also introduces the markets role family.

**What you need:** your Anthropic API key. **Nothing else.** Every filing this
project reads is on [SEC EDGAR](https://www.sec.gov/edgar), which is free,
public, and needs no account. If you have ever been told a research desk
requires a data subscription: not for this.

**Cost:** capped at $60. Filings are long; the analysts read a lot.

---

## Import it

**+ New project → Start from a setup file →** `10-read-the-filings.json`, then
**name the company** in the goal.

Pick one you actually have an opinion about — a company whose product you use,
or an employer, or something you own. The note is far more interesting when you
can argue with it.

## The org

| Team | Role | Who | Does |
|---|---|---|---|
| Research | `equity-analyst` | Maya (lead), Tobias | Reads the filings, does the arithmetic in code, writes the note |
| Review | `compliance-officer` | Owen | Reads what is about to ship and says PASS, FIX, or ESCALATE |

The CEO here is `cio` — the same planning charter as the ordinary `ceo`, plus
a hard line the whole desk runs under: **this company recommends and never
trades.** It never places an order, never touches a brokerage credential, and
nothing it writes is advice for anyone but you. That is in the role prompt
three times over, and the goal repeats it, because it is the thing that must
not drift.

## Primary sources, and what that rules out

The `equity-analyst` charter is unusually strict, and this is why the example
exists:

> Start from the 10-K, the 10-Q, the 8-K, the earnings release, the transcript,
> the proxy. Press coverage and sell-side summaries are **leads, not
> evidence**. Every figure carries its period and its source URL. Say which
> claims you verified and which you inferred.

And the arithmetic: computed in code, in the workspace, with the calculation
shown in the note so a reader can redo it. A margin you computed is a fact; one
you eyeballed is a guess.

## The compliance gate

Owen reads everything before it reaches you and returns one of three verdicts:

- **PASS** — one line, and the note ships.
- **FIX** — the exact edits that would make it shippable, quoting the sentence
  objected to rather than describing it.
- **ESCALATE** — a real legal question, which comes to **you**. The reviewer is
  explicitly not a lawyer and will not pretend to be one.

What it checks: research versus personalised advice; guaranteed or
implied-certain returns; hypothetical results labelled as hypothetical; every
material claim publicly sourced and licensed; positions disclosed; nothing that
could read as promotion.

For a note you write for yourself this is mostly belt-and-braces. The reason it
is in the example anyway is that **a review gate is a shape worth stealing** —
put a differently-motivated agent between the work and the world, and make the
requirement "passed review with no open FIX items" rather than "the work is
good".

## What "done" looks like

```json
{"type": "observable", "target": "deliverables/note.md tracks each headline number over eight quarters, and every figure links to the filing it came from"}
{"type": "observable", "target": "deliverables/note.md names at least one place where management's framing and the statements disagree, with the page reference"}
{"type": "observable", "target": "deliverables/note.md states the bear case in terms a bear would accept, and names a falsifier as a number in a future filing"}
{"type": "observable", "target": "deliverables/compliance-review.md records a PASS dated after the final revision of the note"}
{"type": "command",    "command": "cd /workspace && python3 analysis/checks.py --assert-sources-resolve"}
```

The fourth is the gate. The fifth is worth stealing anywhere you cite things:
a tiny script that walks the links and fails if one does not resolve.

## What to watch

- **The bear case is the tell.** If it is a sentence, the work was not done.
  Fail it on the spot-audit.
- **Eight quarters, not two.** Two quarters can be made to say anything. The
  goal asks for eight because trends are where the story is.
- **Pick one figure a week and open the filing.** Four minutes. It is the
  difference between research and prose that sounds like research.
- **A reviewer that passes everything** is not doing its job any more than one
  that blocks everything. Check its rejection rate over a few notes.

## Try this next

- **Add `risk-manager` or `macro-analyst`** to the org and watch the argument
  change. The markets family in [`roles/`](../roles/) has eleven roles; this
  example uses three.
- **Steal the shape.** The review gate is not about finance. Any project where
  something goes outside — a newsletter, a customer email, a public API change
  — gets better with a differently-motivated agent between the work and the
  world.

---

That is the set. If you have run these in order you have used every project
shape, both authority settings, command tests and observable tests, cycles,
evidence verification, drought, spot-audit, solo mode and a three-team org —
which is most of what yaaf is. What is left is pointing it at something you
actually need.
