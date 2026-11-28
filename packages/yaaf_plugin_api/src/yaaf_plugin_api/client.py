"""Run a plugin's hooks the way yaaf does, on an author's own machine.

`LocalHost` starts the same host program core starts (`yaaf_plugin_api.host`),
as a plain subprocess, and speaks the same frames. It validates every answer
with the same `check_return` and turns every failure into a
`conformance.Failure` with the kind core would give it. That is what makes
`python -m yaaf_plugin_api check --full` the install's verdict before you push.

It is not a sandbox: on your machine the host's in-process guard is all there
is, as in yaaf's own subprocess mode. The install runs the same program behind
a jail or a container.

`run_tests` runs the plugin's own tests/ with pytest, with the plugin and this
package importable, and nothing of the caller's environment but PATH.
"""

from __future__ import annotations

import contextlib
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from yaaf_plugin_api import hooks, wire
from yaaf_plugin_api import types as T
from yaaf_plugin_api.conformance import Failure
from yaaf_plugin_api.manifest import Manifest

API_SRC = Path(__file__).resolve().parent.parent
BOOT = ("import sys; sys.path.insert(0, sys.argv[1]); "
        "from yaaf_plugin_api.host import main; sys.exit(main(sys.argv[2:]))")


def plugin_paths(root: Path) -> list[Path]:
    return [p for p in (root / "src", root) if p.is_dir()]


class LocalHost:
    def __init__(self, root: str | Path, manifest: Manifest, *, python: str | None = None):
        self.root = Path(root).resolve()
        self.manifest = manifest
        self.python = python or sys.executable
        self.proc: subprocess.Popen | None = None
        self.scratch = ""
        self._inbox: queue.Queue = queue.Queue()
        self._id = 0

    def start(self) -> None:
        self.scratch = tempfile.mkdtemp(prefix="yaaf-plugin-check-")
        argv = [self.python, "-I", "-B", "-c", BOOT, str(API_SRC),
                "--module", self.manifest.module, "--scratch", self.scratch]
        for p in plugin_paths(self.root):
            argv += ["--path", str(p)]
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, cwd=self.scratch,
                                     env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                                     start_new_session=True)
        threading.Thread(target=self._read, daemon=True).start()
        first = self._next(20.0, "start")
        if "fatal" in first:
            raise Failure("start", str(first["fatal"]))

    def _read(self) -> None:
        try:
            while (m := wire.read_frame(self.proc.stdout)) is not None:
                self._inbox.put(m)
        except (wire.WireError, OSError, ValueError) as exc:
            self._inbox.put(Failure("protocol", f"unreadable frame: {exc}"))
        self._inbox.put(None)

    def _next(self, timeout: float, during: str) -> dict:
        try:
            m = self._inbox.get(timeout=max(0.0, timeout))
        except queue.Empty:
            self.stop()
            raise Failure("timeout", f"no answer within {timeout:.0f}s ({during})") from None
        if m is None:
            self.stop()
            raise Failure("crash", f"the host exited during {during}")
        if isinstance(m, Failure):
            self.stop()
            raise m
        return m

    def __call__(self, key: hooks.HookKey, args: list, gateway: Any = None) -> Any:
        if self.proc is None or self.proc.poll() is not None:
            self.start()
        limit = self.manifest.limits["action_timeout_s" if key.kind == "data_action"
                                     else "hook_timeout_s"]
        deadline = time.monotonic() + limit
        self._id += 1
        try:
            encoded = wire.encode(list(args))
        except wire.WireError as exc:
            raise Failure("argument", str(exc)) from None
        wire.write_frame(self.proc.stdin, {"id": self._id, "method": "hook", "params": {
            "kind": key.kind, "key": key.key, "args": encoded}})
        while True:
            m = self._next(deadline - time.monotonic(), str(key))
            if "cb" in m:
                wire.write_frame(self.proc.stdin, self._answer(m, key, gateway))
                continue
            violations = [str(v) for v in m.get("violations") or []]
            if violations:
                self.stop()
                raise Failure("violation", "; ".join(violations), violations)
            if "error" in m:
                err = m.get("error") or {}
                raise Failure(str(err.get("type") or "raised"), str(err.get("message")))
            try:
                value = wire.decode(m.get("result"))
            except wire.WireError as exc:
                raise Failure("invalid", str(exc)) from None
            problems = hooks.check_return(key, value, self.manifest)
            if problems:
                raise Failure("invalid", "; ".join(problems))
            return value

    def _answer(self, m: dict, key: hooks.HookKey, gateway: Any) -> dict:
        cb, method = m.get("cb"), m.get("method")
        if method == "gateway.call" and key.kind == "data_action" and gateway is not None:
            params = wire.decode(m.get("params") or {})
            try:
                return {"cb": cb, "result": wire.encode(
                    gateway.call(params.get("tool"), params.get("arguments") or {}))}
            except T.ToolError as exc:
                return {"cb": cb, "error": {"message": exc.message, "state": exc.state}}
        if method == "gateway.call":
            self.stop()
            raise Failure("violation", f"asked for {method} during a hook that may not",
                          [f"callback {method!r} refused"])
        return {"cb": cb, "error": {"message": "not in this conformance run"}}

    def stop(self) -> None:
        if self.proc is not None:
            with contextlib.suppress(Exception):
                os.killpg(self.proc.pid, 9)
            with contextlib.suppress(Exception):
                self.proc.wait(5)
        if self.scratch:
            import shutil

            shutil.rmtree(self.scratch, ignore_errors=True)


def run_tests(root: str | Path, *, python: str | None = None, timeout_s: int = 300,
              extra_paths: list | None = None) -> tuple[str, list[str]]:
    """The plugin's own tests, with pytest: (pass | fail | pending, why)."""
    root = Path(root).resolve()
    if not (root / "tests").is_dir():
        return "fail", ["tests/ is missing"]
    python = python or sys.executable
    probe = subprocess.run([python, "-c", "import pytest"], capture_output=True)
    if probe.returncode != 0:
        return "pending", ["pytest is not installed for the Python the tests would run on"]
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
           "PYTHONPATH": os.pathsep.join([str(API_SRC), *map(str, plugin_paths(root)),
                                          *map(str, extra_paths or [])]),
           "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryDirectory(prefix="yaaf-plugin-tests-") as home:
        env["HOME"] = home
        try:
            proc = subprocess.run(
                [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir",
                 str(root), str(root / "tests")],
                cwd=root, env=env, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return "fail", [f"the plugin's tests took longer than {timeout_s}s"]
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-12:]
    if proc.returncode == 0:
        return "pass", [tail[-1]] if tail else []
    if proc.returncode == 5:
        return "fail", ["tests/ collected no tests"]
    return "fail", ["the plugin's own tests failed:", *tail]
