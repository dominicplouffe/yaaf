"""Invariant selftests: write for whoever reads it — guidance, measured, never capped.

The chairman approved guidance set by the reader (12 Nov 2026) and ruled out
word caps. So this file checks the four things that make guidance work without
a ceiling: the writer is told who reads the result, every deliverable still
reaches him with its own answer quoted — his own files at once, the team's in
one roundup — the platform records whether he opened it, and the CEO's review
shows how the team wrote so it can coach.
"""

from __future__ import annotations

import base64
from datetime import timedelta

from sqlalchemy import select

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Artifact, Message, Project, Run, Task, utcnow
from yaaf.server import writing

TEST = {"type": "command", "command": "test -s out/report.md"}

REPORT = b"""---
title: weekly
---
# Why sales fell

**Sales fell 16% week on week (Nov 2-8 vs Oct 26-Nov 1, US, all ASINs), and
ads explain about a third of it.** Organic fell first. The rest is seasonal.

## Detail
| week | sales |
"""


def _put(client, token, name: str, data: bytes, result_for: str | None = None):
    body = {"filename": name, "content_base64": base64.b64encode(data).decode()}
    if result_for:
        body["result_for"] = result_for
    return client.post("/agent/artifacts", json=body, headers=bearer(token))


# ── the opening ─────────────────────────────────────────────────────────────────


def test_the_opening_is_the_first_real_paragraph_not_the_title_or_the_table():
    opening = writing.opening("report.md", REPORT)
    assert opening.startswith("Sales fell 16% week on week")
    assert "**" not in opening and "#" not in opening and "|" not in opening
    assert "The rest is seasonal." in opening


def test_a_long_opening_is_cut_at_a_sentence_and_data_has_none():
    long = ("This is a sentence that carries one finding with its number. " * 20).encode()
    opening = writing.opening("note.md", long)
    assert len(opening) <= writing.OPENING_CHARS and opening.endswith(".")
    assert writing.opening("panel.csv", b"week,sales\n1,2\n") is None


def _lines(project_id: str, needle: str) -> list[str]:
    with db_session() as session:
        return list(session.execute(
            select(Message.body).where(Message.project_id == project_id,
                                       Message.body.contains(needle))
            .order_by(Message.created_at)
        ).scalars())


def test_a_file_written_for_him_is_announced_at_once_with_its_answer_quoted(client):
    """Who a file was written for never decides whether he is told — only how:
    his own the moment it lands, with what it found."""
    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    put = _put(client, token, "why_sales_fell.md", REPORT, result_for="chairman")
    assert put.status_code == 200, put.text
    assert put.json()["result_for"] == "chairman"

    lines = _lines(project["id"], "why_sales_fell.md")
    assert len(lines) == 1 and "Sales fell 16% week on week" in lines[0]

    results = client.get(f"/observe/projects/{project['id']}/results", headers=ADMIN).json()
    row = next(r for r in results["results"] if r["name"] == "why_sales_fell.md")
    assert row["opening"].startswith("Sales fell 16%")
    assert row["opens"] == 0 and row["result_for"] == "chairman"


def test_the_teams_files_reach_him_together_once_the_work_goes_quiet(client):
    """One line per file was ~150 a day on a busy project. The team's files are
    named in one roundup instead: every one of them, once."""
    project = make_project(client)
    _, token = live_token(project["id"], agent_by_name(project, "Emp2"))
    for name in ("pull_notes.md", "panel.csv"):
        assert _put(client, token, name, REPORT).json()["result_for"] == "team"
    assert _lines(project["id"], "pull_notes.md") == [], "not announced one by one"

    with db_session() as session:
        row = session.get(Project, project["id"])
        assert writing.roundup(session, row, now=utcnow()) == 0, "still mid-burst"
        later = utcnow() + writing.ROUNDUP_QUIET + timedelta(minutes=1)
        assert writing.roundup(session, row, now=later) == 2
        assert writing.roundup(session, row, now=later) == 0, "once"

    (line,) = _lines(project["id"], "pull_notes.md")
    assert "The team published 2 files" in line and "panel.csv" in line
    assert "Emp2" in line and "deliverables?open=" in line


def test_a_project_that_never_goes_quiet_still_reports_within_hours(client):
    project = make_project(client)
    _, token = live_token(project["id"], agent_by_name(project, "Emp2"))
    _put(client, token, "first.md", REPORT)
    with db_session() as session:
        first = session.execute(select(Artifact).where(Artifact.name == "first.md")).scalar_one()
        first.published_at = utcnow() - writing.ROUNDUP_LONGEST - timedelta(minutes=1)
    _put(client, token, "just_now.md", REPORT)
    with db_session() as session:
        assert writing.roundup(session, session.get(Project, project["id"])) == 2


def test_the_teams_roundup_reaches_slack_as_one_message_naming_each_file(client, monkeypatch):
    from tests.selftest_invariants.test_slack_deliverables import _wire_slack
    from yaaf.core import config
    from yaaf.server.slack import bridge

    monkeypatch.setattr(config.settings(), "public_base_url", "https://yaaf.example.com")
    project = make_project(client)
    _wire_slack(client, project)
    _, token = live_token(project["id"], agent_by_name(project, "Emp2"))
    for name in ("a.md", "b.md", "c.md"):
        _put(client, token, name, REPORT)
    posts: list[str] = []
    monkeypatch.setattr(bridge, "_post_message",
                        lambda tok, ch, text: posts.append(text) or f"T{len(posts)}")
    monkeypatch.setattr(bridge, "_upload_file", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("a team file was uploaded on its own")))

    with db_session() as session:
        bridge.mirror_once(session)
    assert posts == [], "nothing goes to Slack before the roundup"

    with db_session() as session:
        writing.roundup(session, session.get(Project, project["id"]),
                        now=utcnow() + writing.ROUNDUP_QUIET + timedelta(minutes=1))
    with db_session() as session:
        bridge.mirror_once(session)
        bridge.mirror_once(session)
    assert len(posts) == 1, "one message, once"
    assert all(f"|{n}>" in posts[0] for n in ("a.md", "b.md", "c.md"))
    assert "https://yaaf.example.com/app/#/projects/" in posts[0]


def test_a_file_made_in_a_conversation_with_him_is_his(client):
    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"], kind="chat_session")
    assert _put(client, token, "two_plans.md", REPORT).json()["result_for"] == "chairman"


def test_the_letter_lists_his_files_first_and_counts_the_teams(client):
    from yaaf.server import briefing

    project = make_project(client)
    _, emp = live_token(project["id"], agent_by_name(project, "Emp2"))
    for i in range(7):
        _put(client, emp, f"team_note_{i}.md", REPORT)
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    _put(client, ceo, "for_you.md", REPORT, result_for="chairman")

    with db_session() as session:
        brief = briefing.project_briefing(session, session.get(Project, project["id"]))
    assert brief["delivered"][0]["name"] == "for_you.md"
    text = briefing.render_text(brief)
    assert text.index("for_you.md") < text.index("The team's own files: 7 files")
    assert "Sales fell 16%" in text.split("The team's own files")[0]
    assert "and 2 more in Deliverables" in text


def test_the_platform_records_a_person_opening_a_published_file(client):
    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    key = _put(client, token, "memo.md", REPORT).json()["key"]
    for _ in range(2):
        assert client.get(f"/observe/projects/{project['id']}/files/content",
                          params={"artifact": key}, headers=ADMIN).status_code == 200
    with db_session() as session:
        row = session.execute(select(Artifact).where(Artifact.storage_key == key)).scalar_one()
        assert row.opens == 2 and row.first_opened_at is not None


# ── who the result is for ───────────────────────────────────────────────────────


def test_the_ceo_says_who_a_result_is_for_and_an_assignment_inherits_it(client):
    project = make_project(client)
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    _, lead = live_token(project["id"], agent_by_name(project, "Lead"))
    team_id = project["teams"][0]["id"]

    task = client.post("/agent/tasks", headers=bearer(ceo), json={
        "team_id": team_id, "title": "Weekly report for the chairman",
        "goal": "why sales fell, for him", "success_test": TEST,
        "result_for": "chairman"}).json()
    assert task["result_for"] == "chairman"

    client.post(f"/agent/tasks/{task['id']}/claim", headers=bearer(lead))
    sub = client.post("/agent/tasks", headers=bearer(lead), json={
        "team_id": team_id, "title": "Pull the ad numbers", "goal": "the ads half",
        "success_test": TEST, "parent_task_id": task["id"],
        "assignee_agent_id": agent_by_name(project, "Emp2")}).json()
    assert sub["result_for"] == "chairman"

    # Unsaid is the team: the reader of most of what agents write.
    plain_task = client.post("/agent/tasks", headers=bearer(ceo), json={
        "team_id": team_id, "title": "Check the feed", "goal": "is the data feed fresh",
        "success_test": TEST}).json()
    assert plain_task["result_for"] == "team"

    assert client.post("/agent/tasks", headers=bearer(ceo), json={
        "team_id": team_id, "title": "Something", "goal": "for whom is this one?",
        "success_test": TEST, "result_for": "everyone"}).status_code == 422


# ── the record the CEO coaches from ─────────────────────────────────────────────


def _publish_as(client, project, agent_name: str, task_id: str | None, name: str, n: int):
    agent_id = (project["ceo_agent_id"] if agent_name == "CEO"
                else agent_by_name(project, agent_name))
    run_id, token = live_token(project["id"], agent_id)
    if task_id:
        with db_session() as session:
            session.get(Run, run_id).task_id = task_id
    body = ("We found one thing and it has a number. " * (n // 9 + 1)).encode()
    assert _put(client, token, name, body).status_code == 200


def test_the_review_shows_how_the_team_wrote_and_who_writes_far_past_its_norm(client):
    project = make_project(client)
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    task = client.post("/agent/tasks", headers=bearer(ceo), json={
        "team_id": project["teams"][0]["id"], "title": "The chairman's weekly",
        "goal": "the weekly, written for him", "success_test": TEST, "result_for": "chairman"})
    assert task.status_code == 200, task.text
    task = task.json()

    for i in range(3):
        _publish_as(client, project, "Emp2", None, f"note_{i}.md", 200)
        _publish_as(client, project, "Lead", None, f"essay_{i}.md", 3000)
    _publish_as(client, project, "CEO", task["id"], "weekly.md", 300)

    with db_session() as session:
        row = session.get(Project, project["id"])
        record = writing.compose(session, row, utcnow() - timedelta(days=1),
                                 utcnow() + timedelta(minutes=1))
        weekly = session.execute(select(Artifact).where(Artifact.name == "weekly.md")).scalar_one()
        weekly.opens = 1

    assert record["for_people"]["files"] == 1
    assert record["for_team"]["files"] == 6
    assert [w["agent"] for w in record["long_writers"]] == ["Lead"]
    assert record["long_writers"][0]["files"] == 3

    # It rides on the review record, which is what the CEO's pack carries.
    from yaaf.server import review_record
    with db_session() as session:
        row = session.get(Project, project["id"])
        frozen = review_record.compose(session, row, utcnow() - timedelta(days=1),
                                       utcnow() + timedelta(minutes=1))
    assert frozen["writing"]["for_people"]["opened"] == 1


def test_nothing_is_refused_for_its_length(client):
    """The chairman ruled out caps. A very long file publishes."""
    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    huge = ("A long piece he asked for, written at length. " * 4000).encode()
    assert _put(client, token, "chapter.md", huge).status_code == 200


def test_the_task_tells_its_writer_who_reads_it(client):
    from yaaf.server.contextpack import _task_context

    project = make_project(client)
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    task = client.post("/agent/tasks", headers=bearer(ceo), json={
        "team_id": project["teams"][0]["id"], "title": "Client report",
        "goal": "for the client", "success_test": TEST, "result_for": "outside"}).json()
    with db_session() as session:
        assert _task_context(session, session.get(Task, task["id"]))["result_for"] == "outside"
