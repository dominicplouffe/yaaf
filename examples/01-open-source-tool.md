# 01 — Ship an open-source tool and prove strangers use it

**You want:** a small developer tool that exists, is installable, and has real
users who aren't you. **You don't want:** three weeks of commits to a repo
nobody has ever visited, reported back to you as "strong progress."

This is the example where yaaf's evidence rules earn their keep. Anyone can
have agents write code. The hard part is the difference between *shipped* and
*shipped and someone cared*, and that difference is the whole project.

## The org

| Team | Role | Members | Does |
|---|---|---|---|
| Build | `engineer` | Mike (lead), Dana | The tool, its tests, its packaging, its release |
| Docs | `writer` | Priya | README, quickstart, the announcement post |

Two engineers is right here: one can be mid-release while the other handles the
issue that just came in. A third mostly waits.

## The goal

Paste this into the goal field, with your own subject:

> Build and publish a small open-source CLI that converts `.env` files to and
> from 1Password secret references, so developers can commit a safe template
> without hand-editing. Ship it to PyPI under an OSI license with a public
> GitHub repo, a README a stranger can follow in under two minutes, and CI that
> runs on every push. Success is adoption by people unaffiliated with this
> project: GitHub stars, issues, or PRs from accounts we don't control, and
> PyPI downloads above the release-day baseline. First release within 10 days;
> evaluate adoption at day 30.

Why this works: it names the artifact (`PyPI` + `public repo`), the reader
("a stranger… under two minutes"), the deadline, and — critically — it names
what would count as working *before* anyone starts, in terms someone outside
can verify.

## Connect GitHub

Settings → **GitHub workspace**. Each project gets a private repo the runner
clones into the sandbox at lease time, so work survives between runs instead of
dying with the container. Repo events also arrive as **attested evidence** —
the platform saw the webhook, no agent typed it in.

That's the point of wiring GitHub even if you'd have used it anyway: a star
recorded through the connector cannot be invented by an agent having a good
day.

## Controlled domains — the field people fill in wrong

List everything **you** control:

```
github:your-github-account
your-personal-site.dev
```

Anything on this list is barred from counting as external evidence. If you
star your own repo from your own account, the platform knows and it doesn't
count. This feels petty until the first time an agent tries it.

## Success tests that can actually fail

The CEO writes these; here's the standard it'll be held to.

```json
{"type": "command", "command": "python -m pytest tests/ -q && python -m build && twine check dist/*"}
{"type": "observable", "target": "https://pypi.org/project/envref/ lists version 0.1.0 and pip install envref works in a clean venv"}
{"type": "observable", "target": "README quickstart followed verbatim on a clean machine produces a converted .env in under 2 minutes"}
```

The first is run by the platform. The second and third are checkable by any
stranger. What gets refused, every time: `{"type": "observable", "target": "the tool is done and works"}`.

## The bet, and its kill date

Early on the CEO will state a falsifiable hypothesis with a kill date — that's
enforced, not optional. Expect something like:

> **H1:** Developers who commit `.env.template` files will adopt a converter if
> it's a single command. **Kill if** by day 30 the repo has fewer than 3
> unaffiliated interactions and PyPI downloads have not exceeded 50 above
> release-day baseline.

At day 30, one of two things happens. The evidence arrives and the CEO doubles
down. Or it doesn't, and the CEO kills it — you'll get the summary in the
Boardroom. You may extend a hypothesis exactly once, in writing:

```bash
yaaf hypotheses my-tool                      # find the id
yaaf extend hypo_… 2026-10-15T00:00:00Z "Launch post lands Monday; give it two weeks past that"
```

Use that sparingly. The extension exists so a real delay doesn't kill a real
bet; it is not a way to keep a dead one warm.

## Constitution settings that matter here

| Setting | Default | For this project | Why |
|---|---|---|---|
| `monthly_usd_cap` | 1000 | 150 | A CLI tool is not a $1000 problem |
| `queue_ceiling` | 8 | 5 | Forces the release to finish before feature #6 starts |
| `drought_hours` | 24 | 72 | Pre-launch there *is* no external evidence yet; 24h of drought pressure before day one is just noise |
| `approval_threshold_usd` | 250 | 25 | You want to hear about a paid service before it's subscribed to |

`drought_hours` is the one to think about. While a drought is active, internal
task creation is refused — the system's way of saying "stop writing tickets to
yourself and go get a real signal." That's correct on day 12 and unhelpful on
day 1.

## What the first day looks like

1. CEO reads the goal, states H1 with its kill date, creates 3–4 tasks:
   scaffold + CI, core conversion, packaging, README.
2. Mike claims the scaffold, works one item, runs the test command himself, and
   completes quoting the output he actually saw.
3. Dana claims core conversion in parallel; Priya blocks on "nothing to
   document yet" and says so rather than inventing a doc.
4. You get nothing in the Boardroom, because nothing needs you. That's the
   system working.

## Your job

- Approve the PyPI name and anything over $25.
- Spot-check: `yaaf audit my-tool --n 5` deals you random completed work.
  Follow one README quickstart yourself. Fail anything that doesn't hold up —
  `yaaf audit-evidence evid_… --bad --note "quickstart step 3 doesn't work on a clean venv"` — and
  the CEO is told its team's work is suspect.
- Press publish on the announcement when it's ready. Distribution is the part
  agents are worst at and you're best at.

## What will actually go wrong

**Nobody stars it.** This is the expected outcome for most small tools, and
it's the honest result, not a failure of the framework. The value here is that
you find out in 30 days for $60 instead of believing a progress report for
three months.

**The launch post is where adoption lives or dies**, and agents write competent,
forgettable announcements. Write the Show HN yourself; let the team write the
docs.

**Engineers will want to add features instead of shipping.** The queue ceiling
and the "build the smallest thing that makes the success test pass" rule in the
engineer role fight this, and you'll see refusals in the run logs when they
try. Leave the ceiling low.
