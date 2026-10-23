"""Invariant selftests: the first example teaches what the product does.

`examples/01-your-first-hour.md` is the page a new chairman follows first, and
its lesson is the plan gate: the CEO proposes, *you* accept, and nothing is
created until you do. Its setup file named none of the five authority rules,
so the platform created it Autonomous — the CEO accepted its own plan, and the
one thing the hour exists to show never happened.
"""

from __future__ import annotations

import json
import pathlib

from yaaf.core.constitution import default_autonomy, preset_name

EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples"


def _constitution(name: str) -> dict:
    bundle = json.loads((EXAMPLES / name).read_text())
    return bundle["project"]["constitution"]


def test_the_first_hour_is_created_supervised_the_way_its_page_says():
    # Through the same function project creation uses, so a spec that falls
    # silent again is caught here, not by a chairman whose plan was accepted
    # for him.
    assert preset_name(default_autonomy(_constitution("01-your-first-hour.json"))) == \
        "supervised"
    assert "Supervised" in (EXAMPLES / "01-your-first-hour.md").read_text()


def test_the_first_hour_hires_only_the_writer_its_page_describes():
    # The page says "one writer named Ada". A file that left the read unset
    # took the form's default card, which asks for a second reader the page
    # never mentions.
    assert _constitution("01-your-first-hour.json")["deliverable_review"] == "none"


# Screens the app no longer has, and the step it no longer asks for. Each was
# still the instruction in a doc a new chairman follows: "go to the Plan tab",
# "paste your Anthropic key" into a form that now picks a saved credential.
RETIRED = ("Plan tab", "**Plan** tab", "Work tab", "**Work** tab", "Decisions screen",
           "Records →", "Paste your Anthropic key")


def test_no_guide_sends_a_reader_to_a_screen_that_no_longer_exists():
    root = EXAMPLES.parent
    guides = [root / "README.md", *sorted((root / "examples").glob("*.md")),
              *sorted(p for p in (root / "docs").rglob("*.md")
                      # The redesign study describes the old layout on purpose.
                      if "ux-redesign" not in p.parts)]
    stale = [f"{path.relative_to(root)}:{n}: {line.strip()[:90]}"
             for path in guides
             for n, line in enumerate(path.read_text().splitlines(), 1)
             # History is allowed to name what was there: "it used to sit…".
             if any(old in line for old in RETIRED) and "used to" not in line]
    assert not stale, "\n".join(stale)
