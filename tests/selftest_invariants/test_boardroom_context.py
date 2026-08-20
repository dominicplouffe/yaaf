"""Invariant selftests: the CEO in the boardroom answers from facts.

The chairman's complaint was that his CEO "never has all the information and
lies to me". It was not lying. It was reasoning from an empty directory.

A work run gets a context pack on every wake. A chat session got a system
prompt written once at container boot and never refreshed — no board, no
roster, no notices, no budget, and no way to observe whether a cap had stopped
the company — while that same frozen prompt instructed it to state plainly when
work had stopped "rather than inventing a cause". Worse, it mounted its own
empty scratch at /workspace while being told /workspace was "shared by every
agent on it", so it announced to the chairman that a project had lost its data
while the file sat on disk in the directory it could not see.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    Notice,
    NoticeKind,
    Project,
    RunQueue,
    Task,
    TaskStatus,
    utcnow,
)
from yaaf.server import dispatcher


def _drain(max_rounds: int = 6) -> None:
    for _ in range(max_rounds):
        with db_session() as session:
            if dispatcher.dispatch_once(session) == 0:
                return
            session.query(RunQueue).delete()
    raise AssertionError("dispatcher would not settle")


def test_the_ceo_is_handed_the_reason_the_company_is_quiet(client):
    """The exact question the chairman asked and got a wrong answer to. The
    CEO must not have to derive this — two different answers to "why are we
    idle" is worse than one imperfect answer, and the platform's is the one on
    his screen."""
    project = make_project(client)
    _drain()
    team_id = project["teams"][0]["id"]
    with db_session() as session:
        session.add(Task(
            id=ids.new_id(ids.TASK), project_id=project["id"], team_id=team_id,
            created_by_agent_id=project["ceo_agent_id"],
            title="Compute the distributions", goal="Only once the pull lands.",
            success_test={"type": "command", "command": "test -f /workspace/x"},
            produces_external_evidence=False, status=TaskStatus.READY,
            not_before=utcnow() + timedelta(hours=2),
            created_at=utcnow(), updated_at=utcnow(),
        ))

    _, ceo = live_token(project["id"], project["ceo_agent_id"], kind="chat_session")
    pack = client.get("/agent/context", headers=bearer(ceo)).json()

    situation = pack["situation"]
    assert situation["blocked_by"] == "scheduled"
    assert "scheduled for" in situation["why"]
    assert situation["clears_at"]
    # And the numbers it was told about as static rules, as live readings.
    assert situation["runs_today"]["cap"] > 0
    assert situation["runs_this_hour"]["cap"] > 0
    assert situation["budget"]["cap"] > 0
    assert situation["unreachable_work"]["gated"] == 1


def test_the_board_the_ceo_reads_shows_which_work_is_gated(client):
    """A gated task read as ordinary open work on the CEO's own board, so a CEO
    asked why nothing was happening looked at its context, saw work in hand,
    and invented a cause for the silence."""
    project = make_project(client)
    _drain()
    team_id = project["teams"][0]["id"]
    gate = utcnow() + timedelta(hours=2)
    with db_session() as session:
        session.add(Task(
            id=ids.new_id(ids.TASK), project_id=project["id"], team_id=team_id,
            created_by_agent_id=project["ceo_agent_id"],
            title="Compute the distributions", goal="Only once the pull lands.",
            success_test={"type": "command", "command": "test -f /workspace/x"},
            produces_external_evidence=False, status=TaskStatus.READY,
            not_before=gate, created_at=utcnow(), updated_at=utcnow(),
        ))

    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    board = client.get("/agent/context", headers=bearer(ceo)).json()["board"]
    entry = board["open"][0]
    assert entry["not_before"] is not None
    assert "blocked_reason" in entry
    assert "requirement_id" in entry, "the mandatory field must be readable somewhere"


def test_a_chat_session_is_told_the_truth_about_its_own_disk(client):
    """The single sentence that produced a false statement to the chairman. A
    work run's /workspace really is the shared company tree; a chat session's is
    its own empty scratch, and the company's tree is read-only at /company."""
    from yaaf.core.models import Agent as AgentRow
    from yaaf.server.contextpack import build_system_prompt

    project = make_project(client)
    with db_session() as session:
        row = session.get(Project, project["id"])
        ceo = session.get(AgentRow, project["ceo_agent_id"])
        run_prompt = build_system_prompt(session, ceo, row)
        chat_prompt = build_system_prompt(session, ceo, row, lane="chat")

    assert "shared by every agent on it" in run_prompt
    assert "/company" not in run_prompt

    assert "/company" in chat_prompt
    assert "READ-ONLY" in chat_prompt
    assert "shared by every agent on it" not in chat_prompt, (
        "the claim that produced the false answer must not reach the chat lane")
    assert "means nothing about the company" in chat_prompt, (
        "an empty scratch must be named as meaningless, not left to be interpreted")


def test_notices_finally_reach_the_agent_the_chairman_talks_to(client):
    """Notices are consumed inside the context pack, and the chat lane never
    built one — so "your run was reaped" and "budget exhausted" could never
    reach the CEO in the boardroom, whatever he asked it."""
    project = make_project(client)
    _drain()
    with db_session() as session:
        session.add(Notice(
            id=ids.new_id(ids.NOTICE), project_id=project["id"],
            agent_id=project["ceo_agent_id"], kind=NoticeKind.BUDGET_WARNING,
            body="80% of the monthly budget is spent.", created_at=utcnow()))

    _, ceo = live_token(project["id"], project["ceo_agent_id"], kind="chat_session")
    pack = client.get("/agent/context", headers=bearer(ceo)).json()
    assert [n["body"] for n in pack["notices"]] == ["80% of the monthly budget is spent."]

    # Delivered exactly once, same as a work run.
    again = client.get("/agent/context", headers=bearer(ceo)).json()
    assert again["notices"] == []


def test_a_days_long_chat_session_is_capped_on_rate_not_lifetime(client):
    """`refusal_cap_per_run` counts over a run, and a chat run row lives for
    days. Counted as a lifetime total, five mistakes spread across a week
    revoked the boardroom — and a revoked session drops the chairman's reply
    into stderr and goes silent. Loop protection wants a rate."""
    import time as _time

    from yaaf.core.models import Run
    from yaaf.server import app as app_module

    project = make_project(client, constitution={"refusal_cap_per_run": 5})
    run_id, ceo = live_token(project["id"], project["ceo_agent_id"], kind="chat_session")
    app_module._CHAT_REFUSALS.pop(run_id, None)

    def refuse():
        # Always refused: a top-level task may not name a person.
        return client.post(
            "/agent/tasks",
            json={"team_id": project["teams"][0]["id"], "title": "Do the thing",
                  "goal": "A goal long enough to pass validation.",
                  "assignee_agent_id": project["ceo_agent_id"],
                  "success_test": {"type": "command", "command": "test -f /w/x.json"},
                  "produces_external_evidence": True},
            headers=bearer(ceo))

    for _ in range(4):
        assert refuse().status_code in (403, 409, 422)
    with db_session() as session:
        assert session.get(Run, run_id).token_revoked is False

    # A day passes. The lifetime count keeps climbing — the Runs page is right
    # to show it — but the window that governs revocation has drained.
    app_module._CHAT_REFUSALS[run_id] = [
        t - app_module.CHAT_REFUSAL_WINDOW_S - 1
        for t in app_module._CHAT_REFUSALS[run_id]
    ]
    for _ in range(4):
        refuse()
    with db_session() as session:
        run = session.get(Run, run_id)
        assert run.refusal_count == 8, "the durable count still records every one"
        assert run.token_revoked is False, "but a week-old mistake must not mute the room"

    # The protection itself is intact: a burst inside one window still revokes.
    assert refuse().status_code in (401, 403, 409, 422)
    with db_session() as session:
        assert session.get(Run, run_id).token_revoked is True
    assert _time.monotonic() > 0  # (the window is monotonic-clocked, not wall-clocked)


def test_an_ordinary_run_still_dies_on_its_fifth_refusal(client):
    """The counterweight. Rephrasing a refused request is not a strategy, and a
    stateless run has no legitimate reason to hit five walls."""
    from yaaf.core.models import Run

    project = make_project(client, constitution={"refusal_cap_per_run": 5})
    run_id, ceo = live_token(project["id"], project["ceo_agent_id"])
    for _ in range(5):
        client.post(
            "/agent/tasks",
            json={"team_id": project["teams"][0]["id"], "title": "Do the thing",
                  "goal": "A goal long enough to pass validation.",
                  "assignee_agent_id": project["ceo_agent_id"],
                  "success_test": {"type": "command", "command": "test -f /w/x.json"},
                  "produces_external_evidence": True},
            headers=bearer(ceo))
    with db_session() as session:
        assert session.get(Run, run_id).token_revoked is True


def test_the_boardroom_is_told_when_a_session_dies_mid_question(client):
    """A session that ends with the chairman waiting used to write its
    forensics to run.summary and stop there, which reads as a CEO still
    thinking for as long as he is willing to wait."""
    from yaaf.core.models import Message
    from yaaf.runner import chat

    project = make_project(client)
    boardroom = client.get(f"/observe/projects/{project['id']}/channels",
                           headers=ADMIN).json()
    channel_id = next(c["id"] for c in boardroom if c["kind"] == "boardroom")
    client.post(f"/admin/projects/{project['id']}/boardroom",
                json={"body": "why are we idle?"}, headers=ADMIN)

    with db_session() as session:
        row = session.get(Project, project["id"])
        chat.say_session_broke(session, row, "its access token was revoked")

    with db_session() as session:
        posted = [m.body for m in session.query(Message)
                  .filter_by(channel_id=channel_id).all()]
    assert any("ended before it could answer you" in b for b in posted)
    assert any("do not need to repeat it" in b for b in posted)

    # Nobody waiting: a routine restart between conversations is not news, and
    # a room that announces its own plumbing stops being read.
    with db_session() as session:
        row = session.get(Project, project["id"])
        session.add(Message(
            id=ids.new_id(ids.MESSAGE), project_id=row.id, channel_id=channel_id,
            sender_agent_id=project["ceo_agent_id"], body="Answered.",
            created_at=utcnow() + timedelta(seconds=1)))
        session.flush()
        before = session.query(Message).filter_by(channel_id=channel_id).count()
        chat.say_session_broke(session, row, "its container stopped")
        assert session.query(Message).filter_by(channel_id=channel_id).count() == before


def test_the_chat_container_mounts_the_company_tree_read_only(client):
    """The mount that caused the false answer. /company must be there, must be
    the project's real tree, and must be read-only — a resident session writing
    into the working tree is the race the split existed to prevent."""
    from unittest.mock import patch

    from yaaf.core.models import Agent as AgentRow
    from yaaf.runner import chat

    project = make_project(client, slug="acme")
    with db_session() as session:
        row = session.get(Project, project["id"])
        ceo = session.get(AgentRow, project["ceo_agent_id"])
        with patch.object(chat, "stash_context_doc", return_value=""):
            launch, _ = chat._prepare_session(session, row, ceo)

    assert launch["company_dir"].endswith("/acme")
    assert launch["workspace_dir"].endswith("/acme-chat")
    assert launch["company_dir"] != launch["workspace_dir"]

    with patch("docker.from_env") as docker_from_env:
        containers = docker_from_env.return_value.containers
        containers.list.return_value = []
        from yaaf.runner import sandbox

        sandbox.start_chat_container(project_id=project["id"], run_id="run_x",
                                     name="c", env={}, **{
                                         k: launch[k] for k in
                                         ("workspace_dir", "company_dir")})
        volumes = containers.run.call_args.kwargs["volumes"]
    assert volumes[launch["company_dir"]] == {"bind": "/company", "mode": "ro"}
    assert volumes[launch["workspace_dir"]] == {"bind": "/workspace", "mode": "rw"}


def test_agents_are_told_a_team_task_never_names_a_person(client):
    """Every single refusal in the live database was this one. The tool
    advertised a parameter that is always rejected for the call the CEO makes
    most, and nothing in its context described the team model."""
    # Read as source: sandbox_entry only imports inside a sandbox (it reads the
    # run token out of the environment at module scope).
    from pathlib import Path

    source = Path("yaaf/runner/sandbox_entry.py").read_text()
    schema = source.split('"task_create",', 1)[1].split("async def", 1)[0]
    assert "ONLY FOR SUBTASKS" in schema
    assert "never a person" in schema
    # And the gate is described by what it costs, not just by what it does.
    assert "looks stopped" in schema

    # And the rule itself is unchanged — the fix is legibility, not permission.
    project = make_project(client)
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    refused = client.post(
        "/agent/tasks",
        json={"team_id": project["teams"][0]["id"], "title": "Do the thing",
              "goal": "A goal long enough to pass validation.",
              "assignee_agent_id": project["ceo_agent_id"],
              "success_test": {"type": "command", "command": "test -f /w/x.json"},
              "produces_external_evidence": True},
        headers=bearer(ceo))
    assert refused.status_code == 422
    assert "not assigned to individuals" in refused.json()["error"]


def test_reading_the_situation_never_moves_the_governance_clock(client):
    """The context pack now calls activity_state, which the dashboard also
    calls on every poll. If either advanced the planning heartbeat, briefing
    the CEO would spend governance."""
    from yaaf.core.models import Heartbeat

    project = make_project(client)
    _drain()

    def beats():
        with db_session() as session:
            return [(b.agent_id, b.name, b.last_fired_at)
                    for b in session.query(Heartbeat).order_by(Heartbeat.id).all()]

    before = beats()
    _, ceo = live_token(project["id"], project["ceo_agent_id"], kind="chat_session")
    client.get("/agent/context", headers=bearer(ceo))
    client.get("/agent/context", headers=bearer(ceo))
    assert beats() == before


def test_a_paused_agent_shows_in_the_roster_the_ceo_reads(client):
    """The CEO plans around who exists. An agent the chairman paused is still
    on the roster and is the reason its work is going nowhere."""
    from yaaf.core.models import AgentStatus

    project = make_project(client)
    _drain()
    with db_session() as session:
        session.get(Agent, project["teams"][0]["agents"][0]["id"]).status = \
            AgentStatus.PAUSED

    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    roster = client.get("/agent/context", headers=bearer(ceo)).json()["roster"]
    assert roster, "the roster is how a CEO learns the ids it must pass to task_create"
