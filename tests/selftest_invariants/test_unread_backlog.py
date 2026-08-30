"""Invariant selftest: a chairman message is never marked read unshown.

The pack promised that "a chairman's message must never be silently eaten",
and kept it only up to `UNREAD_PER_CHANNEL`. The query took the NEWEST ten
pending messages and then advanced the read cursor to *now* regardless of how
many there were — so an eleventh was stamped read having never been rendered
into any pack. No count, no hint, no way for the agent or the chairman to know
a message had been dropped.

Boardroom bursts have exceeded ten four times on this install (23, 14, 10 and
10 messages inside half an hour). The only reason it has not obviously bitten
is that the resident chat session usually drains the boardroom before a
backlog builds — and a paused or rate-capped project has no chat session.

These attack the promise directly: flood a channel, build packs, and assert
that every message reaches a pack exactly once and that none is skipped.
"""

from __future__ import annotations

from tests.conftest import ADMIN, agent_by_name, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Project, Run
from yaaf.server.contextpack import UNREAD_PER_CHANNEL, build_context_pack


def _pack(project: dict, agent_id: str) -> dict:
    run_id, _ = live_token(project["id"], agent_id)
    with db_session() as session:
        return build_context_pack(
            session, session.get(Run, run_id), session.get(Agent, agent_id),
            session.get(Project, project["id"]),
        )


def _say(client, project: dict, body: str) -> None:
    posted = client.post(
        f"/admin/projects/{project['id']}/boardroom",
        json={"body": body}, headers=ADMIN,
    )
    assert posted.status_code == 200, posted.text


def _bodies(pack: dict) -> list[str]:
    return [m["body"] for block in pack.get("unread", []) for m in block["messages"]]


def test_a_burst_larger_than_the_cap_loses_nothing(client):
    """The whole bug, in one test: 23 messages, the size of the real burst."""
    project = make_project(client)
    ceo = project["ceo_agent_id"]
    sent = [f"chairman message {i}" for i in range(23)]
    for body in sent:
        _say(client, project, body)

    seen: list[str] = []
    for _ in range(10):  # generous: 23 messages drain in 3 packs of 10
        seen += _bodies(_pack(project, ceo))

    assert set(sent) <= set(seen), (
        f"dropped forever: {sorted(set(sent) - set(seen))}"
    )


def test_every_message_is_delivered_exactly_once(client):
    """Losing a message is the bug; repeating one is a bill. Neither."""
    project = make_project(client)
    ceo = project["ceo_agent_id"]
    sent = [f"m{i}" for i in range(UNREAD_PER_CHANNEL * 2 + 3)]
    for body in sent:
        _say(client, project, body)

    seen: list[str] = []
    for _ in range(6):
        seen += _bodies(_pack(project, ceo))

    assert sorted(seen) == sorted(sent), "each message once, no more and no less"


def test_the_oldest_are_shown_first_so_the_cut_is_the_tail(client):
    """What gets held back must be what we have not reached, never what we are
    about to stamp read."""
    project = make_project(client)
    for i in range(UNREAD_PER_CHANNEL + 5):
        _say(client, project, f"m{i}")

    first = _bodies(_pack(project, project["ceo_agent_id"]))

    assert first == [f"m{i}" for i in range(UNREAD_PER_CHANNEL)]


def test_a_truncated_channel_says_more_is_queued(client):
    """An agent that cannot see a backlog answers the part it was shown as if
    it were the whole conversation."""
    project = make_project(client)
    for i in range(UNREAD_PER_CHANNEL + 1):
        _say(client, project, f"m{i}")

    block = _pack(project, project["ceo_agent_id"])["unread"][0]

    assert block["more_unread"] is True
    assert "next wake" in block["note"]
    assert "messages_read" in block["note"]


def test_a_channel_within_the_cap_carries_no_backlog_note(client):
    """The note is a real signal, so it must not fire on an ordinary run."""
    project = make_project(client)
    _say(client, project, "just the one")

    block = _pack(project, project["ceo_agent_id"])["unread"][0]

    assert "more_unread" not in block
    assert "note" not in block


def test_a_drained_channel_reports_nothing_unread(client):
    """The cursor must still advance — a fix that never marks anything read
    would replay the boardroom on every wake, forever, at full price."""
    project = make_project(client)
    ceo = project["ceo_agent_id"]
    for i in range(3):
        _say(client, project, f"m{i}")

    assert len(_bodies(_pack(project, ceo))) == 3
    assert _bodies(_pack(project, ceo)) == [], "read once, gone from the pack"


def test_the_backlog_drains_for_an_employee_channel_too(client):
    """The leadership and team rooms carry the bulk of the traffic — one
    project ran 310 messages through leadership — so the guarantee cannot be
    boardroom-only."""
    project = make_project(client)
    lead = agent_by_name(project, "Lead")
    emp = agent_by_name(project, "Emp2")
    _run_id, token = live_token(project["id"], lead)
    team_channel = next(
        c["channel_id"] for c in _pack(project, lead)["you"]["channels"]
        if c["kind"] == "team"
    )
    sent = [f"team note {i}" for i in range(UNREAD_PER_CHANNEL + 4)]
    for body in sent:
        posted = client.post(
            "/agent/messages",
            json={"channel_id": team_channel, "body": body},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert posted.status_code == 200, posted.text

    seen: list[str] = []
    for _ in range(4):
        seen += _bodies(_pack(project, emp))

    assert sorted(seen) == sorted(sent)
