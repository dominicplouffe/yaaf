# Examples — ten things people actually run on yaaf

Each file below is a complete worked example: who you are, what you want, the
exact goal text to paste, the teams to hire, the constitution numbers that
matter, and — the part most people skip — **what counts as done**.

They're written to be read in any order. If you've never created a project,
read [docs/CREATING_A_PROJECT.md](../docs/CREATING_A_PROJECT.md) first; it
walks the dashboard wizard in plain English. These examples assume you've done
that once.

| # | Example | What it teaches |
|---|---|---|
| 01 | [Ship an open-source tool](01-open-source-tool.md) | GitHub workspace, external evidence from unaffiliated accounts, hypotheses with kill dates |
| 02 | [A blog that knows if anyone read it](02-company-blog.md) | WordPress drafts as the approval gate, why your own domain can't be evidence |
| 03 | [A client deliverable you can sell twice](03-client-data-audit.md) | MCP data sources, cloning a working project into a per-client twin |
| 04 | [A standing research desk](04-research-desk.md) | Sourced findings, memory curation, spot-auditing claims |
| 05 | [A support desk that feeds the roadmap](05-support-desk.md) | Slack bridge, user complaints as evidence, suggestions instead of self-assigned work |
| 06 | [One agent, all the guardrails](06-solo-agent-governance.md) | Solo mode — budgets, verification, and audit without the org |
| 07 | [Settle org-vs-solo for your own goal](07-run-the-experiment.md) | `yaaf experiment`: two arms, blind judging, a pre-registered decision |
| 08 | [Find your first paying customers](08-first-paying-customers.md) | Approval thresholds, the side-effect ledger, evidence that survives a skeptic |
| 09 | [An overnight crew for a repo you own](09-repo-maintenance-crew.md) | Success tests as commands the platform runs, run caps, `yaaf spend-report` |
| 10 | [Try the whole thing for $0](10-dry-run-dollar-zero.md) | Mock runtime — rehearse your constitution before spending a cent |

## The two things every example has in common

**The goal is the product.** The CEO plans from it and nothing else. Every
example gives you goal text you can paste and edit. Copy the *shape*: the
outcome that exists at the end, the deliverable and who it's for, the deadline,
and where the inputs come from. "Analyze the data" produces a company that
putters at $8/hour.

**Done is defined before work starts.** Every task carries a success test, and
there are exactly two kinds:

```json
{"type": "command",    "command": "pytest tests/ -q && ruff check ."}
{"type": "observable", "target":  "https://pypi.org/project/foo/ shows version 0.2.0"}
```

A `command` is run by the platform, not the agent — it can actually fail. An
`observable` names something a stranger could check. Prose ("verify it works
well") is refused at the API. The examples show what good tests look like for
each kind of work.

## Running an example from the CLI

Every example is doable entirely in the dashboard. If you'd rather script it,
the shape is the same everywhere:

```bash
export YAAF_API=http://localhost:8080 YAAF_ADMIN_TOKEN=…

yaaf seed-roles roles/                      # once per deployment
yaaf create-user you@example.com "Your Name"
yaaf create-project examples/spec.json      # the JSON each example gives you
yaaf set-secret anthropic --project my-slug --kind billing   # value is prompted
yaaf configure my-slug --billing-secret-id secr_…
yaaf start my-slug
```

Then watch it: `yaaf dashboard <slug>`, `yaaf board <slug>`, `yaaf runs <slug>`,
`yaaf evidence <slug>`, `yaaf spend-report <slug> --days 7`. Talk to your CEO
with `yaaf say <slug> "…"`, and spot-check with `yaaf audit <slug> --n 5`.

## The roles you can hire

From [roles/](../roles/), seeded with `yaaf seed-roles roles/`:

| Slug | Role | Good at | Gets secrets scoped |
|---|---|---|---|
| `ceo` | CEO / Project Manager | Planning, bets, kills. The only agent you talk to | — |
| `engineer` | Software Engineer | Code, infra, deploys, data work | `github` |
| `researcher` | Researcher / Analyst | Market scans, evaluations, sourced answers | — |
| `writer` | Writer / Content | Docs, reports, landing copy | — |
| `blog-writer` | Marketing Blog Writer | SEO-aware posts, publishes WordPress drafts | `wordpress` |
| `marketer` | Marketing | Channels, positioning, campaigns | — |
| `gtm` | Go-to-Market | Offers, pricing, researched outreach | — |
| `sales` | Sales | Follow-ups, objections, proposals | — |
| `support` | Customer Support | Real users, reproductions, routing problems back | — |

Roles are versioned. Edit the JSON, re-seed, and existing teams keep running
their pinned version until you adopt the new one — so a prompt edit can't
silently change a project that's mid-flight.

## A note on honesty

These examples describe what the platform enforces, not what the agents will
achieve. yaaf makes it hard for an agent to *lie to you* about progress. It
does not make the agent good at your business. Read the "What will actually go
wrong" section at the bottom of each example — it's the most useful part.
