"""Invariant selftests: the desk shows live companies, and only those.

The desk answers one question — what breaks if I close this tab? A company
that is over cannot break, so it has no business being in the answer. It also
has no business costing anything: every aggregate the rollup computes is
frozen forever on a terminal project, and the page polls every ten seconds.
"""

from __future__ import annotations

from tests.conftest import ADMIN, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Project, ProjectStatus


def _desk(client, live: bool):
    url = "/observe/desk?live=true" if live else "/observe/desk"
    response = client.get(url, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_live_desk_drops_companies_that_are_over(client):
    live_project = make_project(client, slug="still-going")
    done = make_project(client, slug="all-done")
    client.post(f"/admin/projects/{done['id']}/stop",
                json={"reason": "goal met"}, headers=ADMIN)

    desk = _desk(client, live=True)
    assert [p["slug"] for p in desk["projects"]] == ["still-going"]
    assert desk["finished"] == 1

    # The archive is the Projects page's job, and it still gets everything.
    everything = _desk(client, live=False)
    assert {p["slug"] for p in everything["projects"]} == {"still-going", "all-done"}
    # Counted either way: the desk needs the number even when it is not
    # showing the rows, or "no live companies" reads as "no companies".
    assert everything["finished"] == 1
    assert live_project["id"] in {p["id"] for p in everything["projects"]}


def test_a_completed_company_leaves_the_desk_too(client):
    """Both terminal statuses, not just the one the stop button writes. A
    delivered Deliver company is the *success* end state and the most likely
    thing to accumulate, so leaking it here would fill the desk with exactly
    the companies that went right."""
    project = make_project(client, slug="delivered")
    with db_session() as session:
        session.get(Project, project["id"]).status = ProjectStatus.COMPLETED

    desk = _desk(client, live=True)
    assert desk["projects"] == []
    assert desk["finished"] == 1


def test_a_paused_company_stays_on_the_desk(client):
    """Paused is not terminal and is the whole reason the desk exists: the
    company has stopped and only the chairman restarts it."""
    project = make_project(client, slug="halted")
    client.post(f"/admin/projects/{project['id']}/pause",
                json={"reason": "rethinking the goal"}, headers=ADMIN)

    desk = _desk(client, live=True)
    assert [p["status"] for p in desk["projects"]] == ["paused"]
    assert desk["finished"] == 0


def test_the_desk_reports_yesterday_as_a_pair(client):
    """Spend alone is a vanity number and evidence alone hides what it cost.
    The desk ranks on both together — money moved, nothing proved — so the two
    fields travel with every project or the signal cannot be computed."""
    make_project(client, slug="acme")
    desk = _desk(client, live=True)
    row = desk["projects"][0]
    assert row["spend_24h"] == 0.0
    assert row["evidence_24h"] == 0
