"""Why a run died, in words the seat that paid for it can act on.

The platform has always recorded this. `runs.summary` carries the sandbox's
own account of the death — `Reached maximum budget ($2)`, `sandbox exited 137`,
`oom=true` — and the chairman can read it on `/observe/projects/{id}/runs`. The
CEO could not. Its one run-level read, `roster_activity`, reported
`runs_by_outcome = {"failed": 11}` and stopped there, so the only honest thing
a CEO could say about a sixth of its night was that it had failed.

It said worse than that. Asked to account for a desk spending 77M tokens
overnight, a CEO reported eleven runs that "died without finishing anything"
and proposed standing the seat down. Seven of the eleven had `produced > 0` —
they had claimed tasks, written files and filed work, and were then cut at the
per-run dollar ceiling mid-sentence. Every one of the five tasks that seat
touched that night completed, on the second or third attempt. The seat was not
idle; it was being killed and restarted, and the retries were the cost. The
proposal would have removed the seat that shipped the work.

So two things are reported here rather than one:

* **The cause**, matched from the summary the platform already wrote. Plain
  words, and where there is a lever it is named with its number — a cause of
  death that does not identify the setting that caused it is a symptom.
* **Whether the run produced anything first**, which is the fact that flips the
  conclusion. "Died" and "bought nothing" are different claims and the platform
  has been making the second one while checking the first.

`REAPED` is the honest hole: the janitor recovers a stale lease and writes no
summary, so several hundred of the deaths on this deployment record nothing at
all about why. That is reported as itself rather than guessed at.

**This reaches the CEO's `roster_activity` and nothing else yet.** The
chairman's briefing says "N of M runs died and bought nothing" from the status
column alone (`briefing.py`), which is the same missing read and the same
wrong second claim; `observe` hands the raw summary to a browser that renders
it verbatim. Both should come through here so there is one definition of what
killed a run. Neither is changed yet — the CEO was the surface that had
nothing, and it is the one deciding whose seat to stand down.
"""

from __future__ import annotations

import re

from yaaf.core.models import RunStatus

#: Runs that ended without reaching the platform's own definition of finished.
#: The same set `briefing.DEAD_STATUSES` and `velocity.DEAD_STATUSES` use.
DEAD_STATUSES = (RunStatus.FAILED, RunStatus.REAPED, RunStatus.KILLED,
                 RunStatus.TERMINATED_REFUSALS)

#: The two deaths that carry a number worth reading back: the ceiling that cut
#: the run, and the exit code that killed the container.
_BUDGET = re.compile(r"reached maximum budget \(\$?([\d.]+)\)")
_KILLED_MID = re.compile(r"killed mid-run, exit (\d+)")
_SANDBOX_EXIT = re.compile(r"sandbox exited (\d+) without a result file")

#: What a container exit code means here, and what to do about it. 124 is the
#: runner's own timeout (`sandbox.run_agent_container`), 137 a SIGKILL — which
#: is an OOM or a deliberate stop, and the log header says which.
_EXIT_CODES = {
    "124": ("ran past the run timeout and was killed", "run_timeout_s"),
    "137": ("was killed (SIGKILL) — the container hit its memory limit, "
            "or a stop revoked it mid-run", None),
}

#: Substrings tried against the LOWERCASED summary; the first rule with any hit
#: wins, so the order is most-specific-first. Deliberately a table: a cause
#: nobody has seen yet should be one dict away, the way `recognizers.py` is.
CAUSES: list[tuple[tuple[str, ...], str]] = [
    (("you've hit your org's monthly spend limit",
      "reached your specified api usage limits",
      "credit balance"),
     "the model account's own spend limit, upstream of this project — every "
     "project on this deployment stops together and no project setting raises "
     "it; this one is the chairman's to fix at the provider"),
    (("not logged in", "please run /login", "invalid x-api-key",
      "authentication_error", "oauth token has expired"),
     "the model credential was rejected — nothing this project can spend its "
     "way past; the chairman re-authorises it"),
    (("api error: 529", "overloaded"),
     "the model provider was overloaded — transient, and the run is worth "
     "retrying as-is"),
    (("chat session wedged",),
     "the boardroom chat session stopped calling the platform at all while its "
     "container stayed up — wedged mid-turn, usually on a network connection "
     "that never completed, and restarted once the silence gave it away"),
    (("chat session container lost or mismatched",),
     "the boardroom chat session lost its container and the supervisor "
     "restarted it — talk-lane plumbing, not work"),
    (("chat session ended:",),
     "the boardroom chat session was shut down on purpose because the project "
     "stopped or paused — an orderly close, not a failure"),
    (("reached maximum number of turns",),
     "it used every tool turn a run is allowed (`max_turns`, 100) without "
     "finishing — the work is too big for one run, so it wants splitting "
     "rather than retrying"),
    (("tool call could not be parsed",),
     "the model emitted a tool call the runner could not parse, and the retry "
     "failed the same way"),
    (("agent missing or paused at lease time",),
     "the seat was paused or removed between the run being queued and the "
     "sandbox starting — the run was never really begun"),
    (("backend crashed",),
     "the runner's backend crashed before the sandbox reported anything"),
    (("processerror", "command failed with exit code"),
     "a command the run shelled out to failed, and the sandbox gave up on the "
     "turn rather than the command"),
    (("token revoked", "agent run stopped: token revoked"),
     "its token was revoked mid-run — a stop, a pause, or a stand-down landed "
     "while it was working"),
]

#: What a reaped run records about itself. Nothing, is the answer.
REAPED_CAUSE = (
    "its lease expired and the janitor recovered it — the sandbox stopped "
    "reporting and left nothing behind saying why. This one the platform "
    "genuinely cannot answer yet"
)

UNRECORDED = "not recorded — the run ended without writing a cause"


def cause_of(status: str, summary: str | None) -> str:
    """One plain sentence for why this run ended, from what was written down.

    Never a guess: every branch either quotes a marker the platform itself
    wrote into the summary, or says the cause was not recorded. A cause that
    hedges is worse than no cause, because it gets acted on.
    """
    # Before anything read out of the summary: this status IS the cause, and
    # the summary routinely describes the mechanism instead. A run cut off for
    # refusals ends up killed mid-run with exit 137, and one CEO narrated its
    # own death as "this run's token was revoked" — both true, both the thing
    # that happened to it rather than the reason it happened.
    if status == RunStatus.TERMINATED_REFUSALS:
        return ("it hit the per-run refusal cap — the run kept asking for "
                "something the platform kept refusing (`refusal_cap_per_run`)")

    text = (summary or "").lower()

    budget = _BUDGET.search(text)
    if budget:
        return (f"hit the per-run spend ceiling (${budget.group(1)}) and was cut "
                "off mid-run, not finished — raise `max_usd_per_run`, or set "
                "`run_token_budget` so the run is told to wrap up instead of "
                "being stopped at the line")

    killed = _KILLED_MID.search(text) or _SANDBOX_EXIT.search(text)
    if killed:
        code = killed.group(1)
        if code == "137" and "oom=true" in text:
            return ("was killed for running out of memory (exit 137, "
                    "oom=true) — the container's memory limit, not a project "
                    "setting")
        phrase, lever = _EXIT_CODES.get(code, (f"exited {code} unexpectedly", None))
        recovered = " after checkpointing its usage" if "usage recovered" in text else ""
        return (f"the sandbox {phrase}{recovered}"
                + (f" — the lever is `{lever}`" if lever else ""))

    for markers, cause in CAUSES:
        if any(marker in text for marker in markers):
            return cause

    if status == RunStatus.REAPED:
        return REAPED_CAUSE
    return UNRECORDED
