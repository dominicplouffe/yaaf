# 08 — A game you can actually play

> **Why not just a Claude Skill?** Honestly, one Claude Code session will write
> you a one-file game. What it will not do is put a developer, a UX designer and
> a design analyst in a room with **different charters and conflicting
> priorities**, make them argue in a channel you can read afterwards, and hand
> you the argument as a record. Read `Records → Messages` on this one; the
> argument is more instructive than the game.

**What you get:** a single HTML file you double-click, containing a small
browser game that is genuinely fun for five minutes.

**What it teaches:** a **multi-discipline org** — three teams with different
jobs and different opinions — and what happens when "done" is a *feel*
judgement rather than a test result.

**What you need:** your Anthropic API key. No build tooling, no engine, no
store account. The deliverable runs by opening a file.

**Cost:** capped at $80 — the largest in the set, because three teams and
iteration on feel costs more than one memo.

---

## Import it

**+ New project → Start from a setup file →** `08-a-game-you-can-play.json`.

You do **not** pick the mechanic. The goal deliberately leaves that to the
design team and asks them to tell you what they chose and why before building.
That is the interesting part — watch the Boardroom for it, and push back if the
pitch is boring. Pushing back is your job here.

## The org, and the argument it exists to have

| Team | Role | Who | Cares about |
|---|---|---|---|
| Design | `game-analyst` | Wren | Is the core loop actually fun, and does anything prove it |
| Feel | `game-ux-designer` | Juno | Input latency, legibility of failure, the one-more-go moment |
| Build | `game-developer` | Kit | Frame budget, touch targets, that it runs on a real phone |

Three teams for one HTML file looks like overkill and is the point of the
example. These roles disagree by construction: the analyst wants a mechanic
with a retention argument, the UX designer wants it to feel good in the hand,
the developer wants it to hold 60fps on a mid-range Android. Read
`Records → Messages` — the leadership channel is where that argument happens,
and it is more instructive than the game.

## What "done" looks like

Games are where acceptance tests get hard, because the thing you care about is
not machine-checkable. So this project uses both kinds honestly:

```json
{"type": "command",    "command": "cd /workspace && node -e \"const s=require('fs').readFileSync('deliverables/game.html','utf8'); if(/<script src=|<link .*href=\\\"http/.test(s)) process.exit(1)\""}
{"type": "command",    "command": "cd /workspace && test $(stat -c%s deliverables/game.html) -lt 400000"}
{"type": "observable", "target": "deliverables/game.html opens by double-clicking with no server and no network, and is playable with touch alone"}
{"type": "observable", "target": "every tuning value (gravity, speed, spawn rate, timings) is in one named block at the top of the file"}
{"type": "observable", "target": "deliverables/tuning-notes.md says what was changed and what it felt like before and after"}
```

The commands enforce the constraints a machine *can* check — self-contained, no
external fetches, small enough to load instantly. **Whether it is fun is
yours**, and that is what the spot-audit is for: the platform hands you the
close, you play the game for two minutes, and you say whether you believe it.

There is no way around that, and yaaf does not pretend otherwise. What it does
instead is make sure the question reaches you rather than being self-graded
away.

## What to watch

- **The tuning block.** If gravity is a magic number buried on line 340,
  nobody — including you — is going to iterate on feel. This is a real
  requirement, not a style note.
- **"It runs" reported as "it's done."** The `game-developer` charter is
  explicit that a game task is not finished because the code compiles: the
  collision has to fire, the score has to increment, the save has to survive a
  reload. Hold it to that.
- **Failure legibility.** When you die, do you know why? If not, the game is
  frustrating rather than hard, and those are different.
- **Your phone.** Open the file on it. The goal asks for touch and a
  phone-sized screen, and this is where agents cut corners.

## Try this next

- **Play it, then ask for one change** in the Boardroom — "the jump feels
  floaty", "I want a reason to keep going after 60 seconds". Watch a *feel*
  note turn into a requirement, then into tuning values, then into a
  before/after in the notes.
- Add a fourth team with `game-graphic-designer` if you want art direction in
  the argument too.
- Then **[09 — One idea, thirty days](09-one-idea-thirty-days.md)**, which
  throws the whole org away.
