"""Docker mode's route to a plugin host: the runner's broker (build step 2).

Only the runner holds the Docker socket, so the server asks the runner's broker
to start a host and then talks to it through that connection. The attacks: a
caller without the key, or one asking to mount a path outside the plugins root,
gets no host at all. A host reached this way fails closed exactly as a local
one does, and dies when its connection does. The container's flags are fixed
by the broker, not by the request. And the broker is unreachable from the
sandbox network.

The broker here is real and listens on TCP. The container is replaced by a
local host process, because CI has no Docker daemon to start one in. The flags
the container would get are checked as data.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import pytest
import yaml
from yaaf_plugin_api import Accept, HookKey, ProjectSnapshot, Refuse, Upload

from yaaf.plugins import broker as B
from yaaf.plugins import host as H

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "packages/yaaf_plugin_api/examples/grant-factory"
CTX = ProjectSnapshot("p1", "grant_application", "draft", "0" * 40, date(2027, 1, 1))
KEY = b"k" * 32
GATE = HookKey("check_artifact", "application.json")


class LocalHost:
    """The broker's launch, minus Docker: the same host program as a process."""

    started: list = []

    def __init__(self, spec: dict):
        self.scratch = tempfile.mkdtemp(prefix="yaaf-broker-test-")
        argv = [sys.executable, "-I", "-B", "-S", "-c", H.BOOT, str(H.API_SRC),
                "--module", spec["module"], "--scratch", self.scratch]
        for p in spec["paths"]:
            argv += ["--path", p]
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     env={"PATH": "/usr/bin:/bin"}, cwd=self.scratch)
        LocalHost.started.append(self)

    def write(self, data: bytes) -> None:
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def read_stdout(self, deliver) -> None:
        while chunk := self.proc.stdout.read1(65536):
            deliver(chunk)

    def close(self) -> None:
        self.proc.kill()
        self.proc.wait(5)
        shutil.rmtree(self.scratch, ignore_errors=True)


@pytest.fixture
def plugins_root(tmp_path):
    root = tmp_path / "plugins"
    shutil.copytree(EXAMPLE, root / "grant-factory",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return root


@pytest.fixture
def broker(plugins_root):
    import threading

    LocalHost.started = []
    server = B.BrokerServer(("127.0.0.1", 0), key=KEY, root=plugins_root, launch=LocalHost)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()
    for h in LocalHost.started:
        h.close()


def _pool(broker, spec, key=KEY):
    return H.HostPool(spec, H.BrokerLauncher(broker.server_address, key))


def test_a_host_started_by_the_runner_answers_the_server(broker, plugins_root):
    spec = H.HostSpec.from_checkout(plugins_root / "grant-factory")
    pool = _pool(broker, spec)
    try:
        ok = json.dumps({"title": "G", "budget": [{"rule": "1", "amount": 3}]}).encode()
        assert isinstance(pool.call(GATE, [CTX, Upload("application.json", ok, "s", "r")]),
                          Accept)
        assert isinstance(pool.call(GATE, [CTX, Upload("application.json", b"x", "s", "r")]),
                          Refuse)
        result = pool.call(HookKey("data_action", "call_for_proposals"), [CTX], H.Callbacks(
            gateway_call=lambda tool, args: {"structuredContent": {"funder": "F"}}))
        assert result.artifacts["calls.json"] == b'{"funder": "F"}'
        assert len(LocalHost.started) == 1
    finally:
        pool.close()


def test_a_caller_without_the_key_gets_no_host(broker, plugins_root):
    spec = H.HostSpec.from_checkout(plugins_root / "grant-factory")
    answer, failure = H.ask_or_fail_closed(_pool(broker, spec, key=b"wrong" * 8), GATE,
                                           [CTX, Upload("application.json", b"{}", "s", "r")])
    assert failure.kind == "start" and "authentication failed" in failure.message
    assert isinstance(answer, Refuse) and LocalHost.started == []


def test_a_path_outside_the_plugins_root_is_never_mounted(broker, tmp_path):
    outside = tmp_path / "elsewhere"
    shutil.copytree(EXAMPLE, outside, ignore=shutil.ignore_patterns("__pycache__"))
    spec = H.HostSpec.from_checkout(outside)
    answer, failure = H.ask_or_fail_closed(_pool(broker, spec), GATE,
                                           [CTX, Upload("application.json", b"{}", "s", "r")])
    assert failure.kind == "start" and "not under the plugins root" in failure.message
    assert LocalHost.started == []


def test_a_traversal_or_a_link_out_of_the_root_is_refused(plugins_root, tmp_path):
    root = plugins_root.resolve()
    (plugins_root / "sneaky").symlink_to(tmp_path)
    base = {"module": "m:p", "memory_mb": 256}
    for paths in ([str(root / "grant-factory" / ".." / ".." / "etc")],
                  [str(root / "sneaky")], ["/"]):
        spec, why = B.check_spec({**base, "root": str(root / "grant-factory"),
                                  "paths": paths}, root)
        assert spec is None and "plugins root" in why
    assert B.check_spec({**base, "root": str(root), "paths": [str(root)],
                         "memory_mb": 99_999}, root)[0] is None
    assert B.check_spec({**base, "module": "os; rm -rf", "root": str(root),
                         "paths": [str(root)]}, root)[0] is None


def test_a_host_reached_through_the_broker_dies_with_its_connection(broker, plugins_root):
    spec = H.HostSpec.from_checkout(plugins_root / "grant-factory")
    pool = _pool(broker, spec)
    pool.call(HookKey("brief"), [CTX])
    proc = LocalHost.started[0].proc
    pool.close()
    deadline = time.monotonic() + 5
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert proc.poll() is not None


def test_an_unreachable_runner_fails_closed_and_says_so():
    spec = H.HostSpec.from_checkout(EXAMPLE)
    pool = H.HostPool(spec, H.BrokerLauncher(("127.0.0.1", 1), KEY))
    answer, failure = H.ask_or_fail_closed(pool, HookKey("completion_gate"), [CTX])
    assert failure.kind == "start" and "is the runner up" in failure.message
    assert answer == [H.T.Owed("plugin_unavailable", H.UNAVAILABLE)]


def test_the_container_flags_are_the_brokers_not_the_callers():
    spec = {"module": "grant_factory:plugin", "root": "/var/lib/yaaf/plugins/x",
            "paths": ["/var/lib/yaaf/plugins/x/src", "/var/lib/yaaf/plugins/x"],
            "memory_mb": 256}
    opts = B.container_options(spec, "yaaf-sandbox:latest", {"yaaf.stack": "t"})
    assert opts["network_mode"] == "none" and opts["read_only"] is True
    assert opts["cap_drop"] == ["ALL"] and "no-new-privileges" in opts["security_opt"]
    assert opts["pids_limit"] <= 64 and opts["mem_limit"] == opts["memswap_limit"] == "256m"
    assert all(m["mode"] == "ro" for m in opts["volumes"].values())
    assert set(opts["volumes"]) == {spec["root"], *spec["paths"]}
    assert opts["environment"] == {"LANG": "C.UTF-8"}
    assert all("noexec" in v for v in opts["tmpfs"].values())
    assert opts["command"][:4] == ["python3", "-I", "-B", "-S"] and B.IMAGE_API_SRC in opts["command"]


def test_the_broker_is_off_the_sandbox_network_and_the_runner_mounts_plugins_read_only():
    compose = yaml.safe_load((ROOT / "deploy/docker-compose.yml").read_text())
    runner, api = compose["services"]["runner"], compose["services"]["api"]
    assert "sandbox" not in (runner.get("networks") or ["default"])
    assert not runner.get("ports"), "the broker must not be published"
    mounts = [v for v in runner["volumes"] if "/plugins" in v]
    assert mounts and all(v.endswith(":ro") for v in mounts)
    assert api["environment"]["YAAF_PLUGIN_BROKER_ADDRESS"] == "runner:24250"
    sandbox_df = (ROOT / "deploy/sandbox/Dockerfile").read_text()
    assert f"COPY packages/yaaf_plugin_api/src {B.IMAGE_API_SRC}" in sandbox_df


def test_the_broker_key_comes_from_the_master_key_and_differs_from_it(monkeypatch):
    from yaaf.core.config import settings

    monkeypatch.setattr(settings(), "master_key", "a-master-key-for-tests")
    key = B.secret()
    assert len(key) == 32 and b"a-master-key-for-tests" not in key
    monkeypatch.setattr(settings(), "master_key", "")
    assert "MASTER_KEY" in H.BrokerLauncher(("127.0.0.1", 1)).unavailable()
