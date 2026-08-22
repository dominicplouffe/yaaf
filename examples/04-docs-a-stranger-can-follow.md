# 04 — Documentation a stranger can follow

**What you get:** a quickstart that takes someone from nothing to a working
result in under ten minutes — and a script that proves it does.

**What it teaches:** you can hold *writing* to a `command` test. Most people
never think to try, and it is the difference between docs that are accurate and
docs that merely sound accurate.

**What you need:** your Anthropic API key.

**Cost:** capped at $50.

---

## Import it

**+ New project → Start from a setup file →** `04-docs-a-stranger-can-follow.json`,
then pick your subject. It ships pointed at Python's `httpx` library, which is
a fine dry run — but this earns its keep on:

- **your own project**, whose README you have been meaning to fix for a year
- a tool your team uses that everyone learns by asking the same person
- an internal process that lives in one person's head

For your own code, attach the repo (Settings → Integrations → GitHub) so the
agents can read it and run it.

## The org

| Team | Role | Who | Does |
|---|---|---|---|
| Editorial | `writer` | Ada | Writes it, for a reader who does not already know |
| Verification | `engineer` | Mika | Runs every command in a clean directory and reports what actually happened |

The split is the whole design. The writer's incentive is a document that reads
well; the engineer's is a document that *works*. Docs rot in the gap between
those two, so this puts a different agent in each seat.

## What "done" looks like

```json
{"type": "command",    "command": "cd /workspace && bash deliverables/verify-quickstart.sh"}
{"type": "observable", "target": "every command block in deliverables/quickstart.md appears in verify-quickstart.sh in the same order"}
{"type": "observable", "target": "the quickstart names the first thing that goes wrong for a newcomer and how to tell it apart from the second"}
```

The first test is the interesting one, and it is the whole example: **the
quickstart is executable.** If a step is wrong, out of order, or quietly
assumes something the reader doesn't have, the script exits non-zero and the
requirement does not close. Prose, checked by a machine.

The second exists because otherwise the script and the document drift within a
week — the classic failure of "we have a tested quickstart".

## What you'll see

- **The engineer contradicting the writer** in the team room. This is the
  system working: an agent that ran the thing beats an agent that described it.
- **A `checking` requirement**, where the platform runs the verify script
  itself. Watch `Records → Runs` for the check run — it has no agent attached,
  because nobody is being trusted.
- **Your spot-audit sample** on the observable tests. Follow the quickstart
  yourself once, on a clean directory. Four minutes. If step three doesn't
  work, fail it — and note that the *command* test passed while the *human*
  test failed, which tells you something about what your script was checking.

## What to watch

- **"Configure as appropriate."** The goal bans this phrase because it is where
  documentation goes to die. If it appears anyway, that is a Boardroom
  conversation.
- **Quoted output that is too clean.** The role is told to quote real output,
  never a plausible one. Real output has version numbers and warnings in it.
- **A verify script that only checks exit codes.** `curl -s url > /dev/null`
  exits 0 for a 404 page. Skim it.

## Try this next

- **Make it recurring.** Create the same project in **Operate** mode with a
  weekly cycle and the goal *"keep this quickstart true"*. Every cycle it
  re-runs the verify script against the current version of the tool, and tells
  you what broke. Documentation that maintains itself is a genuinely different
  product from documentation that was once correct.
- Then **[05 — A tutor that adapts](05-a-tutor-that-adapts.md)**, which is that
  rhythm at its most useful.
