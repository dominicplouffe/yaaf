"""Grading a goal against the grammar.

This lives in ``yaaf/`` rather than ``evals/`` for one blunt reason: it is a
tool the Founder calls at runtime, and the deployed image ships ``yaaf*`` and
nothing else (see ``pyproject.toml``). A grader that imported from the eval
harness would work in a checkout and raise ``ModuleNotFoundError`` in
production — the same trap the rehearsal endpoint documents about ``roles/``.

``evals.judge`` re-exports everything here, so the eval harness still has one
place to look for model-graded judgement.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# ── grading a goal against the grammar ──────────────────────────────────────────

#: Where the rubric lives. One copy, read by the Founder's prompt and by the
#: grader below — two copies drift, and then the critic starts failing goals
#: the author was never told about.
GRAMMAR_PATH = Path(__file__).resolve().parent / "GOAL_GRAMMAR.md"

#: The eight elements, in the order the grammar states them. The names are the
#: contract: the UI groups by them and the eval asserts on them, so they may
#: not be renamed without updating both.
GOAL_ELEMENTS = (
    "finish_line",
    "anti_metrics",
    "method_constraints",
    "stated_bias",
    "quality_bar",
    "human_role",
    "resource_posture",
    "failure_deliverable",
)

#: The three the grammar calls very rarely optional — the ones that stop a
#: project grading itself. A goal missing any of these is not ready.
LOAD_BEARING = ("finish_line", "anti_metrics", "failure_deliverable")


@dataclass
class ElementGrade:
    element: str
    verdict: str  # "present" | "weak" | "missing"
    note: str  # what is missing, or the line that carries it


@dataclass
class GoalGrade:
    elements: list[ElementGrade]
    summary: str

    @property
    def missing(self) -> list[str]:
        return [e.element for e in self.elements if e.verdict == "missing"]

    @property
    def weak(self) -> list[str]:
        return [e.element for e in self.elements if e.verdict == "weak"]

    @property
    def ready(self) -> bool:
        """Whether the load-bearing three are all present.

        Deliberately not "everything is present": the grammar says elements can
        legitimately not apply, and a grader that demanded all eight would make
        a one-week Deliver project impossible to start.
        """
        by_name = {e.element: e.verdict for e in self.elements}
        return all(by_name.get(name) == "present" for name in LOAD_BEARING)

    def to_dict(self) -> dict:
        return {
            "elements": [
                {"element": e.element, "verdict": e.verdict, "note": e.note}
                for e in self.elements
            ],
            "summary": self.summary,
            "missing": self.missing,
            "weak": self.weak,
            "ready": self.ready,
        }


#: The shape the grader must answer in. Asked for in the prompt and parsed
#: back, rather than enforced by `output_config`: the Founder runs through the
#: Claude Code CLI so the whole product has one credential story, and the CLI
#: does not expose the Messages API's structured-output parameter. A malformed
#: answer is recoverable — see `_parse` — and a second way to authenticate
#: would not be.
_SHAPE = """{
  "elements": [
    {"element": "<one of: %ELEMENTS%>",
     "verdict": "present | weak | missing",
     "note": "<for present: quote the line that carries it. for weak or missing: "
             "name the specific thing to add, in one sentence>"}
  ],
  "summary": "<two sentences at most: what this goal will do to a company>"
}""".replace("%ELEMENTS%", " | ".join(GOAL_ELEMENTS))

_GRADE_SYSTEM = """\
You grade a yaaf project goal against the goal grammar below. You are a critic, \
not an editor: you never rewrite the goal, and you never comment on its style, \
length or structure. Long is fine. Short is fine. You report only what CONTENT \
is missing.

Grade every one of the eight elements exactly once. An element that genuinely \
does not apply to this project is "present" with a note saying why it does not \
apply — do not mark it missing to be thorough. A note on a missing or weak \
element must name the specific thing to add, so the author can act on it \
without asking you a follow-up question.

Answer with JSON and nothing else — no preamble, no code fence, no commentary:

{shape}

The grammar follows, verbatim.

---

{grammar}
"""


def _parse(text: str) -> dict:
    """The grader's JSON, out of whatever the model wrapped it in.

    A fenced block or a sentence of preamble is a formatting slip, not a
    failed grade, and re-running costs a model call. Take the outermost object
    and try that before giving up.
    """
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start:end + 1])


async def grade_goal(goal: str, *, env: dict[str, str],
                     model: str = "claude-opus-5") -> GoalGrade:
    """Grade goal prose against the eight elements of the goal grammar.

    Runs on the credential the caller hands it, which is the same environment
    every agent in this product runs under — an API key or a subscription
    token, resolved once in `yaaf.core.platform.founder_env`.
    """
    from yaaf.founder.session import run_once

    system = _GRADE_SYSTEM.format(shape=_SHAPE, grammar=GRAMMAR_PATH.read_text())
    text = await run_once(
        prompt=f"Grade this goal:\n\n{goal}",
        system=system,
        env=env,
        model=model,
    )
    payload = _parse(text)
    return GoalGrade(
        elements=[
            ElementGrade(element=e["element"], verdict=e["verdict"], note=e["note"])
            for e in payload["elements"]
        ],
        summary=payload.get("summary", ""),
    )
