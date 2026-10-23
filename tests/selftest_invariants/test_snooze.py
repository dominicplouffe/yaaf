"""Invariant selftests: snooze is personal, and never outlives a clock.

"Not now" on an Inbox row hides it from that person's Needs-you list and badge
until a time they choose. Attacked here: that it is theirs alone (a co-chair's
snooze hides nothing from the chairman), that it cannot run past the moment an
approval or a question decides itself, that it cannot be set on a project the
person cannot open, and that waking it brings it straight back.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, bearer, headers_of, live_token, make_project, person
from yaaf.core.db import db_session
from yaaf.core.models import Approval, utcnow


def _approval(client, project: dict) -> str:
    _, token = live_token(project["id"], project["ceo_agent_id"])
    filed = client.post("/agent/approvals", headers=bearer(token), json={
        "kind": "spend", "title": "Buy the $49 injury-news feed",
        "body": "Team news reaches us a day late.", "if_declined": "We react a day late.",
        "amount_usd": 49})
    assert filed.status_code == 200, filed.text
    return filed.json()["id"]


def _snooze(client, project: dict, key: str, hours: float, headers=ADMIN):
    return client.post("/me/snoozes", headers=headers, json={
        "project_id": project["id"], "item_key": key,
        "until": (utcnow() + timedelta(hours=hours)).isoformat()})


def _snoozed(client, headers=ADMIN) -> set[str]:
    return {s["item_key"] for s in client.get("/observe/desk", headers=headers).json()["snoozed"]}


def test_a_snooze_is_yours_alone(client):
    project = make_project(client)
    key = _approval(client, project)
    co = person("co@example.com")
    added = client.post(f"/admin/projects/{project['id']}/members",
                        json={"email": "co@example.com", "role": "co_chair"}, headers=ADMIN)
    assert added.status_code == 200, added.text

    assert _snooze(client, project, key, 3, headers=headers_of(co)).status_code == 200
    assert key in _snoozed(client, headers_of(co))
    assert key not in _snoozed(client), "the chairman's Inbox is untouched"


def test_it_never_runs_past_the_moment_the_decision_makes_itself(client):
    project = make_project(client)
    key = _approval(client, project)  # expires in the default 7 days
    too_late = _snooze(client, project, key, 24 * 7)
    assert too_late.status_code == 422
    assert "must end by" in too_late.json()["detail"]
    assert _snooze(client, project, key, 24).status_code == 200

    with db_session() as session:
        session.get(Approval, key).expires_at = utcnow() + timedelta(minutes=90)
    refused = _snooze(client, project, key, 0.5)
    assert refused.status_code == 409
    assert "too soon to snooze" in refused.json()["detail"]


def test_a_row_with_no_clock_still_wakes_within_a_week(client):
    project = make_project(client)
    key = f"{project['id']}-scope"
    assert _snooze(client, project, key, 24 * 8).status_code == 422
    assert _snooze(client, project, key, 24 * 6).status_code == 200
    assert _snooze(client, project, key, -1).status_code == 422


def test_not_on_a_project_you_cannot_open(client):
    project = make_project(client)
    stranger = person("stranger@example.com")
    refused = _snooze(client, project, f"{project['id']}-scope", 3,
                      headers=headers_of(stranger))
    assert refused.status_code == 404


def test_waking_brings_it_back_and_an_ended_snooze_is_gone(client):
    project = make_project(client)
    key = _approval(client, project)
    _snooze(client, project, key, 3)
    assert key in _snoozed(client)
    assert client.delete(f"/me/snoozes/{key}", headers=ADMIN).status_code == 200
    assert key not in _snoozed(client)

    _snooze(client, project, key, 3)
    from yaaf.core.models import Snooze

    with db_session() as session:
        session.query(Snooze).filter_by(item_key=key).one().until = utcnow() - timedelta(minutes=1)
    assert key not in _snoozed(client), "an ended snooze hides nothing"
