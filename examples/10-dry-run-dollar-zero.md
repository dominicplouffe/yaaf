# 10 — Rehearse the whole thing for $0

**You want:** to find out what your constitution actually does. **You don't
want:** to find out by spending $400 discovering the drought clock was too
tight.

**Import:** `10-dry-run-dollar-zero.json`. It ships with `runtime: mock`, so it
costs nothing and needs no API key. **Start here if it is your first project.**

| | |
|---|---|
| Shape | **Deliver**, on the mock runtime |
| Teams | Research (`equity-analyst` ×2), Quant (`quant-researcher`), Risk (`risk-manager`) |
| Budget | $25/month — deliberately small, so you watch it trip |
| Cost | $0. No model is called. |

## What the mock runtime is

Scripted stand-in agents that exercise the **real API**, subject to the **real
refusals**. They plan, propose requirements, create tasks, claim them, complete
them, file evidence, and hit every wall your constitution puts up. The only
thing that does not happen is a model call.

So everything you see is true about your rules. Nothing you see is true about
the quality of the work.

## The numbers are set low on purpose

| Rule | Here | Why |
|---|---|---|
| `queue_ceiling` | 6 | So you watch a seventh task get refused |
| `drought_hours` | 6 | So the drought fires within an afternoon, not next week |
| `monthly_usd_cap` | 25 | So the budget warning and the auto-pause are reachable |
| `approval_threshold_usd` | 10 | So an approval actually lands on your desk |
| `chairman_report_interval_hours` | 1 | So you see the cadence refusal work |

## The tour

1. **Start it, then go to Plan.** Watch R-00 — the plan gate. Until scope
   exists, is staged, is estimated, and *fits the money*, no work may be
   created at all. Try accepting a plan that does not fit $25 and read the
   refusal; it shows you the arithmetic.
2. **Accept the plan.** The board opens. Watch tasks appear and get claimed.
3. **Go to Records → Runs.** Every refusal is logged with the rule that fired
   and what to say instead. This is the file to read.
4. **Wait out the drought.** Six hours in, internal task creation is refused
   until something reaches outside. That is the rule most people tune first.
5. **Push the budget.** At 80% the CEO is told to cut burn; at 100% the project
   pauses itself and you get told.
6. **Spot-audit.** Work tab → the sampled close. Fail one on purpose and watch
   the CEO be told its team's work is suspect.

## The deliverable

Write down which rules fired, which fired too early, and which never fired at
all. Then create the real project with those numbers. That is fifteen minutes
that pays for itself the first time a drought clock does not spend a week
nagging a company doing exactly the right thing.

## Switching to real agents

Settings → **Advanced** → runtime `agent_sdk`, then Settings → **Money** →
paste your API key. Or create a fresh project with the same constitution — a
rehearsal is a rehearsal, and starting clean is cheap.
