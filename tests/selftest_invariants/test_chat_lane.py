"""Invariant selftests: the chat lane — a resident CEO conversation that
answers in seconds without loosening a single governance rule.

The rules under attack here: chat never consumes the work lane's caps; a live
chat session owns the boardroom (no double replies from queued runs); a dead
one hands back to the fallback within a lease TTL; chat spend is metered the
moment it happens; and Slack socket mode trusts exactly one human."""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, bearer, channel_of, live_token, make_project
from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.db import db_session
from yaaf.core.models import (
    LedgerEntry,
    Notice,
    NoticeKind,
    Project,
    Run,
    RunQueue,
    RunStatus,
    utcnow,
)
from yaaf.server import dispatcher
from yaaf.server.slack.socket import chairman_text


def _ceo_id(project: dict) -> str:
    return project["ceo_agent_id"]


def _stale(run_id: str) -> None:
    with db_session() as session:
        run = session.get(Run, run_id)
        run.heartbeat_at = utcnow() - timedelta(seconds=settings().lease_ttl_s * 2)


def _drain_dispatcher() -> None:
    """Consume a fresh project's startup wakes (planning heartbeat) so a test
    observes only the wake IT causes. Drained queue rows are deleted so they
    count against neither busy nor wakes/hour."""
    for _ in range(5):
        with db_session() as session:
            if dispatcher.dispatch_once(session) == 0:
                return
            session.query(RunQueue).delete()
    raise AssertionError("dispatcher would not settle")


def test_live_chat_session_owns_the_boardroom(client):
    """A chairman message must produce ONE reply. With the chat session live,
    the dispatcher stays out of the boardroom; the moment the session goes
    stale, the queued-run fallback takes over."""
    project = make_project(client)
    _drain_dispatcher()
    chat_run, _ = live_token(project["id"], _ceo_id(project), kind="chat_session")

    assert client.post(f"/admin/projects/{project['id']}/boardroom",
                       json={"body": "Hello?"}, headers=ADMIN).status_code == 200
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 0
        assert session.query(RunQueue).count() == 0

    _stale(chat_run)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1
        queued = session.query(RunQueue).one()
        assert queued.agent_id == _ceo_id(project)


def test_chat_session_burns_no_work_caps(client):
    """The talk lane must not starve the work lane: with max_concurrent_runs=1
    and a live chat session, a notice still wakes the CEO — the session counts
    against neither concurrency, nor busy, nor wakes/hour."""
    project = make_project(client)
    _drain_dispatcher()
    assert client.patch(f"/admin/projects/{project['id']}",
                        json={"config": {"max_concurrent_runs": 1}},
                        headers=ADMIN).status_code == 200
    live_token(project["id"], _ceo_id(project), kind="chat_session")
    with db_session() as session:
        session.add(Notice(id=ids.new_id(ids.NOTICE), project_id=project["id"],
                           agent_id=_ceo_id(project), kind=NoticeKind.BUDGET_WARNING,
                           body="test notice"))
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1


def test_chat_usage_is_metered_immediately_and_only_for_chat(client):
    project = make_project(client)
    chat_run_id, chat_token = live_token(project["id"], _ceo_id(project), kind="chat_session")
    # A 3-field payload (a chat container from an older image) must still book.
    booked = client.post("/agent/chat_usage",
                         json={"tokens_in": 1200, "tokens_out": 340, "cost_usd": 0.05},
                         headers=bearer(chat_token))
    assert booked.status_code == 200, booked.text
    booked = client.post("/agent/chat_usage",
                         json={"tokens_in": 10, "tokens_out": 20,
                               "cache_creation_tokens": 30_000,
                               "cache_read_tokens": 400_000, "cost_usd": 0.10},
                         headers=bearer(chat_token))
    assert booked.status_code == 200, booked.text
    with db_session() as session:
        entries = session.query(LedgerEntry).filter_by(project_id=project["id"]).all()
        assert len(entries) == 2
        assert all(e.kind == "model_usage" for e in entries)
        assert sum(e.tokens_in for e in entries) == 1210
        assert sum(e.cache_creation_tokens for e in entries) == 30_000
        assert sum(e.cache_read_tokens for e in entries) == 400_000
        run = session.get(Run, chat_run_id)
        assert (run.tokens_in, run.tokens_out) == (1210, 360)
        assert (run.cache_creation_tokens, run.cache_read_tokens) == (30_000, 400_000)

    # An ordinary run must keep metering at finalize — the refusal names it.
    work_run_id, run_token = live_token(project["id"], _ceo_id(project))
    refused = client.post("/agent/chat_usage",
                          json={"tokens_in": 1, "tokens_out": 1},
                          headers=bearer(run_token))
    assert refused.status_code == 403
    assert "finalize" in refused.json()["error"]
    with db_session() as session:
        # The wall is kept on the run, so a storm banner can name it.
        assert "finalize" in (session.get(Run, work_run_id).last_refusal or "")


def test_typing_is_scoped_and_expires(client):
    project = make_project(client)
    boardroom = channel_of(client, project["id"], "boardroom")
    _, chat_token = live_token(project["id"], _ceo_id(project), kind="chat_session")

    assert client.post("/agent/typing", json={"channel_id": boardroom, "seconds": 30},
                       headers=bearer(chat_token)).status_code == 200
    assert client.get(f"/observe/channels/{boardroom}/typing",
                      headers=ADMIN).json()["typing"] is True
    assert client.post("/agent/typing", json={"channel_id": boardroom, "seconds": 0},
                       headers=bearer(chat_token)).status_code == 200
    assert client.get(f"/observe/channels/{boardroom}/typing",
                      headers=ADMIN).json()["typing"] is False

    # An employee is not a boardroom member; presence is membership-gated too.
    employee = project["teams"][0]["agents"][0]["id"]
    _, employee_token = live_token(project["id"], employee)
    assert client.post("/agent/typing", json={"channel_id": boardroom, "seconds": 30},
                       headers=bearer(employee_token)).status_code == 403


def test_dash_reports_chat_live_and_excludes_it_from_running(client):
    project = make_project(client)
    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert dash["chat_live"] is False

    chat_run, _ = live_token(project["id"], _ceo_id(project), kind="chat_session")
    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert dash["chat_live"] is True
    assert dash["runs"]["running"] == 0  # presence, not work

    _stale(chat_run)
    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert dash["chat_live"] is False


def test_reaping_a_chat_session_is_quiet_and_lifetime_is_unlimited(client):
    """Only a stale heartbeat kills a chat session — never the run timeout —
    and its reaping produces no 'your run died' notice: the supervisor
    restarts it without ceremony."""
    from yaaf.core.constitution import effective
    from yaaf.server.janitor import reap_stale_runs

    project = make_project(client)
    chat_run, _ = live_token(project["id"], _ceo_id(project), kind="chat_session")

    # Ancient started_at + fresh heartbeat: an ordinary run would hit the hard
    # timeout; the resident session must not.
    with db_session() as session:
        run = session.get(Run, chat_run)
        run.started_at = utcnow() - timedelta(days=3)
        run.created_at = utcnow() - timedelta(days=3)
    with db_session() as session:
        p = session.get(Project, project["id"])
        assert reap_stale_runs(session, p, effective(session, p.id)) == 0

    _stale(chat_run)
    with db_session() as session:
        p = session.get(Project, project["id"])
        assert reap_stale_runs(session, p, effective(session, p.id)) == 1
        assert session.get(Run, chat_run).status == RunStatus.REAPED
        assert session.query(Notice).filter_by(
            kind=NoticeKind.AGENT_RUN_REAPED).count() == 0


def _fake_sandbox(monkeypatch):
    """In-memory stand-in for the docker layer: {project_id: run_id} plus the
    call log the tests assert on."""
    from yaaf.runner import sandbox

    state = {"containers": {}, "started": [], "stopped": []}
    monkeypatch.setattr(
        sandbox, "start_chat_container",
        lambda *, project_id, run_id, **kw: (state["started"].append(project_id),
                                             state["containers"].__setitem__(project_id, run_id),
                                             "cid")[2])
    monkeypatch.setattr(
        sandbox, "chat_container_run_ids",
        lambda project_id: ({state["containers"][project_id]}
                            if project_id in state["containers"] else set()))
    monkeypatch.setattr(
        sandbox, "stop_chat_container",
        lambda project_id: (state["stopped"].append(project_id),
                            state["containers"].pop(project_id, None), "")[2])
    return state


def _attach_billing(client, project_id: str) -> None:
    secret = client.post("/admin/secrets",
                         json={"project_id": project_id, "name": "anthropic-api-key",
                               "value": "sk-ant-api03-fake", "kind": "billing"},
                         headers=ADMIN).json()
    client.patch(f"/admin/projects/{project_id}",
                 json={"billing_mode": "api_key", "billing_secret_id": secret["id"]},
                 headers=ADMIN)


def test_supervisor_lifecycle(client, monkeypatch):
    """Start when running+billed, adopt-and-heartbeat while alive, end on
    pause, and hold off when boot-looping."""
    from yaaf.runner import chat

    state = _fake_sandbox(monkeypatch)
    project = make_project(client)
    _attach_billing(client, project["id"])

    chat.chat_supervisor_once()
    assert state["started"] == [project["id"]]
    with db_session() as session:
        run = session.query(Run).filter_by(kind="chat_session",
                                           status=RunStatus.RUNNING).one()
        first_beat = run.heartbeat_at
        run_id = run.id

    # Second tick: adopt, don't restart; the tick is the heartbeat.
    chat.chat_supervisor_once()
    assert state["started"] == [project["id"]]
    with db_session() as session:
        assert session.get(Run, run_id).heartbeat_at >= first_beat

    # Pause ends the session and says why.
    client.post(f"/admin/projects/{project['id']}/pause",
                json={"reason": "test"}, headers=ADMIN)
    chat.chat_supervisor_once()
    assert state["stopped"] == [project["id"]]
    with db_session() as session:
        ended = session.get(Run, run_id)
        assert ended.status == RunStatus.KILLED
        assert "paused" in (ended.summary or "")

    # Boot-loop backstop: resume, then burn the start budget for the window.
    client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN)
    for _ in range(3):
        chat.chat_supervisor_once()
        state["containers"].pop(project["id"], None)  # container "dies" instantly
        chat.chat_supervisor_once()
    starts_now = len(state["started"])
    chat.chat_supervisor_once()
    assert len(state["started"]) == starts_now  # held: too many starts in the window


def test_zombie_chat_container_is_replaced_never_adopted(client, monkeypatch):
    """The live failure this pins down: a deploy-time reap left a container
    running whose run row was dead. The old supervisor saw 'a container is
    running' and heartbeated the NEW run row forever — muting the boardroom,
    because the fresh heartbeat also suppressed the fallback wakes while the
    zombie polled with a dead token. Liveness must mean THE container for
    THIS run."""
    from yaaf.runner import chat

    state = _fake_sandbox(monkeypatch)
    project = make_project(client)
    _attach_billing(client, project["id"])

    chat.chat_supervisor_once()
    with db_session() as session:
        first = session.query(Run).filter_by(kind="chat_session",
                                             status=RunStatus.RUNNING).one().id

    # The deploy race: the run row dies while its container keeps running.
    with db_session() as session:
        session.get(Run, first).status = RunStatus.REAPED
    assert state["containers"][project["id"]] == first  # the zombie

    chat.chat_supervisor_once()
    assert state["stopped"] == [project["id"]]  # zombie cleared, not adopted
    with db_session() as session:
        replacement = session.query(Run).filter_by(kind="chat_session",
                                                   status=RunStatus.RUNNING).one()
        assert replacement.id != first
    assert state["containers"][project["id"]] == replacement.id

    # And a mismatched container (row live, container from another run) is
    # also rebuilt rather than heartbeated.
    state["containers"][project["id"]] = "run_somebody_else"
    chat.chat_supervisor_once()
    with db_session() as session:
        rebuilt = session.query(Run).filter_by(kind="chat_session",
                                               status=RunStatus.RUNNING).one()
        assert rebuilt.id != replacement.id
        assert session.get(Run, replacement.id).status == RunStatus.FAILED
    assert state["containers"][project["id"]] == rebuilt.id


def test_boot_backlog_is_answered_not_skipped(client, monkeypatch):
    """A chairman message that arrived while no session was reading must be
    answered by the next session, not filed as history — restart amnesia was
    how a live 'Hello?' went silently unanswered."""
    import importlib
    import sys

    monkeypatch.setenv("YAAF_API_BASE_URL", "http://api:8080")
    monkeypatch.setenv("YAAF_API_TOKEN", "test-token")
    monkeypatch.setenv("YAAF_RUN_ID", "run_test")
    monkeypatch.setenv("YAAF_TOOL_PERMISSIONS", "{}")
    import yaaf.runner.sandbox_entry as sandbox_entry_module

    sys.modules["sandbox_entry"] = importlib.reload(sandbox_entry_module)
    chat_entry = importlib.import_module("yaaf.runner.chat_entry")

    messages = [
        {"id": "m1", "from_user": "u1", "body": "old, already read"},
        {"id": "m2", "from_user": None, "body": "a CEO reply"},
        {"id": "m3", "from_user": "u1", "body": "unanswered while sessions were down"},
    ]
    history, backlog = chat_entry._split_backlog(messages, unread=2)
    assert [m["id"] for m in history] == ["m1"]
    assert [m["id"] for m in backlog] == ["m3"]  # the CEO's own message is not a backlog

    history, backlog = chat_entry._split_backlog(messages, unread=0)
    assert len(history) == 3 and backlog == []


def test_chat_harness_speaks_real_api_paths(client, monkeypatch):
    """The harness's hardcoded paths must be routes the app actually serves —
    the live failure this pins down: a plausible-looking path 404ing and being
    misread as a revoked token."""
    import importlib
    import sys

    monkeypatch.setenv("YAAF_API_BASE_URL", "http://api:8080")
    monkeypatch.setenv("YAAF_API_TOKEN", "test-token")
    monkeypatch.setenv("YAAF_RUN_ID", "run_test")
    monkeypatch.setenv("YAAF_TOOL_PERMISSIONS", "{}")
    import yaaf.runner.sandbox_entry as sandbox_entry_module

    # In the image both entrypoints sit in one directory; mirror that layout.
    sys.modules["sandbox_entry"] = importlib.reload(sandbox_entry_module)
    chat_entry = importlib.import_module("yaaf.runner.chat_entry")

    calls: list[tuple[str, str]] = []

    def record(method, path, payload=None, params=None):
        calls.append((method, path))
        return {"status": 200, "body": []}

    monkeypatch.setattr(chat_entry.base, "_call", record)
    chat_entry._boardroom_channel()
    chat_entry._read_messages("chan_x")
    chat_entry._set_typing("chan_x", 5)
    chat_entry._post_reply("chan_x", "hi")
    chat_entry._book_usage(1, 2, 3, 4, None)

    for method, path in calls:
        response = client.request(method, path)
        assert response.status_code != 404, \
            f"chat harness calls {method} {path}, which the app does not serve"


def test_socket_mode_trusts_exactly_one_human():
    cfg = {"channel": "C123", "chairman": "U777"}

    def envelope(**event) -> dict:
        return {"type": "events_api", "envelope_id": "e1",
                "payload": {"event": {"type": "message", "channel": "C123",
                                      "user": "U777", "text": "hi", "ts": "1.2",
                                      **event}}}

    assert chairman_text(cfg, envelope()) == ("hi", "1.2")
    assert chairman_text(cfg, envelope(user="U666")) is None          # not the chairman
    assert chairman_text(cfg, envelope(channel="C999")) is None       # wrong channel
    assert chairman_text(cfg, envelope(bot_id="B1")) is None          # a bot (even "as" them)
    assert chairman_text(cfg, envelope(subtype="message_changed")) is None  # an edit
    assert chairman_text(cfg, envelope(text="  ")) is None            # empty
    assert chairman_text(cfg, {"type": "hello"}) is None              # non-event
