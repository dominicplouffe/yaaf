"""Does the goal grader actually know a good goal from a brief?

The golden evals next door are scripted, byte-stable and free. This one calls a
model and costs money, so it is opt-in and never runs in CI.

It grades the two ends of a real, documented gap:

- **The brief** (`fixtures/founder_brief.md`) — what the chairman actually
  typed to get a project designed.
- **The goal** (`docs/projects/idea-to-customer.json`) — what came back, which
  he approved and shipped as the worked example.

Both are real artifacts, which is what makes this worth running. The grade has
to come out differently on them, and *in a specific direction*: the finished
goal is ready, the raw brief is not, and the elements the brief is missing are
the ones the Founder exists to supply. If the grader cannot tell those two
apart, nothing downstream works — the Founder grades its own drafts with it.

    python -m evals.founder                 # uses ANTHROPIC_API_KEY
    python -m evals.founder --model claude-sonnet-5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from evals.judge import LOAD_BEARING, GoalGrade, grade_goal

ROOT = Path(__file__).resolve().parent.parent
BRIEF = Path(__file__).resolve().parent / "fixtures" / "founder_brief.md"
REFERENCE = ROOT / "docs" / "projects" / "idea-to-customer.json"

#: What the finished goal added that the brief never said. Established by
#: reading both: the brief already carries the bias-as-a-bias, the chairman's
#: bounded role, the resource posture and the deadline — these three it does
#: not. They are the Founder's job.
THE_GAP = ("anti_metrics", "quality_bar", "failure_deliverable")


def _brief() -> str:
    # Everything after the `---` separator; the preamble is a note to readers.
    return BRIEF.read_text().split("---", 1)[1].strip()


def _reference_goal() -> str:
    return json.loads(REFERENCE.read_text())["project"]["goal"]


def _report(label: str, grade: GoalGrade) -> None:
    mark = {"present": "✓", "weak": "~", "missing": "✗"}
    print(f"\n── {label} " + "─" * max(0, 60 - len(label)))
    for element in grade.elements:
        print(f"  {mark.get(element.verdict, '?')} {element.element:22} {element.note}")
    print(f"  ready: {grade.ready}")
    print(f"  {grade.summary}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="claude-opus-5")
    args = parser.parse_args(argv)

    # Whichever credential this machine has, in the form the CLI reads it —
    # the same two names every agent in a project runs under.
    env = {name: os.environ[name]
           for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
           if os.environ.get(name)}
    if not env:
        print("set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN; "
              "this eval calls a model and costs money.")
        return 2

    brief = asyncio.run(grade_goal(_brief(), env=env, model=args.model))
    _report("the chairman's brief, as typed", brief)

    goal = asyncio.run(grade_goal(_reference_goal(), env=env, model=args.model))
    _report("the goal it produced (the reference)", goal)

    failures: list[str] = []

    # 1. The grader recognises the standard. If this fails, the rubric is wrong
    #    or the grader is too harsh, and every draft will be over-criticised.
    if not goal.ready:
        failures.append(
            "the reference goal did not grade as ready; it is the standard, so either "
            f"the rubric or the grader is wrong (missing: {goal.missing}, weak: {goal.weak})")

    # 2. The grader discriminates. A grader that passes everything is decoration.
    if brief.ready:
        failures.append(
            "the raw brief graded as ready, so the grader is not discriminating — "
            "the brief names no anti-metric and no failure deliverable")

    # 3. The gap is the one we designed the Founder around. This is the part
    #    that would quietly rot: if the brief starts grading as complete on
    #    these, the Founder's stated job has changed and the prompt should say so.
    for element in THE_GAP:
        verdict = next((e.verdict for e in brief.elements if e.element == element), None)
        if verdict == "present":
            failures.append(
                f"the brief was graded 'present' on {element}, which it does not contain; "
                "the Founder is specified as the thing that supplies it")

    print("\n" + "─" * 62)
    print(f"the gap the Founder must close: {', '.join(THE_GAP)}")
    print(f"brief missing: {brief.missing or '(none)'}")
    print(f"load-bearing elements: {', '.join(LOAD_BEARING)}")

    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nPASSED: the grader knows the standard, and knows the brief is not it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
