# 07 — Settle org-vs-solo for your own goal

**You want:** to know whether a CEO plus a team actually beats one good agent
with the same budget, tools, and rules — for *your* kind of work, not in the
abstract. **You don't want:** a vibes-based answer, which is currently the only
kind available anywhere.

This is the experiment the repo runs on itself
([docs/M2_PROTOCOL.md](../docs/M2_PROTOCOL.md)), packaged so you can run it on
your own goal. Two arms, identical governance, blind judging, a decision rule
written down *before* the results exist.

## What the harness does

`yaaf experiment` provisions two projects per goal:

- **Arm S (single)** — solo mode. One agent with CEO-class permissions plans
  and executes. No teams, no channels beyond the boardroom.
- **Arm O (org)** — one CEO plus a team of three (lead + two employees) in the
  role that fits the goal. Hierarchical comms enforced as usual.

Same constitution, same model tier, same token budget, same tools, same egress
allowlist. The only variable is the org structure. Both arms of a pair get
different workspaces so their external evidence can't mingle.

## Write your spec

Copy [experiments/m2.spec.json](../experiments/m2.spec.json) and replace the
pairs with your own goals:

```json
{
  "experiment": "my-test",
  "chairman_email": "you@example.com",
  "billing_mode": "api_key",
  "judge_model": "claude-opus-5",
  "weights": { "evidence": 0.35, "quality": 0.35, "cost": 0.15, "time": 0.15 },
  "tie_margin": 0.03,
  "window_days": 14,
  "budget_tokens": 15000000,
  "budget_usd": 300,
  "evidence_target": 10,
  "pairs": [
    {
      "key": "g1-integration",
      "worker_role": "engineer",
      "goal": "Build and publish a working Stripe→QuickBooks reconciliation script as an open-source repo, with tests and a README a stranger can follow. External evidence: stars, issues, forks, or PRs from accounts unaffiliated with this project, attested via the GitHub connector.",
      "controlled_domains": { "single": [], "org": [] }
    }
  ]
}
```

Two things to get right before you start, because changing them later
invalidates the result:

**The weights.** What are you actually optimizing? The defaults say external
evidence and quality matter equally (0.35 each) and cost and speed matter less
(0.15 each). If you're running this to decide whether to pay for an org's token
overhead, raise `cost`.

**`evidence_target`.** The number of verified external evidence items that
counts as full marks. Set it to something an arm could plausibly hit in the
window; set it too high and both arms score near zero on the dimension you
cared most about.

## Run it

```bash
yaaf experiment init my-test.spec.json
```

Both arms of every pair are created **paused**. Start them within 24 hours of
each other — a week's head start is a confound, not a result:

```bash
yaaf start my-test-g1-integration-single
yaaf start my-test-g1-integration-org
```

Then leave them alone for the window. Check in with:

```bash
yaaf experiment status my-test.spec.json
```

```
== g1-integration (target N=10, budget 15,000,000 tokens)
  single:  RUNNING  E=  4  C=   3,102,884  first_run=2026-08-16T09:02  last_evidence=2026-08-24T11:40  conversion=0.71
     org:  RUNNING  E=  6  C=   7,440,119  first_run=2026-08-16T09:05  last_evidence=2026-08-25T08:12  conversion=0.63
```

`E` is verified external evidence. `C` is tokens. `conversion` is the CEO
judgment metric. The interesting column is usually `C`, and it usually isn't
close.

## The chairman protocol — the part that ruins experiments

You are a confound. Discipline yourself in writing before day one:

- **Answer both arms identically.** Approve anything within budget and law.
  Same response time, same detail, both arms.
- **Do not coach.** No "have you considered posting it to Hacker News" to the
  arm you like. If you help one arm, you've measured your own preference.
- **Never leak arm identity across projects.** Don't paste the org's plan into
  the solo boardroom.
- **Answer within 24 hours**, or the arm you're slower with loses time it
  should have had.

The honest failure mode of a home-run A/B is that you unconsciously nurse the
arm you want to win. Writing the rules down first is the only defense.

## Judge it blind

When the window closes:

```bash
yaaf experiment export my-test.spec.json --out my-bundles
```

Each arm's deliverables and evidence are bundled under a **random label**, with
anything identifying the arm stripped. The label→arm mapping is sealed in
`mapping.sealed.json`. **Do not open it.**

```bash
export ANTHROPIC_API_KEY=…
yaaf experiment judge my-bundles --passes 3
```

Three model passes per bundle on a four-dimension rubric, 0–25 each: fitness,
craft, utility, coherence. Then add your own pass — you are the human judge and
your score carries equal weight with the model's mean:

```bash
yaaf experiment score my-bundles bundle-8f3a1c07 19 16 21 18
```

Score every bundle before you unseal anything. It is remarkable how differently
you read a deliverable when you don't know which arm made it, which is exactly
the point.

## Decide

```bash
yaaf experiment decide my-test.spec.json --bundles my-bundles --out MY_RESULT.md
```

This unseals the mapping, computes the composite per arm —

```
S = 0.35·min(E/N, 1) + 0.35·(Q/100) + 0.15·max(1 − C/budget, 0) + 0.15·max(1 − T/window, 0)
```

— applies the tie margin, and writes the result document. A margin under
`tie_margin` (0.03) is a tie, not a win. Ties are the most likely outcome and
the most useful one: if an org can't beat a soloist by 3% on your own goal,
you've learned something worth more than the token cost.

## What it costs

Two arms × 15M tokens is real money — the frozen M2 spec budgets $300 per pair.
Before committing, run one pair, not three. And run the pair on a goal you
actually want the output of; a throwaway goal costs the same and leaves you
with nothing but a number.

## What will actually go wrong

**Your goal isn't parallelizable, and you learn nothing about orgs.** If the
work is one serial thread, the org arm is a soloist with a project manager and
a coordination bill. Pick a goal with genuinely independent parts if you want
the org thesis tested rather than dismissed by construction.

**One arm hits an external blocker** — an API outage, a suspended account — and
the comparison is ruined. Note it and rerun that pair; don't let a confound into
the result because you'd rather not spend the tokens again.

**The result is a tie and you're disappointed.** Read the token column anyway.
"Same output, 2.4× the cost" is a decision, and it's the one this repo is most
prepared to act on: if the org doesn't win, yaaf-as-org-framework gets shelved
and the governance layer stands on its own.
