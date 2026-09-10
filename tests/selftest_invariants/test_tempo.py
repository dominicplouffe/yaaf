"""Invariant selftests: the tempo card, and re-picking one afterwards.

The new-project form asked "How does it run?" and answered a different
question. Of the twenty-two constitution rules that govern pace, that card
wrote one — so "Cheap and patient" and "Full send" woke a project equally
often: 150 runs a day, a planning wake every six hours, forty message-only
wakes. The chairman who picked the cheap card got a smaller number to overshoot
and a project that ran exactly as hot, and there was no way at all to say "do
it properly, but rarely".

Both bundles now live in `yaaf.core.constitution` beside the rules they write,
which is the move `AUTONOMY_PRESETS` already made and for the same reason: a
preset that lives in the web form is a preset only the web form can offer.

The lines these tests hold:

  * a card is only ever real rules — an invented key is caught here, not
    stored and silently ignored for the life of the project
  * `live` is the field defaults, so nothing on the install moved when this
    shipped
  * silence resolves from the mode, and naming even one pace rule means the
    chairman is choosing rule by rule and nothing is stamped over him
  * a project on hand-tuned rules reports itself as hand-tuned, never as the
    nearest card
  * re-picking is ONE amendment, with a reason, in force immediately — the
    chairman's own words for getting the tempo wrong were "we may start a
    project with one mode in mind, but then realize we got it wrong"
  * a refused bundle leaves the constitution exactly where it was
  * `Project.mode` is not re-pickable, and nothing here pretends it is
"""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN, make_project, seed_role
from yaaf.core.constitution import (
    BAR_PRESETS,
    BAR_RULES,
    TEMPO_PRESETS,
    TEMPO_RULES,
    ConstitutionDoc,
    bar_name,
    default_tempo,
    effective,
    preset_cards,
    tempo_for,
    tempo_name,
    unknown_rules,
)
from yaaf.core.db import db_session
from yaaf.founder.tools import tempo_problems
from yaaf.core.models import Project

# ── the bundles themselves ──────────────────────────────────────────────────────


@pytest.mark.parametrize("name,rules", sorted(TEMPO_PRESETS.items()))
def test_a_tempo_card_writes_only_real_rules(name, rules):
    """The check `unknown_rules` exists for: a Founder once invented
    `reporting_cadence`, which validated, stored, was shown back to the
    chairman in the amendment log, and was never in force. A card carrying a
    key like that would do the same thing to every project that ever picked
    it."""
    assert not unknown_rules(rules), name
    assert set(rules) == set(TEMPO_RULES)
    ConstitutionDoc.model_validate({**ConstitutionDoc().model_dump(), **rules})


@pytest.mark.parametrize("name,rules", sorted(BAR_PRESETS.items()))
def test_a_bar_card_writes_only_real_rules(name, rules):
    assert not unknown_rules(rules), name
    assert set(rules) == set(BAR_RULES)
    ConstitutionDoc.model_validate({**ConstitutionDoc().model_dump(), **rules})


def test_the_two_cards_never_fight_over_a_rule():
    """One rule, one card. Two cards writing the same rule means the last one
    clicked wins and the other card is quietly lying about what it does."""
    assert not set(BAR_RULES) & set(TEMPO_RULES)


def test_live_is_what_the_platform_already_did():
    """Shipping a quieter default would have lowered the daily volume of every
    project on the install without anyone choosing that for any of them —
    exactly the reasoning `runs_hard_stop_multiple` records for defaulting to
    1.0. Only the two quieter cards are new behaviour."""
    defaults = ConstitutionDoc().model_dump()
    assert TEMPO_PRESETS["live"] == {k: defaults[k] for k in TEMPO_RULES}


def test_report_interval_is_not_a_tempo_rule():
    """It is opt-in — 0 means "you never asked and nothing is refused" — and
    tempo is the one bundle stamped on silence. In the tempo bundle it made a
    project nobody said anything about start REFUSING its CEO's unprompted
    statuses, which is the opposite of the direction that rule protects: it
    exists because a CEO posted thirteen statuses in 2h20m, not too few."""
    assert "chairman_report_interval_hours" not in TEMPO_RULES
    assert "chairman_report_interval_hours" in BAR_RULES


@pytest.mark.parametrize("name,rules", sorted(TEMPO_PRESETS.items()))
def test_a_tempo_card_round_trips(name, rules):
    assert tempo_name({**ConstitutionDoc().model_dump(), **rules}) == name


@pytest.mark.parametrize("name,rules", sorted(BAR_PRESETS.items()))
def test_a_bar_card_round_trips(name, rules):
    assert bar_name({**ConstitutionDoc().model_dump(), **rules}) == name


def test_hand_tuned_rules_are_reported_as_hand_tuned():
    """None is the honest answer and the screens must show it as such. A
    chairman who edited four rules by hand is not "on Working hours"; telling
    him he is, is how he reads a card's blurb as a description of his own
    project and believes something untrue about what it costs."""
    mixed = {**ConstitutionDoc().model_dump(), **TEMPO_PRESETS["working_hours"],
             "runs_per_project_per_day": 37}
    assert tempo_name(mixed) is None


# ── silence, and what fills it ──────────────────────────────────────────────────


@pytest.mark.parametrize("mode,frequency,expected", [
    ("explore", None, "live"),
    ("deliver", None, "live"),
    ("operate", "hourly", "live"),
    ("operate", "daily", "working_hours"),
    ("operate", "weekly", "few_times_daily"),
    ("operate", "monthly", "few_times_daily"),
    ("operate", "quarterly", "few_times_daily"),
])
def test_every_shape_of_project_resolves_to_a_card(mode, frequency, expected):
    """Explore and Deliver both end — one against a window, one against a date
    — so racing is correct and there is no long tail to pay for. An Operate
    project rolling a cycle a week has already said it need not be awake 150
    times a day."""
    assert tempo_for(mode, frequency) == expected


def test_naming_one_pace_rule_means_nothing_is_stamped():
    """The mixed-dial rule `default_autonomy` already applies: naming even one
    is somebody choosing rule by rule, and a bundle laid over that overwrites a
    decision they did write with one they did not."""
    stated = {"runs_per_project_per_day": 7}
    assert default_tempo("operate", stated) == stated


def test_an_operate_project_reads_its_own_rhythm():
    """cycle_frequency is itself one of these rules, so it is read from the
    constitution being built rather than passed alongside it."""
    assert default_tempo("operate", {"cycle_frequency": "hourly"})[
        "runs_per_project_per_day"] == 150
    assert default_tempo("operate", {})["runs_per_project_per_day"] == 12


def test_a_weekly_operate_project_is_created_quiet(client):
    """The case the chairman named: research whose scripts are already
    written, which should wake, look, and write a report. It used to be
    created at 150 runs a day with forty message-only wakes on top."""
    project = make_project(client, mode="operate",
                           constitution={"cycle_frequency": "weekly"})
    rules = project["constitution"]
    assert rules["runs_per_project_per_day"] == 12
    # The chatter switch, which nothing has ever set: agents read their
    # channels on the next wake they were having anyway.
    assert rules["inbox_runs_per_project_per_day"] == 0
    # And the rule that makes the rest of it usable rather than a bug — at the
    # default of 6h a project waking three times a day cannot schedule work
    # for tomorrow morning at all.
    assert rules["max_task_gate_hours"] == 36
    assert tempo_name(rules) == "few_times_daily"


def test_an_explore_project_is_created_exactly_as_before(client):
    project = make_project(client, mode="explore")
    defaults = ConstitutionDoc().model_dump()
    assert {k: project["constitution"][k] for k in TEMPO_RULES} == {
        k: defaults[k] for k in TEMPO_RULES}


def test_a_stated_pace_rule_survives_creation(client):
    """A spec file that says 40 gets 40, not a card rounded over it."""
    project = make_project(client, mode="operate",
                           constitution={"cycle_frequency": "weekly",
                                         "runs_per_project_per_day": 40})
    assert project["constitution"]["runs_per_project_per_day"] == 40
    # And nothing else from the bundle was stamped either — one named rule
    # means the whole bundle stands down.
    assert project["constitution"]["inbox_runs_per_project_per_day"] == 40


# ── the cards, as the screens receive them ──────────────────────────────────────


def test_the_status_endpoint_serves_every_card(client):
    """Served rather than mirrored. These used to live in NewProject.tsx and
    nowhere else, so the Founder could not pick one, the MCP surface could not
    pick one, and the settings page could not say which one you were on."""
    presets = client.get("/admin/founder/status", headers=ADMIN).json()["presets"]
    assert [c["value"] for c in presets["tempo"]] == list(TEMPO_PRESETS)
    assert [c["value"] for c in presets["bar"]] == list(BAR_PRESETS)
    for group in presets.values():
        for card in group:
            assert card["label"] and card["blurb"] and card["constitution"]
            assert not unknown_rules(card["constitution"])


def test_a_tempo_card_says_what_it_costs_in_runs():
    """Runs, not dollars. `avg_usd_per_run_box` refuses to answer under five
    priced runs — "a fresh box says nothing rather than guessing" — so the card
    carries the one unit this platform can always state honestly."""
    for card in preset_cards()["tempo"]:
        assert "runs a day" in card["headline"]


# ── changing your mind afterwards ───────────────────────────────────────────────


def _amend(client, project, changes, reason="The tempo was wrong for this desk."):
    return client.post(f"/admin/projects/{project['id']}/amendments",
                       json={"reason": reason, "changes": changes}, headers=ADMIN)


def _rules(_client, project):
    """What is actually in force right now, read the way the server reads it."""
    with db_session() as session:
        return effective(session, project["id"]).model_dump()


def test_re_picking_a_tempo_is_one_amendment_in_force_now(client):
    """"We may start a project with one mode in mind, but then realize we got
    it wrong." Fourteen rules move as one recorded decision with one written
    reason — not fourteen amendments, and not after a waiting period the
    chairman could always have waited out anyway."""
    project = make_project(client, mode="explore")
    assert tempo_name(_rules(client, project)) == "live"

    response = _amend(client, project, TEMPO_PRESETS["few_times_daily"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["amendments"]) == 1
    assert body["amendments"][0]["applied_now"] is True

    assert tempo_name(_rules(client, project)) == "few_times_daily"
    # One recorded decision, carrying every rule that moved and the written
    # reason it moved for — not fourteen rows a reader has to reassemble.
    listed = client.get(f"/admin/projects/{project['id']}/amendments",
                        headers=ADMIN).json()
    assert len(listed) == 1
    assert set(listed[0]["changes"]) == set(TEMPO_RULES)
    assert listed[0]["reason"] == "The tempo was wrong for this desk."


def _with_a_reader(client):
    """A roster carrying a seat whose job is to read what the others wrote.
    Every bar card asks for one, and the server refuses a roster that asks and
    hires nobody — see the test below."""
    seed_role(client, "editor", category="challenge")
    return make_project(
        client, mode="operate", constitution={"cycle_frequency": "weekly"},
        teams=[{"role_slug": "worker", "name": "Work", "employees": ["Ada"]},
               {"role_slug": "editor", "name": "Review", "employees": ["Juno"]}])


def test_re_picking_the_bar_leaves_the_tempo_alone(client):
    """The whole point of splitting the card: a chairman who decides the work
    matters more must not silently buy a project that also runs four times as
    often. Before the split there was one card and no way to move either half
    on its own."""
    project = _with_a_reader(client)
    before = _rules(client, project)
    assert tempo_name(before) == "few_times_daily"
    assert _amend(client, project, BAR_PRESETS["full_send"],
                  reason="This turned out to matter.").status_code == 200
    after = _rules(client, project)
    assert bar_name(after) == "full_send"
    assert {k: after[k] for k in TEMPO_RULES} == {k: before[k] for k in TEMPO_RULES}
    # And the cell that was unreachable before the split: the work is worth the
    # strongest model, and the project still wakes twelve times a day.
    assert after["stakes"] == "high"
    assert after["runs_per_project_per_day"] == 12


def test_re_picking_the_bar_without_a_reader_is_refused_intact(client):
    """Every bar card asks for the work to be read by somebody who did not
    write it, and the server refuses a roster that asks and hires nobody. The
    picker inherits that refusal, which is right — what matters is that it
    refuses the whole bundle rather than landing half of it."""
    project = make_project(client, mode="explore")
    before = _rules(client, project)
    response = _amend(client, project, BAR_PRESETS["full_send"],
                      reason="This turned out to matter.")
    assert response.status_code == 422
    assert "no team on this roster hires a reviewing seat" in response.text
    assert _rules(client, project) == before


def test_a_refused_bundle_leaves_the_constitution_untouched(client):
    """`propose_amendment` validates the whole overlay so a bad amendment is
    refused rather than half-applied. A picker that staged fourteen rules and
    landed nine would be worse than no picker."""
    project = make_project(client, mode="explore")
    before = _rules(client, project)
    broken = {**TEMPO_PRESETS["few_times_daily"], "runs_per_project_per_day": 0}
    assert _amend(client, project, broken).status_code >= 400
    assert _rules(client, project) == before


def test_an_invented_rule_is_refused_rather_than_stored(client):
    project = make_project(client, mode="explore")
    response = _amend(client, project, {"reporting_cadence": 4})
    assert response.status_code >= 400
    assert "no such rule" in response.text


def test_the_mode_is_not_re_pickable(client):
    """Mode decides whether the project has requirements at all, so it is
    frozen at creation — a project of the wrong shape is rebuilt from its setup
    file, not converted. The form now says so where the choice is made; this
    holds the line underneath it."""
    project = make_project(client, mode="explore")
    client.patch(f"/admin/projects/{project['id']}",
                 json={"mode": "operate"}, headers=ADMIN)
    with db_session() as session:
        assert session.get(Project, project["id"]).mode == "explore"


# ── what the Founder is held to ─────────────────────────────────────────────────


def test_the_founder_must_choose_a_tempo():
    """The same argument as `stakes` one level over: an unset dial is a
    decision nobody made and nobody can find later. A project that never
    mentions pace runs at 150 runs a day because nobody said otherwise."""
    assert tempo_problems({"mode": "operate", "constitution": {}})
    assert tempo_problems({"mode": "deliver", "constitution": {}})


def test_explore_is_exempt():
    """Open scope against a clock: the platform's own answer is the right one,
    and a question with no decision behind it is friction."""
    assert tempo_problems({"mode": "explore", "constitution": {}}) == []


def test_the_founder_picks_a_card_never_a_number():
    """`DESIGN_DOCTRINE.md` records a Founder that opened a desk at 12 runs a
    day against a default of 150, defended it twice, and was overruled the next
    day — the chairman's single intervention being to raise it. The numbers on
    these cards were chosen by people who watched projects fail in both
    directions; the Founder's come from one sentence in a brief."""
    invented = {"mode": "operate", "constitution": {"runs_per_project_per_day": 12}}
    problems = tempo_problems(invented)
    assert problems and "not one of the tempo cards" in problems[0]

    for name, rules in TEMPO_PRESETS.items():
        assert tempo_problems({"mode": "operate", "constitution": dict(rules)}) == [], name
