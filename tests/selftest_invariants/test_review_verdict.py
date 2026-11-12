"""The status meeting's verdict: results, then money, then the misses that matter.

A chairman read "Cycle 1: Monday pack with audit note: owed this cycle and never
checked; 6 of 9 one-off requirements met, R-06, R-08, R-10 carried forward. This
month's spend is 60% of plan" under a line saying nothing was waiting on him,
and could not tell whether it was good news. These pin what he asked for: a
miss is judged by whether it matters, not counted; "never checked" always
matters; and a good week with the money fine says so in one line and stops.
Pure function, no app — the same facts the headline reads.
"""

from yaaf.server import review_record

MONDAY_PACK = {"key": "R-09", "name": "Monday pack with audit note",
               "title": "Monday pack with audit note"}


def _operate(standing_verdict: str, carried=("R-06", "R-08", "R-10"), spent=60, cap=100,
             rate=None, runs=(466, 156), failed_checks=0, as_of="2026-11-09T06:31:00+00:00"):
    met = ["R-01", "R-02", "R-03", "R-04", "R-05", "R-07"]
    if standing_verdict == "honoured":
        met.append("R-09")
    record = {
        "cycle": {"ordinal": 1, "status": "missed", "met_keys": met,
                  "carried_keys": list(carried), "cut_keys": [],
                  "standing": [{**MONDAY_PACK, "verdict": standing_verdict}]},
        "spot_checks": {"considered": 0, "drawn": 0, "passed": 0,
                        "failed": failed_checks, "expired": 0},
        "runs": {"total": runs[0], "dead": runs[1], "usd": 0},
    }
    numbers = {"as_of": as_of,
               "budget": {"unit": "USD", "spent_mtd": spent, "cap": cap, "rate": rate}}
    return review_record.verdict("operate", numbers, record)


def test_the_week_in_the_screenshot_names_the_one_miss_that_matters():
    v = _operate("unchecked")
    assert v["results"]["tone"] == "unknown"
    assert "Monday pack" in v["results"]["says"]
    assert [m["key"] for m in v["matters"]] == ["R-09"]
    assert "never checked" in v["matters"][0]["what"]
    # The three slipped one-offs are told apart from it, not counted beside it.
    assert {m["key"] for m in v["minor"]} == {"R-06", "R-08", "R-10"}
    assert not v["fine"]
    assert "1 miss that matters" in v["sentence"]


def test_spending_ahead_of_the_calendar_is_said_as_money_and_names_the_waste():
    # 60% of plan on the 9th, with no measured rate: ahead of the calendar.
    v = _operate("unchecked")
    assert v["money"]["tone"] == "watch"
    assert "$60 of $100" in v["money"]["says"]
    assert "156 of 466 runs died" in v["money"]["says"]


def test_a_good_week_inside_budget_is_one_line_and_stops():
    v = _operate("honoured", spent=20, as_of="2026-11-20T00:00:00+00:00")
    assert v["fine"]
    assert v["sentence"] == "Getting results and within budget. Nothing that matters was missed."
    assert v["matters"] == []
    # The slipped one-offs are still on the record, folded — not erased.
    assert len(v["minor"]) == 3


def test_dead_runs_are_not_mentioned_when_the_money_is_fine():
    v = _operate("honoured", spent=20, as_of="2026-11-20T00:00:00+00:00")
    assert "died" not in v["money"]["says"]


def test_a_result_owed_and_not_delivered_is_a_problem():
    v = _operate("missed")
    assert v["results"]["tone"] == "bad"
    assert v["matters"][0]["what"] == "Monday pack with audit note was not delivered"


def test_over_budget_is_bad_even_with_results_in():
    v = _operate("honoured", spent=120)
    assert v["money"]["tone"] == "bad"
    assert v["money"]["says"].startswith("Over budget")
    assert not v["fine"]


def test_a_measured_rate_that_hits_the_cap_before_month_end_says_the_day():
    v = _operate("honoured", spent=40, rate={"projectable": True,
                                             "lands_on_cap_at": "2026-11-20T00:00:00+00:00",
                                             "days_observed": 8})
    assert v["money"]["tone"] == "bad"
    assert "Nov 20" in v["money"]["says"]


def test_a_measured_rate_inside_the_cap_overrules_the_calendar():
    # Ahead of the calendar, but the measured days say it lands under.
    v = _operate("honoured", spent=60, rate={"projectable": True,
                                             "lands_on_cap_at": None, "days_observed": 8})
    assert v["money"]["tone"] == "good"
    assert v["fine"]


def test_a_failed_spot_check_matters_and_unsettles_good_results():
    v = _operate("honoured", spent=20, failed_checks=2,
                 as_of="2026-11-20T00:00:00+00:00")
    assert v["results"]["tone"] == "unknown"
    assert any("spot-check" in m["what"] for m in v["matters"])
    assert not v["fine"]


def test_deliver_late_against_its_promise_matters():
    numbers = {"commitment": {"status": "at_risk", "deliver_by": "2026-11-10T00:00:00",
                              "projected_done_at": "2026-11-17T00:00:00"},
               "budget": {"unit": "USD", "spent_mtd": 10, "cap": 100}}
    v = review_record.verdict("deliver", numbers, {"requirements_met": []})
    assert v["results"]["tone"] == "bad"
    assert "7 days" in v["results"]["says"]
    assert len(v["matters"]) == 1


def test_a_meeting_before_the_record_does_not_claim_results():
    v = review_record.verdict("operate", {}, None)
    assert v["results"]["tone"] == "unknown"
    assert not v["fine"]
    assert v["money"]["says"] == "No budget is set."
