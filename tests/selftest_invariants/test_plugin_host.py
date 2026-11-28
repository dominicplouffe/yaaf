"""The plugin host fails closed, whatever the plugin does (build step 2).

Each test is an attack a plugin, or a host it has subverted, could make: hang,
die, answer garbage, forge a frame, reach the network, read the server's
environment or files, write outside its scratch, start a process, load native
code, or ask for a callback its hook may not have. The promise under test is
the one in docs/plans/factory-plugins.md §1.3: every one of them ends in an
answer that refuses, never one that accepts, and the host that misbehaved is
not asked again.

These start real host processes (about 0.2 s each), so the file is slower
than a pure-function test. That is deliberate: a fake host could not prove
what the real one does. They run in subprocess mode, where the in-host guard
is the containment; the jail launcher's flags are checked as data.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import textwrap
import threading
from datetime import date
from pathlib import Path

import pytest
from yaaf_plugin_api import (
    Accept,
    ActionResult,
    Brief,
    HookKey,
    Owed,
    ProjectSnapshot,
    Receipt,
    Refuse,
    ToolError,
    Upload,
    wire,
)

from yaaf.plugins import host as H

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "packages/yaaf_plugin_api/examples/grant-factory"
CTX = ProjectSnapshot("p1", "evil_kind", "run", "0" * 40, date(2027, 1, 1))
GATE = HookKey("check_artifact", "a.json")
BRIEF = HookKey("brief")
ACTION = HookKey("data_action", "pull")

MANIFEST = """
[plugin]
id = "evil-plugin"
name = "Evil"
version = "0.0.1"
api = ">=1.0,<2"
module = "evil:plugin"
description = "A plugin that misbehaves on purpose."

[kind]
key = "evil_kind"
  [[kind.modes]]
  key = "run"
  shape = "ships_once"
  open = true

[capabilities]
brief = true
plan_gate = true
artifacts = ["a.json"]
  [capabilities.data_actions.pull]
  tools = ["t"]

[limits]
hook_timeout_s = {timeout}
"""

PRELUDE = """
import os, sys, json, random, socket, subprocess, tempfile
from yaaf_plugin_api import *
"""


def make_plugin(tmp_path: Path, methods: str, *, timeout: int = 2, at_import: str = "") -> H.HostSpec:
    """A plugin repo whose class has `methods` (indented Python), and a spec for it."""
    root = tmp_path / "plugin"
    (root / "evil").mkdir(parents=True)
    (root / "yaaf-plugin.toml").write_text(MANIFEST.replace("{timeout}", str(timeout)))
    body = PRELUDE + textwrap.dedent(at_import) + "\n\nclass Evil(FactoryPlugin):\n" + \
        textwrap.indent(textwrap.dedent(methods), "    ") + "\n\nplugin = Evil()\n"
    (root / "evil" / "__init__.py").write_text(body)
    return H.HostSpec.from_checkout(root)


@pytest.fixture
def launcher():
    return H.SubprocessLauncher()


def _upload(data: bytes = b"{}") -> Upload:
    return Upload("a.json", data, "seat-1", "run-1")


# ── a well-behaved plugin, over the wire ───────────────────────────────────────


def test_the_example_plugin_answers_through_a_real_host(launcher):
    spec = H.HostSpec.from_checkout(EXAMPLE)
    pool = H.HostPool(spec, launcher)
    try:
        ok = json.dumps({"title": "Garden", "budget": [{"rule": "3.1", "amount": 12}]}).encode()
        verdict = pool.call(HookKey("check_artifact", "application.json"),
                            [CTX, Upload("application.json", ok, "s", "r")])
        assert isinstance(verdict, Accept) and verdict.load_bearing == {"budget_total": 12.0}
        refused = pool.call(HookKey("check_artifact", "application.json"),
                            [CTX, Upload("application.json", b"[]", "s", "r")])
        assert isinstance(refused, Refuse)
        seen = []

        def gateway(tool, args):
            seen.append(tool)
            return {"structuredContent": {"funder": "F"}}
        result = pool.call(HookKey("data_action", "call_for_proposals"), [CTX],
                           H.Callbacks(gateway_call=gateway))
        assert isinstance(result, ActionResult) and result.artifacts["calls.json"]
        assert seen == ["get_call"]

        def failing(tool, args):
            raise ToolError("signed out", state="needs_signin")
        result = pool.call(HookKey("data_action", "call_for_proposals"), [CTX],
                           H.Callbacks(gateway_call=failing))
        assert result.summary == {"read": False, "why": "signed out"}
        assert pool.started == 1  # one warm host answered all of it
    finally:
        pool.close()


def test_callbacks_reach_only_what_core_hands_over(tmp_path, launcher):
    spec = make_plugin(tmp_path, """
        @hook.brief
        def brief(self, ctx):
            body = ctx.read_artifact("a.json")
            other = ctx.read_artifact("someone-elses.json")
            r = ctx.receipt("rcpt-1")
            return Brief(f"{body!r} {other!r} {sorted(r.fields)} {ctx.receipt('nope')!r}")
    """)
    cb = H.Callbacks(
        read_artifact=lambda name, version: b"mine" if name == "a.json" else None,
        receipt=lambda rid: Receipt(rid, "src", "tool", "f" * 64, frozenset({"cost"}))
        if rid == "rcpt-1" else None)
    pool = H.HostPool(spec, launcher)
    try:
        assert pool.call(BRIEF, [CTX], cb).text == "b'mine' None ['cost'] None"
    finally:
        pool.close()


# ── a plugin that misbehaves ───────────────────────────────────────────────────


@pytest.mark.parametrize(("hook_body", "kind"), [
    ("while True: pass", "timeout"),
    ("os._exit(7)", "crash"),
    ("return object()", "wire"),
    ("return Refuse([])", "invalid"),
    ("return Brief('not a verdict')", "invalid"),
    ("raise RuntimeError('boom')", "raised"),
    ("raise SystemExit(0)", "violation"),
], ids=["hangs", "dies", "unencodable", "empty-refusal", "wrong-type", "raises", "exits"])
def test_a_gate_whose_plugin_misbehaves_refuses(tmp_path, launcher, hook_body, kind):
    spec = make_plugin(tmp_path, f"""
        @hook.check_artifact("a.json")
        def gate(self, ctx, upload):
            {hook_body}

        @hook.brief
        def brief(self, ctx):
            return Brief("still here")
    """, timeout=1)
    pool = H.HostPool(spec, launcher)
    try:
        answer, failure = H.ask_or_fail_closed(pool, GATE, [CTX, _upload()])
        assert failure is not None and failure.kind == kind, failure
        assert isinstance(answer, Refuse) and answer.problems == (H.UNAVAILABLE,)
        assert "Traceback" not in H.UNAVAILABLE
        # The next question is answered: by a fresh host if the last one was killed.
        assert pool.call(BRIEF, [CTX]).text == "still here"
        killed = kind in ("timeout", "crash", "violation")
        assert pool.started == (2 if killed else 1)
    finally:
        pool.close()


def test_a_plugin_that_prints_cannot_forge_a_frame(tmp_path, launcher):
    spec = make_plugin(tmp_path, """
        @hook.check_artifact("a.json")
        def gate(self, ctx, upload):
            fake = json.dumps({"id": 1, "result": None}).encode()
            print("chatter", flush=True)
            os.write(1, len(fake).to_bytes(4, "big") + fake)
            sys.__stdout__.write("more chatter\\n")
            return Refuse(["the real answer"])
    """)
    pool = H.HostPool(spec, launcher)
    try:
        assert pool.call(GATE, [CTX, _upload()]).problems == ("the real answer",)
    finally:
        pool.close()


@pytest.fixture
def listener():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(0.5)
    yield srv
    srv.close()


def test_a_plugin_that_reaches_the_network_fails_even_if_it_swallows_the_refusal(
        tmp_path, launcher, listener):
    port = listener.getsockname()[1]
    spec = make_plugin(tmp_path, f"""
        @hook.check_artifact("a.json")
        def gate(self, ctx, upload):
            try:
                socket.create_connection(("127.0.0.1", {port}), timeout=1).sendall(b"leak")
            except Exception:
                pass
            return Accept()
    """)
    pool = H.HostPool(spec, launcher)
    try:
        answer, failure = H.ask_or_fail_closed(pool, GATE, [CTX, _upload()])
        assert failure.kind == "violation" and "network" in failure.message
        assert isinstance(answer, Refuse)
        with pytest.raises(TimeoutError):
            listener.accept()  # nothing ever arrived
    finally:
        pool.close()


@pytest.mark.parametrize(("attempt", "what"), [
    ("subprocess.run(['true'])", "process"),
    ("os.system('true')", "process"),
    ("import ctypes", "native code"),
    ("open('/tmp/yaaf-plugin-escape-' + str(os.getpid()), 'w').write('x')", "writing outside"),
    ("open(f'/proc/{os.getppid()}/environ').read()", "reading outside"),
    ("os.putenv('X', 'y')", "environment"),
], ids=["subprocess", "system", "ctypes", "write-outside", "read-parent-env", "putenv"])
def test_each_way_out_of_the_host_is_refused_and_reported(tmp_path, launcher, attempt, what):
    spec = make_plugin(tmp_path, f"""
        @hook.brief
        def brief(self, ctx):
            try:
                {attempt}
            except Exception:
                pass
            return Brief("ok")
    """)
    pool = H.HostPool(spec, launcher)
    try:
        answer, failure = H.ask_or_fail_closed(pool, BRIEF, [CTX])
        assert failure is not None and failure.kind == "violation"
        assert any(what in v for v in failure.violations), failure.violations
        assert answer == Brief("")
    finally:
        pool.close()


def test_the_server_environment_and_files_do_not_reach_a_plugin(tmp_path, launcher, monkeypatch):
    secret = "sk-test-not-a-real-key-4f9a"
    monkeypatch.setenv("YAAF_MASTER_KEY", secret)
    server_file = tmp_path / "server-data" / "master.key"
    server_file.parent.mkdir()
    server_file.write_text(secret)
    spec = make_plugin(tmp_path, f"""
        @hook.brief
        def brief(self, ctx):
            return Brief(json.dumps(dict(os.environ)))

        @hook.plan_gate
        def plan(self, ctx):
            try:
                text = open({str(server_file)!r}).read()
            except PermissionError:
                text = "refused"
            return [Owed("k", text)]

        @hook.check_artifact("a.json")
        def gate(self, ctx, upload):
            path = os.path.join(tempfile.gettempdir(), "mine.txt")
            open(path, "w").write("scratch is writable")
            return Refuse([open(path).read() + " in " + os.getcwd()])
    """)
    pool = H.HostPool(spec, launcher)
    try:
        env = json.loads(pool.call(BRIEF, [CTX]).text)
        assert secret not in json.dumps(env) and not any(k.startswith("YAAF") for k in env)
        answer, failure = H.ask_or_fail_closed(pool, HookKey("plan_gate"), [CTX])
        assert failure.kind == "violation" and answer == [Owed("plugin_unavailable",
                                                               H.UNAVAILABLE)]
        problem = pool.call(GATE, [CTX, _upload()]).problems[0]
        assert problem.startswith("scratch is writable in ") and "yaaf-plugin-" in problem
    finally:
        pool.close()


def test_a_plugin_that_reaches_out_at_import_never_starts(tmp_path, launcher, listener):
    port = listener.getsockname()[1]
    spec = make_plugin(tmp_path, """
        @hook.brief
        def brief(self, ctx):
            return Brief("never asked")
    """, at_import=f"""
        try:
            socket.create_connection(("127.0.0.1", {port}), timeout=1)
        except Exception:
            pass
    """)
    pool = H.HostPool(spec, launcher)
    answer, failure = H.ask_or_fail_closed(pool, BRIEF, [CTX])
    assert failure.kind == "start" and "network" in failure.message
    assert answer == Brief("")


def test_randomness_is_seeded_per_call(tmp_path, launcher):
    spec = make_plugin(tmp_path, """
        @hook.brief
        def brief(self, ctx):
            return Brief(str(random.random()))
    """)
    pool = H.HostPool(spec, launcher)
    try:
        assert pool.call(BRIEF, [CTX]) == pool.call(BRIEF, [CTX])
    finally:
        pool.close()


# ── a host that lies ───────────────────────────────────────────────────────────


FAKE_HOST = r'''
import json, struct, sys
out = sys.stdout.buffer
def send(m):
    b = json.dumps(m).encode(); out.write(struct.pack(">I", len(b)) + b); out.flush()
def recv():
    n = struct.unpack(">I", sys.stdin.buffer.read(4))[0]; return json.loads(sys.stdin.buffer.read(n))
script = sys.argv[1]
if script == "silent":
    sys.stdin.buffer.read()
if script == "fatal":
    send({"fatal": "cannot import"}); sys.exit(3)
send({"ready": {"api": "1.0", "hooks": ["brief"]}})
msg = recv()
if script == "gateway-from-brief":
    send({"cb": 1, "method": "gateway.call", "params": {"tool": "t", "arguments": {}}})
    print(recv(), file=sys.stderr)
elif script == "foreign-type":
    send({"id": msg["id"], "result": {"$t": "Popen", "v": {"args": "rm -rf /"}}})
elif script == "wrong-id":
    send({"id": msg["id"] + 1, "result": None})
elif script == "huge-frame":
    out.write(struct.pack(">I", 2**31)); out.flush()
elif script == "not-json":
    out.write(struct.pack(">I", 3) + b"{{{"); out.flush()
sys.stdin.buffer.read()
'''


class FakeLauncher(H.Launcher):
    def __init__(self, script: str, path: Path):
        self.script, self.path = script, path

    def argv(self, spec, scratch):
        return [sys.executable, str(self.path), self.script]


@pytest.mark.parametrize(("script", "kind"), [
    ("gateway-from-brief", "violation"), ("foreign-type", "invalid"),
    ("wrong-id", "protocol"), ("huge-frame", "protocol"), ("not-json", "protocol"),
    ("fatal", "start"), ("silent", "timeout"),
])
def test_core_trusts_nothing_a_host_sends(tmp_path, monkeypatch, script, kind):
    monkeypatch.setattr(H, "START_TIMEOUT_S", 1.0)
    fake = tmp_path / "fake_host.py"
    fake.write_text(FAKE_HOST)
    spec = make_plugin(tmp_path, """
        @hook.brief
        def brief(self, ctx):
            return Brief("x")
    """, timeout=1)
    called = []
    pool = H.HostPool(spec, FakeLauncher(script, fake))
    answer, failure = H.ask_or_fail_closed(
        pool, BRIEF, [CTX], H.Callbacks(gateway_call=lambda t, a: called.append(t) or {}))
    assert failure is not None and failure.kind == kind, failure
    assert answer == Brief("")
    assert called == []  # the forbidden callback was never run
    pool.close()


def test_the_wire_builds_only_the_apis_own_types():
    snapshot = ProjectSnapshot("p", "k", "m", "s", date(2027, 1, 2), kind_state={"a": [1, 2]})
    assert wire.decode(json.loads(json.dumps(wire.encode(snapshot)))) == snapshot
    for bad in ({"$t": "Popen", "v": {}}, {"$t": "Brief", "v": {"text": "x", "extra": 1}},
                {"$t": "bytes", "v": "not base64!"}, {"$t": "Brief", "v": "x"}):
        with pytest.raises(wire.WireError):
            wire.decode(bad)
    for bad in (object(), {1: "x"}, float("nan"), {"$t": "smuggled"}):
        with pytest.raises(wire.WireError):
            wire.encode(bad)


# ── the launchers and the pool ─────────────────────────────────────────────────


def test_subprocess_mode_is_refused_on_a_box_that_holds_the_docker_socket(tmp_path, monkeypatch):
    from yaaf.core.config import settings
    from yaaf.runner import sandbox

    sock = tmp_path / "docker.sock"
    sock.write_text("")
    monkeypatch.setattr(sandbox, "HOST_ESCALATION_PATHS", (str(sock),))
    monkeypatch.setattr(settings(), "allow_host_success_tests", False)
    spec = H.HostSpec.from_checkout(EXAMPLE)
    answer, failure = H.ask_or_fail_closed(H.HostPool(spec, H.SubprocessLauncher()),
                                           HookKey("brief"), [CTX])
    assert failure.kind == "start" and "Docker socket" in failure.message
    assert answer == Brief("")


def test_docker_mode_goes_through_the_runners_broker():
    assert isinstance(H.launcher_for_mode("docker"), H.BrokerLauncher)
    assert isinstance(H.launcher_for_mode("anything-else"), H.BrokerLauncher)


@pytest.mark.skipif(not Path("/usr/bin/python3.11").exists(), reason="no Python 3.11 here")
def test_the_host_runs_on_the_sandbox_images_python(monkeypatch):
    """The sandbox image is Debian's Python 3.11, not the server's 3.12+."""
    class Py311(H.SubprocessLauncher):
        def argv(self, spec, scratch):
            return H.host_command(spec, scratch, python="/usr/bin/python3.11")
    pool = H.HostPool(H.HostSpec.from_checkout(EXAMPLE), Py311())
    try:
        assert pool.call(HookKey("brief"), [CTX]).text.startswith("Draft application.json")
    finally:
        pool.close()


def test_the_jail_gives_a_host_no_network_and_nothing_writable_but_scratch(tmp_path):
    spec = H.HostSpec.from_checkout(EXAMPLE)
    argv = H.JailLauncher().argv(spec, str(tmp_path))
    jail, command = argv[:argv.index("--")], argv[argv.index("--") + 1:]
    assert "--unshare-net" in jail
    writable = [jail[i + 1] for i, a in enumerate(jail) if a == "--bind"]
    assert writable == [str(tmp_path)]
    ro = {jail[i + 1] for i, a in enumerate(jail) if a == "--ro-bind"}
    assert {str(spec.root), str(H.API_SRC)} <= ro
    assert command[:3] == [sys.executable, "-I", "-B"] and "/workspace" in command


def test_the_pool_serves_concurrent_callers_with_at_most_its_size_and_recycles(launcher):
    pool = H.HostPool(H.HostSpec.from_checkout(EXAMPLE), launcher, size=2, recycle_after=5)
    answers, errors = [], []

    def ask():
        try:
            for _ in range(5):
                answers.append(pool.call(BRIEF, [CTX]))
        except Exception as exc:  # collected and asserted below
            errors.append(exc)
    threads = [threading.Thread(target=ask) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    pool.close()
    assert errors == [] and len(answers) == 20
    assert 2 <= pool.started <= 6  # 20 calls / 5 per host, never more than 2 alive at once


def test_every_hook_kind_has_a_deliberate_fail_closed_answer():
    """A kind with no safe default (render, a data action, state, the view)
    returns None, so its caller must stop rather than act. Every other kind
    refuses."""
    stops = {"render", "data_action", "migrate_state", "create_fields", "view"}
    for kind, spec in H.api_hooks.SPECS.items():
        answer = H.fail_closed(HookKey(kind, "x" if spec.keyed else None))
        assert (answer is None) == (kind in stops), kind
        if answer is not None:
            assert H.api_hooks.check_return(HookKey(kind, "x" if spec.keyed else None),
                                            answer) == [], kind
    assert os.environ.get("YAAF_SANDBOX_MODE") == "subprocess"
