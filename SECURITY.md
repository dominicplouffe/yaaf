# Security

yaaf runs language models with credentials, a shell and a network, on your
hardware, unattended, for weeks. That is the product. This page says what
contains them, what does not, and which of it has been looked at by somebody
other than the author — which, today, is nobody.

**Reporting:** email the address on the commit log, or open a GitHub security
advisory. Not a public issue. There is no bounty; there is a fast reply.

---

## The trust boundaries

There are four principals and they are not equal.

| Principal | Holds | Can |
|---|---|---|
| **Chairman** | the admin bearer token | everything |
| **Claude, via MCP** | its own scoped token, never the admin one | only what its scope names; nothing irreversible in one call |
| **An agent run** | a per-run token, one run, one agent, one project | only the `/agent/*` surface, only inside its own project |
| **Everything an agent reads** | nothing | nothing — and it will try |

The fourth row is the one that matters. Web pages, fetched documents, Slack
messages, another agent's output: all of it is **input**, and none of it is
trusted. Anything an agent wrote arrives at the chairman's client wrapped and
labelled untrusted (`yaaf/server/mcp/envelope.py`). An agent's own token is
scoped so that a compromised agent is bounded by the project it belongs to.

## What contains an agent run

Four things, and it is worth being exact because a fifth used to be on this list:

1. **A fresh container per run.** No privileges (`no-new-privileges`), CPU and
   memory caps, and it dies when the run does.
2. **A role-scoped credential set.** A role declares `secret_scopes`; it gets
   those and nothing else. File-shaped credentials are written to a tmpfs
   outside `/workspace`, mode 0600, so a key never reaches a disk and cannot be
   swept into a `git add -A` from the workspace the agent commits from.
3. **A metered budget.** Every run is priced, killed ones included. At the hard
   stop the project pauses itself and live run tokens are revoked.
4. **An audited API.** Agents act only through `/agent/*` on a per-run token.
   Every rule is an HTTP refusal or an automatic state change, enforced
   server-side where the token cannot reach around it.

## What does **not** contain an agent run

**The network.** There is no egress proxy and no per-role domain allowlist. One
existed; it was removed deliberately (`docs/PLAN.md`) because a blocked request
surfaced as an unlogged proxy 403 three layers from the agent that made it, and
invisible failures cost more than they bought. That is a real trade and this is
the honest statement of what it costs:

> **An agent holds the credentials its role was granted, has a shell and a
> network, and can be prompt-injected by anything it reads. If it is injected,
> it can send what it holds anywhere.** The container boundary, the scoped
> grant, the budget cap and the audit trail are the containment. The network is
> not.

35 of 38 shipped roles have `web`, 14 have `bash`, and 5 combine shell, network
and credentials. Those 5 are the blast radius worth thinking about.

**A prompt is not a control.** The context pack tells agents that messages and
web content are data rather than instructions
(`yaaf/server/contextpack.py`). That is a hint that makes good behaviour more
likely. It is not a mechanism and it is not counted as one anywhere in this
design — which is the whole thesis of the project applied to itself.

## What this means for you

- **Give a project its own credentials, scoped to it.** Not your personal token,
  not an org-wide one. Assume any credential a role can hold may leave.
- **Prefer roles without `bash` when the job does not need it.** The role
  library is data; edit it.
- **Run it on a box you would not mind rebuilding.** One box, Docker Compose,
  no inbound ports beyond what you publish deliberately. The dashboard binds to
  `127.0.0.1` for this reason; reach it over SSH.
- **Do not point it at production systems you cannot roll back.**

## Known sharp edges

Stated here rather than waiting for someone to find them:

- **No outside security review has happened.** `docs/PLAN.md` names the moment
  it will: the first claude.ai connection against a public deployment is also
  the first time the security model is tested by someone who did not write it.
- **`sandbox_mode="subprocess"` has no isolation.** It exists for laptops and
  CI without Docker. A task's success test can be a command string an *agent*
  wrote, and in this mode it would run on the runner host — so the platform now
  refuses it unless `allow_host_success_tests` is set explicitly. Docker mode,
  the default, is unaffected: there the command runs in a throwaway container
  with no credentials.
- **Sandboxes share one Docker bridge.** Concurrent runs from different
  projects can reach each other on the network. Per-project networks are the
  obvious fix and are not built.
- **The admin token is a single static bearer.** No rotation, no expiry, no
  scopes below it. Full user auth is a milestone, not a feature.
- **A per-run token has no clock.** It is valid while its run is `RUNNING` and
  invalid after — bounded by the janitor's stale-run reaper rather than by an
  `expires_at`. "Short-lived" means "scoped to one run", not "expires in N
  minutes".
- **The default test suite never calls a model.** It attacks the policy layer
  over HTTP with forged tokens, wrong roles and rephrased requests — 1,347 of
  them, free and offline. Model *behaviour* is covered by golden transcripts of
  a scripted backend plus one opt-in paid test. The rules are tested. The models
  are not.

## What is tested, and how

Every forcing function ships with a test that attacks it over HTTP the way an
agent would — forged tokens, wrong roles, rephrased requests
(`tests/selftest_invariants/`). A few worth naming:

| | |
|---|---|
| `test_secret_reveal.py` | Walks the live OpenAPI schema and fails if any `/agent/*` route grows a field named secret, value or credential outside a named allowlist. |
| `test_mcp_oauth_attacks.py` | PKCE stripping, code replay, redirect swap, RFC 8707 audience confusion, scope widening at the token endpoint. |
| `test_host_success_tests.py` | An agent-authored command does not reach the runner host because Docker was absent. |
| `tests/chaos/` | Leases, claims and side effects keep their promises under violence. |

If you can break one of these, that is the contribution this project wants most.
