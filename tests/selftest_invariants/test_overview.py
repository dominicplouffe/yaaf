"""Invariant selftests: the overview is its sections' own endpoints, read once.

The project screens used to draw from seventeen requests on different clocks,
so the sidebar and the cards could describe different moments. The overview is
one request, read in one pass — and it is only worth trusting if every section in it
is exactly what that section's own endpoint says, because the CLI and the MCP
tools still read those endpoints one at a time. A section that drifted from its
route would be a second answer to the same question, on the one screen built to
stop that happening.
"""

from __future__ import annotations

import json

from tests.conftest import (
    ADMIN,
    agent_by_name,
    bearer,
    headers_of,
    live_token,
    make_project,
    person,
)
from tests.selftest_invariants.test_scope_verbs import add_requirement
from yaaf.core.models import ProjectMode
from yaaf.server.api import observe

#: Each section, and the request whose answer it must be.
SECTIONS = {
    "dashboard": "",
    "plan": "/plan",
    "approvals": "/approvals",
    "asks": "/asks",
    "hypotheses": "/hypotheses",
    "org": "/org",
    "health": "/health",
    "board": "/board",
    "runs": "/runs?limit=200",
    "today": "/activity?since=today",
    "reviews": "/reviews",
    "agenda": "/agenda?days=14",
    "flags": "/flags",
    "spend_by_team": "/spend?group_by=team&window=month",
}

#: The sections that stamp the instant they were read, and where. Two requests
#: cannot share an instant, so these are the only values allowed to differ.
CLOCKED = {"today": ("until",), "agenda": ("as_of", "horizon")}


def _at_the_same_instant(name: str, mine: dict, theirs: dict) -> dict:
    """The endpoint's answer, re-read at the overview's instant. A replace over
    the whole document rather than the named keys, because the agenda also
    writes its own `now` into any rhythm that is due this minute."""
    text = json.dumps(theirs)
    for key in CLOCKED.get(name, ()):
        text = text.replace(theirs[key], mine[key])
    return json.loads(text)


def _busy_project(client) -> dict:
    """A project with something in every section that can hold something —
    Deliver, so the plan section is a plan and not the planless placeholder."""
    project = make_project(client, mode=ProjectMode.DELIVER)
    pid = project["id"]
    add_requirement(client, project)
    _, token = live_token(pid, agent_by_name(project, "Lead"))
    filed = client.post("/agent/approvals", headers=bearer(token),
                        json={"kind": "spend", "title": "Buy a data pull",
                              "body": "Two months of category data.", "amount_usd": 40})
    assert filed.status_code == 200, filed.text
    _, ceo = live_token(pid, project["ceo_agent_id"])
    asked = client.post("/agent/asks", headers=bearer(ceo), json={
        "title": "Which of the five candidates do you want?",
        "body": "Five researched to equal depth, ranked. My pick is #1.",
        "answer_by_hours": 6,
        "default_action": "Take the top-ranked candidate and tell you I did.",
    })
    assert asked.status_code == 200, asked.text
    said = client.post(f"/admin/projects/{pid}/boardroom", headers=ADMIN,
                       json={"body": "Start with the cheapest data you can find."})
    assert said.status_code == 200, said.text
    return project


def _overview(client, pid: str, headers: dict = ADMIN):
    return client.get(f"/observe/projects/{pid}/overview", headers=headers)


def test_every_section_is_its_own_endpoints_answer(client):
    project = _busy_project(client)
    pid = project["id"]
    response = _overview(client, pid)
    assert response.status_code == 200, response.text
    whole = response.json()

    # Nothing extra and nothing missing: a section added here without a route
    # behind it is a reader the CLI and the MCP tools cannot see.
    assert set(whole) == set(SECTIONS) | {"boardroom"}

    for name, path in SECTIONS.items():
        alone = client.get(f"/observe/projects/{pid}{path}", headers=ADMIN)
        assert alone.status_code == 200, (name, alone.text)
        assert whole[name] == _at_the_same_instant(name, whole[name], alone.json()), (
            f"{name} differs from GET …{path}")

    room = client.get(f"/observe/projects/{pid}/channels", headers=ADMIN).json()
    boardroom = next(c["id"] for c in room if c["kind"] == "boardroom")
    tail = client.get(f"/observe/channels/{boardroom}/messages?limit=6", headers=ADMIN)
    assert whole["boardroom"] == tail.json()
    assert whole["boardroom"][-1]["body"] == "Start with the cheapest data you can find."
    assert [a["title"] for a in whole["approvals"]] == ["Buy a data pull"]
    assert [r["statement"] for r in whole["plan"]["requirements"]] == [
        "A thing is true at the end"]
    assert len(whole["asks"]) == 1


def test_the_costliest_read_is_taken_once(client, monkeypatch):
    """Why work is or is not starting is the dispatcher's whole ladder, read
    without acting on it — the most expensive thing on the page — and both the
    dashboard and the health checks want it. One request, one read of it."""
    project = make_project(client)
    calls = []
    real = observe.activity_state

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(observe, "activity_state", counted)
    response = _overview(client, project["id"])
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    # And it is the same read in both places, not merely one fewer.
    whole = response.json()
    alone = client.get(f"/observe/projects/{project['id']}/health", headers=ADMIN).json()
    assert whole["health"] == alone


def test_an_observer_reads_it_and_a_stranger_cannot_tell_it_exists(client):
    project = make_project(client)
    pid = project["id"]
    watcher = person("obs@example.com")
    seated = client.post(f"/admin/projects/{pid}/members",
                         json={"email": "obs@example.com", "role": "observer"}, headers=ADMIN)
    assert seated.status_code == 200, seated.text
    assert _overview(client, pid, headers_of(watcher)).status_code == 200

    stranger = headers_of(person("stranger@example.com"))
    theirs = _overview(client, pid, stranger)
    missing = _overview(client, "proj_doesnotexist", stranger)
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json(), "the same words for both, or it is an oracle"


def test_it_answers_for_every_shape_and_state_a_project_can_be_in(client):
    """Sixteen readers in one request means one that fails in some state takes
    the whole project screen with it, where before it blanked a single card. So
    each shape, and each state a project spends real time in, must answer."""
    explore = make_project(client, slug="exploring")
    operate = make_project(client, slug="operating", mode=ProjectMode.OPERATE)
    deliver = make_project(client, slug="delivering", mode=ProjectMode.DELIVER)
    draft = make_project(client, slug="drafted", start=False)
    paused = make_project(client, slug="paused")
    assert client.post(f"/admin/projects/{paused['id']}/pause",
                       json={"reason": "a breather"}, headers=ADMIN).status_code == 200
    stopped = make_project(client, slug="stopped")
    assert client.post(f"/admin/projects/{stopped['id']}/stop",
                       json={"reason": "goal met"}, headers=ADMIN).status_code == 200

    for project in (explore, operate, deliver, draft, paused, stopped):
        response = _overview(client, project["id"])
        assert response.status_code == 200, (project["slug"], response.text)
        assert response.json()["dashboard"]["id"] == project["id"]
    assert _overview(client, explore["id"]).json()["plan"]["planless"] is True
