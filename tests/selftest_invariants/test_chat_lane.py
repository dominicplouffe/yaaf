"""Invariant selftests: the chat lane — a resident CEO conversation that
answers in seconds without loosening a single governance rule.

The rules under attack here: chat never consumes the work lane's caps; a live
chat session owns the boardroom (no double replies from queued runs); a dead
one hands back to the fallback within a lease TTL; chat spend is metered the
moment it happens; and Slack socket mode trusts exactly one human."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from tests.conftest import ADMIN, amend, bearer, channel_of, live_token, make_project
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


def _chat_entry(monkeypatch):
    """chat_entry as the image lays it out: both entrypoints in one directory,
    so `sandbox_entry` is importable by bare name. Reloaded, on the SDK's own
    entry: a test elsewhere that reloads it onto another harness's driver
    leaves that driver in the module for whichever test imports it next."""
    import importlib
    import sys

    monkeypatch.setenv("YAAF_API_BASE_URL", "http://api:8080")
    monkeypatch.setenv("YAAF_API_TOKEN", "test-token")
    monkeypatch.setenv("YAAF_RUN_ID", "run_test")
    monkeypatch.setenv("YAAF_TOOL_PERMISSIONS", "{}")
    monkeypatch.delenv("YAAF_CHAT_ENTRY", raising=False)
    import yaaf.runner.sandbox_entry as sandbox_entry_module

    sys.modules["sandbox_entry"] = importlib.reload(sandbox_entry_module)
    import yaaf.runner.chat_entry as chat_entry_module

    return importlib.reload(chat_entry_module)


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
    amend(client, project["id"], max_concurrent_runs=1)
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
    secret = client.post('/admin/settings/llm-credentials', json={'name': 'anthropic-api-key', 'value': 'sk-ant-api03-fake', 'mode': 'api_key'}, headers=ADMIN).json()
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

    # Second tick: adopt, don't restart. The tick no longer WRITES the
    # heartbeat — the session stamps that through its own requests — so what
    # this pins is that a fresh session is left alone and not rebuilt.
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
    chat_entry = _chat_entry(monkeypatch)

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
    chat_entry = _chat_entry(monkeypatch)

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


def test_container_starts_only_after_the_run_is_visible(client, monkeypatch):
    """H1 of the restart churn: the container's first authenticated call must
    find the run row and token already committed. A probe standing in for the
    container checks visibility from its own DB connection at launch time."""
    from yaaf.runner import chat, sandbox

    seen = {}

    def probing_start(*, project_id, run_id, **kw):
        with db_session() as session:  # the container's-eye view
            run = session.get(Run, run_id)
            seen["visible"] = run is not None
            seen["running"] = run is not None and run.status == RunStatus.RUNNING
            seen["token"] = bool(run.token_hash) if run is not None else False
        return "cid"

    monkeypatch.setattr(sandbox, "start_chat_container", probing_start)
    monkeypatch.setattr(sandbox, "chat_container_run_ids", lambda pid: set())
    monkeypatch.setattr(sandbox, "stop_chat_container", lambda pid: "")
    project = make_project(client)
    _attach_billing(client, project["id"])
    chat.chat_supervisor_once()
    assert seen == {"visible": True, "running": True, "token": True}


def test_failed_container_start_fails_the_run(client, monkeypatch):
    from yaaf.runner import chat, sandbox

    monkeypatch.setattr(sandbox, "chat_container_run_ids", lambda pid: set())
    monkeypatch.setattr(sandbox, "stop_chat_container", lambda pid: "")

    def boom(**kw):
        raise RuntimeError("no docker today")

    monkeypatch.setattr(sandbox, "start_chat_container", boom)
    project = make_project(client)
    _attach_billing(client, project["id"])
    chat.chat_supervisor_once()
    with db_session() as session:
        run = session.query(Run).filter_by(kind="chat_session").one()
        assert run.status == RunStatus.FAILED
        assert "failed to start" in (run.summary or "")


def test_restart_backstop_ignores_kills_and_other_workers(client, monkeypatch):
    """Deliberate ends (pause/resume churn) and other workers' failures must
    not lock this box out of the chat lane."""
    from yaaf.runner import chat

    state = _fake_sandbox(monkeypatch)
    project = make_project(client)
    _attach_billing(client, project["id"])
    with db_session() as session:
        for worker, status in [(settings().worker_name, RunStatus.KILLED),
                               (settings().worker_name, RunStatus.KILLED),
                               ("elsewhere", RunStatus.FAILED),
                               ("elsewhere", RunStatus.FAILED),
                               ("elsewhere", RunStatus.FAILED)]:
            session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"],
                            agent_id=_ceo_id(project), kind="chat_session",
                            trigger={"type": "chat_session"}, status=status,
                            worker=worker))
    chat.chat_supervisor_once()
    assert state["started"] == [project["id"]]


def test_inactive_ceo_ends_the_session(client, monkeypatch):
    """A paused CEO used to leave a heartbeating run and a live container for
    the janitor to reap; now the supervisor ends the session itself."""
    from yaaf.core.models import Agent
    from yaaf.runner import chat

    state = _fake_sandbox(monkeypatch)
    project = make_project(client)
    _attach_billing(client, project["id"])
    chat.chat_supervisor_once()
    with db_session() as session:
        run_id = session.query(Run).filter_by(kind="chat_session",
                                              status=RunStatus.RUNNING).one().id
        session.get(Agent, _ceo_id(project)).status = "paused"
    chat.chat_supervisor_once()
    assert state["stopped"] == [project["id"]]
    with db_session() as session:
        ended = session.get(Run, run_id)
        assert ended.status == RunStatus.KILLED
        assert "no active CEO" in (ended.summary or "")


def test_chat_forensics_name_the_cause_of_death():
    """Exit code, OOM flag, and uptime turn four indistinguishable deaths into
    four different lines in the run summary."""
    from yaaf.runner import sandbox

    class FakeContainer:
        def __init__(self, exit_code, oom, log):
            self.attrs = {"State": {"ExitCode": exit_code, "OOMKilled": oom,
                                    "Error": "", "StartedAt": "2026-08-17T00:00:00Z",
                                    "FinishedAt": "2026-08-17T00:00:05Z"}}
            self._log = log

        def reload(self):
            pass

        def logs(self, **kw):
            return self._log.encode()

    oom = sandbox._chat_forensics(FakeContainer(137, True, "boom"))
    assert "exit=137" in oom and "oom=True" in oom and "boom" in oom
    clean = sandbox._chat_forensics(FakeContainer(0, False, "bye"))
    assert "exit=0" in clean and "bye" in clean


def test_stop_chat_container_keeps_every_containers_logs(monkeypatch):
    """The mismatch case has two corpses; discarding all but the last one's
    logs was hiding exactly the evidence that mattered."""
    import sys
    import types

    from yaaf.runner import sandbox

    class FakeContainer:
        def __init__(self, log):
            self.attrs = {"State": {"ExitCode": 1, "OOMKilled": False, "Error": "",
                                    "StartedAt": "s", "FinishedAt": "f"}}
            self._log = log

        def reload(self):
            pass

        def logs(self, **kw):
            return self._log.encode()

        def stop(self, timeout=None):
            pass

        def remove(self, force=False):
            pass

    fake_docker = types.SimpleNamespace()
    containers = types.SimpleNamespace(
        list=lambda all=False, filters=None: [FakeContainer("first corpse"),
                                              FakeContainer("second corpse")])
    fake_docker.from_env = lambda: types.SimpleNamespace(containers=containers)
    monkeypatch.setitem(sys.modules, "docker", fake_docker)
    combined = sandbox.stop_chat_container("proj_x")
    assert "first corpse" in combined and "second corpse" in combined


def test_chat_entry_failure_taxonomy(client, monkeypatch):
    """401/403 mean the token is dead; everything else is weather. And a turn
    whose ResultMessage says error must raise, not return a broken reply."""
    chat_entry = _chat_entry(monkeypatch)

    assert chat_entry._failure_kind(401) == "auth"
    assert chat_entry._failure_kind(403) == "auth"
    for status in (0, 404, 429, 500, 502):
        assert chat_entry._failure_kind(status) == "transient", status

    def result(is_error, subtype):
        return type("R", (), {"is_error": is_error, "subtype": subtype})()

    assert chat_entry._turn_result_ok(result(False, "success"))
    assert not chat_entry._turn_result_ok(result(True, "success"))
    assert not chat_entry._turn_result_ok(result(False, "error_max_turns"))
    assert not chat_entry._turn_result_ok(result(False, "error_during_execution"))


# ── a failed turn says why, and a transient one is waited out ───────────────

OVERLOADED = ("API Error: 529 Overloaded. This is a server-side issue, usually "
              "temporary — try again in a moment.")


class _ScriptExhausted(BaseException):
    """Deliberately not an Exception — the lane retries those. A test that runs
    off the end of its script must fail loudly rather than spin."""


class _ScriptedClient:
    """Stands in for ClaudeSDKClient. Every rebuild draws the next turn from
    one script, so the script is the whole session as the chairman sees it."""

    def __init__(self, turns, builds):
        self._turns, self._builds = turns, builds

    def __call__(self, options):  # ClaudeSDKClient(options)
        self._builds.append(options)
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        pass

    async def receive_response(self):
        if not self._turns:
            raise _ScriptExhausted("the lane took a turn the test did not script")
        for message in self._turns.pop(0):
            yield message


class _Clock:
    """Records a backoff instead of serving it: the schedule is the assertion,
    and the test costs no wall clock."""

    def __init__(self):
        self.slept: list[float] = []

    async def sleep(self, seconds):
        self.slept.append(seconds)


def _result(**overrides):
    from claude_agent_sdk import ResultMessage

    return ResultMessage(**{"subtype": "success", "duration_ms": 1,
                            "duration_api_ms": 1, "is_error": False, "num_turns": 1,
                            "session_id": "sess_1",
                            "usage": {"input_tokens": 10, "output_tokens": 5,
                                      "cache_creation_input_tokens": 0,
                                      "cache_read_input_tokens": 0},
                            **overrides})


def _died(text, status=529):
    """The live shape of an API failure: the CLI reports subtype "success",
    sets is_error, and puts the prose in `result`."""
    return [_result(is_error=True, api_error_status=status, result=text)]


def _broke():
    return [_result(is_error=True, subtype="error_during_execution")]


def _said(text):
    from claude_agent_sdk import AssistantMessage, TextBlock

    return [AssistantMessage(content=[TextBlock(text=text)], model="m"), _result()]


def _session(monkeypatch, tmp_path, turns, stop_after=1):
    """A whole chat session over a scripted SDK and a fake agent API. The
    chairman asks one more question per reply he gets, and his token is revoked
    once `stop_after` replies are posted, so the session ends as a real one does."""
    chat_entry = _chat_entry(monkeypatch)
    posted, builds, clock = [], [], _Clock()
    messages = [{"id": "m0", "from_user": "u1", "body": "SHOP.TO has been filled."}]

    def call(method, path, payload=None, params=None):
        if method == "GET" and path == "/agent/messages":
            if len(posted) >= stop_after:
                return {"status": 401, "body": {}}
            while len(messages) < len(posted) + 1:
                messages.append({"id": f"m{len(messages)}", "from_user": "u1",
                                 "body": "and the next one?"})
            return {"status": 200, "body": list(messages)}
        if method == "POST" and path == "/agent/messages":
            posted.append(payload["body"])
        if path == "/agent/channels":
            return {"status": 200,
                    "body": [{"id": "chan_x", "kind": "boardroom", "unread": 1}]}
        if path == "/agent/context":
            return {"status": 200, "body": {"situation": {"why": "running"}}}
        return {"status": 200, "body": {}}

    (tmp_path / "system_prompt.txt").write_text("You are the CEO.")
    monkeypatch.setattr(chat_entry.base, "STAGING", tmp_path)
    monkeypatch.setattr(chat_entry.base, "_call", call)
    monkeypatch.setattr(chat_entry, "ClaudeSDKClient", _ScriptedClient(turns, builds))
    monkeypatch.setattr(chat_entry, "asyncio", clock)
    return chat_entry, posted, builds, clock


def test_a_dead_turn_carries_the_api_error_not_its_subtype(monkeypatch):
    """A turn that dies on a 529 arrives as subtype "success" with is_error set,
    so naming the failure by subtype raised TurnFailed('success') — a phrase
    that told the chairman nothing and told the transient classifier, which
    reads the exception's text, that an overload was fatal."""
    chat_entry = _chat_entry(monkeypatch)
    monkeypatch.setattr(chat_entry.base, "_call",
                        lambda *a, **k: {"status": 200, "body": {}})

    with pytest.raises(chat_entry.TurnFailed) as caught:
        asyncio.run(chat_entry._run_turn(_ScriptedClient([_died(OVERLOADED)], []),
                                         "chan_x", "hi"))

    assert "529 Overloaded" in str(caught.value)
    assert chat_entry.base._looks_transient(caught.value)
    assert "529 Overloaded" in chat_entry._apology(caught.value, 3)
    # A credential failure is still fatal: retrying one only delays the pause.
    assert not chat_entry.base._looks_transient(
        chat_entry.TurnFailed("Not logged in: please run /login"))


def test_a_transient_turn_backs_off_and_still_answers(monkeypatch, tmp_path):
    """An overloaded API must cost a wait, not an apology. The old lane spent
    its one retry in the same second — two dead attempts inside a second and a
    "(TurnFailed)" — while the 529 had another ten minutes to run."""
    chat_entry, posted, builds, clock = _session(
        monkeypatch, tmp_path,
        [_died(OVERLOADED), _died(OVERLOADED), _said("Sold — 29 shares.")])

    assert asyncio.run(chat_entry.main()) == 0
    assert posted == ["Sold — 29 shares."]  # the answer he asked for, no apology
    assert [s for s in clock.slept
            if s in chat_entry.base.RESUME_BACKOFF_S] == [5.0, 20.0]
    assert len(builds) == 3  # a dead client is not reused: one per attempt


def test_a_fatal_turn_apologises_once_and_names_the_cause(monkeypatch, tmp_path):
    """A failure a retry cannot fix is still poison: one attempt, then an
    answer the chairman can act on rather than an error class name."""
    chat_entry, posted, builds, clock = _session(
        monkeypatch, tmp_path, [_died("Not logged in: please run /login", status=401)])

    assert asyncio.run(chat_entry.main()) == 0
    assert len(posted) == 1
    assert "Not logged in" in posted[0] and "tried once" in posted[0]
    assert [s for s in clock.slept if s in chat_entry.base.RESUME_BACKOFF_S] == []


def test_transient_retries_are_bounded_and_the_apology_counts_them(monkeypatch, tmp_path):
    """Three attempts is the work lane's bound and now the talk lane's. Past it
    the chairman is told what happened, not left watching typing dots."""
    chat_entry, posted, builds, clock = _session(
        monkeypatch, tmp_path, [_died(OVERLOADED)] * 3)

    assert asyncio.run(chat_entry.main()) == 0
    assert len(posted) == 1
    assert "3 times" in posted[0] and "529 Overloaded" in posted[0]
    # Three attempts, then one more client that only finds the token gone.
    assert len(builds) == 4


def test_a_session_that_keeps_answering_is_never_rebuilt_to_death(monkeypatch, tmp_path):
    """The rebuild cap exists to surface an SDK that is systematically broken,
    and it counted a lifetime: six bad turns spread over three days killed a
    session that had answered everything between them. It counts consecutive
    failures now — one answered turn is the proof the SDK works."""
    script = []
    for _ in range(6):
        script += [_broke(), _said("Answered.")]
    chat_entry, posted, builds, _ = _session(monkeypatch, tmp_path, script, stop_after=12)

    assert asyncio.run(chat_entry.main()) == 0  # 6 rebuilds > MAX_CLIENT_REBUILDS
    assert len(posted) == 12
    assert sum(1 for body in posted if "error_during_execution" in body) == 6


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


def test_a_wedged_chat_session_is_restarted_rather_than_adopted_forever(
        client, monkeypatch):
    """A container that is UP is not a container that is WORKING.

    The live failure, 2026-09-17: a session hung mid-turn on an outbound TCP
    handshake that never completed. The container stayed up, so the supervisor
    adopted it and stamped the heartbeat itself on every tick — for twenty
    minutes the run row read RUNNING with a heartbeat from the current second
    while nothing in that container was reading anything. The chairman sent two
    messages and then "hello?!", and because the fresh heartbeat also told
    `chat_session_live` the lane was healthy, the fallback work-run wakes that
    would have answered him the slow way never fired either. It took a human
    noticing.

    So the supervisor asks the session's own traffic instead. Silence long
    enough that it cannot be a slow turn is the same evidence as an exited
    container, and takes the same path.
    """
    from yaaf.runner import chat

    state = _fake_sandbox(monkeypatch)
    project = make_project(client)
    _attach_billing(client, project["id"])

    chat.chat_supervisor_once()
    with db_session() as session:
        run_id = session.query(Run).filter_by(kind="chat_session",
                                              status=RunStatus.RUNNING).one().id

    # Still inside the window: busy is not broken, and restarting a live turn
    # costs the chairman the answer he is waiting for.
    with db_session() as session:
        session.get(Run, run_id).heartbeat_at = utcnow() - timedelta(
            seconds=chat.CHAT_SILENCE_S / 2)
    chat.chat_supervisor_once()
    assert state["stopped"] == []
    with db_session() as session:
        assert session.get(Run, run_id).status == RunStatus.RUNNING

    # Past it, with the container still running: wedged.
    with db_session() as session:
        session.get(Run, run_id).heartbeat_at = utcnow() - timedelta(
            seconds=chat.CHAT_SILENCE_S + 30)
    chat.chat_supervisor_once()

    assert state["stopped"] == [project["id"]]
    with db_session() as session:
        dead = session.get(Run, run_id)
        assert dead.status == RunStatus.FAILED
        # Named as itself, not as the exited-container case it is not: the
        # summary is what the Runs page and `deaths.cause_of` read.
        assert "wedged" in (dead.summary or ""), dead.summary
    assert len(state["started"]) == 2, "a wedged session must be replaced"


def test_a_chat_session_stamps_its_own_heartbeat_by_calling_the_api(client):
    """The signal the supervisor now reads. A resident session polls the
    boardroom every couple of seconds while idle and refreshes typing on every
    streamed message while busy; both authenticate, so its own traffic is
    continuous proof of life and needs no extra endpoint to carry it."""
    project = make_project(client)
    run_id, token = live_token(project["id"], _ceo_id(project), kind="chat_session")
    _stale(run_id)
    with db_session() as session:
        before = session.get(Run, run_id).heartbeat_at

    assert client.get("/agent/context", headers=bearer(token)).status_code == 200

    with db_session() as session:
        assert session.get(Run, run_id).heartbeat_at > before


def test_a_work_run_does_not_stamp_a_chat_heartbeat(client):
    """Scoped to the chat lane. Work runs are leased, heartbeated and reaped by
    the daemon on its own schedule, and a request-driven heartbeat would let a
    chatty run outlive the lease the janitor is there to reclaim."""
    project = make_project(client)
    run_id, token = live_token(project["id"], _ceo_id(project))
    _stale(run_id)
    with db_session() as session:
        before = session.get(Run, run_id).heartbeat_at

    assert client.get("/agent/context", headers=bearer(token)).status_code == 200

    with db_session() as session:
        assert session.get(Run, run_id).heartbeat_at == before


def test_a_new_billing_credential_restarts_the_chat_session(client, monkeypatch):
    """The live failure: the chairman swapped an exhausted subscription for a
    funded one and the boardroom kept answering "session limit" — the resident
    container had the old key in its env and nothing ever replaced it. A new
    credential must end the session deliberately (KILLED, outside the restart
    backstop) and start one that pays with it."""
    from yaaf.runner import chat

    state = _fake_sandbox(monkeypatch)
    project = make_project(client)
    _attach_billing(client, project["id"])
    chat.chat_supervisor_once()
    with db_session() as session:
        first = session.query(Run).filter_by(kind="chat_session",
                                             status=RunStatus.RUNNING).one().id

    chat.chat_supervisor_once()  # nothing moved: adopted, not rebuilt
    assert state["started"] == [project["id"]]

    funded = client.post("/admin/settings/llm-credentials",
                         json={"name": "funded-key", "value": "sk-ant-api03-funded",
                               "mode": "api_key"}, headers=ADMIN).json()
    client.patch(f"/admin/projects/{project['id']}",
                 json={"billing_mode": "api_key", "billing_secret_id": funded["id"]},
                 headers=ADMIN)
    chat.chat_supervisor_once()
    assert state["stopped"] == [project["id"]]
    with db_session() as session:
        ended = session.get(Run, first)
        assert ended.status == RunStatus.KILLED
        assert "credential changed" in (ended.summary or "")
        replacement = session.query(Run).filter_by(kind="chat_session",
                                                   status=RunStatus.RUNNING).one()
    assert state["containers"][project["id"]] == replacement.id
    assert state["started"] == [project["id"], project["id"]]
