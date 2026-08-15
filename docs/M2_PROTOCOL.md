# M2 — Baseline experiment protocol

**Status: FROZEN 2026-08-15 — the chairman approved the goal selection (§4:
all three pairs, G1/G2/G3), the rubric weights (§6), the budget, and the
window. Changes from here require a chairman amendment recorded in §9. The
decision gate remains 2026-09-30 (one extension allowed, in writing).**

## 1. Question and stakes

Does an org structure (CEO + lead + employees) beat a single agent under
identical governance, goal, tools, and token budget?

This is the M2 gate from PLAN.md. If the org arm does not beat the single-agent
arm on the preregistered rubric, yaaf-as-org-framework is killed and the
salvage path (governance harness for single agents) takes over. Decision due
**2026-09-30**; the chairman may extend once, in writing.

## 2. Arms

Both arms run on the same yaaf instance, under the same constitution, with the
same role toolset, model tier, egress allowlist, and token budget. The only
variable is org structure.

- **Arm S (single):** one agent with CEO-class permissions (it creates its own
  tasks against the same policy engine) and a task list. No teams, no channels
  beyond boardroom.
- **Arm O (org):** one CEO + one team of 3 (1 lead + 2 employees) in the role
  fitting the goal. Hierarchical comms enforced as usual.

Each goal pair = two projects (one per arm) created from the same template with
identical constitutions and budgets. Arms of the same pair use different
workspaces/accounts so their external evidence cannot mingle.

## 3. Controls

- Same model tier per role class in both arms (frozen at approval time).
- Same token budget per arm per goal (proposed: **15M tokens**; chairman sets).
- Same wall-clock window per goal (proposed: **14 days** from first run).
- Same tools and egress allowlist; any connector available to one arm is
  available to the other.
- Same constitution: queue ceiling, evidence floor, drought rule, approval
  defaults identical across arms.
- Chairman interaction protocol: chairman answers boardroom approvals within
  24h using a fixed decision rubric (approve anything within budget and law
  that does not leak arm identity between projects); chairman does not coach.

## 4. Goal pairs (chairman-approved 2026-08-15: all three run)

External evidence must be **platform-verifiable**: attested via connectors or
verifiable by the server-side evidence verifier against uncontrolled domains,
postdating task creation, from unaffiliated accounts.

- **G1 — Open-source utility.** Build and publish a small, genuinely useful
  developer tool (PyPI or npm + public GitHub repo). External evidence:
  interactions on the published artifact by unaffiliated accounts within the
  window — stars, issues, PRs, package downloads above baseline. Attested via
  GitHub events (actor not in the project's controlled-account registry) and
  registry download stats pulled by the platform.
- **G2 — Curated technical resource.** Research, build, and publish a curated
  dataset or reference resource (e.g., a benchmark table or annotated dataset)
  on a public host. External evidence: unaffiliated stars/forks/citations/
  inbound links within the window, attested as in G1.
- **G3 — Published technical writing with organic reach.** Produce and publish
  a small series of technical posts (own domain is controlled, so the posts
  themselves are *internal* evidence). External evidence: organic inbound —
  comments/mentions from unaffiliated accounts, referral traffic from
  uncontrolled domains via analytics pull, aggregator pickups. Only counts if
  the platform can attest it (analytics connector) or the verifier can snapshot
  it on an uncontrolled domain.

Each goal ships with a per-goal evidence target **N** (proposed: N=10 verified
items) used for normalization in scoring, set at approval time.

## 5. Measures (per arm, per goal)

1. **E — verified external evidence count** (platform-attested or
   verifier-passed only; agent-claimed-unverified counts zero).
2. **Q — blind-judged deliverable quality** (0–100, §6).
3. **C — tokens spent** (ledger total for the project, including killed runs).
4. **T — wall-clock** from first run to last verified evidence (or window end).
5. **Judgment conversion** (diagnostic, not scored): fraction of created tasks
   that ever produced verified external evidence — the per-CEO judgment metric.

## 6. Blind judging and rubric

- Deliverables are exported, stripped of arm-identifying metadata (project
  names, agent names, channel headers), and assigned random labels.
- **Judge panel:** 3 independent judge-model passes (fixed model + fixed
  prompt, temperature 0) + 1 human (chairman) pass, each scoring the rubric
  below; Q = mean of the four passes, equal weight.
- Rubric per deliverable (each 0–25, sum 0–100):
  1. **Fitness:** does it achieve the goal as specified?
  2. **Craft:** correctness, polish, absence of filler or fabrication.
  3. **Utility to a stranger:** would an unaffiliated person plausibly use it?
  4. **Coherence:** does the body of work hang together vs. scattered output?
- Judge prompts and export script are frozen with this protocol; judged only
  after the window closes.

## 7. Scoring and decision rule

Per goal pair, each arm gets composite
**S = 0.35·min(E/N,1) + 0.35·(Q/100) + 0.15·(1 − C/budget) + 0.15·(1 − T/window)**.

- Pair winner: higher S. Margin under 0.03 = tie.
- **Org beats single** iff Arm O wins a strict majority of non-tied pairs and
  at least one pair outright.
- All-ties or majority-ties: org has failed to beat single (the burden of proof
  is on the hierarchy — it costs more to run).
- The result and full metric table are recorded as a **decision** in the
  platform and in `docs/M2_RESULT.md`.

## 8. Threats to validity, and answers

- **Evidence gaming:** only attested/verified evidence counts; spot-audit
  sample doubled during the experiment; any failed audit voids that item and
  flags the stream.
- **Chairman leakage:** fixed interaction rubric (§3); all chairman messages
  logged; boardroom transcripts included in the audit record.
- **Unequal luck in organic reach:** 2–3 pairs rather than one; windows
  identical; arms launch within 24h of each other per pair.
- **Judge bias toward verbosity:** rubric scores fitness/utility, not volume;
  judges see deliverables, not process logs.
- **Self-use bias:** this experiment does not satisfy the external-validation
  condition of PLAN.md (unaffiliated evidence by 2026-12-31); it only decides
  the org question.

## 9. Amendments

- **2026-08-15 — frozen.** Chairman approved goal selection (all three pairs:
  G1 open-source utility, G2 curated technical resource, G3 technical writing
  with organic reach), rubric weights as drafted (evidence .35, quality .35,
  cost .15, time .15), budget (15M tokens / $300 per arm), and the 14-day
  window. No other changes; protocol as pre-registered.

## 10. Runbook (operational)

1. Chairman approves goals, budget, window, weights → freeze this doc (commit).
2. Provision pairs via the experiment CLI (`yaaf experiment init`, M2 harness).
3. Launch arms per pair within 24h of each other; record start decision.
4. During window: normal governance; doubled spot-audit cadence.
5. Window close: export deliverables, run blind judging, compute composites.
6. Record decision; update PLAN.md status appendix; act on the gate.
