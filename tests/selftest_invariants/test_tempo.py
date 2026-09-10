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

from datetime import timedelta

import pytest

from tests.conftest import ADMIN, make_project, seed_role
from yaaf.core import ids
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
    stated_tempo,
    tempo_for,
    tempo_name,
    unknown_rules,
)
from yaaf.core.db import db_session
from yaaf.core.models import (
    Constitution,
    Notice,
    NoticeKind,
    Project,
    Run,
    RunStatus,
    Task,
    TaskStatus,
    utcnow,
)
from yaaf.founder.tools import tempo_problems
from yaaf.server import briefing

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


# ── noticing afterwards, without rewriting anything ─────────────────────────────


def _checks(client, project) -> dict[str, dict]:
    response = client.get(f"/observe/projects/{project['id']}/health", headers=ADMIN)
    assert response.status_code == 200, response.text
    return {c["key"]: c for c in response.json()["checks"]}


def test_a_rhythm_that_moves_without_the_pace_is_reported(client):
    """cycle_frequency is amendable and the tempo is not re-derived afterwards
    — deliberately: a platform that silently re-paced a live desk because a
    rule moved is a worse failure than the one it fixes. So the chairman is
    told and decides, which is the posture `doctrine-drift` already takes."""
    project = make_project(client, mode="operate",
                           constitution={"cycle_frequency": "weekly"})
    assert "tempo-vs-rhythm" not in _checks(client, project)

    assert _amend(client, project, {"cycle_frequency": "hourly"},
                  reason="This desk turned out to be an order queue.").status_code == 200
    check = _checks(client, project).get("tempo-vs-rhythm")
    assert check and check["status"] == "warn"
    assert "hourly" in check["detail"] and "live" in check["detail"]
    # Told, never applied: the pace rules are exactly where they were.
    assert tempo_name(_rules(client, project)) == "few_times_daily"


def test_a_hand_tuned_project_is_not_nagged_about_its_rhythm(client):
    """No card describes it, so there is no card to say it should be on."""
    project = make_project(client, mode="operate",
                           constitution={"cycle_frequency": "weekly",
                                         "runs_per_project_per_day": 37})
    assert "tempo-vs-rhythm" not in _checks(client, project)


def test_a_budget_that_cannot_buy_the_ceiling_is_reported(client):
    """The FPL desk in this repo's own docs declares $75 a month and 60 runs a
    day. At the one price this platform has ever measured those are different
    projects by a factor of about three, and no screen did that arithmetic."""
    project = make_project(client, mode="explore",
                           constitution={"monthly_usd_cap": 75.0})
    # Silent on a box that has never priced a run: `avg_usd_per_run_box`
    # answers None under five of them rather than guessing.
    assert "tempo-vs-budget" not in _checks(client, project)

    with db_session() as session:
        for _ in range(5):
            session.add(Run(
                id=ids.new_id(ids.RUN), project_id=project["id"],
                agent_id=project["ceo_agent_id"], kind="agent_run", trigger={},
                status=RunStatus.DONE, cost_usd=0.15,
                started_at=utcnow(), finished_at=utcnow()))
    check = _checks(client, project).get("tempo-vs-budget")
    assert check and check["status"] == "warn"
    assert "$75" in check["detail"]


# ── saying where the money went ─────────────────────────────────────────────────


def test_the_brief_says_what_the_runs_were_for(client):
    """A bill is not a lever. The dispatcher classifies every wake at the
    moment it decides to spend one, and that answer reached a filter on one
    screen and the spend CLI — never the chairman. So "this is costing too
    much" had no next question attached to it, and the difference between runs
    that produced deliverables and runs of agents acknowledging each other is
    the whole of what to do about it."""
    project = make_project(client, mode="explore")
    with db_session() as session:
        for kind, trigger in (("agent_run", {"reasons": [{"kind": "task"}]}),
                              ("inbox_run", {"reasons": [{"kind": "messages"}]}),
                              ("inbox_run", {"reasons": [{"kind": "messages"}]}),
                              ("agent_run", {"reasons": [{"kind": "heartbeat"}]})):
            session.add(Run(
                id=ids.new_id(ids.RUN), project_id=project["id"],
                agent_id=project["ceo_agent_id"], kind=kind, trigger=trigger,
                status=RunStatus.DONE, cost_usd=0.15,
                started_at=utcnow(), finished_at=utcnow()))

    with db_session() as session:
        brief = briefing.project_briefing(
            session, session.get(Project, project["id"]),
            since=utcnow() - timedelta(hours=1))
    runs = next(line for line in brief["lines"] if line["kind"] == "runs")
    assert runs["count"] == 4
    assert "1 work" in runs["note"]
    assert "2 reading each other" in runs["note"]
    assert "1 waking up to look around" in runs["note"]


def test_the_brief_names_the_tempo_the_project_thinks_on(client):
    project = make_project(client, mode="operate",
                           constitution={"cycle_frequency": "weekly"})
    with db_session() as session:
        brief = briefing.project_briefing(
            session, session.get(Project, project["id"]),
            since=utcnow() - timedelta(hours=1))
    assert brief["tempo"] == "few_times_daily"


def test_the_capacity_forecast_moves_with_the_tempo(client):
    """The fire drill relaxes the run ceilings — it compresses a fortnight into
    seconds and has no clock — and computes their real effect on the calendar
    from the chairman's own numbers instead. So the forecast is where a quiet
    tempo has to show up, and it is the one place he can see the trade before
    the project exists rather than three weeks in."""
    from yaaf.server.capacity import capacity_forecast

    project = make_project(client, mode="explore")
    with db_session() as session:
        row = session.get(Project, project["id"])
        until = utcnow() + timedelta(days=30)
        fast = capacity_forecast(
            session, row,
            ConstitutionDoc.model_validate(
                {**ConstitutionDoc().model_dump(), **TEMPO_PRESETS["live"]}),
            until)
        quiet = capacity_forecast(
            session, row,
            ConstitutionDoc.model_validate(
                {**ConstitutionDoc().model_dump(), **TEMPO_PRESETS["few_times_daily"]}),
            until)
    assert quiet["available"] < fast["available"], (quiet, fast)


# ── the hazard that had to be checked rather than assumed ───────────────────────


def test_a_quiet_project_out_of_runs_is_not_reported_as_stalled(client):
    """`stall_hours` is deliberately NOT a tempo rule, and this is why.

    The watchdog tells the chairman when a project with work on its board has
    started no run for hours. A project that wakes twelve times a day and
    spends them by lunchtime looks exactly like that — so the obvious move was
    to loosen `stall_hours` on the quiet cards, and the obvious move would have
    been a guess dressed up as a bundle. It is not needed: a project held by a
    ceiling names the moment that ceiling clears, and the watchdog already
    skips anything with a clearing time attached. What it exists for is silence
    nobody can explain, and this silence explains itself.

    Deliberately slow: this drives the real janitor over a real board, because
    the thing under test is the interaction between three subsystems and a
    mocked one of them would prove nothing.
    """
    from yaaf.server import dispatcher, janitor

    project = make_project(client, mode="operate",
                           constitution={"cycle_frequency": "weekly"})
    rules = _rules(client, project)
    assert rules["runs_per_project_per_day"] == 12
    assert rules["stall_hours"] == 3.0

    with db_session() as session:
        # Work sitting ready, and the day's whole allowance already spent —
        # every run of it older than the watchdog's window.
        session.add(Task(
            id=ids.new_id(ids.TASK), project_id=project["id"],
            team_id=project["teams"][0]["id"],
            created_by_agent_id=project["ceo_agent_id"],
            title="Read the traffic report", goal="Read it.",
            success_test={"type": "command", "command": "true"},
            status=TaskStatus.READY, created_at=utcnow(), updated_at=utcnow()))
        for _ in range(rules["runs_per_project_per_day"]):
            session.add(Run(
                id=ids.new_id(ids.RUN), project_id=project["id"],
                agent_id=project["ceo_agent_id"], kind="agent_run", trigger={},
                status=RunStatus.DONE, cost_usd=0.15,
                created_at=utcnow() - timedelta(hours=5),
                started_at=utcnow() - timedelta(hours=5),
                finished_at=utcnow() - timedelta(hours=5)))

    with db_session() as session:
        row = session.get(Project, project["id"])
        state = dispatcher.activity_state(session, row, effective(session, row.id))
        # The project can say why it is quiet and when it stops being quiet,
        # which is the whole difference between a wait and a stall.
        assert state["brief"]["clears_at"]

    with db_session() as session:
        janitor.janitor_once(session)
    with db_session() as session:
        stalled = session.query(Notice).filter(
            Notice.project_id == project["id"],
            Notice.kind == NoticeKind.STALLED).all()
    assert not stalled, [n.body for n in stalled]


# ── what a spec CHOSE, as against what it happens to equal ──────────────────────


def test_a_partial_spec_has_not_chosen_a_card():
    """`live` is byte-identical to the field defaults, so a spec naming one
    pace rule at its default value produces a project that genuinely IS live —
    and `tempo_name` says so, correctly. Nobody chose it. Two callers asked the
    first question when they meant the second, and both were wrong for it."""
    assert tempo_name({**ConstitutionDoc().model_dump(), "queue_ceiling": 8}) == "live"
    assert stated_tempo({"queue_ceiling": 8}) is None
    for name, rules in TEMPO_PRESETS.items():
        assert stated_tempo(dict(rules)) == name


def test_the_founder_cannot_pass_by_naming_one_rule_at_its_default():
    """The gap this closes: a weekly Operate spec setting only `queue_ceiling`
    passed the doctrine check and shipped at 150 runs a day — the exact
    accident both this check and `default_tempo` exist to prevent.
    `queue_ceiling` is also the knob DESIGN_DOCTRINE told Founders to set until
    it moved onto the tempo card, so a real spec will do this."""
    problems = tempo_problems({"mode": "operate", "constitution": {"queue_ceiling": 8}})
    assert problems and "not one of the tempo cards" in problems[0]


def test_the_mcp_preview_survives_a_partial_tempo():
    """It raised KeyError and killed `create_project` before its own confirm
    gate: `default_tempo` stands the bundle down as soon as one rule is named,
    so the dict it returns can be a single key."""
    from yaaf.server.mcp.tools_create import _tempo_line

    line = _tempo_line("operate", {"queue_ceiling": 8})
    assert "set rule by rule" in line and "queue_ceiling: 8" in line
    assert "few_times_daily" in _tempo_line("operate", {})
    assert _tempo_line(
        "operate", dict(TEMPO_PRESETS["few_times_daily"])).startswith("few_times_daily")


# ── the health checks, read where the constitution is guarded ───────────────────


def test_health_survives_a_project_with_no_constitution(client):
    """The tempo checks used to read `effective()` unguarded, above the block
    whose `except LookupError` exists for exactly this — so a project in the
    one state the `activity` check was written to report 500'd the endpoint
    instead of reporting it."""
    project = make_project(client, mode="explore")
    with db_session() as session:
        for row in session.query(Constitution).filter(
                Constitution.project_id == project["id"]).all():
            session.delete(row)
    checks = _checks(client, project)
    assert checks["activity"]["status"] == "fail"
    assert "no constitution row" in checks["activity"]["detail"]


def test_the_budget_warning_counts_both_lanes(client):
    """The inbox ceiling is a separate additive allowance — `daily_runs_used`
    excludes those runs from the work total on purpose — so a `live` project's
    real ceiling is 190 runs a day, not 150. Counting one lane understated the
    warning by about a quarter."""
    project = make_project(client, mode="explore",
                           constitution={"monthly_usd_cap": 75.0})
    with db_session() as session:
        for _ in range(5):
            session.add(Run(
                id=ids.new_id(ids.RUN), project_id=project["id"],
                agent_id=project["ceo_agent_id"], kind="agent_run", trigger={},
                status=RunStatus.DONE, cost_usd=0.15,
                started_at=utcnow(), finished_at=utcnow()))
    detail = _checks(client, project)["tempo-vs-budget"]["detail"]
    assert "190 runs a day" in detail
    assert "150 of work plus 40 of reading" in detail


def test_the_brief_names_both_cards(client):
    """The footprint question: tempo reported in three places and the bar card
    in none."""
    project = make_project(client, mode="operate",
                           constitution={"cycle_frequency": "weekly"})
    with db_session() as session:
        brief = briefing.project_briefing(
            session, session.get(Project, project["id"]),
            since=utcnow() - timedelta(hours=1))
    assert brief["tempo"] == "few_times_daily"
    assert "bar" in brief
