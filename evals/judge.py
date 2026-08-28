"""Model-graded judgement: transcripts (planned) and goals (shipped).

The golden diff plus declarative expectations catch *mechanical* regressions
in scripted scenarios: a changed refusal, a lost wake, an extra call. What
they cannot grade is planning *quality* on real-model transcripts — did the
CEO pick a falsifiable bet, decompose sensibly, kill what deserved killing?
PLAN §"Agent runtime" reserves that for a judge model replaying recorded
transcripts against a rubric.

This module pins down the seam so the harness and CI shape don't have to
change when the judge lands: a judge consumes a (scenario name, transcript)
pair — the same normalized event stream the goldens store — and returns a
verdict. Implementations will call a model; none exists yet, and nothing in
CI depends on one.

What *is* implemented here is the other half: :func:`grade_goal`, which grades
a project's goal prose against ``yaaf/founder/GOAL_GRAMMAR.md``. It lives beside the
transcript hook because both answer the same shape of question — is this any
good, judged against a written rubric — and keeping one home for that means
one place to look when a grade seems wrong. It grades a document rather than a
transcript, so it is a sibling of the hook above, not an implementation of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class JudgeVerdict:
    scenario: str
    rule_adherence: float  # 0..1 — did the agent work with the refusals, not against them?
    planning_quality: float  # 0..1 — rubric-scored judgment: bets, tests, decomposition
    rationale: str
    flags: list[str] = field(default_factory=list)  # e.g. "refusal-rephrase loop"


class TranscriptJudge(Protocol):
    """Anything that can grade a recorded transcript."""

    def judge(self, scenario_name: str, transcript: list[dict]) -> JudgeVerdict: ...


def judge_transcript(
    scenario_name: str,
    transcript: list[dict],
    judge: TranscriptJudge | None = None,
) -> JudgeVerdict:
    """Planned hook for judging planning quality on real-model transcripts.

    Not implemented: the CI gate for scripted scenarios is the golden diff
    plus the declarative expectations, at $0. When a judge implementation
    exists, pass it in; recording real-model transcripts through the same
    RecordingClient is the intended capture path.
    """
    if judge is not None:
        return judge.judge(scenario_name, transcript)
    raise NotImplementedError(
        "Judge-model scoring is a planned hook for real-model transcripts; "
        "no implementation ships yet (see evals/README.md)."
    )




# ── grading a goal against the grammar ──────────────────────────────────────────
#
# Implemented in `yaaf.founder.grade` because the Founder calls it at runtime
# and the deployed image ships `yaaf*` only. Re-exported here so the eval
# harness still has one front door for model-graded judgement.

from yaaf.founder.grade import (  # noqa: E402
    GOAL_ELEMENTS,
    LOAD_BEARING,
    ElementGrade,
    GoalGrade,
    grade_goal,
)

__all__ = [
    "GOAL_ELEMENTS",
    "LOAD_BEARING",
    "ElementGrade",
    "GoalGrade",
    "JudgeVerdict",
    "TranscriptJudge",
    "grade_goal",
    "judge_transcript",
]
