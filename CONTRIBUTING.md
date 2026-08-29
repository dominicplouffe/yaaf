# Contributing to yaaf

Thanks for looking under the hood. yaaf is a platform for running autonomous
agents under rules they can't talk their way out of — and the codebase holds
itself to the same standard: every rule ships with a test that attacks it.

## Getting set up

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # Python 3.12+
.venv/bin/python -m pytest tests/ -n auto  # whole suite, offline, $0 in tokens
.venv/bin/ruff check .                   # lint
cd web && npm install && npm run build   # type-checks the SPA
```

`-n auto` matters: the tests are HTTP round-trips against a real app, so they
parallelise almost linearly — about four minutes serial, about a minute and a
half on four cores. Drop it when you want a readable failure from one test.

The suite runs on SQLite by default, which is the fastest loop and the right
one for almost every change. **CI runs Postgres**, because SQLite cannot
exercise `FOR UPDATE SKIP LOCKED`, JSONB, `LISTEN/NOTIFY`, `TIMESTAMPTZ`, or
the `ALTER TABLE` half of the migration chain — a suite green on SQLite alone
is green about a database nobody deploys. Run Postgres locally before pushing
anything that touches leasing, JSON columns, timestamps or migrations:

```bash
docker run -d --rm -p 55432:5432 -e POSTGRES_PASSWORD=postgres postgres:16
YAAF_TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55432/postgres \
  .venv/bin/python -m pytest tests/ -n auto
```

Each xdist worker builds a database of its own, so the two flags compose.

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
sandbox escapes, secret exposure). Contact the maintainer privately
via GitHub (profile email or a private security advisory on this repo).

## Where to start

Read [docs/PLAN.md](docs/PLAN.md) first — the thesis, the kill conditions, an
honest ledger of what exists, and what is deliberately not built.
[docs/FEATURES.md](docs/FEATURES.md) is the map of the surface;
[docs/AGENT_API.md](docs/AGENT_API.md) is what an agent actually sees.

Good first contributions: more invariant attacks, more chaos, more golden-eval
scenarios, evidence connectors for new services, and roles for a domain the
library does not cover yet.
