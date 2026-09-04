"""Invariant selftest: reading a long conversation backwards loses nothing.

The boardroom is the chairman's only speaking channel, so it is the page he
leaves open — and it used to hand the browser the last two hundred messages and
re-read all of them every two seconds. The fix is to poll a short tail and page
backwards from it as he scrolls, which puts the whole weight of the feature on
one promise: **the pages, walked from the newest to the oldest, must reconstruct
the conversation exactly once.** A cursor that drops a message loses it from the
chat forever; one that repeats a message prints it twice at the seam.

That promise is why the cursor is a message id rather than a timestamp.
`created_at` is not unique here — a chairman line and a CEO reply can share an
instant, and a seeded or imported batch certainly can — and a timestamp cursor
then has to choose between `<`, which silently drops the tied sibling, and
`<=`, which returns the boundary row forever and never advances through a tie
group larger than a page. These tests attack both halves of that, and the tie
test is the one that would not exist if the decision had gone the other way.
"""

from __future__ import annotations

from tests.conftest import ADMIN, channel_of, make_project
from yaaf.core.db import db_session
from yaaf.core.ids import MESSAGE, new_id
from yaaf.core.models import Message, utcnow


def _say(client, project: dict, body: str) -> None:
    posted = client.post(
        f"/admin/projects/{project['id']}/boardroom", json={"body": body}, headers=ADMIN,
    )
    assert posted.status_code == 200, posted.text


def _page(client, channel: str, limit: int, before: str | None = None) -> list[dict]:
    url = f"/observe/channels/{channel}/messages?limit={limit}"
    if before is not None:
        url += f"&before={before}"
    response = client.get(url, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def _walk(client, channel: str, limit: int) -> list[dict]:
    """Every page, newest to oldest, concatenated back into reading order —
    exactly what the chat does as the reader scrolls up."""
    seen: list[dict] = []
    page = _page(client, channel, limit)
    while page:
        seen = page + seen
        if len(page) < limit:
            break
        page = _page(client, channel, limit, before=page[0]["id"])
    return seen


def test_the_tail_is_the_newest_and_reads_forwards(client):
    """What the page opens on: the last N, oldest first."""
    project = make_project(client)
    channel = channel_of(client, project["id"], "boardroom")
    for i in range(40):
        _say(client, project, f"line {i}")

    tail = _page(client, channel, 10)
    assert len(tail) == 10
    assert [m["body"] for m in tail] == [f"line {i}" for i in range(30, 40)], (
        "the tail must be the newest ten, in reading order"
    )
    assert [m["at"] for m in tail] == sorted(m["at"] for m in tail)


def test_paging_backwards_covers_every_message_exactly_once(client):
    """The promise the whole feature rests on."""
    project = make_project(client)
    channel = channel_of(client, project["id"], "boardroom")
    for i in range(40):
        _say(client, project, f"line {i}")

    walked = _walk(client, channel, 7)  # a page size that divides nothing evenly
    ids = [m["id"] for m in walked]
    assert len(ids) == len(set(ids)), "a message appeared on two pages"
    everything = _page(client, channel, 500)
    assert ids == [m["id"] for m in everything], (
        "walking backwards must rebuild the conversation, in order, with no gap"
    )
    assert [f"line {i}" for i in range(40)] == [
        m["body"] for m in walked if m["body"].startswith("line ")
    ]


def test_ties_on_created_at_lose_nothing_and_repeat_nothing(client):
    """Messages written in the same instant, more of them than fit on a page.

    Ordering by the timestamp alone leaves their order up to the database, so a
    page boundary landing inside the tie can drop a message or serve it twice.
    The id tiebreak is what makes the walk total; without it this test fails one
    way or the other depending on which comparison the cursor uses.
    """
    project = make_project(client)
    channel = channel_of(client, project["id"], "boardroom")
    instant = utcnow()
    with db_session() as session:
        for i in range(9):
            session.add(Message(
                id=new_id(MESSAGE), project_id=project["id"], channel_id=channel,
                sender_user_id="chair", body=f"same instant {i}", created_at=instant,
            ))

    walked = _walk(client, channel, 4)
    tied = [m["body"] for m in walked if m["body"].startswith("same instant ")]
    assert len(tied) == 9, f"the tie group lost or repeated messages: {tied}"
    assert len(set(tied)) == 9


def test_a_cursor_from_another_channel_is_refused(client):
    """A wrong cursor must fail loudly. Ignoring it would return the *newest*
    page to a caller asking for an older one, so the chat would look like it was
    loading history while prepending messages it already had."""
    project = make_project(client)
    boardroom = channel_of(client, project["id"], "boardroom")
    leadership = channel_of(client, project["id"], "leadership")
    _say(client, project, "a line in the boardroom")

    stranger = _page(client, boardroom, 10)[0]["id"]
    response = client.get(
        f"/observe/channels/{leadership}/messages?limit=10&before={stranger}", headers=ADMIN,
    )
    assert response.status_code == 400, response.text
    assert "cursor" in response.json()["detail"]

    unknown = client.get(
        f"/observe/channels/{boardroom}/messages?limit=10&before=msg_nope", headers=ADMIN,
    )
    assert unknown.status_code == 400, unknown.text


def test_walking_off_the_start_terminates(client):
    """There has to be a bottom to the scroll, or the page never stops asking."""
    project = make_project(client)
    channel = channel_of(client, project["id"], "boardroom")
    for i in range(5):
        _say(client, project, f"line {i}")

    everything = _page(client, channel, 500)
    assert _page(client, channel, 10, before=everything[0]["id"]) == []


def test_a_named_sender_still_reads_as_a_name(client):
    """The page's name map is built from the senders on the page now, instead of
    every agent and team in the project on every poll. The names it produces
    must not change: `Channels.tsx` prints them, and the MCP read tool uses them
    as the author on its untrusted-text envelope."""
    project = make_project(client)
    channel = channel_of(client, project["id"], "boardroom")
    _say(client, project, "from the chairman")
    with db_session() as session:
        session.add(Message(
            id=new_id(MESSAGE), project_id=project["id"], channel_id=channel,
            sender_agent_id=project["ceo_agent_id"], body="from the CEO",
            created_at=utcnow(),
        ))

    by_body = {m["body"]: m for m in _page(client, channel, 10)}
    assert by_body["from the chairman"]["from_name"] == "chairman"
    assert by_body["from the CEO"]["from_name"] == "CEO"
