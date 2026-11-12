"""Invariant selftests: who a published file is for sets the form it is held to.

People opened 2 of 17 published Markdown files in a day; agents read notes
back in full hundreds of times and carried each one for the rest of their
run. So every file names its reader. A file for people is one screen unless
the chairman asked for a long piece, in his own words. A note for agents is
the facts without the prose, and is the team's — never announced to him.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Artifact, Message, Notice, Project, Reader, utcnow
from yaaf.server import readers


def _words(n: int) -> bytes:
    sentence = "The weekly spend fell by four percent against the prior week. "
    return (sentence * (n // 11 + 1)).encode()


def _put(client, token, name: str, data: bytes, **extra):
    return client.post(
        "/agent/artifacts",
        json={"filename": name, "content_base64": base64.b64encode(data).decode(), **extra},
        headers=bearer(token),
    )


def _announced(project_id: str, name: str) -> bool:
    with db_session() as session:
        lines = session.execute(
            select(Message.body).where(Message.project_id == project_id)).scalars().all()
        notices = session.execute(
            select(Notice).where(Notice.project_id == project_id)).scalars().all()
    return (any(name in line for line in lines)
            or any(name in (n.title or "") + (n.body or "") for n in notices))


def test_a_file_for_people_is_one_screen_unless_he_asked_for_more(client):
    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])

    long = _put(client, token, "findings.md", _words(readers.PEOPLE_MAX_WORDS + 200),
                reader="people")
    assert long.status_code == 422
    assert f"{readers.PEOPLE_MAX_WORDS} words at most" in long.json()["error"]

    short = _put(client, token, "findings.md", _words(readers.PEOPLE_MAX_WORDS - 60),
                 reader="people")
    assert short.status_code == 200, short.text
    assert short.json()["reader"] == "people"
    # A file for people is still announced to him, as it always was.
    assert _announced(project["id"], "findings.md")


def test_a_long_piece_needs_the_chairmans_own_words_quoted_exactly(client):
    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        session.get(Project, project["id"]).goal = (
            "Publish one 2,000-word essay on pricing every Monday for our customers.")
    body = _words(readers.PEOPLE_MAX_WORDS * 3)

    # The agent's paraphrase of what he wanted is not him asking.
    invented = _put(client, token, "essay.md", body, reader="people",
                    asked_for="the chairman wants an in-depth essay")
    assert invented.status_code == 422
    assert "quoted" in invented.json()["error"]

    # Two words occur everywhere by accident; they license nothing.
    assert _put(client, token, "essay.md", body, reader="people",
                asked_for="2,000-word essay").status_code == 422

    quoted = _put(client, token, "essay.md", body, reader="people",
                  asked_for="“one 2,000-word essay on pricing every Monday”")
    assert quoted.status_code == 200, quoted.text

    # The reason for the length travels with the file, where he can read it.
    files = client.get(f"/observe/projects/{project['id']}/files", headers=ADMIN).json()
    receipt = next(r for r in files["published"] if r["name"] == "essay.md")
    assert receipt["reader"] == "people"
    assert "2,000-word essay" in receipt["asked_for"]


def test_words_a_person_posted_count_as_him_asking(client):
    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        boardroom = session.execute(
            select(Message.channel_id).where(Message.project_id == project["id"])
        ).scalars().first()
        if boardroom is None:
            from yaaf.core.models import Channel, ChannelKind
            boardroom = session.execute(
                select(Channel.id).where(Channel.project_id == project["id"],
                                         Channel.kind == ChannelKind.BOARDROOM)
            ).scalar_one()
        session.add(Message(id="msg_readers_test", project_id=project["id"],
                            channel_id=boardroom, sender_user_id="usr_chairman",
                            body="Send me the full audit with every campaign in it please."))
    assert _put(client, token, "audit.md", _words(2000), reader="people",
                asked_for="the full audit with every campaign").status_code == 200


def test_a_note_for_agents_is_compact_and_never_reaches_the_chairman(client):
    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])

    over = _put(client, token, "lens.md", _words(readers.AGENTS_MAX_WORDS + 300),
                reader="agents")
    assert over.status_code == 422
    assert "CSV or JSON" in over.json()["error"]

    note = _put(client, token, "lens.md", _words(readers.AGENTS_MAX_WORDS - 100),
                reader="agents")
    assert note.status_code == 200, note.text

    # Not announced: no boardroom line, no notice, nothing queued for his Slack.
    assert not _announced(project["id"], "lens.md")
    with db_session() as session:
        row = session.execute(select(Artifact).where(Artifact.name == "lens.md")).scalar_one()
        assert row.reader == Reader.AGENTS
        assert row.slack_ts == readers.NOT_FOR_PEOPLE

    # Not on his Results, and not in the morning letter's "delivered".
    results = client.get(f"/observe/projects/{project['id']}/results", headers=ADMIN).json()
    assert "lens.md" not in json.dumps(results)
    from yaaf.server.briefing import _delivered
    with db_session() as session:
        row = session.get(Project, project["id"])
        now = utcnow()
        assert _delivered(session, row, now - timedelta(days=1), now + timedelta(minutes=1)) == []

    # Still on the shelf for the team, marked as theirs.
    files = client.get(f"/observe/projects/{project['id']}/files", headers=ADMIN).json()
    receipt = next(r for r in files["published"] if r["name"] == "lens.md")
    assert receipt["reader"] == "agents"


def test_data_is_never_refused_for_its_size(client):
    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    rows = b"week,spend,sales\n" + b"2026-11-02,4120.50,18233.10\n" * 20_000
    for reader in ("people", "agents"):
        assert _put(client, token, f"panel_{reader}.csv", rows,
                    reader=reader).status_code == 200


def test_an_old_caller_without_a_reader_is_treated_as_writing_for_people(client):
    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    assert _put(client, token, "memo.md", _words(200)).status_code == 200
    assert _put(client, token, "memo.md", _words(2000)).status_code == 422


# ── the agent's own tool ────────────────────────────────────────────────────────


@pytest.fixture()
def sandbox_entry(monkeypatch):
    pytest.importorskip("claude_agent_sdk")
    monkeypatch.setenv("YAAF_API_BASE_URL", "http://api:8080")
    monkeypatch.setenv("YAAF_API_TOKEN", "test-token")
    monkeypatch.setenv("YAAF_RUN_ID", "run_test")
    monkeypatch.setenv("YAAF_TOOL_PERMISSIONS", "{}")
    import yaaf.runner.sandbox_entry as mod

    return importlib.reload(mod)


def test_the_agent_must_say_who_a_file_is_for(sandbox_entry, monkeypatch):
    monkeypatch.setattr(
        sandbox_entry, "_call",
        lambda *a, **k: pytest.fail("a put with no reader must never reach the platform"))
    reply = asyncio.run(sandbox_entry.t_artifact_put.handler(
        {"filename": "x.md", "content_base64": base64.b64encode(b"hi").decode()}))
    out = json.loads(reply["content"][0]["text"])
    assert out["status"] == 422 and "reader" in out["body"]["error"]


def test_the_rule_the_agent_reads_carries_the_numbers_the_platform_enforces(sandbox_entry):
    """The sandbox cannot import the server, so the publish-time rule is a
    copy. A copy whose numbers drift tells agents one limit and refuses them
    at another."""
    rule = sandbox_entry.READER_RULE
    assert f"{readers.AGENTS_MAX_WORDS:,} words" in rule
    assert f"{readers.PEOPLE_MAX_WORDS} words" in rule
    assert sandbox_entry.READER_RULE in sandbox_entry.t_artifact_put.description
