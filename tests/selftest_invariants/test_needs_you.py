"""Invariant selftests: "what needs you" is one number, and a finished delivery
is in it.

Two failures, found by running the product rather than reading it:

  * A Deliver project with every requirement true counts down to closing
    itself — Complete is one of the three moments the chairman chose to be
    asked at — and the Inbox, the badge, `yaaf status` and the briefing all
    said nothing was waiting. The only places it appeared were an email, the
    boardroom, and a banner repeated on every tab.
  * The count was re-added by hand in four places from different members.
    One project read "2 decisions waiting" in `yaaf status` and "Waiting on
    you: 1" in `yaaf briefing`, because the briefing never counted asks.

So `policy.needs_you` is the definition, the desk carries it, and every other
reader quotes it. These attack it over HTTP, the way a page or a CLI reads it.
"""

from __future__ import annotations

from tests.conftest import ADMIN, amend, make_project
from tests.selftest_invariants.test_asks import _ask, _project
from tests.selftest_invariants.test_delivered_stands_down import delivered
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import Project
from yaaf.server import briefing, dispatcher, janitor


def _row(client, project: dict) -> dict:
    desk = client.get("/observe/desk", params={"only": project["id"]}, headers=ADMIN).json()
    (row,) = desk["projects"]
    return row


def test_a_finished_delivery_is_in_the_queue_not_a_banner(client):
    project = delivered(client, "signoff-queue")
    row = _row(client, project)

    assert row["pending_signoff"] is not None
    assert row["pending_signoff"]["met"] == 1
    assert row["needs_you"]["signoff"] == 1
    assert row["needs_you"]["total"] >= 1
    # The sentence every surface quotes says so, instead of "Nothing open".
    assert row["status_line"]["verdict"] == "needs_you"
    assert row["status_line"]["headline"].startswith("Finished: sign it off")

    # And the banner it replaces is gone, so it is said once, in the queue.
    with db_session() as session:
        live = session.get(Project, project["id"])
        state = dispatcher.activity_state(session, live, effective(session, live.id))
    assert state["blocked_by"] == "delivery_ready"
    assert state["brief"]["banner"] is False
    health = client.get(f"/observe/projects/{project['id']}/health", headers=ADMIN).json()
    assert not [c for c in health["checks"] if c["key"] == "activity"]


def test_the_signoff_names_when_silence_completes_it(client):
    """A decision with a default says when the default lands. The clock starts
    at the announcement, which the janitor writes."""
    project = delivered(client, "signoff-clock")
    amend(client, project["id"], complete_after_hours=24)
    assert _row(client, project)["pending_signoff"]["closes_at"] is None  # not yet told
    with db_session() as session:
        janitor.check_delivery_ready(session, session.get(Project, project["id"]))
    signoff = _row(client, project)["pending_signoff"]
    assert signoff["closes_at"] is not None


def test_completing_takes_it_off_the_queue(client):
    project = delivered(client, "signoff-done")
    assert client.post(f"/admin/projects/{project['id']}/complete",
                       headers=ADMIN).status_code == 200
    row = _row(client, project)
    assert row["pending_signoff"] is None
    assert row["needs_you"]["signoff"] == 0


def test_the_briefing_counts_what_the_desk_counts(client):
    """The briefing re-added its own total and left out asks: one ask read as
    "Waiting on you: nothing" in the morning letter, beside a desk that said 1."""
    project = _project(client, slug="brief-counts-asks")
    assert _ask(client, project).status_code == 200
    row = _row(client, project)
    assert row["needs_you"]["asks"] == 1
    with db_session() as session:
        live = session.get(Project, project["id"])
        waiting = briefing._waiting(session, live, effective(session, live.id))
    assert waiting["total"] == row["needs_you"]["total"] == 1
    assert waiting["asks"] == 1


def test_the_status_line_counts_the_same_total(client):
    # Mock runtime, so nothing has to pay: an unpaid project's line is
    # "Cannot run", which outranks the count on purpose.
    project = make_project(client, slug="line-counts-asks",
                           integrations={"runtime": "mock"})
    assert _ask(client, project).status_code == 200
    assert _ask(client, project, title="And which launch date do you want?").status_code == 200
    row = _row(client, project)
    assert row["needs_you"]["total"] == 2
    assert row["status_line"]["headline"] == "2 decisions waiting on you."


# ── the row a portfolio draws ───────────────────────────────────────────────────


def test_a_row_carries_its_outcomes_not_its_tasks(client):
    """Progress is drawn as outcomes. A row that counted closed tasks drew a
    busy project as a progressing one; the path is one entry per requirement
    in scope, in stage order, with its status, and a proposed one is an item
    the scope decision can be taken on where it is drawn."""
    from tests.selftest_invariants.test_requirements import (
        accept,
        ceo_token,
        deliver_project,
        propose,
    )

    project = deliver_project(client, slug="path-row")
    token = ceo_token(project)
    first = propose(client, token, statement="The archive exists, every filing saved")
    accept(client, first["requirement_id"], project)
    propose(client, token, statement="The comparison table exists, one baseline")

    row = _row(client, project)
    keys = [n["key"] for n in row["path"]]
    assert len(keys) == 2 and keys == sorted(keys)
    assert {n["status"] for n in row["path"]} >= {"proposed"}
    assert all(n["kind"] == "requirement" for n in row["path"])
    (item,) = row["scope_items"]
    assert item["statement"] == "The comparison table exists, one baseline"
    assert row["needs_you"]["plan"] == 1


def test_a_milestone_is_a_date_the_platform_holds_or_none(client):
    """The row's next milestone is never invented: with no commitment, no bet
    and no window there is none, and the row says so rather than guessing."""
    project = make_project(client, slug="no-milestone", integrations={"runtime": "mock"})
    assert _row(client, project)["next_milestone"] is None


def test_a_reopened_delivery_promises_no_close_it_will_not_make(client):
    """A reopen switches auto-completion off until scope moves
    (janitor._complete_itself), so the sign-off row may not name a close time:
    it would be a date that passes with the project still open."""
    project = delivered(client, "signoff-reopened")
    amend(client, project["id"], complete_after_hours=24)
    with db_session() as session:
        janitor.check_delivery_ready(session, session.get(Project, project["id"]))
    assert _row(client, project)["pending_signoff"]["closes_at"] is not None
    assert client.post(f"/admin/projects/{project['id']}/complete",
                       headers=ADMIN).status_code == 200
    assert client.post(f"/admin/projects/{project['id']}/reopen",
                       headers=ADMIN, json={}).status_code == 200
    row = _row(client, project)
    assert row["pending_signoff"] is not None
    assert row["pending_signoff"]["closes_at"] is None
    assert "closes itself" not in row["status_line"]["headline"]


def test_the_letter_lists_everything_it_counts():
    """The briefing's total includes expired bets and frozen threads, so the
    list under it has to name them — a count and a list that disagree is what
    this module set out to remove."""
    waiting = {"total": 2, "approvals": 0, "soonest_expiry": None, "plan": 0,
               "verdicts": 0, "commitment": False, "flags_raised": 0, "open_reviews": 0,
               "heads_ups": 0, "to_read": 0, "asks": 0, "signoff": None,
               "bets": 1, "frozen": 1}
    lines = briefing._waiting_lines(waiting, lambda iso: iso)
    assert lines[0] == "Waiting on you: 2"
    assert any("bet" in line for line in lines[1:])
    assert any("frozen" in line for line in lines[1:])


def test_a_rule_label_keeps_capitals_only_for_an_acronym():
    from yaaf.core.constitution import rule_label

    assert rule_label("queue_ceiling")[0].islower()
    assert not rule_label("unknown_key_here")[0].isupper()
    # "A daily wake…" is not an acronym; mid-sentence it reads lower-case.
    assert rule_label("ceo_daily_at") == "a daily wake at this time"
    assert rule_label("brief_daily_at") == "a daily briefing at this time"
