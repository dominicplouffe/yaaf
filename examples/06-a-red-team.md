# 06 — A red team that can block the build

> **Why not just a Claude Skill?** Because you cannot be your own skeptic. Ask
> one agent to build something and check it, and it checks the things it already
> thought of — its blind spots are, by construction, the same in both roles.
> Here a **different agent, with a charter that rewards breaking things,** holds
> a requirement the builders cannot close. That is not a prompt asking politely
> for rigour; it is a refusal in the server.

**What you get:** one small tool that has actually been attacked, and a written
record of what was tried and what held.

**What it teaches:** the strongest argument for multi-agent over one agent —
adversarial review that can genuinely say no.

**What you need:** your Anthropic API key.

**Cost:** capped at $90 — attacking costs more than building, which is the
correct ratio and rarely the one people budget for.

---

## Import it

**+ New project → Start from a setup file →** `06-a-red-team.json`, then pick
what gets built. It ships with a CSV validator, which is a good default because
hardening it is genuinely interesting.

Keep it **small**. The build is not the point; surviving the red team is. Things
that work well:

- a CSV/JSON validator against a schema
- a date-range parser (`"last tuesday"`, `"2026-03-01..2026-03-14"`)
- a template renderer with escaping
- a retry/backoff wrapper
- a config loader with defaults and overrides

Anything with input, edges, and a claim worth falsifying.

## The org, and the rule between them

| Team | Role | Who | Job |
|---|---|---|---|
| Build | `engineer` | Mika (lead), Dev | Writes the code and its tests |
| Red Team | `adversary` | Sasha | Breaks it. **Never edits it.** |

The separation is the mechanism. `adversary`'s charter is explicit that it finds
and they fix, because a red team that patches what it finds stops being able to
tell you whether the fix was real.

Its other charter lines are what make it useful rather than noisy:

- **A finding is real only with the input that reproduces it.** No reproduction,
  no finding — file it as a hunch, separately, or do the work.
- **Never report a defect you have not personally made happen.**
- **"I could not break it" is a complete deliverable.** Explicitly, so it does
  not manufacture cosmetic nitpicks to look busy.

## The plan, and the requirement that matters

Stage it so the gate is real:

| | Requirement | Acceptance test |
|---|---|---|
| R-01 | The tool does the job on well-formed input | `command` — `python3 -m unittest discover -q` |
| R-02 | A first adversarial pass is written up, ranked, each finding reproducible | `observable` — every finding in `deliverables/redteam-01.md` names the exact input |
| R-03 | Every finding from pass one is fixed or explicitly accepted with a reason | `command` — a regression test exists per fixed finding |
| R-04 | **A full adversarial pass finds nothing new** | `observable` — `deliverables/redteam-final.md` records a complete pass with no new findings, and lists what was tried and what could not be tested |

**R-04 is the whole example**, and three separate server rules stop Build from
closing it:

- **Team tasks are claimed by the team lead.** The task that produces
  `redteam-final.md` belongs to Red Team; Mika claiming it is a 403.
- **The requester owns the test.** yaaf refuses to let the agent that *wrote* a
  success test be the agent it judges — *"you wrote this task's success test, so
  you may not also be the one it judges. Whoever asks for the work owns the
  check — that is the whole reason the check means anything."*
- **Nothing but the platform writes `met`.** There is no agent tool for it. On
  an observable a script cannot check, the close lands on **your** desk (or the
  CEO's, under `verdict_policy: ceo_judgment`) — and either way it is disclosed.

None of that is etiquette in a prompt. Each is an HTTP refusal an agent cannot
argue with.

## The attack worth watching for

The `adversary` charter says to break the code deliberately and confirm the
tests notice. A suite that stays green while the code is broken is a finding —
and usually the most valuable one in the project, because it means every other
green result you have been trusting meant nothing.

Look for it in `redteam-01.md`. If it is not there, ask for it in the Boardroom.

## What to watch

- **Findings with no reproduction.** The charter forbids them. If one appears,
  it is a real defect in your red team, and you should fail it on the
  spot-audit — that verdict is recorded and the CEO is told.
- **A red team that never finds anything, ever.** Either the build is
  extraordinary or the attacking is shallow. Read what it says it *tried*; the
  charter requires that list precisely so you can judge.
- **A red team that always finds something.** Manufactured findings to look
  busy is the named failure mode of this seat. Rank-one findings that are
  cosmetic are the tell.
- **Build fixing things Red Team did not raise.** Scope creep with a
  justification attached is still scope creep.

## The honest footnote

That "requester owns the test" rule has one exemption, and it is worth knowing
about because it is a real cost rather than a loophole: **solo mode**. With one
agent in the company there is nobody to hand the check to, so the rule degrades
to nothing and a solo project self-grades every observable.

That is not a bug and it is not swept under the carpet — it is the honest
difference between running one agent and running a company, and it is why
[09 — One idea, thirty days](09-one-idea-thirty-days.md) leans so hard on
*external* evidence the platform fetches itself. When nobody inside can check
you, the check has to come from outside.

## Try this next

- **Add the red team to a project you already trust.** Take
  [03](03-a-desk-the-world-wakes.md) or [04](04-an-overnight-crew.md), add an
  `adversary` team, and make one requirement conditional on its sign-off. This
  shape transfers to anything that ships.
- Then **[07 — Tell me what changed](07-tell-me-what-changed.md)**, back to
  running unattended.
