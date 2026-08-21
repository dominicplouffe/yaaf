# Contributing to yaaf

Thanks for looking under the hood. yaaf is a platform for running autonomous
agents under rules they can't talk their way out of — and the codebase holds
itself to the same standard: every rule ships with a test that attacks it.

## Getting set up

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/        # whole suite, offline, $0 in tokens
.venv/bin/ruff check .                   # lint
cd web && npm install && npm run build   # type-checks the SPA
```

The suite runs on SQLite by default. To run it against real Postgres (CI does
both):

```bash
docker run -d --rm -p 55432:5432 -e POSTGRES_PASSWORD=postgres postgres:16
YAAF_TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55432/postgres \
  .venv/bin/python -m pytest tests/
```

## The house rules (they're the product)

1. **A rule is a refusal or an automatic state change — never a prompt, and
   never just a notification.** If your PR adds a rule, the enforcement must
   live server-side where an agent's token can't reach around it. "The agent
   is instructed not to" does not count.
2. **Every rule ships with an attack.** New forcing function → new test in
   `tests/selftest_invariants/` that tries to break it *over HTTP, as an agent
   would* — forged tokens, wrong roles, rephrased requests. If you can't
   write the attack, the rule probably isn't enforced.
3. **Agent-visible behavior is regression-gated.** If your change alters what
   agents see or how the rules respond (prompts, context packs, refusal
   texts, policy), the golden evals will fail. That's the gate working. If
   the change is deliberate, re-record with `python -m evals.run --record`
   and say so in the PR — a golden diff without an explanation is a red flag.
4. **Comments explain *why*, not *what*.** The codebase narrates its reasons
   ("a vacationing chairman degrades, never deadlocks"). Match that. Delete
   comments that restate the code.
5. **Don't break the crash story.** Anything that touches runs, leases,
   claims, or side effects should keep its promises under violence — the
   chaos suite (`tests/chaos/`) is where those promises are checked.
6. **Secrets stay out.** No credentials in code, tests, fixtures, or
   committed config — including "example" values that look real.

## Pull requests

- Small and single-purpose beats large and heroic.
- Say what rule/behavior changed and how it's enforced and tested.
- `pytest`, `ruff check .`, and `npm run build` must all pass — CI runs them
  on every PR, on both databases.
- Commit messages: a short line that says why, not a changelog.

## Reporting security issues

Please don't open a public issue for anything exploitable (auth bypasses,
sandbox or egress escapes, secret exposure). Contact the maintainer privately
via GitHub (profile email or a private security advisory on this repo).

## Where to start

Read [docs/PLAN.md](docs/PLAN.md) first — it's the founding plan, including
what would kill the project. The status appendix at the bottom is the honest
ledger of what exists. Good first contributions: more invariant attacks, more
chaos, more golden-eval scenarios, evidence connectors for new services.
