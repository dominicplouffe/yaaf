"""Invariant selftest: how good the work has to be is read from the brief.

The quality cards — Full send, Balanced, Keep it cheap, Show me proof — sat
beside the pace cards and were read as one speed dial; "Full send" set $5,000
a month and a stronger model, and the chairman heard "go fast". He asked that
the brief decide it instead, on the form and in the Founder alike.

What this file attacks:

1. A reading rests on the chairman's own words. Its quote must be in the
   brief, at the create door, whichever door — a reason nobody can find in
   what he wrote is a reason the reader made up.
2. A reading never moves the money. The bar bundles carry none.
3. His own choice outranks any reading, and needs no quote.
"""

from __future__ import annotations

from sqlalchemy import select

from tests.conftest import ADMIN, make_project
from yaaf.core.constitution import BAR_PRESETS, BAR_RULES, effective
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog
from yaaf.server import bar_reading

GOAL = ("Rebuild the pricing page our paying customers land on, and ship it before "
        "the spring launch. Every number on it must be one we can cite.")


def _create(client, slug: str, *, goal: str = GOAL, constitution=None, quality=None):
    make_project(client, slug="seed")  # the chairman and roles exist
    body = {"slug": slug, "name": slug.title(), "goal": goal,
            "chairman_email": "chair@example.com", "ceo_role_slug": "ceo",
            "ceo_name": "CEO", "mode": "deliver",
            "constitution": constitution or {},
            "teams": [{"role_slug": "worker", "name": "Alpha",
                       "employees": ["Lead", "Emp2"], "lead": "Lead"}]}
    if quality is not None:
        body["quality"] = quality
    return client.post("/admin/projects", json=body, headers=ADMIN)


# ── the reading ────────────────────────────────────────────────────────────────


def test_the_word_rules_read_who_receives_the_work():
    reading = bar_reading.rules(GOAL)
    assert reading.stakes == "high" and reading.by == "rules"
    assert reading.quote and bar_reading.quote_problem(GOAL, reading.quote) is None
    assert reading.proof is True  # "one we can cite"

    curious = bar_reading.rules("Just curious which home printer is best for photos.")
    assert curious.stakes == "low"

    plain = bar_reading.rules("Write up how our deploy process works today.")
    assert (plain.stakes, plain.quote, plain.by) == ("normal", None, "default")


def test_a_model_that_quotes_words_not_in_the_brief_is_not_believed(monkeypatch):
    """The quote is the check. A reading resting on something the chairman
    never wrote is replaced by the word rules, not shown to him."""
    monkeypatch.setattr(bar_reading, "READER", lambda session, secret, goal:
                        bar_reading.BarReading("low", False, "a throwaway experiment", "fast-model"))
    with db_session() as session:
        reading = bar_reading.read_brief(session, GOAL, None)
    assert reading.by == "rules" and reading.stakes == "high"


def test_a_model_reading_with_a_real_quote_stands(monkeypatch):
    monkeypatch.setattr(bar_reading, "READER", lambda session, secret, goal:
                        bar_reading.BarReading("high", False, "paying customers land on",
                                               "fast-model"))
    with db_session() as session:
        reading = bar_reading.read_brief(session, GOAL, None)
    assert (reading.stakes, reading.by) == ("high", "fast-model")


def test_the_form_reads_a_brief_before_the_project_exists(client):
    make_project(client, slug="seed")
    response = client.post("/admin/brief/bar", json={"goal": GOAL}, headers=ADMIN)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stakes"] == "high" and body["quote"] in GOAL
    assert body["rules"]["stakes"] == "high"
    assert body["rules"]["require_evidence_verification"] is True
    assert "monthly_usd_cap" not in body["rules"]


# ── the money is not the bar's ─────────────────────────────────────────────────


def test_no_quality_bundle_carries_money():
    for name, rules in BAR_PRESETS.items():
        assert not {"monthly_usd_cap", "approval_threshold_usd",
                    "project_usd_budget"} & set(rules), name
    assert "monthly_usd_cap" not in BAR_RULES


# ── the create door holds both routes to the same check ────────────────────────


def test_a_quote_not_in_the_brief_is_refused_at_create(client):
    refused = _create(client, "forged", constitution={"stakes": "high"},
                      quality={"stakes": "high", "proof": False,
                               "quote": "investors will read this", "by": "fast-model"})
    assert refused.status_code == 422, refused.text
    assert "not in the brief" in refused.text


def test_a_reading_the_constitution_contradicts_is_refused(client):
    refused = _create(client, "contradicts", constitution={"stakes": "low"},
                      quality={"stakes": "high", "proof": False,
                               "quote": "paying customers land on", "by": "fast-model"})
    assert refused.status_code == 422, refused.text
    assert "one of them is wrong" in refused.text


def test_a_reading_that_holds_is_recorded_with_its_words(client):
    created = _create(client, "recorded", constitution={"stakes": "high"},
                      quality={"stakes": "high", "proof": True,
                               "quote": "paying customers land on", "by": "fast-model"})
    assert created.status_code == 200, created.text
    with db_session() as session:
        row = session.execute(select(AuditLog).where(
            AuditLog.project_id == created.json()["id"],
            AuditLog.action == "bar.read")).scalar_one()
        assert row.detail["quote"] == "paying customers land on"
        assert effective(session, created.json()["id"]).stakes == "high"


def test_his_own_choice_needs_no_quote(client):
    created = _create(client, "his-call", constitution={"stakes": "low"},
                      quality={"stakes": "low", "proof": False, "quote": None,
                               "by": "chairman"})
    assert created.status_code == 200, created.text


def test_the_founder_is_held_to_the_same_words():
    from yaaf.founder.tools import stamp_quality

    draft = {"goal": GOAL, "constitution": {"stakes": "high"},
             "stakes_quote": "investors will read this"}
    problems = stamp_quality(draft)
    assert problems and "stakes_quote" in problems[0] and "not in the brief" in problems[0]

    draft = {"goal": GOAL, "constitution": {"stakes": "high"},
             "stakes_quote": "paying   customers land on"}
    assert stamp_quality(draft) == []
    assert draft["quality"] == {"stakes": "high", "proof": False,
                                "quote": "paying customers land on", "by": "founder"}
    assert bar_reading.reading_problem(GOAL, draft["constitution"], draft["quality"]) is None
