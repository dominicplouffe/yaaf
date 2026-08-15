"""Invariant selftests: the Slack bridge. The Postgres bus is canonical —
Slack mirrors it and offers exactly one signed inbound door (the chairman in
the boardroom); nothing on the Slack side may create, drop, or duplicate bus
state, and a mirrored message is mirrored exactly once."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

from sqlalchemy import select

from tests.conftest import ADMIN, bearer, channel_of, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog, Message, Project
from yaaf.server.slack import bridge

BOT_TOKEN = "xoxb-test-token"
SIGNING = "test-signing-secret"


class _Recorder:
    """Stands in for bridge._post_message: records every would-be Slack post
    and hands back monotonically increasing ts values."""

    def __init__(self) -> None:
        self.posts: list[dict] = []

    def __call__(self, token: str, channel: str, text: str) -> str:
        self.posts.append({"token": token, "channel": channel, "text": text})
        return f"{1700000000 + len(self.posts)}.000100"


def _slack_project(client) -> dict:
    """A started project with both secrets stored and the slack config merged
    in through the ordinary admin surfaces — no privileged setup path."""
    project = make_project(client)
    for name, value in (("slack-bot-token", BOT_TOKEN),
                        ("slack-signing-secret", SIGNING)):
        response = client.post(
            "/admin/secrets",
            json={"project_id": project["id"], "name": name, "value": value},
            headers=ADMIN,
        )
        assert response.status_code == 200, response.text
    team_id = project["teams"][0]["id"]
    patched = client.patch(
        f"/admin/projects/{project['id']}",
        json={"config": {"slack": {
            "bot_token_secret": "slack-bot-token",
            "signing_secret": "slack-signing-secret",
            "chairman_slack_user": "U-CHAIR",
            "channel_map": {
                "boardroom": "C-BOARD",
                "leadership": "C-LEAD",
                f"team:{team_id}": "C-TEAM",
            },
        }}},
        headers=ADMIN,
    )
    assert patched.status_code == 200, patched.text
    return project


def _sign(raw: bytes, timestamp: str, secret: str = SIGNING) -> str:
    # Computed exactly as Slack documents it: v0=<hex hmac of "v0:<ts>:<body>">.
    base = b"v0:" + timestamp.encode() + b":" + raw
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def _event_body(channel: str = "C-BOARD", user: str = "U-CHAIR",
                text: str = "Focus on the launch.",
                ts: str = "1723710000.000200", **event_extra) -> bytes:
    return json.dumps({
        "type": "event_callback",
        "event": {"type": "message", "channel": channel, "user": user,
                  "text": text, "ts": ts, **event_extra},
    }).encode()


def _post_event(client, project_id: str, raw: bytes,
                timestamp: str | None = None, signature: str | None = None):
    timestamp = timestamp or str(int(time.time()))
    return client.post(
        f"/webhooks/slack/{project_id}",
        content=raw,
        headers={
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature or _sign(raw, timestamp),
            "Content-Type": "application/json",
        },
    )


def _message_count(project_id: str) -> int:
    with db_session() as session:
        return len(list(session.execute(
            select(Message).where(Message.project_id == project_id)
        ).scalars()))


def test_mirror_posts_each_message_once_and_stamps_ts(client, monkeypatch):
    project = _slack_project(client)
    recorder = _Recorder()
    monkeypatch.setattr(bridge, "_post_message", recorder)

    posted = client.post(f"/admin/projects/{project['id']}/boardroom",
                         json={"body": "Ship the widget."}, headers=ADMIN)
    assert posted.status_code == 200, posted.text
    # And an agent message, posted the way an agent would: over HTTP with a
    # run token, into the leadership channel the CEO may write to.
    _, token = live_token(project["id"], project["ceo_agent_id"])
    leadership = channel_of(client, project["id"], "leadership")
    agent_post = client.post(
        "/agent/messages",
        json={"channel_id": leadership, "body": "Board wants the widget shipped."},
        headers=bearer(token),
    )
    assert agent_post.status_code == 200, agent_post.text

    with db_session() as session:
        assert bridge.mirror_once(session) == 2

    texts = {post["channel"]: post["text"] for post in recorder.posts}
    assert texts["C-BOARD"] == "Chair: Ship the widget."
    assert texts["C-LEAD"] == "CEO: Board wants the widget shipped."
    assert all(post["token"] == BOT_TOKEN for post in recorder.posts)
    with db_session() as session:
        unmirrored = list(session.execute(
            select(Message).where(Message.project_id == project["id"],
                                  Message.slack_ts.is_(None))
        ).scalars())
    assert unmirrored == []

    # Second tick: everything already carries a slack_ts — nothing re-posts.
    with db_session() as session:
        assert bridge.mirror_once(session) == 0
    assert len(recorder.posts) == 2


def test_chairman_inbound_becomes_boardroom_message(client, monkeypatch):
    project = _slack_project(client)

    response = _post_event(client, project["id"], _event_body())
    assert response.status_code == 200, response.text
    message_id = response.json()["message_id"]

    boardroom = channel_of(client, project["id"], "boardroom")
    with db_session() as session:
        message = session.get(Message, message_id)
        chairman = session.get(Project, project["id"]).chairman_user_id
        assert message.channel_id == boardroom
        assert message.sender_user_id == chairman  # the chairman speaks, not a bot
        assert message.sender_agent_id is None
        assert message.body == "Focus on the launch."
        assert message.slack_ts == "1723710000.000200"

    # Slack redelivers on slow acks; the event ts dedupes to one bus row.
    again = _post_event(client, project["id"], _event_body())
    assert again.status_code == 200
    assert again.json() == {"ignored": "duplicate delivery"}
    assert _message_count(project["id"]) == 1

    # Born with a slack_ts, so the mirror never echoes it back to Slack.
    recorder = _Recorder()
    monkeypatch.setattr(bridge, "_post_message", recorder)
    with db_session() as session:
        assert bridge.mirror_once(session) == 0
    assert recorder.posts == []


def test_forged_stale_or_unconfigured_inbound_is_refused(client):
    project = _slack_project(client)
    raw = _event_body()

    forged = _post_event(client, project["id"], raw, signature="v0=" + "0" * 64)
    assert forged.status_code == 403

    wrong_secret = _post_event(
        client, project["id"], raw,
        signature=_sign(raw, str(int(time.time())), secret="not-the-secret"),
    )
    assert wrong_secret.status_code == 403

    # A perfectly signed request from an hour ago is a replay, not a delivery.
    stale_ts = str(int(time.time()) - 3600)
    stale = _post_event(client, project["id"], raw, timestamp=stale_ts,
                        signature=_sign(raw, stale_ts))
    assert stale.status_code == 403

    # No slack config at all: nothing to verify against — refuse.
    bare = make_project(client, slug="noslack")
    unconfigured = _post_event(client, bare["id"], raw)
    assert unconfigured.status_code == 403

    assert _message_count(project["id"]) == 0
    assert _message_count(bare["id"]) == 0


def test_non_chairman_or_wrong_channel_is_ignored_but_audited(client):
    project = _slack_project(client)

    intruder = _post_event(client, project["id"],
                           _event_body(user="U-STRANGER", ts="1723710001.000001"))
    assert intruder.status_code == 200  # 200 on purpose: Slack retries non-2xx
    assert intruder.json() == {"ignored": "not the chairman's Slack user"}

    wrong_room = _post_event(client, project["id"],
                             _event_body(channel="C-LEAD", ts="1723710002.000002"))
    assert wrong_room.status_code == 200
    assert wrong_room.json() == {"ignored": "not the mapped boardroom channel"}

    # The bridge's own mirror posts come back as bot events; dropped, unaudited.
    echo = _post_event(client, project["id"],
                       _event_body(bot_id="B-BRIDGE", ts="1723710003.000003"))
    assert echo.status_code == 200
    assert echo.json() == {"ignored": "bot or non-user message"}

    assert _message_count(project["id"]) == 0
    with db_session() as session:
        reasons = [row.detail.get("reason") for row in session.execute(
            select(AuditLog).where(AuditLog.project_id == project["id"],
                                   AuditLog.action == "webhook.slack_ignored")
        ).scalars()]
    assert reasons.count("not the chairman's Slack user") == 1
    assert reasons.count("not the mapped boardroom channel") == 1


def test_url_verification_echoes_challenge(client):
    project = _slack_project(client)
    raw = json.dumps({"type": "url_verification",
                      "challenge": "3eZbrw1aBl2rZgRNFdxV2595E9CY3gmdALWMmHkvFXO"}).encode()
    response = _post_event(client, project["id"], raw)
    assert response.status_code == 200
    assert response.json() == {
        "challenge": "3eZbrw1aBl2rZgRNFdxV2595E9CY3gmdALWMmHkvFXO"
    }
    assert _message_count(project["id"]) == 0
