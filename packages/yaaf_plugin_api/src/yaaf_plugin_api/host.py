"""The plugin host: the one program that imports a plugin and answers core.

Core starts it inside a sandbox (docs/plans/factory-plugins.md §3) and talks to
it over stdin/stdout in `wire` frames:

    host → core   {"ready": {"api", "hooks"}}  or  {"fatal": "why"}   once, at start
    core → host   {"id", "method": "hook", "params": {"kind", "key", "args"}}
    host → core   {"cb", "method": "read_artifact" | "receipt" | "gateway.call", "params"}
    core → host   {"cb", "result"} | {"cb", "error": {"message", "state"}}
    host → core   {"id", "result", "violations"} | {"id", "error", "violations"}

The sandbox is the isolation: no network, a read-only plugin, scratch on a
tmpfs, no secrets in the environment. The guard below runs inside the host as
well. It is defense in depth, and it produces telemetry for MIN-12: a Python
audit hook refuses network, process creation, native code loading, and writes
or reads outside the paths this host was given. Each refusal is reported with
the call that caused it, and core fails that call even if the plugin caught the
PermissionError. An audit hook is not a sandbox. Native code, or Python that
deliberately digs the guard out of the interpreter, could get past it. That is
why `ctypes` is refused, and why the docker and jail launchers (no network
namespace, read-only mounts, an empty environment), not this file, are what
core relies on. In `subprocess` mode (development and CI only) this guard is
all there is, exactly as a run has no isolation in that mode.

Whatever the plugin prints goes to stderr. File descriptor 1 is the protocol's
alone, so a stray `print` cannot forge a frame.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import os
import random
import sys
import traceback
from pathlib import Path
from typing import Any

from yaaf_plugin_api import types as T
from yaaf_plugin_api import wire
from yaaf_plugin_api.hooks import FactoryPlugin, HookKey
from yaaf_plugin_api.version import API_VERSION

#: Callbacks a hook may make per call, and for which hooks. A data action alone
#: reaches a data source, through core.
MAX_CALLBACKS = 2_000
CALLBACKS = {"read_artifact", "receipt", "gateway.call"}
GATEWAY_HOOKS = {"data_action"}
#: Every audit event that reaches outside the host, and what it is called in a refusal.
_DENIED = {
    "socket.connect": "network", "socket.bind": "network", "socket.getaddrinfo": "network",
    "socket.sendto": "network", "socket.sendmsg": "network", "socket.gethostbyname": "network",
    "subprocess.Popen": "process", "os.system": "process", "os.exec": "process",
    "os.posix_spawn": "process", "os.spawn": "process", "os.fork": "process",
    "os.forkpty": "process", "os.kill": "process", "os.killpg": "process",
    "ctypes.dlopen": "native code", "ctypes.dlsym": "native code",
    "ctypes.cdata": "native code", "sys.setprofile": "tracing",
    "sys.settrace": "tracing",
}
_WRITE_EVENTS = {"os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.chown",
                 "os.link", "os.symlink", "os.truncate", "os.utime", "shutil.rmtree"}


class Guard:
    """Refuses what a plugin may never do, and remembers each attempt."""

    def __init__(self, read_roots: list[str], scratch: str):
        self.read_roots = [os.path.realpath(r) for r in read_roots if r]
        self.scratch = os.path.realpath(scratch)
        self.violations: list[str] = []

    def _inside(self, path: Any, roots: list[str]) -> bool:
        if isinstance(path, int):
            return True  # an already-open descriptor: it was checked when it was opened
        try:
            real = os.path.realpath(os.fsdecode(path))
        except (TypeError, ValueError):
            return False
        return any(real == r or real.startswith(r.rstrip("/") + "/") for r in roots)

    def deny(self, what: str, detail: str) -> None:
        text = f"{what} is not allowed in a plugin hook ({detail})"
        self.violations.append(text[:300])
        raise PermissionError(f"yaaf plugin host: {text}")

    def __call__(self, event: str, args: tuple) -> None:
        if event in _DENIED:
            self.deny(_DENIED[event], event)
        elif event == "open":
            path, mode, flags = (list(args) + [None, None, None])[:3]
            writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT
                                                    | os.O_APPEND | os.O_TRUNC))
            if writes and not self._inside(path, [self.scratch]):
                self.deny("writing outside scratch", f"open {path!r}")
            if not writes and not self._inside(path, [*self.read_roots, self.scratch]):
                self.deny("reading outside the plugin", f"open {path!r}")
        elif event in _WRITE_EVENTS:
            path = args[0] if args else None
            if not self._inside(path, [self.scratch]):
                self.deny("changing files outside scratch", f"{event} {path!r}")
        elif event in ("os.putenv", "os.unsetenv"):
            self.deny("changing the environment", event)


class _Channel:
    """The protocol stream, and the callbacks a running hook makes on it."""

    def __init__(self, rx, tx):
        self.rx, self.tx = rx, tx
        self.next_cb = 0
        self.used = 0
        self.allowed: set[str] = set()

    def send(self, message: dict) -> None:
        wire.write_frame(self.tx, message)

    def callback(self, method: str, params: dict) -> Any:
        if method not in self.allowed:
            raise PermissionError(f"yaaf plugin host: {method} is not available to this hook")
        self.used += 1
        if self.used > MAX_CALLBACKS:
            raise PermissionError(f"yaaf plugin host: more than {MAX_CALLBACKS} callbacks "
                                  "in one hook")
        self.next_cb += 1
        cb = self.next_cb
        self.send({"cb": cb, "method": method, "params": wire.encode(params)})
        reply = wire.read_frame(self.rx)
        if reply is None:
            raise SystemExit(0)  # core went away mid-call: nothing left to answer
        if reply.get("cb") != cb:
            raise wire.WireError("core answered a different callback")
        if "error" in reply:
            err = reply["error"] or {}
            if method == "gateway.call":
                raise T.ToolError(str(err.get("message", "the call failed")),
                                  state=str(err.get("state", "unreachable")))
            return None  # read_artifact and receipt promise None for "not in this project"
        return wire.decode(reply.get("result"))


class _Gateway:
    def __init__(self, channel: _Channel):
        self._channel = channel

    def call(self, tool: str, arguments=None):
        return self._channel.callback("gateway.call", {"tool": str(tool),
                                                       "arguments": dict(arguments or {})})


def _with_callbacks(value: Any, channel: _Channel) -> Any:
    if isinstance(value, T.ProjectSnapshot):
        return dataclasses.replace(
            value,
            _read_artifact=lambda name, version=None: channel.callback(
                "read_artifact", {"name": name, "version": version}),
            _receipt=lambda rid: channel.callback("receipt", {"id": rid}))
    return value


def load_plugin(module_ref: str) -> FactoryPlugin:
    module_name, _, attr = module_ref.partition(":")
    plugin = getattr(importlib.import_module(module_name), attr)
    if not isinstance(plugin, FactoryPlugin):
        raise TypeError(f"{module_ref} is a {type(plugin).__name__}, not a FactoryPlugin")
    return plugin


def serve(plugin: FactoryPlugin, channel: _Channel, guard: Guard) -> int:
    found = plugin.hooks()
    channel.send({"ready": {"api": API_VERSION, "hooks": sorted(map(str, found))}})
    while True:
        try:
            message = wire.read_frame(channel.rx)
        except wire.WireError as exc:
            print(f"yaaf plugin host: bad frame from core: {exc}", file=sys.stderr)
            return 2
        if message is None:
            return 0
        rid = message.get("id")
        if message.get("method") != "hook":
            channel.send({"id": rid, "error": {"type": "protocol",
                                               "message": "only hook calls are answered"}})
            continue
        params = message.get("params") or {}
        key = HookKey(str(params.get("kind")), params.get("key"))
        fn = found.get(key)
        if fn is None:
            channel.send({"id": rid, "error": {"type": "missing",
                                               "message": f"this plugin has no @hook.{key}"},
                          "violations": []})
            continue
        channel.allowed = {"read_artifact", "receipt"} | (
            {"gateway.call"} if key.kind in GATEWAY_HOOKS else set())
        channel.used = 0
        guard.violations = []
        try:
            args = [_with_callbacks(a, channel) for a in wire.decode(params.get("args") or [])]
            if key.kind in GATEWAY_HOOKS:
                args.append(_Gateway(channel))
        except wire.WireError as exc:
            channel.send({"id": rid, "error": {"type": "wire", "message": str(exc)},
                          "violations": []})
            continue
        # Seeded per call: a hook that reaches for randomness gets the same
        # numbers on every call, so the determinism check (MIN-11) measures the
        # plugin and not the dice.
        random.seed(0)
        try:
            result = fn(*args)
            reply = {"id": rid, "result": wire.encode(result), "violations": guard.violations}
        except BaseException as exc:  # a plugin's failure is a reply, never the host's death
            if isinstance(exc, (SystemExit, KeyboardInterrupt)) and not guard.violations:
                guard.violations.append(f"the hook tried to stop the host ({type(exc).__name__})")
            traceback.print_exc(file=sys.stderr)
            kind = "wire" if isinstance(exc, wire.WireError) else "raised"
            reply = {"id": rid, "error": {"type": kind,
                                          "message": f"{type(exc).__name__}: {exc}"[:500]},
                     "violations": guard.violations}
        channel.send(reply)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="yaaf-plugin-host")
    parser.add_argument("--module", required=True)
    parser.add_argument("--path", action="append", default=[],
                        help="a directory the plugin imports from (repeatable)")
    parser.add_argument("--scratch", required=True)
    args = parser.parse_args(argv)

    # The protocol keeps fd 1 to itself: the plugin's stdout, Python- or C-level,
    # lands on stderr.
    tx = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    rx = os.fdopen(os.dup(0), "rb", buffering=0)
    os.close(0)
    channel = _Channel(rx, tx)

    # The plugin is read-only (mounted so in a jail or container): an import
    # must not try to write its bytecode beside it.
    sys.dont_write_bytecode = True
    os.environ.clear()
    os.environ.update({"HOME": args.scratch, "TMPDIR": args.scratch, "LANG": "C.UTF-8"})
    os.chdir(args.scratch)
    sys.path[:0] = [p for p in args.path if p]
    import tempfile

    tempfile.tempdir = args.scratch
    roots = [*args.path, sys.prefix, sys.base_prefix, sys.exec_prefix,
             *(p for p in sys.path if p), str(Path(__file__).resolve().parent.parent),
             "/dev/urandom", "/dev/null", "/etc/localtime", "/usr/share/zoneinfo"]
    guard = Guard(roots, args.scratch)
    # Armed once, before the plugin's first line runs, and never disarmed: a
    # switch the host could flip is a switch plugin code could find and flip.
    # The host's own work (frames on descriptors it already holds, reading its
    # own modules) trips none of it.
    sys.addaudithook(guard)
    try:
        plugin = load_plugin(args.module)
    except BaseException as exc:
        traceback.print_exc(file=sys.stderr)
        why = f"{args.module} could not be loaded: {type(exc).__name__}: {exc}"
        if guard.violations:
            why += f" ({'; '.join(guard.violations)})"
        channel.send({"fatal": why[:1000]})
        return 3
    if guard.violations:
        channel.send({"fatal": f"{args.module} reached outside the host at import: "
                               + "; ".join(guard.violations)})
        return 3
    return serve(plugin, channel, guard)


if __name__ == "__main__":
    sys.exit(main())
