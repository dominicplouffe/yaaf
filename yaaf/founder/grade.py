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
#: company grading itself. A goal missing any of these is not ready.
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


_GRADE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["elements", "summary"],
    "properties": {
        "elements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["element", "verdict", "note"],
                "properties": {
                    "element": {"type": "string", "enum": list(GOAL_ELEMENTS)},
                    "verdict": {"type": "string", "enum": ["present", "weak", "missing"]},
                    "note": {
                        "type": "string",
                        "description": (
                            "For present: quote the line that carries it. For weak or "
                            "missing: name the specific thing to add, in one sentence. "
                            "Never 'consider adding more detail'."
                        ),
                    },
                },
            },
        },
        "summary": {
            "type": "string",
            "description": "Two sentences at most: what this goal will do to a company.",
        },
    },
}

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

The grammar follows, verbatim.

---

{grammar}
"""


def grade_goal(goal: str, *, client, model: str = "claude-opus-5") -> GoalGrade:
    """Grade goal prose against the eight elements of the goal grammar.

    The client is injected rather than constructed so this stays usable from
    three places that resolve credentials differently: the Founder (platform
    credential), the eval harness (whatever the run is configured with), and a
    developer at a REPL.
    """
    grammar = GRAMMAR_PATH.read_text()
    response = client.messages.create(
        model=model,
        max_tokens=8000,
        system=[{
            "type": "text",
            "text": _GRADE_SYSTEM.format(grammar=grammar),
            # The rubric is the same on every call and dwarfs the goal; caching
            # it is the difference between paying for it once and every time.
            "cache_control": {"type": "ephemeral"},
        }],
        output_config={"format": {"type": "json_schema", "schema": _GRADE_SCHEMA}},
        messages=[{"role": "user", "content": f"Grade this goal:\n\n{goal}"}],
    )
    payload = json.loads("".join(b.text for b in response.content if b.type == "text"))
    return GoalGrade(
        elements=[
            ElementGrade(element=e["element"], verdict=e["verdict"], note=e["note"])
            for e in payload["elements"]
        ],
        summary=payload["summary"],
    )
