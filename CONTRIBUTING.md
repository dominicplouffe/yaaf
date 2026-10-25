# Contributing to yaaf

Thanks for looking under the hood. yaaf is a platform for running autonomous
agents under rules they can't talk their way out of — and the codebase holds
itself to the same standard: every rule ships with a test that attacks it.

## Getting set up

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # Python 3.12+
.venv/bin/python -m pytest tests/ -n auto  # whole suite, offline, $0 in tokens
.venv/bin/python -m pytest -m paid tests/       # the one test that calls a model
.venv/bin/ruff check .                   # lint
cd web && npm install && npm run build   # type-checks the SPA
```

`-n auto` matters: the tests are HTTP round-trips against a real app and the
work is the app's own Python, so they parallelise almost linearly — about four
minutes serial, about a minute on four cores. Drop it when you want a readable
failure from one test.

**If you have Docker installed, the suite sets one thing for you**, and it is
worth knowing why. A task's success test can be a command an agent wrote, so
the platform refuses to run one on a host holding the Docker socket — which is
every box with Docker up, including a GitHub runner. `tests/conftest.py` sets
`YAAF_ALLOW_HOST_SUCCESS_TESTS=1` for itself, next to the `subprocess` sandbox
mode it already declares, so seven command-check tests do not fail purely
because of what is installed on your laptop. Export the variable as `0` if you
want to watch the refusal fire; `tests/selftest_invariants/test_host_success_tests.py`
proves it fires either way, from its own stubs.

### Run the smallest tier that answers your question

The full suite costs about a minute. That is cheap once and expensive after
every edit, so match the tier to the question:

```bash
.venv/bin/python -m pytest --testmon -q        # after each edit — only what you changed
.venv/bin/python -m pytest tests/…/test_x.py   # does this area still work
.venv/bin/python -m pytest -q -n auto          # once, before you commit
```

`--testmon` reruns only the tests whose covered code moved; it needs one full
run first to build its map. It is a development accelerator and never a gate —
CI always runs everything. `CLAUDE.md` states the same tiers for agents working
in this repo.

Four patterns keep the suite fast, three of them enforced by
`tests/selftest_invariants/test_suite_hygiene.py` rather than by this paragraph:
never build an app in a test (use `client`), never build a schema in a test (the
fixture gives you an empty one), and never run a fire drill for a report you only
read (use the `report` fixture). Adding a deliberately slow test is fine; adding
one by accident is what those catch.

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

CI runs the same suite split across four sharded jobs, balanced by
`.test_durations`. That file is committed so the shards stay even; regenerate it
when the balance drifts (`pytest -q --store-durations --clean-durations`).
Unknown tests are assigned the average, so a stale file degrades gracefully and
never blocks anyone. **If CI outgrows two minutes again, add shards** — that is
the knob, and it is why CI time tracks shard count rather than test count.

## The house rules (they're the product)

1. **A rule is a refusal or an automatic state change — never a prompt, and
   never just a notification.** If your PR adds a rule, the enforcement must
   live server-side where an agent's token can't reach around it. "The agent
   is instructed not to" does not count.

   **An automatic state change may point either way.** A rule that *fires when
   a project is under-reaching* is as legitimate as one that refuses when it
   over-reaches, and is enforced the same way: server-side, where an agent's
   token can't reach around it. This half of the rule was always here and was
   never read — the platform grew ~200 refusal sites and two floors, and the
   word "ambition" appears in `yaaf/**/*.py` exactly zero times. A floor is
   not a lesser rule or a special case. "The agent is encouraged to" does not
   count, for the same reason its mirror image doesn't.
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

Read [docs/FEATURES.md](docs/FEATURES.md) first — the map of the surface.
[docs/GOVERNANCE.md](docs/GOVERNANCE.md) is every rule and its default;
[docs/AGENT_API.md](docs/AGENT_API.md) is what an agent actually sees.

Good first contributions: more invariant attacks, more chaos, more golden-eval
scenarios, evidence connectors for new services, and roles for a domain the
library does not cover yet.
