# 03 — A tool that actually runs

**What you get:** a small command-line tool in the project's workspace, with
tests, that does something you actually wanted.

**What it teaches:** the `command` acceptance test — the point where "done"
stops being anybody's opinion. **This is the example that shows you what yaaf
is for.**

**What you need:** your Anthropic API key. No repo, no CI, no accounts. (You
*can* attach a GitHub repo; see the end.)

**Cost:** capped at $60, per-run capped at $3.

---

## Import it

**+ New project → Start from a setup file →** `03-a-tool-that-runs.json`, then
**describe the tool you want** in place of the capitalised placeholder.

Keep it genuinely small — one job, done well. Things that work here:

- a folder of markdown files → a report of broken relative links
- a CSV → the same CSV with one column reformatted, safely, in place
- a directory → the ten biggest files, grouped by extension
- a log file → a count of each distinct error, newest first

Things that don't: anything needing a service, an account, or a schema you
haven't described.

## The one constraint, and why it is there

The goal says **standard library only**. That is not a style preference:

> Every run gets a **fresh container**. The workspace persists; the environment
> does not. So if an agent runs `pip install pytest` during its run, that
> package is gone by the time the platform runs the acceptance test — and the
> check fails for a reason that has nothing to do with the code.

An acceptance test may rely on what the sandbox image ships (`python3` and the
standard library, `pandas`, `duckdb`, `node`, `jq`, `git`, `curl`) or on what
lives in the workspace. Nothing else. `unittest` is stdlib; `pytest` is not.

That is the kind of thing you learn by having it bite you, so it is written
into the goal here instead.

## What "done" looks like

```json
{"type": "command", "command": "cd /workspace && python3 -m unittest discover -s tests -q"}
{"type": "command", "command": "cd /workspace && python3 -m tool --help | head -1"}
{"type": "command", "command": "cd /workspace && python3 -m tool fixtures/broken && test $? -ne 0"}
```

**The platform runs these. Not the agent.** In its own sandbox, at completion
time, and it records the pass/fail and the output. There is no path by which an
agent reports a green test it never ran — the tool to do that does not exist.

The third one is worth stealing: it asserts the *failure* path. A tool that
exits 0 whatever happens is useless in a script, and it is exactly what you get
if you only ever test the happy case.

## What you'll see

- **Work → the board.** Tasks in `checking` are the ones where the platform is
  running the command right now. That status exists because "the agent said it
  passed" and "it passed" are different facts.
- **A task that goes `blocked`.** After three consecutive failed checks a task
  blocks itself and becomes a question rather than a loop. Read the block
  reason — an agent that stopped and asked is behaving correctly.
- **Records → Runs → the trace.** Watch an engineer actually run its own tests
  before completing. The role's charter tells it to; the platform then checks.

## What to watch

- **Tests that assert nothing.** `assertTrue(True)` passes. Skim the test file
  once — it is thirty seconds and it is the only way to catch this.
- **Scope creep.** The `engineer` charter forbids adding features nobody asked
  for; if you see them anyway, that is a role-prompt problem worth telling it
  about in the Boardroom.
- **The boring half:** no arguments, empty input, a file it cannot read. The
  goal asks for these explicitly because they are what gets skipped.

## Try this next

- **Attach a repo.** Settings → Integrations → GitHub workspace. The runner
  clones it into the sandbox at lease time, so work survives between runs, and
  repo events arrive as **platform-attested evidence** — proof no agent typed
  in. A free GitHub account is all this needs.
- Then **[04 — Docs a stranger can follow](04-docs-a-stranger-can-follow.md)**,
  which uses the same trick to prove *prose*.
