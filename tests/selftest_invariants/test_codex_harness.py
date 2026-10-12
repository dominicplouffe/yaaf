"""Invariant selftests: the Codex CLI harness, held to the contract every harness
signs (docs/RUNTIMES.md, M4).

The streams replayed here are the real CLI's (0.162.1) — recorded against a
stand-in for the Responses API, because the live one was not reachable from
where they were recorded (tests/fixtures/harnesses/codex/README.md). The CLI is
replaced by a stand-in binary that prints a recorded stream, writes the session
file the real one writes as it goes, and exits as the real one did. Offline
and free.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import pathlib
import sys
import tomllib
from types import SimpleNamespace

import pytest

pytest.importorskip("claude_agent_sdk")

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "harnesses" / "codex"
#: A price an install might set for an OpenAI model, per million tokens.
PRICE = {"input": 1.0, "output": 8.0, "cache_read": 0.1, "cache_write_5m": 0.0,
         "cache_write_1h": 0.0, "web_search": 0.01, "as_of": "2026-10-12"}

#: Stands in for `codex`: records its argv, stdin and config; writes each
#: thread's session file from the step's `rollout` lines; prints the recorded
#: stream; optionally hangs (the governor's case); exits as recorded.
FAKE_CODEX = """#!PYTHON
import json, os, pathlib, sys, time
state = pathlib.Path(os.environ["FAKE_CODEX_STATE"])
n = len(list(state.glob("call-*.json")))
step = json.loads(os.environ["FAKE_CODEX_SCRIPT"])[n]
home = pathlib.Path(os.environ["CODEX_HOME"])
(state / f"call-{n}.json").write_text(json.dumps({
    "argv": sys.argv[1:], "stdin": sys.stdin.read(), "pid": os.getpid(),
    "config": (home / "config.toml").read_text(),
    "env": {k: v for k, v in os.environ.items() if k in (
        "CODEX_HOME", "OPENAI_API_KEY", "CODEX_AUTH_JSON")}}))
lines = pathlib.Path(step["fixture"]).read_text().splitlines()
sessions = home / "sessions" / "2026" / "10" / "12"
sessions.mkdir(parents=True, exist_ok=True)
for thread, rollout in step.get("rollouts", {}).items():
    with (sessions / f"rollout-2026-10-12T00-00-00-{thread}.jsonl").open("a") as f:
        for record in rollout:
            f.write(json.dumps(record) + "\\n")
for line in lines:
    print(line, flush=True)
if step.get("hang"):
    time.sleep(3600)
sys.exit(step.get("exit", 0))
"""


def _records(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines() if line]


def _thread(name: str) -> str:
    return _records(name)[0]["thread_id"]


def _usage(inp: int, cached: int, out: int) -> dict:
    return {"type": "event_msg", "payload": {"type": "token_count", "info": {
        "total_token_usage": {"input_tokens": inp, "cached_input_tokens": cached,
                              "cache_write_input_tokens": 0, "output_tokens": out,
                              "reasoning_output_tokens": 0, "total_tokens": inp + out}}}}


def _staged(monkeypatch, tmp_path, perms: dict | None = None, env: dict | None = None):
    for name, value in {"YAAF_API_BASE_URL": "http://api:8080", "YAAF_API_TOKEN": "test-token",
                        "YAAF_RUN_ID": "run_test", "YAAF_MODEL": "gpt-6-sol",
                        "YAAF_EFFORT": "high", "YAAF_AGENT_KIND": "employee",
                        "YAAF_TOOL_PERMISSIONS": json.dumps(perms or {}),
                        "OPENAI_API_KEY": "sk-test-openai", **(env or {})}.items():
        monkeypatch.setenv(name, value)
    import yaaf.runner.sandbox_entry as entry_module

    base = importlib.reload(entry_module)
    monkeypatch.setitem(sys.modules, "sandbox_entry", base)
    import yaaf.runner.codex_cli_entry as codex_module

    codex = importlib.reload(codex_module)
    workspace = tmp_path / "workspace"
    staging = workspace / ".yaaf"
    staging.mkdir(parents=True)
    monkeypatch.setattr(base, "WORKSPACE", workspace)
    monkeypatch.setattr(base, "STAGING", staging)
    monkeypatch.setattr(base, "CREDENTIAL_DIR", tmp_path / "credentials")
    monkeypatch.setattr(base, "RESUME_BACKOFF_S", (0.0, 0.0))
    monkeypatch.setattr(base, "PRICE", PRICE)
    monkeypatch.setattr(codex, "WATCH_INTERVAL_S", 0.02)
    (staging / "system_prompt-run_test.txt").write_text("You are the critic.")
    (staging / "context-run_test.json").write_text("{}")
    return SimpleNamespace(base=base, codex=codex, staging=staging, tmp=tmp_path,
                           mp=monkeypatch, workspace=workspace)


@pytest.fixture()
def stage(monkeypatch, tmp_path):
    return lambda **kw: _staged(monkeypatch, tmp_path, **kw)


@pytest.fixture()
def run(stage):
    return stage()


def _drive(r, script: list[dict]) -> tuple[int, dict, list[dict]]:
    state = r.tmp / "fake-codex"
    state.mkdir(exist_ok=True)
    binary = r.tmp / "codex"
    binary.write_text(FAKE_CODEX.replace("PYTHON", sys.executable))
    binary.chmod(0o755)
    r.mp.setattr(r.codex, "CODEX", str(binary))
    r.mp.setenv("FAKE_CODEX_STATE", str(state))
    r.mp.setenv("FAKE_CODEX_SCRIPT", json.dumps(
        [{**step, "fixture": str(FIXTURES / step["fixture"])} for step in script]))
    # Bounded: the governor's case hangs the stand-in on purpose, and a
    # governor that failed to stop it must fail this test, not stall the suite.
    code = asyncio.run(asyncio.wait_for(r.base.run_harness(r.codex.CodexCliDriver), 30))
    calls = [json.loads(p.read_text()) for p in sorted(state.glob("call-*.json"))]
    for call in calls:
        call["toml"] = tomllib.loads(call["config"])
    return code, json.loads((r.staging / "result-run_test.json").read_text()), calls


def _cost(inp: int, cached: int, out: int, searches: int = 0) -> float:
    return ((inp - cached) * PRICE["input"] + cached * PRICE["cache_read"]
            + out * PRICE["output"]) / 1_000_000 + searches * PRICE["web_search"]


# ── 1. usage parses to the bill ─────────────────────────────────────────────────


def test_a_reply_is_metered_and_priced(run):
    code, result, calls = _drive(run, [{"fixture": "text_reply.jsonl"}])
    assert code == 0 and result["ok"] is True and result["error_kind"] is None
    # input_tokens counts the cached part; the bill does not count it twice.
    assert (result["tokens_in"], result["cache_read_tokens"]) == (200, 1000)
    assert result["tokens_out"] == 40
    assert result["cost_usd"] == pytest.approx(_cost(1200, 1000, 40))
    assert result["priced_usd"] == pytest.approx(result["cost_usd"])
    assert "Hi! What are we working on today?" in result["summary"]
    assert calls[0]["argv"][:3] == ["exec", "--json", "--skip-git-repo-check"]
    assert calls[0]["argv"][-1] == "-" and "Do ONE unit of work" in calls[0]["stdin"]


def test_a_web_search_is_counted_and_priced(run):
    _, result, _ = _drive(run, [{"fixture": "web_search.jsonl"}])
    assert result["cost_usd"] == pytest.approx(_cost(1200, 1000, 40, searches=1))


def test_every_thread_is_billed_sub_agents_included(run):
    """The models Codex knows best are given sub-agents whatever the config
    says, and the turn's own figure covers only its own thread. The session
    files cover them all, and the larger is the bill."""
    main, helper = _thread("text_reply.jsonl"), "01a130ff-0000-7000-8000-00000000beef"
    _, result, _ = _drive(run, [{"fixture": "text_reply.jsonl", "rollouts": {
        main: [_usage(1200, 1000, 40)], helper: [_usage(5000, 0, 300)]}}])
    assert result["tokens_out"] == 340
    assert result["cost_usd"] == pytest.approx(_cost(6200, 1000, 340))


def test_tool_calls_are_traced(run):
    _drive(run, [{"fixture": "tool_call.jsonl"}])
    trace = (run.staging / "trace-run_test.jsonl").read_text()
    assert '"type": "mcp_tool_call"' in trace and '\\"tool\\": \\"board\\"' in trace


# ── 4. a dead credential reads as one ───────────────────────────────────────────


def test_a_dead_key_is_auth_and_is_never_retried(run):
    code, result, calls = _drive(run, [{"fixture": "invalid_key.jsonl", "exit": 1},
                                       {"fixture": "text_reply.jsonl"}])
    assert code == 1 and result["error_kind"] == "auth"
    assert len(calls) == 1, "a dead key was retried"
    assert "401 Unauthorized" in result["summary"]


# ── 5. a transient failure resumes the same thread ──────────────────────────────


def test_an_overloaded_api_resumes_the_same_thread(run, tmp_path):
    thread = _thread("text_reply.jsonl")
    failed = tmp_path / "overloaded.jsonl"
    failed.write_text("\n".join(json.dumps(r) for r in [
        {"type": "thread.started", "thread_id": thread}, {"type": "turn.started"},
        {"type": "turn.failed", "error": {"message": "unexpected status 503 Service "
                                                     "Unavailable: overloaded"}}]) + "\n")
    code, result, calls = _drive(run, [
        {"fixture": str(failed), "exit": 1, "rollouts": {thread: [_usage(900, 0, 20)]}},
        {"fixture": "text_reply.jsonl", "rollouts": {thread: [_usage(2100, 1000, 60)]}}])
    assert code == 0 and result["ok"] is True, result["summary"]
    assert len(calls) == 2
    assert calls[1]["argv"][-3:] == ["resume", thread, "-"]
    assert calls[1]["stdin"] == run.base.RESUME_PROMPT
    # The failed attempt spent what its session file says; the resumed one,
    # what the file grew by — never the whole file twice.
    assert result["tokens_out"] == 20 + 40
    assert result["cost_usd"] == pytest.approx(_cost(900, 0, 20) + _cost(1200, 1000, 40))


# ── 2. the governor ─────────────────────────────────────────────────────────────


def test_the_cap_stops_the_cli_mid_attempt(run):
    """Codex states usage only when the turn ends; its session file states it
    per model call. The governor reads the file and kills the CLI at the line,
    and what was spent up to the kill is metered."""
    run.mp.setattr(run.base, "MAX_USD", 0.01)
    thread = _thread("text_reply.jsonl")
    code, result, calls = _drive(run, [{
        "fixture": "text_reply.jsonl", "hang": True,
        "rollouts": {thread: [_usage(2000, 0, 2000)]}}])
    assert code == 1 and result["ok"] is False and result["error_kind"] == "fatal"
    assert "per-run cap reached mid-attempt" in result["summary"]
    assert result["cost_usd"] == pytest.approx(_cost(2000, 0, 2000))
    with pytest.raises(ProcessLookupError):
        os.kill(calls[0]["pid"], 0)


def test_an_unpriced_model_on_an_api_key_never_starts_the_cli(run):
    run.mp.setattr(run.base, "PRICE", None)
    code, result, calls = _drive(run, [{"fixture": "text_reply.jsonl"}])
    assert code == 1 and result["error_kind"] == "fatal" and "no price" in result["summary"]
    assert calls == []


def test_a_chatgpt_plan_is_metered_in_tokens_and_needs_no_price(stage):
    login = json.dumps({"tokens": {"access_token": "a", "refresh_token": "r"}})
    r = stage(env={"CODEX_AUTH_JSON": login, "OPENAI_API_KEY": ""})
    r.mp.delenv("OPENAI_API_KEY")
    r.mp.setattr(r.base, "PRICE", None)
    code, result, calls = _drive(r, [{"fixture": "text_reply.jsonl"}])
    assert code == 0 and result["cost_usd"] is None and result["tokens_out"] == 40
    home = pathlib.Path(calls[0]["env"]["CODEX_HOME"])
    # The login is a file in the run's own home, readable by nobody else, and
    # in no child's environment; the built-in provider uses it.
    assert json.loads((home / "auth.json").read_text())["tokens"]["refresh_token"] == "r"
    assert oct((home / "auth.json").stat().st_mode & 0o777) == "0o600"
    assert "CODEX_AUTH_JSON" not in calls[0]["env"]
    assert "model_provider" not in calls[0]["toml"]


# ── 6–8. what the model is offered, and what it is kept from ────────────────────


def test_the_run_is_configured_against_what_an_agent_could_plant(run):
    """Codex reads `.codex/config.toml` and `AGENTS.md` from the workspace and
    trusts a workspace once it has run there; an agent could plant either.
    The home is the run's own, outside the workspace, the workspace is named
    untrusted, project docs are capped at zero, and Codex's own prompt is
    replaced by the seat's."""
    _, _, calls = _drive(run, [{"fixture": "text_reply.jsonl"}])
    config, home = calls[0]["toml"], pathlib.Path(calls[0]["env"]["CODEX_HOME"])
    assert run.workspace not in home.parents and home.parent == run.tmp / "credentials"
    assert config["projects"][str(run.workspace)] == {"trust_level": "untrusted"}
    assert config["project_doc_max_bytes"] == 0
    assert pathlib.Path(config["model_instructions_file"]).read_text() == "You are the critic."
    assert config["skills"] == {"include_instructions": False}
    assert (config["model"], config["model_reasoning_effort"]) == ("gpt-6-sol", "high")
    assert config["approval_policy"] == "never"
    assert config["model_provider"] == "yaaf_openai"
    assert config["model_providers"]["yaaf_openai"]["env_key"] == "OPENAI_API_KEY"
    assert config["features"]["unbounded_connection_retries"] is False
    assert not any(config["features"][f] for f in ("goals", "multi_agent", "plugins", "apps"))


@pytest.mark.parametrize(("perms", "shell", "web", "file_tools"), [
    ({}, False, "disabled", True),
    ({"bash": True}, True, "disabled", False),
    ({"files": False, "data_processing": True, "web": True}, True, "live", False),
    ({"files": False}, False, "disabled", False),
])
def test_a_grant_the_role_leaves_off_is_a_tool_codex_does_not_have(stage, perms, shell, web,
                                                                   file_tools):
    r = stage(perms=perms)
    _, _, calls = _drive(r, [{"fixture": "text_reply.jsonl"}])
    config = calls[0]["toml"]
    assert config["features"]["shell_tool"] is shell
    assert config["features"]["unified_exec"] is shell
    assert config["web_search"] == web
    yaaf = config["mcp_servers"]["yaaf"]
    assert ("Write" in yaaf["enabled_tools"]) is file_tools
    assert (yaaf["env"].get("YAAF_SERVE_FILE_TOOLS") == "1") is file_tools
    assert set(yaaf["enabled_tools"]) - {"Read", "Write", "Edit", "Glob", "Grep"} == {
        t.name for t in r.base.run_tools()}


def test_neither_the_shell_nor_the_tool_server_is_handed_the_model_key(run):
    _, _, calls = _drive(run, [{"fixture": "text_reply.jsonl"}])
    config = calls[0]["toml"]
    policy = config["shell_environment_policy"]
    assert policy["inherit"] == "all" and policy["ignore_default_excludes"] is True
    assert "OPENAI_API_KEY" in policy["exclude"] and "CODEX_AUTH_JSON" in policy["exclude"]
    assert "OPENAI_API_KEY" not in config["mcp_servers"]["yaaf"]["env"]
    # Codex itself still has it, to pay with.
    assert calls[0]["env"]["OPENAI_API_KEY"] == "sk-test-openai"


def test_the_roles_skills_are_the_only_ones(stage):
    r = stage(perms={"skills": [{"name": "Find The Flaw", "description": "for critique",
                                 "body": "Start from the weakest claim."}]})
    _, _, calls = _drive(r, [{"fixture": "text_reply.jsonl"}])
    instructions = pathlib.Path(calls[0]["toml"]["model_instructions_file"]).read_text()
    assert "## find-the-flaw" in instructions and "Start from the weakest claim." in instructions
    assert calls[0]["toml"]["skills"] == {"include_instructions": False}


def test_data_sources_mount_with_their_allowlist_and_sse_says_why_not(stage):
    perms = {"mcp_servers": [
        {"name": "warehouse", "url_env": "WAREHOUSE_URL", "bearer_env": "WAREHOUSE_TOKEN",
         "tools": ["query"]},
        {"name": "feed", "url_env": "FEED_URL", "transport": "sse"}]}
    r = stage(perms=perms, env={"WAREHOUSE_URL": "https://wh.example/mcp",
                                "WAREHOUSE_TOKEN": "wh-secret",
                                "FEED_URL": "https://feed.example/sse"})
    _, _, calls = _drive(r, [{"fixture": "text_reply.jsonl"}])
    servers = calls[0]["toml"]["mcp_servers"]
    assert servers["warehouse"] == {"url": "https://wh.example/mcp",
                                    "http_headers": {"Authorization": "Bearer wh-secret"},
                                    "enabled_tools": ["query"]}
    assert "feed" not in servers
    trace = (r.staging / "trace-run_test.jsonl").read_text()
    assert '"data_source_unmounted"' in trace and "not SSE" in trace


def test_the_tool_server_serves_yaafs_file_tools_when_asked(stage):
    r = stage()
    r.mp.setitem(sys.modules, "loop_harness", importlib.import_module(
        "yaaf.runner.loop_harness"))
    import yaaf.runner.yaaf_tools as tools_module

    tools = importlib.reload(tools_module)
    assert "Write" not in {t.name for t in tools.served()}
    r.mp.setenv("YAAF_SERVE_FILE_TOOLS", "1")
    assert {"Read", "Write", "Edit", "Glob", "Grep"} <= {t.name for t in tools.served()}


@pytest.mark.parametrize(("message", "kind"), [
    ("unexpected status 401 Unauthorized: Incorrect API key provided", "auth"),
    ("unexpected status 403 Forbidden", "auth"),
    ("unexpected status 429 Too Many Requests", "transient"),
    ("unexpected status 500 Internal Server Error", "transient"),
    ("unexpected status 400 Bad Request: invalid model", "fatal"),
    ("stream disconnected before completion", "transient"),
])
def test_what_a_failure_is_comes_from_the_status_codex_states(run, message, kind):
    driver = run.codex.CodexCliDriver("p", run.staging / "p.txt")
    error = run.base.CliRunError("Codex turn failed", result={"message": message})
    assert driver.error_kind(error) == kind
