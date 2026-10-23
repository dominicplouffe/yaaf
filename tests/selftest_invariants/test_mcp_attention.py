"""Invariant selftests: two questions a chairman asks Claude, answerable in one
call each.

"What needs me?" took a `desk` call plus `flags` and `heads_ups` per project,
because the desk rows carry approvals and questions but not the flags the CEO
raised, the heads-ups he asked for, or an open review. "What changed since
yesterday?" could not be asked at all: the briefing view took no window, only
the read mark, though the CLI has had `--hours` all along.
"""

from __future__ import annotations

from tests.conftest import make_project
from tests.selftest_invariants.test_inbox_tiers import heads_up, with_order
from tests.selftest_invariants.test_mcp_auth import mint
from tests.selftest_invariants.test_mcp_untrusted import call, payload
from yaaf.server.mcp.envelope import OPEN

SLUG = "acme"


def test_one_desk_call_carries_the_heads_up_waiting_on_him(client):
    project = make_project(client, slug=SLUG)
    with_order(project)
    filed = heads_up(client, project)
    assert filed.status_code == 200, filed.text
    reader = mint(client)

    desk = payload(call(client, reader["token"], "desk", {}))
    mine = [row for row in desk["also_yours"] if row["kind"] == "heads_up"]
    assert len(mine) == 1
    assert mine[0]["project"] == SLUG
    assert mine[0]["answer_with"] == "heads_up_ack"
    # The CEO wrote the title, so it arrives as data, never as an instruction.
    assert mine[0]["what"].startswith(OPEN)
    assert "No trade tomorrow" in mine[0]["what"]


def test_a_quiet_desk_says_nothing_is_also_waiting(client):
    make_project(client, slug=SLUG)
    reader = mint(client)
    assert payload(call(client, reader["token"], "desk", {}))["also_yours"] == []


def test_the_briefing_can_be_asked_about_the_last_day(client):
    make_project(client, slug=SLUG)
    reader = mint(client)
    brief = payload(call(client, reader["token"], "project",
                         {"project": SLUG, "view": "briefing", "hours": 24}))
    assert brief["window_source"] == "the window you asked for"
    assert 23.9 <= brief["window_hours"] <= 24.1


def test_or_since_a_date(client):
    make_project(client, slug=SLUG)
    reader = mint(client)
    brief = payload(call(client, reader["token"], "project",
                         {"project": SLUG, "view": "briefing", "since": "2026-01-01"}))
    assert brief["since"].startswith("2026-01-01")


def test_the_desk_leads_with_a_sentence_naming_what_is_not_fine(client):
    """Read before the rows, and written before the trim: a long desk loses
    rows to the context budget, and the one that needed him must survive."""
    project = make_project(client, slug=SLUG)
    with_order(project)
    heads_up(client, project)
    make_project(client, slug="calm", integrations={"runtime": "mock"})
    reader = mint(client)

    desk = payload(call(client, reader["token"], "desk", {}))
    assert list(desk)[0] == "summary"
    summary = desk["summary"]
    assert summary.startswith("2 projects.")
    # The suite's default project has nothing to pay with: that is news.
    assert "Cannot run: nothing is set to pay for it." in summary
    assert "1 other is fine." in summary
    assert summary.endswith("Also yours: 1 heads-up.")
    # Platform words only; the CEO's title stays in the rows, wrapped.
    assert "No trade tomorrow" not in summary
