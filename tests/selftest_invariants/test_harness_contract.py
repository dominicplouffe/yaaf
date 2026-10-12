"""Invariant selftests: the contract every harness signs (docs/RUNTIMES.md).

A harness is transport. It may change how a model is driven; it may not change
what a run is metered at, how a dead credential is read, when a run retries,
how far the per-run cap reaches, or what the model is offered. So the recorded
streams in tests/fixtures/harnesses/ are replayed through every Claude harness
— the CLI through a stand-in `claude` binary, the Agent SDK through the SDK's
own message parser, which is how it reads the real CLI's output — and each
must land the same result.json. A difference is the pattern's fault, and this
is where it shows up first.

Offline and free: nothing here reaches a model. The stdio tool server is the
one real subprocess, because "it serves the tools" is a claim about a process.
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

sdk = pytest.importorskip("claude_agent_sdk")

from claude_agent_sdk._internal.message_parser import parse_message  # noqa: E402

TESTS = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = TESTS / "fixtures" / "harnesses" / "claude"
RUNNER = TESTS.parent / "yaaf" / "runner"

#: Stands in for `claude`: records its argv and stdin, replays one recorded
#: stream per invocation, exits as the real CLI did when it was recorded.
FAKE_CLI = """#!PYTHON
import json, os, pathlib, sys, time
state = pathlib.Path(os.environ["FAKE_CLI_STATE"])
n = len(list(state.glob("call-*.json")))
step = json.loads(os.environ["FAKE_CLI_SCRIPT"])[n]
if step.get("die_before_reading"):
    sys.stderr.write("error: unknown option '--made-up'\\n")
    (state / f"call-{n}.json").write_text(json.dumps({"argv": sys.argv[1:], "stdin": ""}))
    sys.exit(2)
(state / f"call-{n}.json").write_text(json.dumps(
    {"argv": sys.argv[1:], "stdin": sys.stdin.read(), "pid": os.getpid()}))
for line in pathlib.Path(step["fixture"]).read_text().splitlines():
    print(line, flush=True)
if step.get("hang"):
    time.sleep(3600)
sys.exit(step.get("exit", 0))
"""


def _records(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines() if line]


def _staged(monkeypatch, tmp_path) -> SimpleNamespace:
    for name, value in {"YAAF_API_BASE_URL": "http://api:8080", "YAAF_API_TOKEN": "test-token",
                        "YAAF_RUN_ID": "run_test", "YAAF_TOOL_PERMISSIONS": "{}"}.items():
        monkeypatch.setenv(name, value)
    import yaaf.runner.sandbox_entry as entry_module

    base = importlib.reload(entry_module)
    # The image lays the entrypoints out in one directory, so the CLI harness
    # imports `sandbox_entry` by bare name — as chat_entry does.
    monkeypatch.setitem(sys.modules, "sandbox_entry", base)
    import yaaf.runner.claude_cli_entry as cli_module

    cli = importlib.reload(cli_module)
    workspace = tmp_path / "workspace"
    staging = workspace / ".yaaf"
    staging.mkdir(parents=True)
    monkeypatch.setattr(base, "WORKSPACE", workspace)
    monkeypatch.setattr(base, "STAGING", staging)
    monkeypatch.setattr(base, "CREDENTIAL_DIR", tmp_path / "credentials")
    monkeypatch.setattr(base, "RESUME_BACKOFF_S", (0.0, 0.0))
    (staging / "system_prompt-run_test.txt").write_text("You are the CEO.")
    (staging / "context-run_test.json").write_text("{}")
    return SimpleNamespace(base=base, cli=cli, staging=staging, tmp=tmp_path, mp=monkeypatch)


@pytest.fixture()
def run(monkeypatch, tmp_path):
    return _staged(monkeypatch, tmp_path)


def _result(r) -> dict:
    return json.loads((r.staging / "result-run_test.json").read_text())


def _on_sdk(r, script: list[dict]) -> tuple[int, dict, list[dict]]:
    """The Agent SDK harness, fed each fixture through the SDK's own parser,
    raising what the SDK raises when the CLI exits non-zero."""
    calls: list[dict] = []

    async def fake_query(prompt=None, options=None):
        step = script[len(calls)]
        calls.append({"stdin": prompt, "resume": options.resume,
                      "max_budget_usd": options.max_budget_usd})
        last_error = None
        for record in _records(step["fixture"]):
            if record.get("type") == "result" and record.get("is_error"):
                last_error = record
            message = parse_message(record)
            if message is not None:
                yield message
        if step.get("exit", 0):
            if last_error is not None:
                raise sdk.ResultError("Claude Code returned an error result: "
                                      + r.cli._error_result_text(last_error),
                                      data=last_error, exit_code=step["exit"])
            raise sdk.ProcessError(f"Command failed with exit code {step['exit']}",
                                   exit_code=step["exit"])

    r.mp.setattr(r.base, "query", fake_query)
    code = asyncio.run(r.base.main())
    return code, _result(r), calls


def _fake_cli(r, script: list[dict]) -> pathlib.Path:
    state = r.tmp / "fake-cli"
    state.mkdir(exist_ok=True)
    binary = r.tmp / "claude"
    binary.write_text(FAKE_CLI.replace("PYTHON", sys.executable))
    binary.chmod(0o755)
    r.mp.setattr(r.cli, "CLAUDE", str(binary))
    r.mp.setenv("FAKE_CLI_STATE", str(state))
    r.mp.setenv("FAKE_CLI_SCRIPT", json.dumps(
        [{**step, "fixture": str(FIXTURES / step["fixture"])} for step in script]))
    return state


def _calls(state: pathlib.Path) -> list[dict]:
    calls = [json.loads(p.read_text()) for p in sorted(state.glob("call-*.json"))]
    for call in calls:
        argv = call["argv"]
        call["resume"] = next((a.split("=", 1)[1] for a in argv if a.startswith("--resume=")),
                              None)
        call["max_budget_usd"] = (float(argv[argv.index("--max-budget-usd") + 1])
                                  if "--max-budget-usd" in argv else None)
    return calls


def _on_cli(r, script: list[dict]) -> tuple[int, dict, list[dict]]:
    """The Claude CLI harness, against a binary that replays the fixture."""
    state = _fake_cli(r, script)
    code = asyncio.run(r.base.run_harness(r.cli.ClaudeCliDriver))
    return code, _result(r), _calls(state)


HARNESSES = {"claude_agent_sdk": _on_sdk, "claude_cli": _on_cli}


def _both(run_fixture, script: list[dict], **globals_) -> dict[str, tuple]:
    """Run one script on every harness, each in a fresh staging directory."""
    out = {}
    for name, drive in HARNESSES.items():
        r = run_fixture(name)
        for key, value in globals_.items():
            r.mp.setattr(r.base, key, value)
        out[name] = drive(r, script)
    return out


@pytest.fixture()
def fresh(monkeypatch, tmp_path_factory):
    """Independent staging per harness, so one test can run every harness and
    compare what each left behind."""
    return lambda label: _staged(monkeypatch, tmp_path_factory.mktemp(label))


def _same(results: dict[str, tuple], ignore=("trace_path", "summary")) -> dict:
    (_, first, _), *rest = results.values()
    for _, other, _ in rest:
        assert {k: v for k, v in other.items() if k not in ignore} == \
               {k: v for k, v in first.items() if k not in ignore}
    return first


# ── 1. usage parses to the same bill ────────────────────────────────────────────


def test_a_reply_is_metered_the_same_on_every_harness(fresh):
    results = _both(fresh, [{"fixture": "text_reply.jsonl"}])
    result = _same(results, ignore=("trace_path",))
    assert result["ok"] is True and result["error_kind"] is None
    assert (result["tokens_in"], result["tokens_out"]) == (2, 13)
    assert (result["cache_creation_tokens"], result["cache_read_tokens"]) == (1987, 535)
    assert result["cost_usd"] == pytest.approx(0.0081355)
    assert "Hi! What are we working on today?" in result["summary"]
    # The plan meter rides home from the recorded rate-limit event, either way.
    assert {w["key"]: w["utilization"] for w in result["plan"]["windows"]} == {
        "five_hour": 0.03, "seven_day": 0.45}


def test_every_harness_prices_the_run_beside_the_bill_it_reports(fresh):
    """The gap check's input (docs/RUNTIMES.md): a harness that reports its own
    bill is priced from the catalog too, and the two are compared. The
    recorded run wrote its whole prompt to the 1-hour cache, priced above the
    5-minute one, so a pricer that ignored the TTL split would be wrong by
    exactly that.

    They differ, and by a known amount: Claude Code 2.1.296 bills these
    tokens at $0.0081355, the catalog at $0.008189. The whole $0.0000535 is
    the 535 cache-read tokens — the CLI prices them at $0.10 per million, the
    published list at $0.20 — which is the kind of drift the check exists to
    surface."""
    from yaaf.runtimes import catalog

    price = catalog.price("claude-sonnet-5-5").as_dict()
    results = _both(fresh, [{"fixture": "text_reply.jsonl"}], PRICE=price)
    result = _same(results, ignore=("trace_path",))
    assert result["cost_usd"] == pytest.approx(0.0081355)
    assert result["priced_usd"] == pytest.approx(
        (2 * price["input"] + 13 * price["output"] + 535 * price["cache_read"]
         + 1987 * price["cache_write_1h"]) / 1_000_000)
    assert result["priced_usd"] - result["cost_usd"] == pytest.approx(
        535 * (price["cache_read"] - 0.10) / 1_000_000)


def test_a_model_the_catalog_cannot_price_is_still_billed_as_reported(fresh):
    results = _both(fresh, [{"fixture": "text_reply.jsonl"}], PRICE=None)
    result = _same(results, ignore=("trace_path",))
    assert result["cost_usd"] == pytest.approx(0.0081355)
    assert result["priced_usd"] is None


# ── 4. a dead credential reads as one, whatever the prose says ──────────────────


def test_a_dead_key_is_auth_on_every_harness_and_is_never_retried(fresh):
    """The recorded prose — "Invalid API key · Fix external API key" — matched
    no marker the runner had. The fields beside it are what classify it now."""
    results = _both(fresh, [{"fixture": "invalid_key.jsonl", "exit": 1},
                            {"fixture": "text_reply.jsonl"}])
    result = _same(results)
    assert result["ok"] is False and result["error_kind"] == "auth"
    for _, outcome, calls in results.values():
        assert len(calls) == 1, "a dead key was retried"
        assert "Invalid API key" in outcome["summary"]


# ── 5. a dropped stream resumes in place ────────────────────────────────────────


def test_a_dropped_stream_resumes_the_same_session_on_every_harness(fresh):
    results = _both(fresh, [{"fixture": "connection_drop.jsonl", "exit": 1},
                            {"fixture": "text_reply.jsonl"}])
    result = _same(results)
    assert result["ok"] is True
    # Both attempts were billed, so both are metered.
    assert result["tokens_out"] == 100 + 13
    assert result["cost_usd"] == pytest.approx(0.25 + 0.0081355)
    for name, (_, outcome, calls) in results.items():
        assert len(calls) == 2, name
        assert calls[0]["resume"] is None
        assert calls[1]["resume"] == "7d1c2f0e-0000-4000-8000-000000000001", name
        assert calls[1]["stdin"].startswith("Your previous response was interrupted"), name
        assert "resuming in place" in outcome["summary"]


def test_the_resume_prompt_is_what_the_resumed_cli_is_told(fresh):
    r = fresh("cli")
    state = _fake_cli(r, [{"fixture": "connection_drop.jsonl", "exit": 1},
                          {"fixture": "text_reply.jsonl"}])
    asyncio.run(r.base.run_harness(r.cli.ClaudeCliDriver))
    calls = _calls(state)
    assert "Do ONE unit of work" in calls[0]["stdin"]
    assert calls[1]["stdin"] == r.base.RESUME_PROMPT


# ── 2. the per-run cap holds across resumes ─────────────────────────────────────


def test_a_resumed_attempt_is_handed_only_what_is_left_of_the_cap(fresh):
    results = _both(fresh, [{"fixture": "connection_drop.jsonl", "exit": 1},
                            {"fixture": "text_reply.jsonl"}], MAX_USD=1.0)
    for name, (_, _, calls) in results.items():
        assert calls[0]["max_budget_usd"] == pytest.approx(1.0), name
        assert calls[1]["max_budget_usd"] == pytest.approx(0.75), name


def test_a_run_that_has_spent_its_cap_is_not_resumed(fresh):
    """Each resume is a fresh model process with a fresh budget of its own; a
    cap handed to every attempt whole bounded a run at three times itself."""
    results = _both(fresh, [{"fixture": "connection_drop.jsonl", "exit": 1},
                            {"fixture": "text_reply.jsonl"}], MAX_USD=0.2)
    result = _same(results)
    assert result["ok"] is False
    assert result["cost_usd"] == pytest.approx(0.25), "what was spent is still metered"
    for name, (_, outcome, calls) in results.items():
        assert len(calls) == 1, f"{name} resumed past the cap"
        assert "per-run cap of $0.2 reached" in outcome["summary"]


# ── 3. a kill is metered, not free ──────────────────────────────────────────────


def test_a_kill_mid_run_leaves_a_metered_checkpoint_and_no_process(fresh):
    r = fresh("kill")
    state = _fake_cli(r, [{"fixture": "text_reply.jsonl", "hang": True}])
    result_path = r.staging / "result-run_test.json"

    async def kill_once_billed():
        task = asyncio.create_task(r.base.run_harness(r.cli.ClaudeCliDriver))
        for _ in range(400):
            await asyncio.sleep(0.025)
            if result_path.exists() and json.loads(result_path.read_text())["tokens_out"]:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(kill_once_billed())
    result = json.loads(result_path.read_text())
    assert result["partial"] is True and result["ok"] is False
    assert result["tokens_out"] == 13 and result["cost_usd"] == pytest.approx(0.0081355)
    pid = _calls(state)[0]["pid"]
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


# ── 6–8. what the model is offered ──────────────────────────────────────────────


def _flags(argv: list[str]) -> dict:
    out, i = {}, 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("--") and "=" in token:
            key, value = token.split("=", 1)
            out[key] = value
        elif token.startswith("--"):
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is not None and not nxt.startswith("--"):
                out[token] = nxt
                i += 1
            else:
                out[token] = True
        i += 1
    return out


@pytest.mark.parametrize("perms", [
    {},
    {"bash": True, "web": True},
    {"files": False, "data_processing": True},
    {"bash": True, "skills": [{"name": "Write Better Emails", "body": "…"}]},
])
def test_the_cli_is_started_with_the_flags_the_sdk_itself_builds(run, perms):
    """Read off the SDK, not guessed: for every option the SDK harness sets,
    the CLI harness passes the flag the SDK would have built from it. Fails
    the day the SDK changes how it drives the CLI, which is the day to look."""
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    for key, value in {"MAX_USD": 2.0, "TASK_BUDGET": 50000, "PERMS": perms}.items():
        run.mp.setattr(run.base, key, value)
    prompt_path = run.staging / "system_prompt-run_test.txt"
    options = run.base.SdkDriver("You are the CEO.", prompt_path).options
    options.cli_path = "claude"
    sdk_flags = _flags(SubprocessCLITransport(prompt="x", options=options)._build_command())
    cli_flags = _flags(run.cli.ClaudeCliDriver("You are the CEO.", prompt_path).argv(None))
    for flag in ("--tools", "--allowedTools", "--max-turns", "--model", "--permission-mode",
                 "--setting-sources", "--effort", "--max-budget-usd", "--task-budget"):
        assert cli_flags.get(flag) == sdk_flags.get(flag), flag
    # The system prompt by file rather than by string, and the MCP servers by
    # file rather than inline JSON — the same servers either way.
    assert pathlib.Path(cli_flags["--system-prompt-file"]).read_text() == sdk_flags["--system-prompt"]
    sdk_servers = json.loads(sdk_flags["--mcp-config"])["mcpServers"]
    cli_servers = json.loads(pathlib.Path(cli_flags["--mcp-config"]).read_text())["mcpServers"]
    assert sorted(cli_servers) == sorted(sdk_servers)


def test_the_mcp_config_is_kept_off_the_workspace(run):
    """It carries the run token and any data source's bearer, so it lives where
    file credentials live — never in the git clone the agent commits from."""
    path = run.cli.ClaudeCliDriver("x", run.staging / "system_prompt-run_test.txt").mcp_config_path
    assert run.base.WORKSPACE not in path.parents
    assert oct(path.stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("kind", ["ceo", "employee"])
def test_the_stdio_server_serves_exactly_the_tools_this_agent_is_sent(run, kind):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable, args=[str(RUNNER / "yaaf_tools.py")],
                                   env={**os.environ, "YAAF_AGENT_KIND": kind})

    async def listing():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return (await session.list_tools()).tools

    served = {t.name: getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
              for t in asyncio.run(listing())}
    run.mp.setattr(run.base, "AGENT_KIND", kind)
    assert served == {t.name: run.base.wire_schema(t) for t in run.base.run_tools()}
    assert ("hire" in served) == (kind == "ceo")


# ── the classification the fixtures exercise, field by field ────────────────────


def test_the_fields_decide_auth_without_any_prose(run):
    """The marker list also learned "invalid api key", so the recorded fixture
    passes on prose alone; this is the half that does not need words at all."""
    kind = run.base.structured_error_kind
    assert kind({"api_error_status": 401}, None) == "auth"
    assert kind({"api_error_status": 403}, None) == "auth"
    assert kind(None, "authentication_failed") == "auth"
    assert kind(None, "billing_error") == "auth"
    assert kind({"api_error_status": 529}, "server_error") is None
    assert kind(None, "rate_limit") is None


# ── the image carries every harness ─────────────────────────────────────────────


def test_the_sandbox_image_carries_every_harness_and_what_it_imports():
    """The image copies single files, not the package. An entrypoint whose
    sibling is missing from it fails at the first import of the first run on
    that harness — on a deployed box, at whatever hour that is."""
    import ast
    import re

    from yaaf.runner.backends import get_backend
    from yaaf.runtimes.harnesses import HARNESSES

    dockerfile = (TESTS.parent / "deploy" / "sandbox" / "Dockerfile").read_text()
    copied = set(re.findall(r"COPY yaaf/runner/(\w+\.py) /opt/yaaf/\1", dockerfile))
    entrypoints = {get_backend(name).entrypoint for name, h in HARNESSES.items()
                   if h.family != "mock"}
    assert entrypoints <= copied, entrypoints - copied
    siblings = {p.name for p in RUNNER.glob("*.py")}
    # What the image's venv installs, by import name; and what arrives only as
    # a dependency of something installed (the anthropic SDK's HTTP client).
    pip_line = re.search(r"pip install --no-cache-dir \\\n(.*)", dockerfile).group(1)
    installed = {re.split(r"[<>=]", word.strip('"'))[0].replace("-", "_")
                 for word in pip_line.split()}
    installed |= {"httpx2"}
    for name in copied:
        tree = ast.parse((RUNNER / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            else:
                continue
            for imported in names:
                top = imported.split(".")[0]
                if top in sys.stdlib_module_names:
                    continue  # yaaf/runner/types.py is not copied, so `types` is the stdlib's
                if f"{top}.py" in siblings:
                    assert f"{top}.py" in copied, (name, top)
                else:
                    assert top in installed, (name, top)


def test_a_tool_call_through_the_stdio_server_reaches_the_agent_api_as_this_run(run):
    """Listing tools proves the catalog; this proves the wire. A call made over
    MCP lands on /agent/* with the run's own token, and the API's answer —
    refusals included, since the refusal text is the interface — comes back to
    the model verbatim."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    seen: list[tuple[str, str | None]] = []

    class AgentApi(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — the stdlib's name
            seen.append((self.path, self.headers.get("Authorization")))
            body = json.dumps({"error": "refused", "rule": "test_wall"}).encode()
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), AgentApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    params = StdioServerParameters(
        command=sys.executable, args=[str(RUNNER / "yaaf_tools.py")],
        env={**os.environ, "YAAF_API_BASE_URL": f"http://127.0.0.1:{server.server_port}",
             "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})

    async def call_board():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool("board", {})

    try:
        result = asyncio.run(call_board())
    finally:
        server.shutdown()
    assert seen == [("/agent/board", "Bearer test-token")]
    text = result.content[0].text
    assert '"status": 403' in text and "test_wall" in text


def test_a_cli_that_dies_before_reading_its_prompt_fails_with_its_own_reason(fresh):
    """A context pack larger than a pipe, and a CLI that exits at startup: the
    write breaks the pipe, and "connection reset" would read as a network blip
    and be retried. The CLI's exit code and stderr are the reason instead."""
    r = fresh("startup")
    (r.staging / "context-run_test.json").write_text(json.dumps({"pad": "x" * 2_000_000}))
    state = _fake_cli(r, [{"fixture": "text_reply.jsonl", "die_before_reading": True},
                          {"fixture": "text_reply.jsonl"}])
    assert asyncio.run(r.base.run_harness(r.cli.ClaudeCliDriver)) == 1
    result = _result(r)
    assert len(_calls(state)) == 1, "a startup failure was retried as a transient"
    assert result["error_kind"] == "fatal"
    assert "unknown option" in result["summary"]
