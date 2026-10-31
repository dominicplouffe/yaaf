"""Invariant selftests: the conversations (docs/RUNTIMES.md, M6) — the
boardroom chat held on the CEO's own seat, on any harness that can hold one.

The rule under attack is R7: a CEO seat holding the boardroom chat must be on
a harness with `conversation`. Every harness registered today can hold one, so
the rule is attacked against a registered harness that cannot — it reads the
registry, never a name — at the role version, the start, the door that turns
the chat on, the health checklist, and the supervisor's own backstop.

And the lane itself: the session is pinned to, priced for and paid by the
seat it talks as, and a harness other than the SDK holds it as resumed
attempts of its own driver — yaaf's loop by continuing its transcript.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types

import httpx
import pytest

from tests.conftest import ADMIN, ROLE_MIN, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Run, RunStatus
from yaaf.runtimes import harnesses
from yaaf.runtimes.harnesses import Harness

OFFLINE = {"files": True, "bash": False, "web": False}


@pytest.fixture()
def one_shot(monkeypatch):
    """A harness that runs work and cannot resume a session: everything a
    Claude harness can do but hold the boardroom."""
    monkeypatch.setitem(harnesses.HARNESSES, "one_shot", Harness(
        name="one_shot", label="One-shot harness", family="cli",
        providers=("anthropic",), accepts=("anthropic:api_key", "anthropic:subscription")))


def _role(client, slug: str, **fields):
    return client.post("/admin/roles", headers=ADMIN, json={
        "slug": slug, "name": slug.title(), **ROLE_MIN, **fields})


def _credential(client, name: str = "Key", **fields) -> str:
    response = client.post("/admin/settings/llm-credentials", headers=ADMIN, json={
        "name": name, "mode": "api_key", "value": "sk-ant-api03-test-value", **fields})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _fake_sandbox(monkeypatch) -> list[dict]:
    from yaaf.runner import sandbox

    launched: list[dict] = []
    monkeypatch.setattr(sandbox, "start_chat_container",
                        lambda **kw: (launched.append(kw), "cid")[1])
    monkeypatch.setattr(sandbox, "chat_container_run_ids", lambda project_id: set())
    monkeypatch.setattr(sandbox, "stop_chat_container", lambda project_id: "")
    return launched


# ── R7: the CEO's seat must be able to hold a conversation ──────────────────────


def test_a_ceo_role_cannot_be_written_onto_a_harness_that_cannot_converse(client, one_shot):
    make_project(client, start=False)
    refused = _role(client, "ceo", is_ceo_role=True, harness="one_shot")
    assert refused.status_code == 422
    assert "cannot hold the boardroom conversation" in refused.text
    # Any other seat may run there: R7 is about the chairman's real-time room.
    assert _role(client, "worker", harness="one_shot").status_code == 200


def test_a_project_whose_ceo_cannot_converse_does_not_start_with_the_chat_on(
        client, one_shot):
    make_project(client, start=False)
    project = make_project(client, slug="silent", start=False,
                           billing_secret_id=_credential(client))
    with db_session() as session:
        session.get(Agent, project["ceo_agent_id"]).harness_override = "one_shot"
    start = f"/admin/projects/{project['id']}/start"
    refused = client.post(start, headers=ADMIN)
    assert refused.status_code == 422
    assert "CEO: One-shot harness cannot hold the boardroom conversation" in refused.text
    # With the chat off the boardroom is answered by queued runs, which any
    # harness can take.
    assert client.patch(f"/admin/projects/{project['id']}", headers=ADMIN, json={
        "integrations": {"chat": {"enabled": False}}}).status_code == 200
    assert client.post(start, headers=ADMIN).status_code == 200
    # And it cannot come back on under the same seat.
    back_on = client.patch(f"/admin/projects/{project['id']}", headers=ADMIN,
                           json={"integrations": {"chat": {"enabled": True}}})
    assert back_on.status_code == 422 and "boardroom conversation" in back_on.text


def test_it_is_named_on_the_health_checklist(client, one_shot):
    make_project(client, start=False)
    project = make_project(client, slug="checked", billing_secret_id=_credential(client))
    with db_session() as session:
        session.get(Agent, project["ceo_agent_id"]).harness_override = "one_shot"
    seats = [c for c in client.get(f"/observe/projects/{project['id']}/health",
                                   headers=ADMIN).json()["checks"] if c["key"] == "seats"]
    assert seats and "cannot hold the boardroom conversation" in seats[0]["detail"]


def test_the_supervisor_holds_no_session_on_a_seat_that_cannot_converse(
        client, one_shot, monkeypatch):
    """The backstop for a seat changed behind every door: no session, so the
    boardroom falls back to queued runs — and the CEO's work runs are not
    refused for a rule about the chat."""
    from yaaf.runner import chat
    from yaaf.runner.daemon import _seat_problem

    launched = _fake_sandbox(monkeypatch)
    make_project(client, start=False)
    project = make_project(client, slug="backstop", billing_secret_id=_credential(client))
    chat.chat_supervisor_once()
    assert len(launched) == 1
    with db_session() as session:
        session.get(Agent, project["ceo_agent_id"]).harness_override = "one_shot"
    chat.chat_supervisor_once()
    with db_session() as session:
        ended = session.query(Run).filter_by(kind="chat_session").one()
        assert ended.status == RunStatus.KILLED
        assert "cannot hold a conversation" in ended.summary
        from yaaf.core.models import Project

        assert _seat_problem(session, session.get(Project, project["id"]),
                             session.get(Agent, project["ceo_agent_id"])) is None
    chat.chat_supervisor_once()
    assert len(launched) == 1


# ── the session runs on the CEO's own seat ─────────────────────────────────────


def test_a_claude_ceo_is_held_by_the_sdk_as_it_always_was(client, monkeypatch):
    from yaaf.runner import chat

    launched = _fake_sandbox(monkeypatch)
    make_project(client, start=False)
    make_project(client, slug="claude-ceo", billing_secret_id=_credential(client))
    chat.chat_supervisor_once()
    env = launched[0]["env"]
    assert env["YAAF_CHAT_ENTRY"] == "sandbox_entry.py"
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-api03-test-value"
    with db_session() as session:
        run = session.query(Run).filter_by(kind="chat_session").one()
        assert (run.provider, run.harness) == ("anthropic", "claude_agent_sdk")


def test_a_deepseek_ceo_talks_on_deepseek_priced_and_paid_as_its_runs_are(
        client, monkeypatch):
    from yaaf.runner import chat

    launched = _fake_sandbox(monkeypatch)
    make_project(client, start=False)
    assert client.put("/admin/settings/model-prices/deepseek-chat", headers=ADMIN,
                      json={"input": 0.28, "output": 0.42}).status_code == 200
    assert _role(client, "ceo", is_ceo_role=True, model="deepseek-chat",
                 tool_permissions=OFFLINE).status_code == 200
    deepseek = _credential(client, "DeepSeek key", provider="deepseek",
                           value="sk-deepseek-test-value")
    created = client.post("/admin/projects", headers=ADMIN, json={
        "slug": "deepseek-ceo", "name": "DeepSeek CEO", "chairman_email": "chair@example.com",
        "goal": "Prove the framework works end to end.", "ceo_role_slug": "ceo",
        "ceo_name": "CEO", "billing_secret_id": deepseek,
        "teams": [{"role_slug": "worker", "name": "Alpha", "employees": ["Lead"],
                   "model_overrides": {"Lead": "deepseek-chat"}}]})
    assert created.status_code == 200, created.text
    started = client.post(f"/admin/projects/{created.json()['id']}/start", headers=ADMIN)
    assert started.status_code == 200, started.text
    chat.chat_supervisor_once()
    env = launched[0]["env"]
    assert env["YAAF_CHAT_ENTRY"] == "loop_entry.py"
    assert env["DEEPSEEK_API_KEY"] == "sk-deepseek-test-value"
    assert "ANTHROPIC_API_KEY" not in env
    assert json.loads(env["YAAF_PRICE"])["output"] == 0.42
    assert json.loads(env["YAAF_PROVIDER"])["base_url"] == "https://api.deepseek.com/v1"
    with db_session() as session:
        run = session.query(Run).filter_by(kind="chat_session").one()
        assert (run.model, run.provider, run.harness) == (
            "deepseek-chat", "deepseek", "yaaf_loop")


# ── the sandbox side: a conversation on any driver ──────────────────────────────


def _chat_entry(monkeypatch, entry: str = "sandbox_entry.py", **env):
    for name, value in {"YAAF_API_BASE_URL": "http://api:8080", "YAAF_API_TOKEN": "t",
                        "YAAF_RUN_ID": "run_chat", "YAAF_TOOL_PERMISSIONS": "{}",
                        "YAAF_CHAT_ENTRY": entry, **env}.items():
        monkeypatch.setenv(name, value)
    import yaaf.runner.sandbox_entry as sandbox_entry_module

    monkeypatch.setitem(sys.modules, "sandbox_entry", importlib.reload(sandbox_entry_module))
    import yaaf.runner.chat_entry as chat_entry_module

    return importlib.reload(chat_entry_module)


class _ScriptedDriver:
    """A driver whose every attempt is one turn of a script, recording the
    prompt and the session it was asked to resume."""

    def __init__(self, base, turns: list[str], fullness: float | None = None):
        self.base, self.turns, self.attempts = base, turns, []
        self._fullness = fullness

    async def attempt(self, prompt, resume=None):
        self.attempts.append({"prompt": prompt, "resume": resume})
        yield self.base.Session("thread-1")
        yield self.base.Text(self.turns.pop(0))
        yield self.base.AttemptResult(cost_usd=0.002, tokens_in=40, tokens_out=8,
                                      cache_creation=0, cache_read=100)

    def error_kind(self, exc):
        return None

    def fullness(self):
        return self._fullness


def test_a_driver_holds_the_boardroom_one_resumed_attempt_per_turn(monkeypatch, tmp_path):
    chat_entry = _chat_entry(monkeypatch, entry="scripted_entry.py")
    driver = _ScriptedDriver(chat_entry.base, ["Two shipped.", "The third ships Friday."])
    monkeypatch.setitem(sys.modules, "scripted_entry", types.SimpleNamespace(
        make_driver=lambda system, path: driver))
    posted, booked = [], []
    messages = [{"id": "m0", "from_user": "u1", "body": "How many shipped?"}]

    def call(method, path, payload=None, params=None):
        if method == "GET" and path == "/agent/messages":
            if len(posted) >= 2:
                return {"status": 401, "body": {}}
            if len(posted) == 1 and len(messages) == 1:
                messages.append({"id": "m1", "from_user": "u1", "body": "And the third?"})
            return {"status": 200, "body": list(messages)}
        if method == "POST" and path == "/agent/messages":
            posted.append(payload["body"])
        if path == "/agent/chat_usage":
            booked.append(payload)
        if path == "/agent/channels":
            return {"status": 200, "body": [{"id": "chan", "kind": "boardroom", "unread": 1}]}
        return {"status": 200, "body": {}}

    (tmp_path / "system_prompt.txt").write_text("You are the CEO.")
    monkeypatch.setattr(chat_entry.base, "STAGING", tmp_path)
    monkeypatch.setattr(chat_entry.base, "_call", call)

    assert asyncio.run(chat_entry.main()) == 0
    assert posted == ["Two shipped.", "The third ships Friday."]
    # The second turn resumes the session the first one named.
    assert [a["resume"] for a in driver.attempts] == [None, "thread-1"]
    assert "And the third?" in driver.attempts[1]["prompt"]
    assert [(b["tokens_in"], b["cache_read_tokens"], b["cost_usd"]) for b in booked] == [
        (40, 100, 0.002), (40, 100, 0.002)]


PRICE = {"input": 0.28, "output": 0.42, "cache_read": 0.028, "cache_write_5m": 0.0,
         "cache_write_1h": 0.0, "web_search": 0.01}
DEEPSEEK = {"name": "deepseek", "protocol": "openai_chat",
            "base_url": "https://api.deepseek.com/v1", "key_env": "DEEPSEEK_API_KEY"}


def _loop_modules(monkeypatch, chat_entry):
    """yaaf's loop and its wire client, as the image stages them beside the
    chat entry, with the platform and the toolbox stubbed out."""
    import yaaf.runner.loop_harness as loop_module

    monkeypatch.setitem(sys.modules, "loop_harness", importlib.reload(loop_module))
    import yaaf.runner.loop_entry as api_module

    api = importlib.reload(api_module)
    monkeypatch.setattr(chat_entry.base, "_call", lambda *a, **k: {"status": 200, "body": {}})
    monkeypatch.setattr(chat_entry.base, "_builtin_tools", lambda: [])
    monkeypatch.setattr(chat_entry.base, "_external_mcp_servers", lambda: {})
    return sys.modules["loop_harness"], api


def _streamed(reply: str, prompt_tokens: int = 100) -> httpx.Response:
    """One finished reply on the OpenAI chat shape, as DeepSeek streams it."""
    lines = [{"choices": [{"index": 0, "delta": {"content": reply}}]},
             {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
             {"choices": [], "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 5}}]
    body = "".join(f"data: {json.dumps(line)}\n\n" for line in lines) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=body.encode())


def test_yaafs_loop_carries_the_conversation_in_its_transcript(monkeypatch):
    """The loop resumes nothing on disk: its transcript is the session. A
    finished reply followed by the chairman's next word is a new turn on the
    same transcript — not the interrupted call made again."""
    chat_entry = _chat_entry(
        monkeypatch, entry="openai_compatible_entry.py", YAAF_MODEL="deepseek-chat",
        YAAF_PRICE=json.dumps(PRICE), DEEPSEEK_API_KEY="sk-test",
        YAAF_PROVIDER=json.dumps(DEEPSEEK))
    loop, api = _loop_modules(monkeypatch, chat_entry)
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return _streamed(["Two shipped.", "Friday."][len(sent) - 1])

    client = api.WireClient(model="deepseek-chat", spec=chat_entry.base.PROVIDER, effort=None,
                            web_search=False, transport=httpx.MockTransport(handler))
    conversation = chat_entry.DriverConversation(loop.LoopDriver(client, "You are the CEO."))

    async def talk():
        return [await conversation.turn("chan", "How many shipped?"),
                await conversation.turn("chan", "And the third?")]

    assert asyncio.run(talk()) == ["Two shipped.", "Friday."]
    assert [(m["role"], m["content"]) for m in sent[1]["messages"][1:]] == [
        ("user", "How many shipped?"), ("assistant", "Two shipped."),
        ("user", "And the third?")]


# ── a long conversation stays on what the chairman just said ──────────────────
#
# Seen live on 10/31: a DeepSeek CEO's session opened on "when was post 2070
# written?". Each turn re-read ~10k tokens of state, so yaaf's loop filled its
# window every few turns and summarised — on the transcript's FIRST turn. The
# chairman asked about the post he had commissioned and was told about 2070,
# three times in an hour, and one instruction of his was never acted on.


def test_a_conversation_compacted_mid_turn_answers_the_latest_words(monkeypatch):
    """The loop's summary is written on the request being answered — in a
    conversation the chairman's latest line, never the session's first."""
    chat_entry = _chat_entry(
        monkeypatch, entry="openai_compatible_entry.py", YAAF_MODEL="deepseek-chat",
        YAAF_PRICE=json.dumps(PRICE), DEEPSEEK_API_KEY="sk-test",
        YAAF_PROVIDER=json.dumps(DEEPSEEK))
    loop, api = _loop_modules(monkeypatch, chat_entry)
    monkeypatch.setattr(chat_entry.base, "MODEL_FLAGS", {"context": 1_000})
    sent: list[dict] = []
    # The first reply fills the window, so the next turn is summarised before
    # it is answered: the summary call, then the answer on top of it.
    script = [("Oct 26.", 900), ("The chairman asked about 2070; now he asks about 2111.", 50),
              ("2111 is live.", 300)]

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return _streamed(*script[len(sent) - 1])

    client = api.WireClient(model="deepseek-chat", spec=chat_entry.base.PROVIDER, effort=None,
                            web_search=False, transport=httpx.MockTransport(handler))
    conversation = chat_entry.DriverConversation(loop.LoopDriver(client, "You are the CEO."))

    async def talk():
        first = await conversation.turn("chan", "When was post 2070 written?")
        assert conversation.spent(), "a conversation this full starts over from the boardroom"
        return [first, await conversation.turn("chan", "Is the Claude Code post live?")]

    assert asyncio.run(talk()) == ["Oct 26.", "2111 is live."]
    [answered] = [m["content"] for m in sent[2]["messages"] if m["role"] == "user"]
    assert answered.startswith("Is the Claude Code post live?")
    assert "When was post 2070 written?" not in answered


def test_a_reprimed_conversation_hears_each_line_once(monkeypatch, tmp_path):
    """A driver that fills up starts over from the boardroom, primed with
    the record — its own last reply included — and a line the chairman sent
    in the gap arrives once, as what he just said: never also in the history
    it is told not to re-answer."""
    chat_entry = _chat_entry(monkeypatch, entry="scripted_entry.py")
    monkeypatch.setattr(chat_entry, "POLL_S", 0)
    drivers = [_ScriptedDriver(chat_entry.base, ["Two shipped."], fullness=0.9),
               _ScriptedDriver(chat_entry.base, ["Friday."], fullness=0.1)]
    made = iter(drivers)
    monkeypatch.setitem(sys.modules, "scripted_entry", types.SimpleNamespace(
        make_driver=lambda system, path: next(made)))
    posted: list[str] = []
    messages = [{"id": "m0", "from_user": "u1", "body": "How many shipped?"}]

    def call(method, path, payload=None, params=None):
        if method == "GET" and path == "/agent/messages":
            return {"status": 401, "body": {}} if len(posted) >= 2 \
                else {"status": 200, "body": list(messages)}
        if method == "POST" and path == "/agent/messages":
            posted.append(payload["body"])
            messages.append({"id": f"r{len(posted)}", "from_agent": "ceo",
                             "body": payload["body"]})
            if len(posted) == 1:
                messages.append({"id": "m1", "from_user": "u1", "body": "And the third?"})
        if path == "/agent/channels":
            return {"status": 200, "body": [{"id": "chan", "kind": "boardroom", "unread": 1}]}
        return {"status": 200, "body": {}}

    (tmp_path / "system_prompt.txt").write_text("You are the CEO.")
    monkeypatch.setattr(chat_entry.base, "STAGING", tmp_path)
    monkeypatch.setattr(chat_entry.base, "_call", call)

    assert asyncio.run(chat_entry.main()) == 0
    assert posted == ["Two shipped.", "Friday."]
    [primed] = drivers[1].attempts
    assert primed["resume"] is None, "a re-prime is a new conversation, not a resumed one"
    history, said = primed["prompt"].split("The chairman just said:")
    assert "Chairman: How many shipped?" in history and "You (CEO): Two shipped." in history
    assert "And the third?" not in history
    assert said.strip() == "Chairman [m1]: And the third?"


# ── the Founder and the grader, on the platform key, whatever its provider ─────

#: A project the Founder's own draft check accepts.
DRAFT = {
    "slug": "sell-something", "name": "Sell Something",
    "goal": "Find a product worth building and take money from strangers by 31 December. "
            "Revenue through the payment processor's signed webhook is the only result "
            "that counts; signups and traffic are not.",
    "mode": "explore", "ceo_role_slug": "ceo", "ceo_name": "Founder",
    "teams": [{"role_slug": "worker", "name": "Build", "employees": ["Mika"], "lead": "Mika"}],
    "constitution": {"monthly_usd_cap": 120.0},
}
PLATFORM = "/admin/settings/anthropic"


def _chat_api(monkeypatch, replies: list[dict]) -> list[dict]:
    """The OpenAI-compatible chat API the Founder reaches on an OpenAI-shaped
    provider, scripted: each request gets the next message, streamed the way
    the shape streams it — content, then each tool call, then usage."""
    from yaaf.runtimes import wire

    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"url": str(request.url), "auth": request.headers.get("authorization"),
                     **json.loads(request.content)})
        reply = replies[len(sent) - 1]
        if "status" in reply:
            return httpx.Response(reply["status"], json=reply["body"])
        delta = {k: v for k, v in reply.items() if k != "tool_calls"}
        lines = [{"choices": [{"index": 0, "delta": delta}]}]
        lines += [{"choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, **call}]}}]}
                  for i, call in enumerate(reply.get("tool_calls") or [])]
        lines += [{"choices": [{"index": 0, "delta": {}, "finish_reason":
                                "tool_calls" if reply.get("tool_calls") else "stop"}]},
                  {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}]
        body = "".join(f"data: {json.dumps(line)}\n\n" for line in lines) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=body.encode())

    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(handler))
    return sent


def _deepseek_founder(client) -> None:
    client.post("/admin/users", headers=ADMIN, json={
        "email": "chair@example.com", "display_name": "Chair", "is_admin": True})
    from tests.conftest import seed_role

    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")
    installed = client.put(PLATFORM, headers=ADMIN, json={
        "provider": "deepseek", "value": "sk-deepseek-platform", "model": "deepseek-chat"})
    assert installed.status_code == 200, installed.text


def _settle(client, message: str) -> dict:
    import time

    thread_id = client.post("/admin/founder/threads", headers=ADMIN,
                            json={"message": message}).json()["id"]
    for _ in range(200):
        state = client.get(f"/admin/founder/threads/{thread_id}", headers=ADMIN).json()
        if not state["running"]:
            return state
        time.sleep(0.05)
    raise AssertionError("the turn never settled")


def test_the_founder_drafts_on_deepseek_through_its_chat_api(client, monkeypatch):
    _deepseek_founder(client)
    sent = _chat_api(monkeypatch, [
        {"content": None, "reasoning_content": "thinking beside the call",
         "tool_calls": [{"id": "c1", "type": "function", "function": {
             "name": "propose_bundle", "arguments": json.dumps({"project": DRAFT})}}]},
        {"content": "Explore, because you are still choosing what to build."},
    ])
    thread = _settle(client, "I want to find an idea and sell it.")
    assert thread["last_error"] is None, thread
    assert thread["draft_bundle"]["project"]["slug"] == "sell-something"
    first, second = sent
    assert first["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert first["auth"] == "Bearer sk-deepseek-platform"
    assert first["model"] == "deepseek-chat"
    assert first["messages"][0]["role"] == "system"
    # The Founder's seven tools and nothing else — no built-in of any kind.
    from yaaf.founder.tools import TOOL_NAMES

    assert sorted(t["function"]["name"] for t in first["tools"]) == sorted(TOOL_NAMES)
    tool_result = json.loads(second["messages"][-1]["content"])
    assert second["messages"][-1]["role"] == "tool" and tool_result["ok"] is True
    # DeepSeek's thinking mode refuses a tool loop that drops the reasoning
    # streamed beside a call (this asserted R1's opposite rule until the API
    # reversed it), so it goes back on the turn it came with.
    [called] = [m for m in second["messages"] if m.get("tool_calls")]
    assert called["reasoning_content"] == "thinking beside the call"


def test_the_grader_runs_on_the_same_credential(monkeypatch):
    from yaaf.founder.grade import grade_goal

    sent = _chat_api(monkeypatch, [{"content": json.dumps({"elements": [
        {"element": "finish_line", "verdict": "present", "note": "named"}],
        "summary": "fine"})}])
    grade = asyncio.run(grade_goal("Sell it by Friday.", env={
        "CUSTOM_LLM_API_KEY": "local", "CUSTOM_LLM_BASE_URL": "http://ollama:11434/v1"},
        model="custom/llama3.1:8b"))
    assert grade.summary == "fine"
    # A custom server: its address is the credential's, the provider off the name.
    assert sent[0]["url"] == "http://ollama:11434/v1/chat/completions"
    assert sent[0]["model"] == "llama3.1:8b" and "tools" not in sent[0]


def test_a_dead_platform_key_on_another_provider_says_auth(monkeypatch):
    from yaaf.founder.session import FounderModelError, run_once

    _chat_api(monkeypatch, [{"status": 401, "body": {"error": {
        "message": "Authentication Fails, Your api key is invalid"}}}])
    with pytest.raises(FounderModelError) as caught:
        asyncio.run(run_once(prompt="ready?", system="check", model="deepseek-chat",
                             env={"DEEPSEEK_API_KEY": "dead"}))
    assert caught.value.kind == "auth" and "Authentication Fails" in str(caught.value)


@pytest.mark.parametrize(("body", "says"), [
    # The model is the Founder's road; the credential pays it — one decision.
    ({"provider": "deepseek", "value": "sk-deepseek-platform", "model": "gpt-5.5"},
     "gpt-5.5, which is OpenAI's"),
    # A ChatGPT login pays the Codex CLI and nothing that runs in this process.
    ({"provider": "openai", "mode": "subscription", "value": '{"tokens": {"a": 1}}',
      "model": "gpt-5.5"}, "takes an API key"),
    ({"provider": "custom", "value": "local-key", "model": "custom/llama3.1:8b"},
     "carries the server's address"),
])
def test_a_platform_credential_the_founder_cannot_run_on_is_refused(client, body, says):
    refused = client.put(PLATFORM, headers=ADMIN, json=body)
    assert refused.status_code == 422 and says in refused.text, refused.text


def test_a_key_is_enough_where_the_provider_has_a_frontier_model(client):
    """The Founder's model is a class (docs/PROVIDERS.md §7): pasting a DeepSeek
    key with no model named gives a Founder on DeepSeek's frontier model —
    where it used to be refused for defaulting to Claude's."""
    installed = client.put(PLATFORM, headers=ADMIN, json={
        "provider": "deepseek", "value": "sk-deepseek-platform"})
    assert installed.status_code == 200, installed.text
    assert installed.json()["model"] == "deepseek-reasoner"
    shown = client.get("/admin/settings", headers=ADMIN).json()["anthropic"]
    assert (shown["model"], shown["model_ref"]) == ("deepseek-reasoner", "@frontier")


def test_a_provider_with_no_frontier_model_needs_the_founders_model_named(client):
    refused = client.put(PLATFORM, headers=ADMIN, json={
        "provider": "groq", "value": "gsk-platform-key"})
    assert refused.status_code == 422
    assert "Groq has no Frontier model set" in refused.text
    named = client.put(PLATFORM, headers=ADMIN, json={
        "provider": "groq", "value": "gsk-platform-key", "model": "groq/kimi-k2-instruct"})
    assert named.status_code == 200, named.text


def test_the_founders_model_cannot_drift_from_the_credential(client):
    _deepseek_founder(client)
    refused = client.put(f"{PLATFORM}/model", headers=ADMIN, json={"model": "claude-opus-5-5"})
    assert refused.status_code == 422 and "pays DeepSeek" in refused.text
    assert client.put("/admin/founder/model", headers=ADMIN,
                      json={"model": "claude-opus-5-5"}).status_code == 422
    assert client.put(f"{PLATFORM}/model", headers=ADMIN,
                      json={"model": "deepseek-reasoner"}).status_code == 200
    shown = client.get("/admin/settings", headers=ADMIN).json()["anthropic"]
    assert (shown["provider"], shown["model"]) == ("deepseek", "deepseek-reasoner")
    from yaaf.core import platform

    with db_session() as session:
        assert platform.founder_env(session) == {"DEEPSEEK_API_KEY": "sk-deepseek-platform"}


def test_a_custom_servers_address_travels_with_the_platform_key(client):
    installed = client.put(PLATFORM, headers=ADMIN, json={
        "provider": "custom", "value": "local-key", "base_url": "http://ollama:11434/v1/",
        "model": "custom/llama3.1:8b"})
    assert installed.status_code == 200, installed.text
    from yaaf.core import platform

    with db_session() as session:
        assert platform.founder_env(session) == {
            "CUSTOM_LLM_API_KEY": "local-key", "CUSTOM_LLM_BASE_URL": "http://ollama:11434/v1"}
