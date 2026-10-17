"""The boardroom's unread count: what each reader has not yet seen.

Unread is every boardroom message after the reader's mark that somebody else
wrote. The mark moves only on purpose — the chat page saying how far it showed,
or the reader speaking — never because a screen polled. Per member, like the
briefing's mark: a co-chair reading the room says nothing about the chairman.
"""

from __future__ import annotations

from sqlalchemy import select

from tests.conftest import ADMIN, bearer, channel_of, headers_of, live_token, make_project
from tests.selftest_invariants.test_auth_roles import seat
from tests.selftest_invariants.test_slack import _event_body, _post_event, _slack_project
from yaaf.core.db import db_session
from yaaf.core.models import Message


def _unread(client, project: dict, headers=ADMIN) -> int | None:
    dash = client.get(f"/observe/projects/{project['id']}", headers=headers)
    assert dash.status_code == 200, dash.text
    return dash.json()["boardroom_unread"]


def _ceo_says(client, project: dict, body: str) -> None:
    _, token = live_token(project["id"], project["ceo_agent_id"])
    posted = client.post("/agent/messages", headers=bearer(token), json={
        "channel_id": channel_of(client, project["id"], "boardroom"), "body": body})
    assert posted.status_code == 200, posted.text


def _seen(client, project: dict, until: str | None = None, headers=ADMIN):
    response = client.post(f"/admin/projects/{project['id']}/boardroom/seen",
                           json={"until": until} if until else {}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _newest_at(project: dict) -> str:
    with db_session() as session:
        return session.execute(
            select(Message.created_at).where(Message.project_id == project["id"])
            .order_by(Message.created_at.desc()).limit(1)
        ).scalar_one().isoformat()


def test_what_the_ceo_says_is_unread_until_the_page_shows_it(client):
    project = make_project(client)
    _seen(client, project)
    assert _unread(client, project) == 0

    _ceo_says(client, project, "The memo is drafted.")
    _ceo_says(client, project, "And filed.")
    assert _unread(client, project) == 2
    assert _unread(client, project) == 2, "looking at the count must not clear it"

    assert _seen(client, project)["boardroom_unread"] == 0
    assert _unread(client, project) == 0


def test_a_message_that_lands_after_the_page_rendered_stays_unread(client):
    """The page reports the newest message it showed, not "now", so a line
    that arrived in between is not swallowed."""
    project = make_project(client)
    _seen(client, project)
    _ceo_says(client, project, "First.")
    shown = _newest_at(project)
    _ceo_says(client, project, "Second, while the page was rendering.")

    _seen(client, project, until=shown)
    assert _unread(client, project) == 1


def test_the_mark_never_moves_backwards(client):
    """A stale tab reporting an old position must not resurrect what was read."""
    project = make_project(client)
    _ceo_says(client, project, "Old news.")
    old = _newest_at(project)
    _seen(client, project)
    _seen(client, project, until=old)
    assert _unread(client, project) == 0


def test_your_own_words_are_never_unread_and_speaking_reads_the_room(client):
    project = make_project(client)
    _seen(client, project)
    _ceo_says(client, project, "Want the long version?")
    assert _unread(client, project) == 1

    posted = client.post(f"/admin/projects/{project['id']}/boardroom",
                         json={"body": "Short, please."}, headers=ADMIN)
    assert posted.status_code == 200, posted.text
    assert _unread(client, project) == 0


def test_each_member_has_their_own_count(client):
    project = make_project(client)
    co = headers_of(seat(client, project, "co@example.com", "co_chair"))
    _seen(client, project)
    _seen(client, project, headers=co)
    _ceo_says(client, project, "Update.")

    _seen(client, project, headers=co)
    assert _unread(client, project, headers=co) == 0
    assert _unread(client, project) == 1, "a co-chair reading must not clear the chair's"


def test_a_platform_line_counts(client):
    """The deliverable line, a need, a finished project — news for the reader."""
    import base64

    project = make_project(client)
    _seen(client, project)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    published = client.post("/agent/artifacts", headers=bearer(token), json={
        "filename": "memo.md", "content_base64": base64.b64encode(b"# memo").decode()})
    assert published.status_code == 200, published.text
    assert _unread(client, project) == 1


def test_the_desk_row_carries_the_count(client):
    """So the Projects page can say which boardroom has news."""
    project = make_project(client)
    _seen(client, project)
    _ceo_says(client, project, "News.")
    desk = client.get("/observe/desk", headers=ADMIN)
    assert desk.status_code == 200, desk.text
    row = next(p for p in desk.json()["projects"] if p["id"] == project["id"])
    assert row["boardroom_unread"] == 1


def test_replying_in_slack_reads_the_room(client):
    """Slack never says what he has read, only what he has said. Saying
    something in the Slack boardroom is reading everything before it."""
    project = _slack_project(client)
    _seen(client, project)
    _ceo_says(client, project, "Did you see the memo?")
    assert _unread(client, project) == 1

    answered = _post_event(client, project["id"], _event_body(text="Yes."))
    assert answered.status_code == 200, answered.text
    assert _unread(client, project) == 0
