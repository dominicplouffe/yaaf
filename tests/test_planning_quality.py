"""CI gate: is the CEO's *judgment* any good, not just its compliance?

The golden evals next door grade mechanics — a changed refusal, a lost wake, a
count that moved. They are byte-stable and free, and they are blind to the
thing this project actually cares about: a CEO can satisfy every one of them
while quietly losing. `evals/judge.py` reserved `planning_quality` for that
question and left it `NotImplementedError` for months; this is the gate that
consumes it.

**This test costs money.** It makes two model calls per run, by the chairman's
explicit decision (PLAN-01 open question 2, answered 2026-09-06: "every PR,
accept the cost"). Everything else in `tests/` is $0 in tokens and stays that
way — if you are adding a test, this is not the file to copy.

Two properties keep a paid, non-deterministic gate from becoming a flaky one
that somebody eventually deletes:

1. **It grades comparatively, not absolutely.** Model scores drift; the gap
   between a CEO that escalated and one that went quiet does not. Asserting
   "the driven run beats the timid run by a margin" survives model variance in
   a way that "scores at least 0.7" does not.
2. **An unreachable judge is not a bad grade.** An outage, a rate limit or a
   missing credential raises `JudgeUnavailable`, which is reported as an error
   distinct from a quality failure. Whether that blocks the build is a
   deployment choice, not a property of the run being graded — see
   `YAAF_JUDGE_REQUIRED` below.
"""

from __future__ import annotations

import os

import pytest

from evals import harness
from evals.judge import JudgeUnavailable, judge_transcript

SCENARIO = harness.SCENARIOS_DIR / "losing_ceo.yaml"

#: CI sets this. With it set, a judge that cannot be reached FAILS the build
#: rather than skipping it — otherwise a CI box with no credential turns the
#: gate off silently, which is the failure mode every optional gate dies of.
#: Unset (a laptop, a fork's PR) it skips, because there is nothing to grade
#: with and nothing dishonest about saying so.
REQUIRED = os.environ.get("YAAF_JUDGE_REQUIRED") == "1"

#: The driven CEO must beat the timid one by this much on planning quality.
#: Not a score threshold — a gap. See the module docstring.
MIN_GAP = 0.25

#: And the timid run must not score well in absolute terms either, or the
#: judge is grading tidiness. Generous, because the point is the gap.
TIMID_CEILING = 0.5


def _graded(behavior: str):
    scenario = harness.load_scenario(SCENARIO)
    scenario.behaviors = {**scenario.behaviors, "CEO": behavior}
    result = harness.run_scenario(scenario)
    return judge_transcript(
        f"{scenario.name}:{behavior.rsplit('.', 1)[-1]}",
        result.transcript,
        goal=scenario.project.get("goal", ""),
    )


def _judge_or_skip(behavior: str):
    try:
        return _graded(behavior)
    except JudgeUnavailable as exc:
        if REQUIRED:
            pytest.fail(
                f"the planning-quality judge was required and unavailable: {exc}. "
                "This is an infrastructure failure, not a bad grade — the run was "
                "not judged at all."
            )
        pytest.skip(f"planning-quality judge unavailable: {exc}")


def test_the_judge_can_tell_reaching_from_compliance():
    """The gate. A CEO that goes quiet while losing must grade worse.

    Both runs are the same project, the same board and the same capacity. The
    only difference is what the CEO did about being behind. If the judge scores
    them alike, it is measuring orderliness and is worth nothing as a gate —
    which is exactly the blindness that let six prompt-level fixes wash out.
    """
    driven = _judge_or_skip("evals.behaviors.losing_ceo")
    timid = _judge_or_skip("evals.behaviors.timid_ceo")

    gap = driven.planning_quality - timid.planning_quality
    assert gap >= MIN_GAP, (
        f"the judge scored a CEO that went quiet while losing "
        f"({timid.planning_quality:.2f}) within {gap:.2f} of one that named the "
        f"deficit, asked the question and commissioned work "
        f"({driven.planning_quality:.2f}).\n"
        f"  driven: {driven.rationale}\n"
        f"  timid:  {timid.rationale}"
    )
    assert timid.planning_quality <= TIMID_CEILING, (
        f"the timid CEO scored {timid.planning_quality:.2f} on planning quality "
        f"in absolute terms. It took no refusals and broke no ceiling, so a judge "
        f"grading compliance would score it well — that is the bug.\n"
        f"  {timid.rationale}"
    )


def test_a_timid_run_is_flagged_by_name():
    """The rationale has to be usable, not just a number.

    A score tells you the build failed. A flag tells the person reading CI
    *what* the CEO did, which is the difference between a gate people fix and
    a gate people mute.
    """
    timid = _judge_or_skip("evals.behaviors.timid_ceo")
    assert timid.flags or timid.rationale, (
        "the judge failed a run without saying why; a gate that cannot explain "
        "itself gets muted"
    )
