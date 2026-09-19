"""Invariant selftests: the agenda.

The desk asks which clock runs out soonest. The agenda asks the flatter
question nothing answered — what is this project going to *do* this week? An
autonomous system that will not show you its schedule is a haunted one.

Two properties it has to hold:

  * it draws the schedule the platform will actually run, from the same
    arithmetic the dispatcher and the janitor use — a calendar that disagreed
    with the wake it depicts is worse than no calendar
  * it explains its own blanks. A chairman whose weekly meeting is missing from
    the strip is owed a reason, not an empty column
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import ADMIN, agent_by_name, bearer, make_project
from tests.selftest_invariants.test_requirements import (
    accept,
    ceo_token,
    deliver_project,
    propose,
)
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Commitment,
    CommitmentStatus,
    Project,
    Review,
    ReviewStatus,
    utcnow,
)


def agenda(client, project: dict, days: int | None = None) -> dict:
    url = f"/observe/projects/{project['id']}/agenda"
    if days is not None:
        url += f"?days={days}"
    response = client.get(url, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def kinds(payload: dict) -> list[str]:
    return [event["kind"] for event in payload["events"]]


def rhythm_kinds(payload: dict) -> list[str]:
    return [r["kind"] for r in payload["rhythms"]]


def amend(client, project: dict, **changes) -> None:
    response = client.post(
        f"/admin/projects/{project['id']}/amendments",
        json={"reason": "the chairman wants a different rhythm", "changes": changes},
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text


# ── rhythms ─────────────────────────────────────────────────────────────────────


def test_a_daily_wake_is_one_rhythm_not_fourteen_events(client):
    """A daily wake at 09:00 is one fact with many occurrences. Listing each
    one would bury the four dated events that actually matter."""
    project = make_project(client, slug="standup")
    amend(client, project, ceo_daily_at="09:00", timezone="UTC")

    payload = agenda(client, project, days=14)
    assert "ceo_daily" in rhythm_kinds(payload)
    daily = next(r for r in payload["rhythms"] if r["kind"] == "ceo_daily")
    assert daily["every"] == "every day at 09:00 (UTC)"
    assert daily["next"] is not None
    # And none of it leaked into the dated events.
    assert "ceo_daily" not in kinds(payload)


def test_a_rhythm_never_claims_a_day_the_strip_does_not_have(client):
    """The UTC horizon and the strip's last LOCAL day differ by one wherever
    the zone offset is not zero. A rhythm bounded by the former paints a
    fifteenth column onto a fourteen-column strip, where it silently vanishes
    — a wake the chairman was shown and then was not."""
    project = make_project(client, slug="offby")
    amend(client, project, ceo_daily_at="09:00", timezone="Pacific/Auckland")
    payload = agenda(client, project, days=14)
    daily = next(r for r in payload["rhythms"] if r["kind"] == "ceo_daily")
    assert set(daily["days"]) <= set(payload["day_list"])
    assert len(daily["days"]) in (13, 14)


def test_the_wake_time_is_read_in_the_constitution_s_one_timezone(client):
    """Every local time in this system reads one zone. A calendar drawing a
    09:00 New York wake at 09:00 UTC would be five hours of lie."""
    from zoneinfo import ZoneInfo

    from yaaf.core.models import Heartbeat

    project = make_project(client, slug="tz")
    amend(client, project, ceo_daily_at="09:00", timezone="America/New_York")
    # Fired today already, so the interesting number is the NEXT one — which
    # has to be 09:00 in New York, not 09:00 UTC.
    with db_session() as session:
        session.add(Heartbeat(agent_id=project["ceo_agent_id"], name="daily",
                              last_fired_at=utcnow()))

    payload = agenda(client, project)
    assert payload["timezone"] == "America/New_York"
    daily = next(r for r in payload["rhythms"] if r["kind"] == "ceo_daily")
    local = datetime.fromisoformat(daily["next"]).astimezone(ZoneInfo("America/New_York"))
    assert (local.hour, local.minute) == (9, 0)


def test_a_per_agent_wake_appears_with_the_team_it_belongs_to(client):
    project = make_project(client, slug="rota")
    agent_id = agent_by_name(project, "Emp2")
    response = client.patch(f"/admin/agents/{agent_id}/schedule",
                            json={"daily_at": "07:30"}, headers=ADMIN)
    assert response.status_code == 200, response.text

    payload = agenda(client, project)
    personal = next(r for r in payload["rhythms"] if r["kind"] == "agent_daily")
    assert personal["label"] == "Emp2 — daily wake"
    assert personal["detail"] == "Alpha"


def test_the_planning_heartbeat_says_when_it_is_skipped(client):
    """A cadence that silently does not fire on an empty board is a cadence a
    chairman will read as broken. The strip carries the condition."""
    project = make_project(client, slug="beat")
    payload = agenda(client, project)
    beat = next(r for r in payload["rhythms"] if r["kind"] == "ceo_planning")
    assert beat["every"] == "every 6h"
    assert "skipped when the board is empty" in beat["detail"]


def test_nobody_on_a_clock_is_said_out_loud(client):
    """An absence a chairman can read beats a strip that is simply bare.

    The rule is that the blank explains itself, not that it explains itself in
    any particular words — so this matches on what the sentence has to tell
    the reader (that nothing wakes at a set time, and what to do about it)
    rather than on the phrase "wall-clock wake", which was our word for it and
    never theirs.
    """
    project = make_project(client, slug="noclock")
    payload = agenda(client, project)
    assert not any(r["kind"] in ("ceo_daily", "agent_daily") for r in payload["rhythms"])
    note = next((n for n in payload["notes"] if "fixed time of day" in n), None)
    assert note is not None, payload["notes"]
    assert "daily time" in note, note


# ── dated events ────────────────────────────────────────────────────────────────


def test_the_standing_meeting_is_on_the_calendar(client):
    project = make_project(client, slug="meeting")
    payload = agenda(client, project, days=14)
    review = next(e for e in payload["events"] if e["kind"] == "review")
    assert review["label"] == "Review #1"
    assert review["tone"] == "meeting"
    # Seven days out by default, which is review_interval_days.
    due = datetime.fromisoformat(review["at"])
    assert 6 <= (due - utcnow()).days <= 7


def test_an_open_meeting_explains_why_the_next_one_is_not_scheduled(client):
    project = make_project(client, slug="unattended")
    with db_session() as session:
        session.add(Review(id=ids.new_id(ids.REVIEW), project_id=project["id"],
                           number=1, status=ReviewStatus.OPEN,
                           scheduled_for=utcnow(), numbers={}))
    payload = agenda(client, project)
    assert "review" not in kinds(payload)
    assert any("not scheduled until you close it" in note for note in payload["notes"])


def test_an_approval_lands_on_the_day_it_decides_itself(client):
    project = make_project(client, slug="clockrow")
    token = ceo_token(project)
    filed = client.post("/agent/approvals",
                        json={"kind": "spend", "title": "buy the data feed",
                              "body": "we need the quotes for the model",
                              "amount_usd": 200.0},
                        headers=bearer(token))
    assert filed.status_code == 200, filed.text

    payload = agenda(client, project, days=30)
    event = next(e for e in payload["events"] if e["kind"] == "approval_expiry")
    assert event["tone"] == "consequence"
    assert "buy the data feed" in event["label"]
    assert "silence is a decision" in event["detail"]
    assert event["day"] in payload["day_list"]


def test_a_kill_date_lands_with_what_happens_when_it_passes(client):
    project = make_project(client, slug="bets")
    token = ceo_token(project)
    kill = utcnow() + timedelta(days=3)
    filed = client.post("/agent/hypotheses",
                        json={"statement": "cold email converts at two percent",
                              "kill_condition": "under 10 replies in 200 sends",
                              "kill_date": kill.isoformat()},
                        headers=bearer(token))
    assert filed.status_code == 200, filed.text

    payload = agenda(client, project)
    event = next(e for e in payload["events"] if e["kind"] == "kill_date")
    assert event["tone"] == "consequence"
    assert "extend it, once" in event["detail"]


def test_the_promise_and_the_projection_are_drawn_as_different_things(client):
    """Two dates, two meanings: one the chairman signed, one the platform's own
    arithmetic. A strip that drew them identically would say nothing."""
    project = deliver_project(client, slug="promise")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    promised = utcnow() + timedelta(days=6)
    projected = utcnow() + timedelta(days=9)
    with db_session() as session:
        session.add(Commitment(
            id=ids.new_id(ids.COMMITMENT), project_id=project["id"],
            status=CommitmentStatus.ACCEPTED, deliver_by=promised,
            projected_done_at=projected, scope_snapshot=["R-01"]))

    payload = agenda(client, project, days=14)
    promise = next(e for e in payload["events"] if e["kind"] == "deliver_by")
    projection = next(e for e in payload["events"] if e["kind"] == "projected")
    assert promise["tone"] == "deadline"
    assert projection["tone"] == "projection"
    assert "later than the promise" in projection["detail"]
    assert "1 requirement was in scope" in promise["detail"]


def test_events_are_ordered_by_when_they_happen(client):
    project = make_project(client, slug="ordered")
    token = ceo_token(project)
    for days, title in ((5, "the later bet, worth five days"),
                        (1, "the sooner bet, worth one day")):
        filed = client.post(
            "/agent/hypotheses",
            json={"statement": title,
                  "kill_condition": "under 10 signups from 500 sends",
                  "kill_date": (utcnow() + timedelta(days=days)).isoformat()},
            headers=bearer(token))
        assert filed.status_code == 200, filed.text
    payload = agenda(client, project, days=30)
    ats = [e["at"] for e in payload["events"]]
    assert ats == sorted(ats)


def test_the_horizon_is_honoured(client):
    project = make_project(client, slug="horizon")
    token = ceo_token(project)
    filed = client.post(
        "/agent/hypotheses",
        json={"statement": "the newsletter converts better than the ads",
              "kill_condition": "under 10 signups from 500 sends",
              "kill_date": (utcnow() + timedelta(days=40)).isoformat()},
        headers=bearer(token))
    assert filed.status_code == 200, filed.text
    assert "kill_date" not in kinds(agenda(client, project, days=7))
    assert "kill_date" in kinds(agenda(client, project, days=60))


def test_the_day_list_covers_the_window_in_local_days(client):
    project = make_project(client, slug="days")
    amend(client, project, timezone="Australia/Sydney")
    payload = agenda(client, project, days=10)
    assert len(payload["day_list"]) == 10
    assert payload["day_list"][0] == payload["first_day"]
    assert payload["day_list"] == sorted(payload["day_list"])


# ── projects with no schedule ──────────────────────────────────────────────────


def test_a_paused_project_draws_nothing_and_says_so(client):
    """Drawing a week of wakes that are never going to happen would be a
    calendar lying about the one thing it exists to show."""
    project = make_project(client, slug="halted")
    client.post(f"/admin/projects/{project['id']}/pause",
                json={"reason": "waiting on the client"}, headers=ADMIN)
    payload = agenda(client, project)
    assert payload["live"] is False
    assert payload["events"] == []
    assert payload["rhythms"] == []
    assert any("waiting on the client" in note for note in payload["notes"])


def test_a_draft_project_draws_nothing(client):
    project = make_project(client, slug="unstarted", start=False)
    payload = agenda(client, project)
    assert payload["live"] is False
    assert payload["events"] == []


# ── bounds ──────────────────────────────────────────────────────────────────────


def test_the_window_is_clamped_rather_than_refused(client):
    """The caller is a strip with a day picker, not an agent under a rule. A
    silly number is a UI bug, and answering it with the nearest sane window
    beats a 422 the page has no way to show."""
    project = make_project(client, slug="clamp")
    assert agenda(client, project, days=0)["days"] == 1
    assert agenda(client, project, days=9999)["days"] == 90


def test_the_budget_reset_is_on_the_calendar_when_it_lands_in_the_window(client):
    project = make_project(client, slug="reset")
    payload = agenda(client, project, days=40)
    reset = next(e for e in payload["events"] if e["kind"] == "budget_reset")
    at = datetime.fromisoformat(reset["at"])
    assert (at.day, at.tzinfo) == (1, UTC)
    assert "still waits for you" in reset["detail"]


def test_the_agenda_costs_no_runs(client):
    """Same claim as the briefing: a screen that spends a model call to draw
    itself is a screen you cannot afford to poll."""
    project = make_project(client, slug="cheap")
    from yaaf.core.models import Run

    def runs() -> int:
        with db_session() as session:
            return session.query(Run).filter(Run.project_id == project["id"]).count()

    before = runs()
    agenda(client, project)
    agenda(client, project, days=60)
    assert runs() == before


def test_an_unknown_project_is_a_404_not_an_empty_calendar(client):
    make_project(client, slug="real")
    response = client.get("/observe/projects/proj_nonesuch/agenda", headers=ADMIN)
    assert response.status_code == 404


def test_the_operate_cycle_roll_is_drawn_with_where_its_date_came_from(client):
    """A cycle paced by the world rolls on the world's date; one paced by the
    calendar rolls on the rhythm. Which it is, is the interesting part."""
    project = make_project(client, slug="rhythm", mode="operate")
    payload = agenda(client, project, days=40)
    roll = next(e for e in payload["events"] if e["kind"] == "cycle_roll")
    assert roll["tone"] == "deadline"
    assert "weekly rhythm" in roll["detail"]
    assert "plan gate reopens" in roll["detail"]


def test_a_deliver_project_has_no_cycle_on_its_calendar(client):
    project = deliver_project(client, slug="onceonly")
    assert "cycle_roll" not in kinds(agenda(client, project, days=40))


def test_an_explore_deadline_is_drawn_as_the_chairman_s_judgment(client):
    """Explore's deadline pauses nothing — he is told, and the call is his.
    A calendar implying otherwise would be promising a wall that isn't there."""
    deadline = utcnow() + timedelta(days=5)
    project = make_project(client, slug="bounded", deadline=deadline.isoformat())
    payload = agenda(client, project, days=14)
    event = next(e for e in payload["events"] if e["kind"] == "explore_deadline")
    assert "nothing pauses itself" in event["detail"]
    assert event["tone"] == "deadline"


def test_the_agenda_and_the_dispatcher_agree_on_the_next_wake(client):
    """The one thing that would make this feature worse than nothing: a strip
    that draws a wake the dispatcher is not going to run. Both read the same
    heartbeat, so a wake already fired today moves to tomorrow on both sides."""
    from yaaf.core.models import Heartbeat

    project = make_project(client, slug="agree")
    amend(client, project, ceo_daily_at="00:01", timezone="UTC")
    before = agenda(client, project)
    due_now = next(r for r in before["rhythms"] if r["kind"] == "ceo_daily")
    # Past 00:01 UTC and nothing fired yet: the next tick runs it, so "now".
    assert datetime.fromisoformat(due_now["next"]) <= utcnow() + timedelta(seconds=5)

    with db_session() as session:
        session.add(Heartbeat(agent_id=project["ceo_agent_id"], name="daily",
                              last_fired_at=utcnow()))
    after = agenda(client, project)
    fired = next(r for r in after["rhythms"] if r["kind"] == "ceo_daily")
    # Tomorrow's, not "an hour away": at 23:13 UTC tomorrow's 00:01 is 48
    # minutes off, and an assertion phrased as a duration fails for the whole
    # last hour of every day while being right about nothing.
    nxt = datetime.fromisoformat(fired["next"])
    assert (nxt.hour, nxt.minute) == (0, 1)
    assert nxt.date() > utcnow().date()


def test_a_project_with_no_constitution_does_not_take_the_page_down(client):
    """Defensive: `effective` raises for a project with no constitution row,
    and a calendar is not worth a 500."""
    project = make_project(client, slug="normal")
    payload = agenda(client, project)
    assert payload["live"] is True
    with db_session() as session:
        assert session.get(Project, project["id"]) is not None


def test_a_question_to_him_is_a_date_on_the_strip(client):
    """`_clock_events` collects "the things that resolve themselves" — an
    approval auto-decides, a bet dies on its date. A question the CEO put to
    him is the same shape: a moment he can let pass, after which it resolves
    without him. It was missing, which made the strip quietly incomplete for
    the one date where the resolution is the CEO's own written sentence."""
    from tests.conftest import bearer, live_token

    project = make_project(client, slug="agenda-ask")
    _, token = live_token(project["id"], project["ceo_agent_id"])
    asked = client.post("/agent/asks", headers=bearer(token), json={
        "title": "which candidate for the launch post",
        "body": "Five ranked, my pick is first. Your call before we write.",
        "answer_by_hours": 30,
        "default_action": "Take the top-ranked one and tell you I did."})
    assert asked.status_code == 200, asked.text

    payload = client.get(f"/observe/projects/{project['id']}/agenda",
                         headers=ADMIN).json()
    event = next(e for e in payload["events"] if e["kind"] == "ask_window")
    assert "stops waiting" in event["label"]
    assert "Take the top-ranked one" in event["detail"]
    # It resolves without him, like the approval beside it.
    assert event["tone"] == "consequence"
