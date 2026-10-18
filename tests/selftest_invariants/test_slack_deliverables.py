"""Invariant selftest: a published deliverable reaches Slack, once.

The shelf is a screen the chairman has to remember to visit, and the record of
how that goes is a run of messages hunting for work that was sitting published
the whole time — "where are my deliverables?", "where is the report??", "what
are the file names for the deliverables that I wanted?" — and, from the desk
whose memo was written, posted and filed but never pushed:

    CHAIRMAN: can you push to me every morning at 8:30 instead of me going to
              look for a file

So: the file goes to the room he is already in. The upload is tried first
because the file itself is the point; an app wired only for messages has no
`files:write`, which is a configuration fact rather than a failure, so the
summary goes instead and says what is missing. Either way the row is stamped
exactly once — the same promise `Message.slack_ts` has always made.
"""

from __future__ import annotations

import base64
from urllib.parse import quote

from sqlalchemy import select

from tests.conftest import ADMIN, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Artifact, Channel, ChannelKind, Message
from yaaf.server.slack import bridge

BOT_TOKEN = "xoxb-test-token"


def _refuse_summary(*_a, **_k):
    raise AssertionError("posted a summary when the upload was available")


def _wire_slack(client, project: dict) -> None:
    """A boardroom mapped to Slack, through the ordinary admin surfaces —
    the same route `test_slack.py` uses, and no privileged setup path."""
    stored = client.post(
        "/admin/secrets",
        json={"project_id": project["id"], "name": "slack-bot-token",
              "value": BOT_TOKEN},
        headers=ADMIN,
    )
    assert stored.status_code == 200, stored.text
    patched = client.patch(
        f"/admin/projects/{project['id']}",
        json={"integrations": {"slack": {
            "bot_token_secret": "slack-bot-token",
            "channel_map": {"boardroom": "C0BOARD"},
        }}},
        headers=ADMIN,
    )
    assert patched.status_code == 200, patched.text


def _publish(client, token: str, filename: str, body: bytes = b"the report"):
    return client.post(
        "/agent/artifacts",
        json={"filename": filename, "content_base64": base64.b64encode(body).decode()},
        headers={"Authorization": f"Bearer {token}"},
    )


def _artifacts(project_id: str) -> list[Artifact]:
    with db_session() as session:
        return list(session.execute(
            select(Artifact).where(Artifact.project_id == project_id)
        ).scalars())


def test_a_published_deliverable_is_uploaded_to_the_boardroom(client, monkeypatch):
    project = make_project(client)
    _wire_slack(client, project)
    _run_id, token = live_token(project["id"], project["ceo_agent_id"])
    assert _publish(client, token, "gw2_memo.md", b"# GW2 MEMO").status_code == 200

    uploads: list[dict] = []
    monkeypatch.setattr(bridge, "_upload_file",
                        lambda tok, ch, name, data, comment: uploads.append(
                            {"channel": ch, "name": name, "data": data,
                             "comment": comment}) or "F123")
    monkeypatch.setattr(bridge, "_post_message", _refuse_summary)

    with db_session() as session:
        assert bridge.mirror_once(session) >= 1

    assert len(uploads) == 1, "the file itself is the point"
    assert uploads[0]["channel"] == "C0BOARD"
    assert uploads[0]["name"] == "gw2_memo.md"
    assert uploads[0]["data"] == b"# GW2 MEMO", "the real bytes, not a description"
    assert "gw2_memo.md" in uploads[0]["comment"]


def test_a_deliverable_is_delivered_exactly_once(client, monkeypatch):
    """The promise `Message.slack_ts` has always made, kept for the shelf."""
    project = make_project(client)
    _wire_slack(client, project)
    _run_id, token = live_token(project["id"], project["ceo_agent_id"])
    _publish(client, token, "report.md")

    calls: list[str] = []
    monkeypatch.setattr(bridge, "_upload_file",
                        lambda *a, **k: calls.append("up") or "F1")

    with db_session() as session:
        bridge.mirror_once(session)
        bridge.mirror_once(session)
        bridge.mirror_once(session)

    assert len(calls) == 1, "three ticks, one announcement"
    assert _artifacts(project["id"])[0].slack_ts == "F1"


def test_an_app_without_files_write_posts_the_summary_and_says_so(client, monkeypatch):
    """`missing_scope` is a configuration fact, not breakage. The chairman
    still gets told the deliverable exists, and told what would attach it."""
    project = make_project(client)
    _wire_slack(client, project)
    _run_id, token = live_token(project["id"], project["ceo_agent_id"])
    _publish(client, token, "wasted_spend.csv", b"col\n1\n")

    def _no_scope(*_a, **_k):
        raise RuntimeError("completeUploadExternal failed: missing_scope")

    posts: list[str] = []
    monkeypatch.setattr(bridge, "_upload_file", _no_scope)
    monkeypatch.setattr(bridge, "_post_message",
                        lambda tok, ch, text: posts.append(text) or "1712.0001")

    with db_session() as session:
        assert bridge.mirror_once(session) >= 1

    assert len(posts) == 1
    assert "wasted_spend.csv" in posts[0]
    assert "files:write" in posts[0], "say what is missing, not just that it failed"
    assert _artifacts(project["id"])[0].slack_ts == "1712.0001", (
        "a summary is still a delivery; it must not retry forever"
    )


def test_a_failed_delivery_is_retried_rather_than_lost(client, monkeypatch):
    """Slack being down must never consume the announcement."""
    project = make_project(client)
    _wire_slack(client, project)
    _run_id, token = live_token(project["id"], project["ceo_agent_id"])
    _publish(client, token, "report.md")

    def _down(*_a, **_k):
        raise RuntimeError("slack is down")

    monkeypatch.setattr(bridge, "_upload_file", _down)
    monkeypatch.setattr(bridge, "_post_message", _down)

    with db_session() as session:
        bridge.mirror_once(session)
    assert _artifacts(project["id"])[0].slack_ts is None, "still owed, so still NULL"

    monkeypatch.setattr(bridge, "_upload_file", lambda *a, **k: "F9")
    with db_session() as session:
        bridge.mirror_once(session)
    assert _artifacts(project["id"])[0].slack_ts == "F9", "the next tick delivers it"


def test_a_project_without_slack_delivers_nothing_and_does_not_stall(client):
    """Most projects have no Slack at all. They must cost nothing here."""
    project = make_project(client)
    _run_id, token = live_token(project["id"], project["ceo_agent_id"])
    _publish(client, token, "report.md")

    with db_session() as session:
        assert bridge.mirror_once(session) == 0
    assert _artifacts(project["id"])[0].slack_ts is None


def test_a_deliverable_that_cannot_be_read_is_still_announced(client, monkeypatch):
    """A shelf row whose bytes are gone is a real state (a moved store, a
    restore). The chairman should hear about the deliverable regardless."""
    project = make_project(client)
    _wire_slack(client, project)
    _run_id, token = live_token(project["id"], project["ceo_agent_id"])
    _publish(client, token, "report.md")

    from yaaf.core import artifacts as artifacts_mod

    class _Empty:
        def get_bytes(self, key):
            raise FileNotFoundError(key)

    monkeypatch.setattr(artifacts_mod, "store", lambda: _Empty())
    posts: list[str] = []
    monkeypatch.setattr(bridge, "_post_message",
                        lambda tok, ch, text: posts.append(text) or "1712.9")

    with db_session() as session:
        assert bridge.mirror_once(session) >= 1

    assert len(posts) == 1 and "report.md" in posts[0]


def test_a_shelf_that_predates_the_feature_is_not_flooded_into_slack(client, monkeypatch):
    """The backfill, asserted rather than assumed.

    Every artifact already on a shelf when this shipped predates the mechanism
    and was never owed a delivery. Left unmarked they read as a backlog, and
    the first tick after deploy would empty a whole shelf into one channel:
    seventeen files on the install this was written against, and 232 on a
    project that is merely paused rather than finished.
    """
    project = make_project(client)
    _wire_slack(client, project)
    _run_id, token = live_token(project["id"], project["ceo_agent_id"])
    for name in ("old1.md", "old2.md", "old3.md"):
        _publish(client, token, name)

    # Exactly what migration 0021 does to the rows it finds already there.
    with db_session() as session:
        for row in session.execute(
            select(Artifact).where(Artifact.project_id == project["id"])
        ).scalars():
            row.slack_ts = "pre-delivery"

    calls: list[str] = []
    monkeypatch.setattr(bridge, "_upload_file", lambda *a, **k: calls.append("x") or "F1")
    monkeypatch.setattr(bridge, "_post_message", lambda *a, **k: calls.append("x") or "1.0")

    with db_session() as session:
        assert bridge.mirror_once(session) == 0
    assert calls == [], "an old shelf owes nothing"

    # And the next real publish still goes.
    assert _publish(client, token, "new.md").status_code == 200
    with db_session() as session:
        assert bridge.mirror_once(session) == 1
    assert len(calls) == 1


def _boardroom_lines(project_id: str) -> list[Message]:
    with db_session() as session:
        return list(session.execute(
            select(Message)
            .join(Channel, Channel.id == Message.channel_id)
            .where(Message.project_id == project_id,
                   Channel.kind == ChannelKind.BOARDROOM)
        ).scalars())


def test_a_deliverable_is_announced_in_the_boardroom_chat_too(client):
    """Slack announces a deliverable in the boardroom channel; the boardroom
    chat must not be the one room that never hears about it. Slack is a
    viewport onto the bus, so it may not know something the bus does not."""
    project = make_project(client)
    _run_id, token = live_token(project["id"], project["ceo_agent_id"])
    assert _publish(client, token, "gw2_memo.md", b"# GW2 MEMO").status_code == 200

    lines = [m for m in _boardroom_lines(project["id"]) if "gw2_memo.md" in m.body]
    assert len(lines) == 1
    assert lines[0].sender_agent_id is None and lines[0].sender_user_id is None, (
        "a platform line: it spends no cadence and wakes nobody"
    )


def test_the_boardroom_line_does_not_double_the_slack_announcement(client, monkeypatch):
    """The file upload is the line's Slack delivery. Mirroring the line as
    text as well would say the same thing twice in the same channel."""
    project = make_project(client)
    _wire_slack(client, project)
    _run_id, token = live_token(project["id"], project["ceo_agent_id"])
    _publish(client, token, "report.md")

    uploads: list[str] = []
    posts: list[str] = []
    monkeypatch.setattr(bridge, "_upload_file",
                        lambda *a, **k: uploads.append("up") or "F1")
    # Recorded, not raised: the mirror swallows a failed post and retries it,
    # so a raising fake would let a double announcement pass silently.
    monkeypatch.setattr(bridge, "_post_message",
                        lambda tok, ch, text: posts.append(text) or "1.0")

    with db_session() as session:
        bridge.mirror_once(session)
        bridge.mirror_once(session)
    assert uploads == ["up"]
    assert posts == [], "the file was the announcement; the line rides on it"


def test_the_boardroom_line_links_to_the_file_itself(client):
    """A chat line that names a file and makes the chairman go find it is half
    an announcement. The name opens that file's preview — the same
    `?open=<key>` the Overview card links with — even when the name is
    markdown-hostile."""
    project = make_project(client)
    _run_id, token = live_token(project["id"], project["ceo_agent_id"])
    _publish(client, token, "q3 [draft]*.md")

    with db_session() as session:
        key = session.execute(
            select(Artifact.storage_key).where(Artifact.project_id == project["id"])
        ).scalar_one()
    (line,) = [m for m in _boardroom_lines(project["id"]) if "draft" in m.body]
    assert (f"[**q3 \\[draft\\]\\*.md**](#/projects/{project['id']}/deliverables"
            f"?open={quote(key, safe='')})") in line.body
