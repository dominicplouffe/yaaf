"""Invariant selftests: pins are a person's own, and the setup checklist reads
the project rather than remembering it.

Pins put the places a person keeps returning to one click away. Attacked here:
that a pin can only ever name a place inside a project (never another site),
that one person's pins change nobody else's sidebar, that a pin to a project
the person cannot open is refused, and that the default reaches everyone who
never touched theirs.

The checklist exists only while a new project is being set up. Attacked here:
that a step is done because the project has the thing, not because somebody
ticked it; that the one step a project cannot run without cannot be skipped
away; that an observer cannot put the list away for the chairman; and that a
project which was already running when this shipped never shows it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from tests.conftest import ADMIN, headers_of, make_project, person
from yaaf.core.db import db_session
from yaaf.core.models import Project

REPO = Path(__file__).resolve().parents[2]


def _pins(client, headers=ADMIN) -> list[dict]:
    response = client.get("/me/pins", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["pins"]


def test_deliverables_is_pinned_until_you_change_it(client):
    body = client.get("/me/pins", headers=ADMIN).json()
    assert body["default"] is True
    assert [p["to"] for p in body["pins"]] == ["details/deliverables"]

    cleared = client.put("/me/pins", headers=ADMIN, json={"pins": []})
    assert cleared.status_code == 200
    assert cleared.json() == {"pins": [], "default": False, "max": 5}, \
        "an empty list is a choice, not a reason to bring the default back"


def test_a_pin_can_only_name_a_place_inside_a_project(client):
    for target in ("https://evil.example/x", "//evil.example", "/admin", "javascript:alert(1)",
                   "Details/deliverables", "settings?s=<script>", "../../auth/logout"):
        refused = client.put("/me/pins", headers=ADMIN,
                             json={"pins": [{"to": target, "label": "x"}]})
        assert refused.status_code == 422, target
    kept = client.put("/me/pins", headers=ADMIN, json={"pins": [
        {"to": "settings?s=secrets", "label": "Secrets & variables"},
        {"to": "details/activity/runs", "label": "Runs"}]})
    assert kept.status_code == 200, kept.text
    assert [p["to"] for p in kept.json()["pins"]] == ["settings?s=secrets", "details/activity/runs"]


def test_five_pins_at_most(client):
    six = [{"to": f"details/{t}", "label": t} for t in
           ("roadmap", "tasks", "deliverables", "spend", "reviews", "activity")]
    assert client.put("/me/pins", headers=ADMIN, json={"pins": six}).status_code == 422
    assert client.put("/me/pins", headers=ADMIN, json={"pins": six[:5]}).status_code == 200


def test_your_pins_are_yours_alone(client):
    project = make_project(client)
    other = person("other@example.com")
    file_pin = {"to": "details/deliverables?open=notes%2Fa.md", "label": "a.md",
                "project_id": project["id"]}
    refused = client.put("/me/pins", headers=headers_of(other), json={"pins": [file_pin]})
    assert refused.status_code == 404, "a pin into a project you cannot open"

    assert client.put("/me/pins", headers=ADMIN, json={"pins": [file_pin]}).status_code == 200
    assert _pins(client) == [file_pin]
    assert [p["to"] for p in _pins(client, headers_of(other))] == ["details/deliverables"]


def _setup(client, project_id: str, headers=ADMIN) -> dict:
    response = client.get(f"/observe/projects/{project_id}/setup", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _act(client, project_id: str, action: str, step: str | None = None, headers=ADMIN):
    return client.post(f"/admin/projects/{project_id}/setup", headers=headers,
                       json={"action": action, "step": step})


def test_a_step_is_done_when_the_project_has_it(client):
    project = make_project(client, start=False)
    view = _setup(client, project["id"])
    assert view["open"] is True
    states = {s["key"]: s["state"] for s in view["steps"]}
    assert states["people"] == "todo"

    person("co@example.com")
    added = client.post(f"/admin/projects/{project['id']}/members", headers=ADMIN,
                        json={"email": "co@example.com", "role": "co_chair"})
    assert added.status_code == 200, added.text
    states = {s["key"]: s["state"] for s in _setup(client, project["id"])["steps"]}
    assert states["people"] == "done", "nobody ticked it; the project has a second person"


def test_the_key_cannot_be_skipped_while_it_is_missing(client):
    project = make_project(client, start=False)
    with db_session() as session:
        row = session.get(Project, project["id"])
        row.billing_secret_id = None
        row.integrations = {**(row.integrations or {}), "runtime": "agent_sdk"}
    key = next(s for s in _setup(client, project["id"])["steps"] if s["key"] == "key")
    assert key["state"] == "todo" and key["required"] is True

    refused = _act(client, project["id"], "skip", "key")
    assert refused.status_code == 422
    assert "cannot be skipped" in refused.json()["detail"]


def test_skipping_every_optional_step_settles_the_list(client):
    project = make_project(client, start=False)
    view = _setup(client, project["id"])
    for step in view["steps"]:
        if step["state"] == "todo" and not step["required"]:
            assert _act(client, project["id"], "skip", step["key"]).status_code == 200
    view = _setup(client, project["id"])
    todo = [s["key"] for s in view["steps"] if s["state"] == "todo"]
    assert view["open"] is bool(todo)
    assert view["settled"] == view["total"] - len(todo)

    back = _act(client, project["id"], "unskip", "people").json()
    assert next(s for s in back["steps"] if s["key"] == "people")["state"] == "todo"
    assert back["next"]["key"] == "people" or back["next"]["key"] in todo


def test_closing_it_is_steering_not_reading(client):
    project = make_project(client, start=False)
    observer = person("watch@example.com")
    added = client.post(f"/admin/projects/{project['id']}/members", headers=ADMIN,
                        json={"email": "watch@example.com", "role": "observer"})
    assert added.status_code == 200, added.text
    assert _act(client, project["id"], "close", headers=headers_of(observer)).status_code == 403
    assert _setup(client, project["id"], headers_of(observer))["open"] is True

    assert _act(client, project["id"], "close").status_code == 200
    assert _setup(client, project["id"])["open"] is False
    assert _act(client, project["id"], "reopen").status_code == 200
    assert _setup(client, project["id"])["open"] is True


def test_a_project_already_running_never_shows_the_list(client):
    running = make_project(client, slug="running")
    draft = make_project(client, slug="draft", start=False)
    spec = importlib.util.spec_from_file_location(
        "m0056", REPO / "migrations" / "versions" / "0056_shortcuts.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    from yaaf.core.db import engine
    with engine().begin() as connection:
        migration._close_started(connection)
    assert _setup(client, running["id"])["open"] is False
    assert _setup(client, draft["id"])["closed_at"] is None, \
        "a project still being set up keeps its checklist"
