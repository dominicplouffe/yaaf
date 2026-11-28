"""Core's side of the plugin host: start one, ask it a question, trust nothing it says.

A plugin's code runs in its own process, in a sandbox, behind
`yaaf_plugin_api.host` (docs/plans/factory-plugins.md §3). This module starts
those processes, keeps a few warm per plugin version, and turns every way a
host can fail into an answer that is safe to act on:

* **Nothing the host sends is trusted.** Frames are size-capped and decoded into
  the API's own types only (`wire.decode`). A callback is answered only when
  the hook being asked may make it: `gateway.call` only from a data action.
  This is checked here, not in the host, because the host is the plugin's
  process. Every answer is validated (`hooks.check_return`) before a caller
  sees it.
* **Fail closed** (§1.3). A host that does not start, hangs, dies, breaks the
  protocol, reaches outside its sandbox or answers something unusable is a
  `HostFailure`. `ask_or_fail_closed` turns one into the answer that refuses
  instead of the one that accepts: a gate refuses, an owed list owes, a
  receipt's fields are unknown. A host that timed out, died or misbehaved is
  killed and never asked again. The pool starts a fresh one.
* **No secrets cross.** A host starts with an empty environment, and its
  working directory is a scratch folder of its own. In `host` mode the jail
  also gives it no network namespace and read-only mounts; `subprocess` mode
  has only the in-host guard and is refused on a box holding the Docker socket,
  by the same rule as host-mode success tests.

In `docker` mode only the runner holds the Docker socket, so the runner starts
host containers and the server reaches them through its broker
(`yaaf/plugins/broker.py`, `BrokerLauncher`). If the runner is down, plugin
calls fail closed.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yaaf_plugin_api import hooks as api_hooks
from yaaf_plugin_api import manifest as api_manifest
from yaaf_plugin_api import types as T
from yaaf_plugin_api import wire

log = logging.getLogger("yaaf.plugins.host")

#: Where the API package's source sits in this checkout or install: the host
#: imports it from here, before the plugin's own paths.
API_SRC = Path(api_manifest.__file__).resolve().parent.parent
#: The host's first lines: put the API on the path, then run the host's main.
#: Run with -I, so nothing from the server's environment shapes the import path.
BOOT = ("import sys; sys.path.insert(0, sys.argv[1]); "
        "from yaaf_plugin_api.host import main; sys.exit(main(sys.argv[2:]))")
START_TIMEOUT_S = 20.0
POOL_SIZE = 2
RECYCLE_AFTER = 200
STDERR_TAIL = 4000
#: The callbacks each hook kind may make. The host enforces the same, but the
#: host is the plugin's process, so this is the copy that counts.
CALLBACKS_FOR = {kind: {"read_artifact", "receipt"} for kind in api_hooks.SPECS}
CALLBACKS_FOR["data_action"] = {"read_artifact", "receipt", "gateway.call"}
for _kind in ("classify_tools", "fields_of_read", "migrate_state", "create_fields",
              "validate_create"):
    CALLBACKS_FOR[_kind] = set()  # no snapshot, so nothing to call back about
MAX_CALLBACK_BYTES = 64 * 1024 * 1024

UNAVAILABLE = ("this project's plugin could not be consulted, so nothing it gates can be "
               "accepted right now; try again, and if it keeps failing the chairman will "
               "see why on the project's plugin status")


class HostFailure(Exception):
    """A host could not give a usable answer. `kind` says how, for the log and
    the conformance report. The message never reaches an agent: an agent gets
    the fail-closed answer, which says only that the plugin could not be consulted."""

    def __init__(self, kind: str, message: str, *, violations: list[str] | None = None,
                 stderr: str = ""):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.violations = list(violations or [])
        self.stderr = stderr


@dataclass(frozen=True)
class HostSpec:
    """One plugin version, ready to run: where its code is and what it may use."""

    manifest: api_manifest.Manifest
    #: The commit SHA (or, before step 3, any stable label of the checkout).
    version: str
    root: Path
    #: Directories the plugin imports from, in order: its src/, its root, its built venv.
    paths: tuple[Path, ...]

    @classmethod
    def from_checkout(cls, root: str | Path, *, version: str = "local",
                      site_packages: tuple[Path, ...] = ()) -> HostSpec:
        root = Path(root).resolve()
        manifest = api_manifest.load_path(root)
        paths = tuple(p for p in (root / "src", root) if p.is_dir()) + tuple(site_packages)
        return cls(manifest, version, root, paths)

    @property
    def hook_timeout_s(self) -> float:
        return float(self.manifest.limits["hook_timeout_s"])

    @property
    def action_timeout_s(self) -> float:
        return float(self.manifest.limits["action_timeout_s"])

    @property
    def memory_mb(self) -> int:
        return int(self.manifest.limits["memory_mb"])


@dataclass
class Callbacks:
    """What core answers a hook's callbacks with, for one call and one project.
    A missing callable answers 'not found'. It never falls back to anything wider."""

    read_artifact: Callable[[str, int | None], bytes | None] | None = None
    receipt: Callable[[str], T.Receipt | None] | None = None
    #: Raises yaaf_plugin_api.ToolError for every way a call can fail.
    gateway_call: Callable[[str, Mapping[str, Any]], T.ToolResult] | None = None
    calls: list[str] = field(default_factory=list)


# ── launchers ──────────────────────────────────────────────────────────────────


def host_command(spec: HostSpec, scratch: str, *, python: str | None = None) -> list[str]:
    argv = [python or sys.executable, "-I", "-B", "-c", BOOT, str(API_SRC),
            "--module", spec.manifest.module, "--scratch", scratch]
    for p in spec.paths:
        argv += ["--path", str(p)]
    return argv


def _limits(memory_mb: int) -> Callable[[], None]:
    """Resource caps applied in the child before it runs a line of Python."""
    def apply() -> None:
        import resource

        mem = memory_mb * 1024 * 1024
        for name, value in (("RLIMIT_AS", mem), ("RLIMIT_FSIZE", 64 * 1024 * 1024),
                            ("RLIMIT_CORE", 0), ("RLIMIT_NOFILE", 256)):
            limit = getattr(resource, name, None)
            if limit is not None:
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(limit, (value, value))
    return apply


class Launcher:
    name = "abstract"

    def unavailable(self) -> str | None:
        """Why this launcher cannot start a host on this machine, or None."""
        return None

    def argv(self, spec: HostSpec, scratch: str) -> list[str]:
        raise NotImplementedError

    def spawn(self, spec: HostSpec, scratch: str, stderr) -> subprocess.Popen:
        why = self.unavailable()
        if why:
            raise HostFailure("start", why)
        return subprocess.Popen(
            self.argv(spec, scratch), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=stderr, cwd=scratch, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            start_new_session=True, preexec_fn=_limits(spec.memory_mb), close_fds=True)


class SubprocessLauncher(Launcher):
    """Development and CI only: the host is a plain process. The in-host guard
    is all the containment there is, the same as an agent run in this mode."""

    name = "subprocess"

    def unavailable(self) -> str | None:
        from yaaf.core.config import settings
        from yaaf.runner import sandbox

        if sandbox._host_holds_the_keys() and not getattr(settings(),
                                                          "allow_host_success_tests", False):
            return ("refused: sandbox_mode is 'subprocess' and this machine holds the Docker "
                    "socket, so a plugin's code would run beside root on the box. Run in "
                    "docker or host mode, or set YAAF_ALLOW_HOST_SUCCESS_TESTS=1 if you "
                    "meant it")
        return None

    def argv(self, spec: HostSpec, scratch: str) -> list[str]:
        return host_command(spec, scratch)


class JailLauncher(Launcher):
    """Host mode: the host runs in a bubblewrap jail with no network namespace,
    the plugin and its paths mounted read-only, and its scratch as the one
    writable directory."""

    name = "host"

    def unavailable(self) -> str | None:
        from yaaf.runner import host_jail

        return host_jail.unavailable()

    def argv(self, spec: HostSpec, scratch: str) -> list[str]:
        from yaaf.runner import host_jail

        inner = host_command(spec, "/workspace")
        argv = host_jail.jail_argv(scratch, inner)
        split = argv.index("--")
        binds = ["--unshare-net"]
        for path in dict.fromkeys([str(API_SRC), str(spec.root), *map(str, spec.paths)]):
            if host_jail._exposes(path):
                raise HostFailure("start", f"{path} would expose {host_jail._exposes(path)}")
            binds += ["--ro-bind", path, path]
        return argv[:split] + binds + argv[split:]


class _SocketProc:
    """A broker connection, shaped like the Popen a Host drives: stdin, stdout,
    poll, kill. Closing it is what stops the container on the runner's side."""

    pid = None

    def __init__(self, conn: socket.socket):
        self.conn = conn
        self.stdin = conn.makefile("wb")
        self.stdout = conn.makefile("rb", buffering=0)
        self.closed = False

    def poll(self):
        return 0 if self.closed else None

    def kill(self) -> None:
        self.closed = True
        with contextlib.suppress(OSError):
            self.conn.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            self.conn.close()

    def wait(self, timeout=None):
        return 0


class BrokerLauncher(Launcher):
    """Docker mode: the runner starts the host container (yaaf/plugins/broker.py)
    and this connects to it. Only the runner holds the Docker socket."""

    name = "docker"

    def __init__(self, address: tuple[str, int] | None = None, key: bytes | None = None):
        self._address = address
        self._key = key

    def address(self) -> tuple[str, int]:
        if self._address is not None:
            return self._address
        from yaaf.core.config import settings

        host, _, port = settings().plugin_broker_address.rpartition(":")
        return host, int(port)

    def unavailable(self) -> str | None:
        try:
            self.address()
            if self._key is None:
                from yaaf.plugins import broker

                broker.secret()
        except (RuntimeError, ValueError) as exc:
            return f"the plugin broker cannot be reached: {exc}"
        return None

    def spawn(self, spec: HostSpec, scratch: str, stderr) -> _SocketProc:
        from yaaf.plugins import broker

        why = self.unavailable()
        if why:
            raise HostFailure("start", why)
        key = self._key if self._key is not None else broker.secret()
        try:
            conn = socket.create_connection(self.address(), timeout=START_TIMEOUT_S)
        except OSError as exc:
            raise HostFailure("start", f"the runner's plugin broker at "
                                       f"{':'.join(map(str, self.address()))} is not "
                                       f"reachable ({exc}); is the runner up?") from None
        reader = conn.makefile("rb", buffering=0)
        try:
            challenge = wire.read_frame(reader)
            nonce = str((challenge or {}).get("challenge", ""))
            if not nonce:
                raise HostFailure("start", "the broker sent no challenge")
            broker.send_frame(conn, {"auth": broker.sign(nonce, key), "spec": {
                "module": spec.manifest.module, "root": str(spec.root),
                "paths": [str(p) for p in spec.paths], "memory_mb": spec.memory_mb}})
            answer = wire.read_frame(reader) or {}
            if answer.get("broker") != "ok":
                raise HostFailure("start", f"the broker refused: {answer.get('why', 'no reason')}")
        except (OSError, wire.WireError) as exc:
            conn.close()
            raise HostFailure("start", f"the broker handshake failed: {exc}") from None
        except HostFailure:
            conn.close()
            raise
        conn.settimeout(None)
        return _SocketProc(conn)


def launcher_for_mode(mode: str | None = None) -> Launcher:
    if mode is None:
        from yaaf.core.config import settings

        mode = getattr(settings(), "sandbox_mode", "docker")
    return {"subprocess": SubprocessLauncher, "host": JailLauncher}.get(mode, BrokerLauncher)()


# ── one host ───────────────────────────────────────────────────────────────────


_EOF = object()


class Host:
    """One running plugin host. Used by one caller at a time; the pool sees to that."""

    def __init__(self, spec: HostSpec, launcher: Launcher):
        self.spec = spec
        self.launcher = launcher
        self.calls = 0
        self.dead = False
        self.hooks: frozenset[str] = frozenset()
        self._scratch = tempfile.mkdtemp(prefix="yaaf-plugin-")
        self._stderr = tempfile.TemporaryFile()  # noqa: SIM115 — lives as long as the host; kill() closes it
        self._inbox: queue.Queue = queue.Queue()
        self._next_id = 0
        self.proc: subprocess.Popen | None = None
        try:
            self.proc = launcher.spawn(spec, self._scratch, self._stderr)
            threading.Thread(target=self._read, name=f"plugin-host-{spec.manifest.id}",
                             daemon=True).start()
            ready = self._next(START_TIMEOUT_S, "start")
            if "fatal" in ready:
                raise HostFailure("start", str(ready["fatal"])[:1000], stderr=self.stderr_tail())
            info = ready.get("ready")
            if not isinstance(info, dict) or not isinstance(info.get("hooks"), list):
                raise HostFailure("protocol", "the host's first frame was not 'ready'")
            self.hooks = frozenset(str(h) for h in info["hooks"])
        except BaseException:
            self.kill()
            raise

    def _read(self) -> None:
        try:
            while True:
                message = wire.read_frame(self.proc.stdout)
                if message is None:
                    break
                self._inbox.put(message)
        except (wire.WireError, OSError, ValueError) as exc:
            self._inbox.put(HostFailure("protocol", f"unreadable frame: {exc}"))
        self._inbox.put(_EOF)

    def _next(self, timeout: float, during: str) -> dict:
        try:
            message = self._inbox.get(timeout=max(0.0, timeout))
        except queue.Empty:
            raise HostFailure("timeout", f"no answer within {timeout:.1f}s ({during})",
                              stderr=self.stderr_tail()) from None
        if message is _EOF:
            code = self.proc.poll()
            raise HostFailure("crash", f"the host exited (code {code}) during {during}",
                              stderr=self.stderr_tail())
        if isinstance(message, HostFailure):
            raise message
        return message

    def stderr_tail(self) -> str:
        try:
            self._stderr.flush()
            size = self._stderr.seek(0, os.SEEK_END)
            self._stderr.seek(max(0, size - STDERR_TAIL))
            return self._stderr.read().decode(errors="replace")
        except (OSError, ValueError):
            return ""

    def _send(self, message: dict) -> None:
        try:
            wire.write_frame(self.proc.stdin, message)
        except (OSError, ValueError, wire.WireError) as exc:
            raise HostFailure("crash", f"could not write to the host: {exc}") from None

    def call(self, key: api_hooks.HookKey, args: list, callbacks: Callbacks | None = None,
             timeout: float | None = None) -> Any:
        """The hook's answer, decoded and validated, or HostFailure."""
        if self.dead:
            raise HostFailure("crash", "this host was already stopped")
        callbacks = callbacks or Callbacks()
        allowed = CALLBACKS_FOR.get(key.kind, set())
        timeout = timeout or (self.spec.action_timeout_s if key.kind == "data_action"
                              else self.spec.hook_timeout_s)
        deadline = time.monotonic() + timeout
        self._next_id += 1
        rid = self._next_id
        self.calls += 1
        try:
            try:
                encoded = wire.encode(list(args))
            except wire.WireError as exc:
                # Core's own argument could not cross: nothing was sent, and the
                # host is still good.
                raise HostFailure("argument", f"the question could not be sent: {exc}") \
                    from None
            self._send({"id": rid, "method": "hook", "params": {
                "kind": key.kind, "key": key.key, "args": encoded}})
            while True:
                message = self._next(deadline - time.monotonic(), str(key))
                if "cb" in message:
                    self._send(self._answer(message, allowed, callbacks))
                    continue
                if message.get("id") != rid:
                    raise HostFailure("protocol", "the host answered a call it was not asked")
                violations = [str(v)[:300] for v in message.get("violations") or []][:20]
                if violations:
                    raise HostFailure("violation", "; ".join(violations), violations=violations)
                if "error" in message:
                    err = message.get("error") or {}
                    raise HostFailure(str(err.get("type") or "raised"),
                                      str(err.get("message") or "the hook failed")[:500],
                                      stderr=self.stderr_tail())
                try:
                    value = wire.decode(message.get("result"))
                except wire.WireError as exc:
                    raise HostFailure("invalid", f"the answer is not an API value: {exc}") \
                        from None
                problems = api_hooks.check_return(key, value, self.spec.manifest)
                if problems:
                    raise HostFailure("invalid", "; ".join(problems)[:1000])
                return value
        except HostFailure as exc:
            if exc.kind in ("timeout", "crash", "protocol", "violation"):
                # A host that hung, died, lied or reached out is not asked again.
                self.kill()
            raise

    def _answer(self, message: dict, allowed: set[str], callbacks: Callbacks) -> dict:
        cb, method = message.get("cb"), message.get("method")
        if method not in allowed:
            # The host asked for what this hook may not have. That is the
            # plugin's process misbehaving, not a lookup that missed.
            raise HostFailure("violation", f"asked for {method!r} during a hook that may not",
                              violations=[f"callback {method!r} refused"])
        try:
            params = wire.decode(message.get("params") or {})
        except wire.WireError as exc:
            raise HostFailure("protocol", f"unreadable callback: {exc}") from None
        if not isinstance(params, dict):
            raise HostFailure("protocol", "callback params must be an object")
        callbacks.calls.append(method)
        try:
            if method == "read_artifact":
                body = callbacks.read_artifact(str(params.get("name")), params.get("version")) \
                    if callbacks.read_artifact else None
                if body is not None and len(body) > MAX_CALLBACK_BYTES:
                    return {"cb": cb, "error": {"message": "artifact too large to hand over"}}
                return {"cb": cb, "result": wire.encode(body)} if body is not None else \
                    {"cb": cb, "error": {"message": "no such artifact in this project"}}
            if method == "receipt":
                found = callbacks.receipt(str(params.get("id"))) if callbacks.receipt else None
                return {"cb": cb, "result": wire.encode(found)} if found is not None else \
                    {"cb": cb, "error": {"message": "no such receipt in this project"}}
            if callbacks.gateway_call is None:
                return {"cb": cb, "error": {"message": "no data source is reachable",
                                            "state": "unreachable"}}
            args = params.get("arguments")
            result = callbacks.gateway_call(str(params.get("tool")),
                                            args if isinstance(args, dict) else {})
            return {"cb": cb, "result": wire.encode(result)}
        except T.ToolError as exc:
            return {"cb": cb, "error": {"message": exc.message[:500], "state": exc.state}}
        except wire.WireError as exc:
            return {"cb": cb, "error": {"message": f"the answer could not be sent: {exc}"}}

    def kill(self) -> None:
        if self.dead:
            return
        self.dead = True
        if self.proc is None:
            with contextlib.suppress(Exception):
                self._stderr.close()
            shutil.rmtree(self._scratch, ignore_errors=True)
            return
        with contextlib.suppress(Exception):
            os.killpg(self.proc.pid, 9)
        with contextlib.suppress(Exception):
            self.proc.kill()
        with contextlib.suppress(Exception):
            self.proc.wait(timeout=5)
        for stream in (self.proc.stdin, self.proc.stdout):
            with contextlib.suppress(Exception):
                stream.close()
        with contextlib.suppress(Exception):
            self._stderr.close()
        shutil.rmtree(self._scratch, ignore_errors=True)


# ── the pool ───────────────────────────────────────────────────────────────────


class HostPool:
    """A few warm hosts for one plugin version. A host is recycled after
    RECYCLE_AFTER calls and replaced after any failure that killed it; since
    hooks hold no state, a fresh host answers exactly as the old one would."""

    def __init__(self, spec: HostSpec, launcher: Launcher | None = None, *,
                 size: int = POOL_SIZE, recycle_after: int = RECYCLE_AFTER):
        self.spec = spec
        self.launcher = launcher or launcher_for_mode()
        self.size = size
        self.recycle_after = recycle_after
        self._idle: list[Host] = []
        self._live = 0
        self._lock = threading.Condition()
        self.started = 0

    def _acquire(self, timeout: float) -> Host:
        deadline = time.monotonic() + timeout
        with self._lock:
            while True:
                while self._idle:
                    host = self._idle.pop()
                    if not host.dead and host.proc.poll() is None:
                        return host
                    self._live -= 1
                if self._live < self.size:
                    self._live += 1
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise HostFailure("busy", f"every host of {self.spec.manifest.id} is busy")
                self._lock.wait(remaining)
        try:
            host = Host(self.spec, self.launcher)
        except BaseException:
            with self._lock:
                self._live -= 1
                self._lock.notify()
            raise
        self.started += 1
        return host

    def _release(self, host: Host) -> None:
        with self._lock:
            if host.dead or host.calls >= self.recycle_after:
                host.kill()
                self._live -= 1
            else:
                self._idle.append(host)
            self._lock.notify()

    def call(self, key: api_hooks.HookKey, args: list, callbacks: Callbacks | None = None,
             timeout: float | None = None) -> Any:
        host = self._acquire(START_TIMEOUT_S)
        try:
            return host.call(key, args, callbacks, timeout)
        finally:
            self._release(host)

    def close(self) -> None:
        with self._lock:
            for host in self._idle:
                host.kill()
            self._live -= len(self._idle)
            self._idle.clear()


# ── fail closed ────────────────────────────────────────────────────────────────


def fail_closed(key: api_hooks.HookKey) -> Any:
    """The answer that refuses, for a hook whose host could not answer: what a
    caller acts on when it cannot have the plugin's real one. None means there
    is no safe default and the caller must stop (render, a data action, state)."""
    kind = key.kind
    if kind in ("check_artifact", "check_review"):
        return T.Refuse([UNAVAILABLE])
    if kind in ("plan_gate", "completion_gate"):
        return [T.Owed("plugin_unavailable", UNAVAILABLE)]
    if kind == "fields_of_read":
        return T.ReadFields.unknown()  # proves the read happened, backs no citation
    if kind == "classify_tools":
        return T.Coverage()  # nothing is reachable until the plugin says what is
    if kind == "setup_step":
        return T.StepState(False, "This project's plugin could not be consulted.")
    if kind == "brief":
        return T.Brief("")
    if kind == "validate_create":
        return [UNAVAILABLE]
    return None


def ask_or_fail_closed(pool: HostPool, key: api_hooks.HookKey, args: list,
                       callbacks: Callbacks | None = None) -> tuple[Any, HostFailure | None]:
    """(answer, None), or (the fail-closed answer, why) when the host could not
    give one. The caller logs or records the failure; it never acts on a guess."""
    try:
        return pool.call(key, args, callbacks), None
    except HostFailure as exc:
        log.warning("plugin %s@%s: %s failed closed (%s): %s", pool.spec.manifest.id,
                    pool.spec.version[:12], key, exc.kind, exc.message)
        return fail_closed(key), exc
