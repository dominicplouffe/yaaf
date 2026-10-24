"""Invariant selftests: the OpenCode CLI harness, held to the contract every
harness signs (docs/RUNTIMES.md, M5).

The streams replayed here are the real CLI's (1.19.0) — recorded against a
stand-in chat API with the network cut to loopback, because no provider was
reachable from where they were recorded (tests/fixtures/harnesses/opencode/
README.md). The CLI is replaced by a stand-in binary that records what it was
started with and prints a recorded stream. Offline and free.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import pathlib
import sys
from types import SimpleNamespace

import pytest

from tests.conftest import ADMIN, ROLE_MIN, make_project

pytest.importorskip("claude_agent_sdk")

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "harnesses" / "opencode"
#: A price an install might set for a DeepSeek model, per million tokens.
PRICE = {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write_5m": 3.75,
         "cache_write_1h": 3.75, "web_search": 0.01, "as_of": "2026-10-12"}
SPEC = {"name": "deepseek", "base_url": "https://api.deepseek.com/v1",
        "key_env": "DEEPSEEK_API_KEY", "reports_cost": False, "search": False,
        "failure_markers": ["authentication fails"]}

#: Stands in for `opencode`: records its argv, stdin, config, key file and
#: environment; can plant a file where an agent could (the next launch must
#: not see it); prints a recorded stream, `repeat` times; optionally hangs
#: (the governor's case); exits as recorded.
FAKE_OPENCODE = """#!PYTHON
import json, os, pathlib, re, sys, time
state = pathlib.Path(os.environ["FAKE_OPENCODE_STATE"])
n = len(list(state.glob("call-*.json")))
step = json.loads(os.environ["FAKE_OPENCODE_SCRIPT"])[n]
config_dir = pathlib.Path(os.environ["XDG_CONFIG_HOME"]) / "opencode"
config = json.loads((config_dir / "opencode.json").read_text())
key_path = re.match(r"\\{file:(.*)\\}", config["provider"]["yaaf"]["options"]["apiKey"]).group(1)
prompt_path = re.match(r"\\{file:(.*)\\}", config["agent"]["yaaf"]["prompt"]).group(1)
(state / f"call-{n}.json").write_text(json.dumps({
    "argv": sys.argv[1:], "stdin": sys.stdin.read(), "cwd": os.getcwd(), "config": config,
    "key": pathlib.Path(key_path).read_text(), "prompt": pathlib.Path(prompt_path).read_text(),
    "planted_seen": (config_dir / "plugins" / "evil.js").exists(),
    "preseeded": json.loads((config_dir / "package.json").read_text()),
    "env": dict(os.environ)}))
if step.get("charge"):
    # What yaaf's tool server does when the model searches through it.
    with open(step["ledger"], "a") as ledger:
        ledger.write(json.dumps({"usd": step["charge"]}) + "\\n")
if step.get("plant"):
    (config_dir / "plugins").mkdir(exist_ok=True)
    (config_dir / "plugins" / "evil.js").write_text("steal()")
lines = pathlib.Path(step["fixture"]).read_text().splitlines()
for _ in range(step.get("repeat", 1)):
    for line in lines:
        print(line, flush=True)
        time.sleep(step.get("pace", 0))
if step.get("hang"):
    time.sleep(3600)
sys.exit(step.get("exit", 0))
"""


def _staged(monkeypatch, tmp_path, perms: dict | None = None, env: dict | None = None):
    for name, value in {"YAAF_API_BASE_URL": "http://api:8080", "YAAF_API_TOKEN": "test-token",
                        "YAAF_RUN_ID": "run_test", "YAAF_MODEL": "deepseek-chat",
                        "YAAF_EFFORT": "high", "YAAF_AGENT_KIND": "employee",
                        "YAAF_TOOL_PERMISSIONS": json.dumps(perms or {}),
                        "YAAF_PROVIDER": json.dumps(SPEC),
                        "DEEPSEEK_API_KEY": "sk-test-deepseek", **(env or {})}.items():
        monkeypatch.setenv(name, value)
    import yaaf.runner.sandbox_entry as entry_module

    base = importlib.reload(entry_module)
    monkeypatch.setitem(sys.modules, "sandbox_entry", base)
    import yaaf.runner.opencode_cli_entry as opencode_module

    opencode = importlib.reload(opencode_module)
    workspace = tmp_path / "workspace"
    staging = workspace / ".yaaf"
    staging.mkdir(parents=True)
    monkeypatch.setattr(base, "WORKSPACE", workspace)
    monkeypatch.setattr(base, "STAGING", staging)
    monkeypatch.setattr(base, "CREDENTIAL_DIR", tmp_path / "credentials")
    monkeypatch.setattr(base, "RESUME_BACKOFF_S", (0.0, 0.0))
    monkeypatch.setattr(base, "PRICE", PRICE)
    (staging / "system_prompt-run_test.txt").write_text("You are the critic.")
    (staging / "context-run_test.json").write_text("{}")
    return SimpleNamespace(base=base, opencode=opencode, staging=staging, tmp=tmp_path,
                           mp=monkeypatch, workspace=workspace)


@pytest.fixture()
def stage(monkeypatch, tmp_path):
    return lambda **kw: _staged(monkeypatch, tmp_path, **kw)


@pytest.fixture()
def run(stage):
    return stage()


def _drive(r, script: list[dict]) -> tuple[int, dict, list[dict], list[dict]]:
    state = r.tmp / "fake-opencode"
    state.mkdir(exist_ok=True)
    binary = r.tmp / "opencode"
    binary.write_text(FAKE_OPENCODE.replace("PYTHON", sys.executable))
    binary.chmod(0o755)
    r.mp.setattr(r.opencode, "OPENCODE", str(binary))
    r.mp.setenv("FAKE_OPENCODE_STATE", str(state))
    r.mp.setenv("FAKE_OPENCODE_SCRIPT", json.dumps(
        [{**step, "fixture": str(FIXTURES / step["fixture"])} for step in script]))
    # Bounded: the governor's cases hang the stand-in on purpose.
    code = asyncio.run(asyncio.wait_for(r.base.run_harness(r.opencode.OpenCodeDriver), 30))
    calls = [json.loads(p.read_text()) for p in sorted(state.glob("call-*.json"))]
    trace = [json.loads(line) for line in
             (r.staging / "trace-run_test.jsonl").read_text().splitlines()]
    return code, json.loads((r.staging / "result-run_test.json").read_text()), calls, trace


#: What every recorded `step_finish` states: 600 uncached in, 40 out, 10 of
#: reasoning, 400 read from the cache.
STEP = (600 * PRICE["input"] + 50 * PRICE["output"] + 400 * PRICE["cache_read"]) / 1e6


# ── 1. usage parses to the bill ─────────────────────────────────────────────────


def test_a_reply_is_metered_and_priced_with_its_reasoning_as_output(run):
    code, result, _, _ = _drive(run, [{"fixture": "text_reply.jsonl"}])
    assert code == 0 and result["ok"] is True and result["error_kind"] is None
    assert (result["tokens_in"], result["tokens_out"], result["cache_read_tokens"]) == (
        600, 50, 400)
    assert result["cost_usd"] == pytest.approx(STEP)
    assert result["priced_usd"] == pytest.approx(STEP)
    assert "Hello from the fake model." in result["summary"]


def test_every_model_call_is_a_bill_and_a_tool_call_is_traced(run):
    code, result, _, trace = _drive(run, [{"fixture": "tool_call.jsonl"}])
    assert code == 0 and result["cost_usd"] == pytest.approx(2 * STEP)
    calls = [t for t in trace if t["type"] == "tool_use"]
    assert [(c["tool"], c["status"]) for c in calls] == [("yaaf_board", "completed")]


# ── 2. what it is started with ──────────────────────────────────────────────────


def test_the_run_is_started_headless_on_the_seats_provider(run):
    _drive(run, [{"fixture": "text_reply.jsonl"}])
    from yaaf.runner.opencode_cli_entry import LOCKDOWN

    (call,) = _calls(run)
    assert call["argv"] == ["run", "--format", "json"]
    assert "Do ONE unit of work" in call["stdin"]
    assert call["cwd"] == str(run.workspace)
    provider = call["config"]["provider"]["yaaf"]
    assert provider["npm"] == "@ai-sdk/openai-compatible"
    assert provider["options"]["baseURL"] == "https://api.deepseek.com/v1"
    assert call["key"] == "sk-test-deepseek"
    assert call["config"]["model"] == "yaaf/deepseek-chat"
    assert call["prompt"].startswith("You are the critic.")
    # No title call, no hosted defaults, no sharing; its plugin package
    # pre-seeded at the pinned version, so it fetches nothing at startup.
    assert call["config"]["agent"]["title"] == {"disable": True}
    assert (call["config"]["enabled_providers"], call["config"]["share"]) == (["yaaf"],
                                                                             "disabled")
    assert call["preseeded"] == {"dependencies": {"@opencode-ai/plugin": "1.19.0"}}
    assert all(call["env"].get(k) == v for k, v in LOCKDOWN.items())


def _calls(r) -> list[dict]:
    return [json.loads(p.read_text())
            for p in sorted((r.tmp / "fake-opencode").glob("call-*.json"))]


def test_the_model_key_reaches_no_shell_and_no_tool_server(run):
    _drive(run, [{"fixture": "text_reply.jsonl"}])
    (call,) = _calls(run)
    # OpenCode's environment is what its shell and every MCP server inherit.
    assert "DEEPSEEK_API_KEY" not in call["env"]
    assert "sk-test-deepseek" not in json.dumps(call["env"])
    assert "sk-test-deepseek" not in json.dumps(call["config"]["mcp"])
    assert call["config"]["mcp"]["yaaf"]["environment"]["YAAF_RUN_ID"] == "run_test"


@pytest.mark.parametrize(("perms", "allowed"), [
    ({}, {"yaaf_*", "read", "glob", "grep", "list", "edit", "external_directory"}),
    ({"files": False}, {"yaaf_*"}),
    ({"bash": True}, {"yaaf_*", "read", "glob", "grep", "list", "edit", "external_directory",
                      "bash"}),
    ({"web": True, "files": False}, {"yaaf_*", "webfetch"}),
])
def test_a_grant_the_role_leaves_off_is_a_tool_opencode_does_not_have(stage, perms, allowed):
    r = stage(perms=perms)
    _drive(r, [{"fixture": "text_reply.jsonl"}])
    (call,) = _calls(r)
    rules = call["config"]["permission"]
    # Everything denied first, so the sub-agent, the todo list, skills and
    # anything OpenCode adds later are off unless a grant allows them back.
    assert list(rules)[0] == "*" and rules["*"] == "deny"
    assert {k for k, v in rules.items() if v == "allow"} == allowed
    assert "task" not in rules


def test_data_sources_mount_with_their_allowlist_and_sse_says_why_not(stage):
    r = stage(perms={"mcp_servers": [
        {"name": "crm", "url_env": "CRM_URL", "tools": ["lookup"]},
        {"name": "wiki", "url_env": "WIKI_URL"},
        {"name": "legacy", "url_env": "LEGACY_URL", "transport": "sse"}]},
        env={"CRM_URL": "https://crm.example/mcp", "WIKI_URL": "https://wiki.example/mcp",
             "LEGACY_URL": "https://legacy.example/sse"})
    _, _, calls, trace = _drive(r, [{"fixture": "text_reply.jsonl"}])
    config = calls[0]["config"]
    assert config["mcp"]["crm"] == {"type": "remote", "url": "https://crm.example/mcp",
                                    "enabled": True}
    assert "legacy" not in config["mcp"]
    rules = config["permission"]
    assert rules.get("crm_lookup") == "allow" and "crm_*" not in rules
    assert rules.get("wiki_*") == "allow"
    assert [t["source"] for t in trace if t["type"] == "data_source_unmounted"] == ["legacy"]


def test_the_roles_skills_are_inline_and_the_only_ones(stage):
    r = stage(perms={"skills": [{"name": "Pricing", "description": "how we price",
                                 "body": "Always quote in CAD."}]})
    _drive(r, [{"fixture": "text_reply.jsonl"}])
    (call,) = _calls(r)
    assert "## pricing" in call["prompt"] and "Always quote in CAD." in call["prompt"]
    assert call["env"]["OPENCODE_DISABLE_EXTERNAL_SKILLS"] == "1"


# ── 3. what an agent could plant ────────────────────────────────────────────────


def test_a_plugin_an_agent_planted_in_the_workspace_never_loads(run):
    """OpenCode's plugin loader executes what it finds between the workspace
    and the repository root, whatever its own switches say — so before every
    launch it is moved aside, and the trace says where."""
    (run.workspace / ".opencode" / "plugins").mkdir(parents=True)
    (run.workspace / ".opencode" / "plugins" / "x.js").write_text("steal()")
    (run.workspace / "opencode.json").write_text('{"plugin": ["evil"]}')
    _, _, _, trace = _drive(run, [{"fixture": "text_reply.jsonl"}])
    assert not (run.workspace / ".opencode").exists()
    assert not (run.workspace / "opencode.json").exists()
    moved = {pathlib.Path(t["path"]).name for t in trace
             if t["type"] == "opencode_config_quarantined"}
    assert moved == {".opencode.quarantined-run_test", "opencode.json.quarantined-run_test"}
    assert (run.workspace / ".opencode.quarantined-run_test" / "plugins" / "x.js").exists()


def test_what_a_run_wrote_into_its_own_config_is_gone_on_the_next_launch(run):
    """The resume keeps the session — and nothing an agent wrote into the
    config directory between launches."""
    code, _, calls, _ = _drive(run, [
        {"fixture": "server_error.jsonl", "exit": 1, "plant": True},
        {"fixture": "text_reply.jsonl"}])
    assert code == 0 and len(calls) == 2
    assert calls[1]["planted_seen"] is False


# ── 4. failures, classified by the fields OpenCode states ───────────────────────


def test_a_dead_key_is_auth_and_is_never_retried(run):
    code, result, calls, _ = _drive(run, [{"fixture": "invalid_key.jsonl", "exit": 1},
                                          {"fixture": "text_reply.jsonl"}])
    assert code == 1 and result["error_kind"] == "auth" and len(calls) == 1
    assert "HTTP 401" in result["summary"] and "Incorrect API key provided" in result["summary"]


@pytest.mark.parametrize("fixture", ["rate_limited.jsonl", "server_error.jsonl"])
def test_an_overloaded_provider_resumes_the_same_session(run, fixture):
    session = json.loads((FIXTURES / fixture).read_text())["sessionID"]
    code, result, calls, _ = _drive(run, [{"fixture": fixture, "exit": 1},
                                          {"fixture": "text_reply.jsonl"}])
    assert code == 0 and result["ok"] is True
    assert calls[1]["argv"] == ["run", "--format", "json", "--session", session]


def test_an_error_event_fails_the_run_even_when_the_cli_exits_zero(run):
    code, result, _, _ = _drive(run, [{"fixture": "invalid_key.jsonl", "exit": 0}])
    assert code == 1 and result["error_kind"] == "auth"


# ── 5. the governor ─────────────────────────────────────────────────────────────


def test_the_cap_stops_the_cli_between_calls(stage):
    """A model that keeps calling tools is stopped at the dollar line —
    OpenCode's own step limit and loop guard stop nothing."""
    r = stage(env={"YAAF_MAX_USD": str(STEP * 5.5)})
    code, result, _, _ = _drive(r, [{"fixture": "tool_call.jsonl", "repeat": 50,
                                     "pace": 0.02, "hang": True}])
    assert code == 1 and result["error_kind"] == "fatal"
    assert "per-run cap reached" in result["summary"]
    assert STEP * 5.5 <= result["cost_usd"] < STEP * 8


def test_a_run_that_calls_tools_forever_stops_at_the_call_limit(run):
    run.mp.setattr(run.opencode, "MAX_STEPS", 5)
    code, result, _, _ = _drive(run, [{"fixture": "tool_call.jsonl", "repeat": 50,
                                       "pace": 0.02, "hang": True}])
    assert code == 0 and "[stopped at the 5-call limit]" in result["summary"]
    assert result["cost_usd"] == pytest.approx(5 * STEP)


def test_an_unpriced_model_never_starts_the_cli(run):
    run.mp.setattr(run.base, "PRICE", None)
    code, result, calls, _ = _drive(run, [{"fixture": "text_reply.jsonl"}])
    assert code == 1 and calls == [] and "no price on this install" in result["summary"]


# ── 6. the doors: what the registry says it can and cannot do ───────────────────


def test_a_role_granted_the_web_is_refused_for_it(client):
    make_project(client, start=False)
    client.put("/admin/settings/model-prices/deepseek-chat", headers=ADMIN,
               json={"input": 0.28, "output": 0.42})
    body = {"slug": "researcher", "name": "Researcher", **ROLE_MIN, "model": "deepseek-chat",
            "harness": "opencode"}
    refused = client.post("/admin/roles", headers=ADMIN, json={
        **body, "tool_permissions": {"files": True, "web": True}})
    assert refused.status_code == 422 and "OpenCode CLI cannot search the web" in refused.text
    assert client.post("/admin/roles", headers=ADMIN, json={
        **body, "tool_permissions": {"files": True, "bash": True}}).status_code == 200
    # Its only search is the install's own, served as a yaaf tool: once there
    # is a backend, a role granted the web is honoured on it (R2).
    assert client.put("/admin/settings/web-search", headers=ADMIN, json={
        "backend": "brave", "key": "brave-key-1234"}).status_code == 200
    assert client.post("/admin/roles", headers=ADMIN, json={
        **body, "tool_permissions": {"files": True, "web": True}}).status_code == 200


SEARCHING = {"YAAF_SEARCH": json.dumps({"backend": "brave", "price_usd": 0.004,
                                        "base_url": "https://api.search.brave.com/res/v1"}),
             "YAAF_SEARCH_KEY": "brave-install-key"}


def test_the_tool_server_offers_search_only_to_a_run_promised_it(stage):
    r = stage(perms={"web": True}, env=SEARCHING)
    import yaaf.runner.yaaf_tools as tools_module

    tools = importlib.reload(tools_module)
    for perms, search, offered in (({"web": True}, json.loads(SEARCHING["YAAF_SEARCH"]), True),
                                   ({"web": False}, json.loads(SEARCHING["YAAF_SEARCH"]), False),
                                   ({"web": True}, None, False)):
        r.mp.setattr(r.base, "PERMS", perms)
        r.mp.setattr(r.base, "SEARCH", search)
        assert ("web_search" in {t.name for t in tools.served()}) is offered, (perms, search)


def test_opencode_never_holds_the_search_key_and_its_tool_server_does(stage):
    r = stage(perms={"web": True, "files": True}, env=SEARCHING)
    code, _, calls, _ = _drive(r, [{"fixture": "text_reply.jsonl"}])
    assert code == 0
    assert "YAAF_SEARCH_KEY" not in calls[0]["env"]
    server = calls[0]["config"]["mcp"]["yaaf"]["environment"]
    assert server["YAAF_SEARCH_KEY"] == "brave-install-key"


def test_a_search_through_the_tool_server_is_billed_into_the_run_as_it_lands(stage):
    r = stage(perms={"web": True, "files": True}, env=SEARCHING)
    code, result, _, trace = _drive(r, [{
        "fixture": "tool_call.jsonl", "charge": 0.004,
        "ledger": str(r.staging / "search-run_test.jsonl")}])
    assert code == 0, result["summary"]
    searches = [line for line in trace if line.get("subtype") == "web_search"]
    assert [line["cost_usd"] for line in searches] == [0.004]
    tokens_only = sum(line["cost_usd"] for line in trace
                      if line.get("type") == "result" and line.get("subtype") != "web_search")
    assert result["cost_usd"] == pytest.approx(tokens_only + 0.004)


def test_openrouter_is_not_among_its_providers(client):
    make_project(client, start=False)
    refused = client.post("/admin/roles", headers=ADMIN, json={
        "slug": "router", "name": "Router", **ROLE_MIN, "model": "openrouter/qwen/qwen3-coder",
        "harness": "opencode", "tool_permissions": {"files": True}})
    assert refused.status_code == 422 and "OpenCode CLI cannot run" in refused.text


def test_the_image_pins_the_version_the_driver_preseeds(run):
    # Through `run`, like every other test here: the driver imports its base as
    # the image lays them out (`import sandbox_entry`), and that base reads its
    # environment on import. A bare import passed only when a staged test had
    # already loaded the driver in the same worker, and failed whenever the CI
    # shards were rebalanced so that none had.
    VERSION = run.opencode.VERSION

    dockerfile = (FIXTURES.parents[2].parent / "deploy" / "sandbox" / "Dockerfile").read_text()
    assert f"opencode-ai@{VERSION}" in dockerfile
    assert os.path.exists(FIXTURES / "README.md")
