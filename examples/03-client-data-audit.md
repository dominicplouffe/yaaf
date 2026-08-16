# 03 — A client deliverable you can sell twice

**You want:** to take a client's data, analyze it, and hand back a report worth
paying for — and then do the same thing for the next client next week without
rebuilding anything. **You don't want:** a bespoke pile of prompts per customer
and a growing suspicion that the numbers in the deck were made up.

The first run of this is a project. The second run is a **clone** — same teams,
same constitution, same credential wiring, pointed at a different data source.
That's the whole business model in one feature.

## The org

| Team | Role | Members | Does |
|---|---|---|---|
| Data | `engineer` | Mike | Pulls from the client's data source, crunches, produces tables |
| Research | `researcher` | Suzy | Benchmarks, what "good" looks like in this vertical |
| Report | `writer` | Janet | Turns findings into something a business owner can act on |

One person per team. A sole member acts as their own lead, so you get no
coordination overhead and no meeting simulation.

## The goal

Written for an Amazon PPC audit; swap the subject for whatever you audit —
ad spend, cloud bills, subscription sprawl, warehouse throughput.

> Audit the customer's Amazon advertising account and produce a plain-English
> deck a non-technical business owner can act on: where money is being wasted,
> which campaigns to fix first in priority order, and the expected monthly
> savings for each fix with the arithmetic shown. All numbers come from the
> connected "ppc" data source — use real figures only, never illustrative or
> estimated ones, and state the date range every figure covers. Final deck
> delivered by August 20th.

The two clauses doing the heavy lifting: *"the arithmetic shown"* and
*"never illustrative or estimated ones."* Analysis work fails in one specific
way — a confident number nobody can trace — and these make that failure visible
in the deliverable itself.

## Connect the data source

Settings → **Data sources — MCP servers**. One form:

| Field | Value |
|---|---|
| Name | `ppc` |
| URL | `https://api.client-example.com:3845/mcp` — the full address, not a hostname |
| Token | only if the server requires one |
| Roles | Software Engineer only |

Save. Then everything happens by itself: the URL is stored encrypted, the
egress allowlist opens exactly that host for exactly that role, the proxy
reloads within about fifteen seconds, and the next engineer run finds the
source's tools simply *there*. The save also calls the server and tells you if
it doesn't answer — you find out now, not at 3am tomorrow when a run burns
tokens against a dead endpoint.

The researcher and writer never get the source. That's not tidiness; it's the
containment model. A role that can't reach the client's data can't leak it.

## Success tests for analysis work

The failure mode here is a beautiful chart built on nothing, so tie every test
to the source:

```json
{"type": "command", "command": "python analysis/wasted_spend.py --range 2026-05-01:2026-07-31 && python -m pytest analysis/tests -q"}
{"type": "observable", "target": "findings.md lists the top 10 wasted-spend campaigns with campaign id, spend, ACOS, and the source query that produced each row"}
{"type": "observable", "target": "Every savings figure in the deck traces to a row in findings.md; spot-recomputing any two by hand matches"}
```

That last one is the good one. It makes the deck auditable by a stranger with a
calculator — which is exactly what your client will be.

## Then clone it for the next client

This is the payoff. Settings → **Duplicate this project**:

- Give the twin a slug and name (`ppc-audit-northwind`).
- The clone copies teams, roles, constitution, config, and the credential
  wiring — a clone never leaves this deployment, so it doesn't need you to
  re-enter secrets.
- Point the twin's data source at the new client's URL. The clone checks the
  new source answers before you start; a data source that can't respond is
  exactly what a clone exists to rule out.

```bash
yaaf projects                  # find the source project
# clone from Settings → Duplicate this project, then:
yaaf say ppc-audit-northwind "Same audit, new client. Data source 'ppc' now points at Northwind. Deadline Sept 4. Their spend is 4x the last one — expect the long tail to matter more."
```

Ten minutes from "we won the deal" to a company working on it.

For moving a setup to a *different* deployment — a colleague's machine, a
server — use the **Setup file** instead (Settings → Setup file). It exports the
shape without the secrets: teams, constitution, config, and the data-source
wiring stripped of credentials. The importing side supplies its own keys.

## Constitution settings that matter here

| Setting | Default | For this project | Why |
|---|---|---|---|
| `monthly_usd_cap` | 1000 | 200 | Price the audit knowing this is your COGS |
| `queue_ceiling` | 8 | 6 | Analysis fans out fast; keep it converging |
| `drought_hours` | 24 | 336 (14 days) | This is an internal deliverable — there is no external world to hear from until the client replies |
| `approval_threshold_usd` | 250 | 50 | Nothing here should need to buy anything |

`drought_hours` matters more here than anywhere else. Client-deliverable work
produces almost no external evidence by design — the deck goes to one person
under NDA. Leave it at 24h and the system will spend the engagement telling a
team that's doing exactly the right thing to go find a stranger.

## Your job

- **Read the findings before the deck.** If `findings.md` is thin, the deck
  will be confident and thin.
- **Recompute two numbers by hand.** Every engagement. It takes four minutes
  and it is the difference between a service you can defend and one you can't.
- **Answer the ambiguity.** Halfway in, the CEO will ask something like "three
  campaigns have negative margin but drive the top-selling SKU's rank — do we
  recommend killing them?" That question is why you're in the loop.
