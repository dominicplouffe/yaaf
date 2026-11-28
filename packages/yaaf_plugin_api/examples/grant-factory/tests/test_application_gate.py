"""The example's own attack: a budget line with no rule is refused.

Runs on the plain API today; the SDK harness (build step 6) will drive the same
case over HTTP against a real yaaf."""

import json
from datetime import date

from grant_factory import plugin
from yaaf_plugin_api import ProjectSnapshot, Refuse, Upload


def test_a_budget_line_with_no_rule_is_refused():
    ctx = ProjectSnapshot("p1", "grant_application", "draft", "0" * 40, date(2027, 1, 1))
    doc = {"title": "Garden", "budget": [{"amount": 10}]}
    verdict = plugin.check_application(ctx, Upload("application.json",
                                                   json.dumps(doc).encode(), "s1", "r1"))
    assert isinstance(verdict, Refuse)
    assert any("rule" in p for p in verdict.problems)
