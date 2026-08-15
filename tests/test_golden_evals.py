"""CI gate: the golden-transcript evals (PLAN §"Agent runtime").

Role prompts and templates are editable in the UI; this file is what makes
that safe. Each scripted scenario re-runs against the real app with the
MockModel backend — fast, $0 in tokens — and must (a) satisfy its declarative
expectations and (b) normalize to exactly the recorded golden transcript.
A diff is a regression; re-record deliberately with:

    python -m evals.run --record
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals import harness

SCENARIOS = harness.list_scenarios()


def test_scenarios_exist():
    assert SCENARIOS, "no eval scenarios found under evals/scenarios/"


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario_matches_golden(path: Path):
    scenario = harness.load_scenario(path)
    result = harness.run_scenario(scenario)

    assert not result.failures, (
        "declarative expectations failed:\n- " + "\n- ".join(result.failures)
    )

    golden = harness.read_golden(scenario)
    assert golden is not None, (
        f"missing golden transcript {harness.golden_path(scenario)}; "
        "record it with: python -m evals.run --record"
    )
    if golden != result.normalized:
        diff = "\n".join(harness.unified_diff(golden, result.normalized, scenario.name))
        pytest.fail(f"transcript regression vs golden:\n{diff}")
