# The `yaaf` CLI

Everything the dashboard does, scriptable. Useful for provisioning, CI, and
poking at a running deployment.

```bash
export YAAF_API=http://localhost:8080
export YAAF_ADMIN_TOKEN=…
```

Both have fallbacks: `YAAF_API` defaults to `http://localhost:8080`, and the
token is read from `deploy/.env` (or `.env`) if you run from the repo root, so
in practice you can usually just type `yaaf`.

Every command that takes a project accepts **either a slug or an id**.

---

## Setup

| Command | What it does |
|---|---|
| `yaaf seed-roles [DIR]` | Load every `*.json` in the directory (default `roles/`) into the role library. Re-running creates new role *versions*. |
| `yaaf create-user EMAIL NAME` | Create a user. Idempotent by email. |
| `yaaf create-project SPEC_FILE` | Create a project from a JSON spec (shape below). |
| `yaaf set-secret NAME [--project P] [--scopes a,b] [--kind service\|billing]` | Store an encrypted secret. **The value is prompted** — never echoed, logged, or left in shell history. Omit `--project` for a platform-level secret. |
| `yaaf configure PROJECT [--runtime …] [--billing-secret-id …] [--billing-mode …] [--ceo-heartbeat-hours …] [--max-concurrent-runs …]` | Update mutable configuration. **Never the constitution** — that's an amendment. |

### Project spec shape

```json
{
  "slug": "my-project",
  "name": "My Project",
  "goal": "A goal the CEO can plan from — the outcome, the deliverable, who it is for, the deadline, and where the inputs come from.",
  "chairman_email": "you@example.com",
  "billing_mode": "api_key",
  "controlled_domains": ["mycompany.com"],
  "constitution": {"queue_ceiling": 5, "monthly_usd_cap": 300},
  "config": {"runtime": "mock", "ceo_heartbeat_hours": 4},
  "ceo_role_slug": "ceo",
  "ceo_name": "CEO",
  "teams": [
    {"role_slug": "engineer", "name": "Engineering",
     "employees": ["Ada", "Grace"], "lead": "Ada"}
  ],
  "solo": false
}
```

- `constitution` holds **overrides** onto the defaults; omit it for stock rules.
- `controlled_domains` are the domains that **cannot** count as external
  evidence — your own site proving your own success is not proof.
- `solo: true` places the CEO as the sole member of the only team (which must
  have no employees), so one brain plans and executes under identical policy.

## Lifecycle

```bash
yaaf start my-slug
yaaf pause my-slug --reason "waiting on the client"
yaaf stop  my-slug --reason "shipped"
```

## Observation

| Command | Shows |
|---|---|
| `yaaf projects` | Every project with status and headline numbers. |
| `yaaf dashboard PROJECT` | The full project dashboard payload. |
| `yaaf board PROJECT` | The task board. |
| `yaaf org PROJECT` | Teams, leads, employees, who's awake. |
| `yaaf channels PROJECT` | Channels and their ids. |
| `yaaf tail CHANNEL_ID [--limit 30]` | Recent messages, formatted, frozen threads marked. |
| `yaaf runs PROJECT [--limit 20]` | Recent runs: agent, wake reason, cost, status. |
| `yaaf ledger PROJECT` | Spend entries. |
| `yaaf evidence PROJECT` | External evidence and verification status. |
| `yaaf hypotheses PROJECT` | Live bets and their kill dates. |
| `yaaf approvals PROJECT` | Pending approvals. |
| `yaaf audit PROJECT [--n 5]` | A random spot-audit sample of recent evidence and completed tasks. |

## Chairman actions

| Command | What it does |
|---|---|
| `yaaf say PROJECT "message"` | Speak in the boardroom — your only channel. |
| `yaaf decide APPROVAL_ID --approve\|--deny [--note …]` | Decide an approval. |
| `yaaf extend HYPOTHESIS_ID NEW_KILL_DATE REASON` | Your one-time extension of an expired bet (ISO datetime). |
| `yaaf unfreeze MESSAGE_ID` | Release a thread the pair circuit breaker froze. |
| `yaaf audit-evidence EVIDENCE_ID --ok\|--bad [--note …]` | Record your verdict on a piece of evidence. |

Amending the constitution is deliberately not a one-liner — it needs a written
reason. Use Settings → Rules in force, or `POST
/admin/projects/{id}/amendments` (see [GOVERNANCE.md](GOVERNANCE.md#changing-a-rule)).

## Egress

```bash
yaaf egress-sync [--out deploy/proxy/generated] [--proxy-container deploy-proxy-1]
```

Fetches the per-role squid config from the API, writes it, and reloads the
proxy. **Only needed on multi-box setups** — the single-box compose file has
the runner do this automatically. Run it on the box that hosts `deploy/`.

Without `--proxy-container` it prints the reload command for you to run.

## Spend analysis

```bash
yaaf spend-report [PROJECT] [--days N] [--all-projects]
```

Breaks LLM spend down by agent, role, model, and wake reason. Defaults to the
current calendar month.

> This one reads the **database directly**, not the API: it needs
> `YAAF_DATABASE_URL`, not `YAAF_API`/`YAAF_ADMIN_TOKEN`. Run it where the
> server runs, or point `YAAF_DATABASE_URL` at the same Postgres.

## The experiment harness

`yaaf experiment` runs the org-vs-single-agent experiment in
[M2_PROTOCOL.md](M2_PROTOCOL.md). Two arms per goal, identical governance,
blind judging, a pre-registered decision rule.

| Command | What it does |
|---|---|
| `yaaf experiment init SPEC` | Provision both arms for every goal pair. Created **paused** — you start each pair's arms within 24h of each other, per protocol. |
| `yaaf experiment status SPEC` | Live metric table per pair and arm. |
| `yaaf experiment export SPEC …` | Bundle each arm's deliverables under sealed random labels. The label→arm mapping is written separately — **do not open it until scores are recorded.** |
| `yaaf experiment judge …` | Judge-model passes over every blind bundle (needs `ANTHROPIC_API_KEY`). |
| `yaaf experiment score BUNDLES LABEL FITNESS CRAFT UTILITY` | Record your own rubric pass for one blind bundle. |
| `yaaf experiment decide …` | Unseal the mapping, compute the preregistered composites, apply the decision rule, write the result document. Run only after judging. |

See [`examples/07-run-the-experiment.md`](../examples/07-run-the-experiment.md)
for a walkthrough on your own goal.

---

## A full provisioning script

```bash
export YAAF_API=http://localhost:8080 YAAF_ADMIN_TOKEN=…

yaaf seed-roles roles/                                        # once per deployment
yaaf create-user you@example.com "Your Name"
yaaf create-project my-spec.json
yaaf set-secret anthropic --project my-slug --kind billing    # value prompted
yaaf configure my-slug --billing-secret-id secr_…
yaaf start my-slug

yaaf say my-slug "Morning. What's the plan for the week?"
```
