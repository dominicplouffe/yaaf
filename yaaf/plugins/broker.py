"""The runner's plugin-host broker: docker mode's way to a sandboxed host.

In a docker deployment only the runner holds the Docker socket, and the API
server, which asks the gates, must not: it is the internet-facing process, and
the socket is root on the box (docs/plans/factory-plugins.md, step 2
decision). So the runner listens on the stack's internal network, and the
server's `BrokerLauncher` connects to it, one TCP connection per host:

    broker → server   {"challenge": nonce}
    server → broker   {"auth": HMAC(secret, nonce), "spec": {module, root, paths, memory_mb}}
    broker → server   {"broker": "ok"}  or  {"broker": "refused", "why": ...} and close
    then              the host's own frames, both ways, byte for byte

The broker does not read the host's frames. It proxies them, and core checks
them on the server exactly as it does for a local host. What the broker holds
is the launch: the container's flags are fixed here (`container_options`), not
sent by the server, and the paths the server may ask to mount are limited to
the plugins root. A leaked secret therefore buys a host that runs a plugin
already on disk under the same flags, and nothing wider.

The secret is derived from YAAF_MASTER_KEY, which both services already hold,
so there is no new key to provision. The listener is reachable only on the
stack's default network: the runner is not on the sandbox network, so no agent
container can reach it at all.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import logging
import secrets
import socket
import socketserver
import struct
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from yaaf_plugin_api import wire

log = logging.getLogger("yaaf.plugins.broker")

AUTH_TIMEOUT_S = 10.0
#: Where the API package's source sits in the sandbox image (deploy/sandbox/Dockerfile).
IMAGE_API_SRC = "/opt/yaaf-plugin-api/src"
SCRATCH = "/scratch"


def secret() -> bytes:
    from yaaf.core.config import settings

    key = settings().master_key
    if not key:
        raise RuntimeError("YAAF_MASTER_KEY is not set; the plugin broker cannot authenticate")
    return hmac.new(key.encode(), b"yaaf plugin broker v1", hashlib.sha256).digest()


def sign(nonce: str, key: bytes) -> str:
    return hmac.new(key, nonce.encode(), hashlib.sha256).hexdigest()


def _plugins_root() -> Path:
    from yaaf.core.config import settings

    return Path(settings().plugins_root).resolve()


def check_spec(spec: Any, root: Path) -> tuple[dict | None, str | None]:
    """The spec a broker will launch, or why it will not: only a module name,
    paths under the plugins root, and a memory cap within the ceiling."""
    from yaaf_plugin_api.manifest import LIMIT_CEILINGS, MODULE

    if not isinstance(spec, dict):
        return None, "spec must be an object"
    module = spec.get("module")
    if not isinstance(module, str) or not MODULE.match(module):
        return None, "module must be package.module:object"
    paths = spec.get("paths")
    if not isinstance(paths, list) or not paths or len(paths) > 8:
        return None, "paths must list 1-8 directories"
    resolved = []
    for p in [spec.get("root"), *paths]:
        if not isinstance(p, str):
            return None, "every path is a string"
        real = Path(p).resolve()
        if real != root and root not in real.parents:
            return None, f"{p} is not under the plugins root"
        resolved.append(str(real))
    memory = spec.get("memory_mb")
    if not isinstance(memory, int) or isinstance(memory, bool) or \
            not 1 <= memory <= LIMIT_CEILINGS["memory_mb"]:
        return None, "memory_mb is out of range"
    return {"module": module, "root": resolved[0], "paths": resolved[1:],
            "memory_mb": memory}, None


def container_options(spec: dict, image: str, labels: dict) -> dict:
    """The one place a plugin host container's flags are decided. Nothing here
    comes from the request except which plugin, already checked by check_spec."""
    command = ["python3", "-I", "-B", "-c",
               "import sys; sys.path.insert(0, sys.argv[1]); "
               "from yaaf_plugin_api.host import main; sys.exit(main(sys.argv[2:]))",
               IMAGE_API_SRC, "--module", spec["module"], "--scratch", SCRATCH]
    for p in spec["paths"]:
        command += ["--path", p]
    mounts = {p: {"bind": p, "mode": "ro"} for p in dict.fromkeys([spec["root"], *spec["paths"]])}
    return {
        "image": image, "command": command, "labels": labels, "volumes": mounts,
        "network_mode": "none", "read_only": True,
        "tmpfs": {SCRATCH: "rw,noexec,nosuid,size=64m", "/tmp": "rw,noexec,nosuid,size=16m"},
        "working_dir": SCRATCH, "environment": {"LANG": "C.UTF-8"},
        "mem_limit": f"{spec['memory_mb']}m", "memswap_limit": f"{spec['memory_mb']}m",
        "nano_cpus": 1_000_000_000, "pids_limit": 32, "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"], "stdin_open": True, "tty": False,
        "detach": True, "auto_remove": False,
    }


class _DockerHost:
    """One plugin host container, attached: stdin in, stdout out, stderr logged."""

    def __init__(self, spec: dict):
        import docker

        from yaaf.core.config import settings
        from yaaf.runner.sandbox import _stack_labels

        self.client = docker.from_env()
        opts = container_options(spec, settings().sandbox_image,
                                 _stack_labels({"yaaf.plugin_host": "1"}))
        self.container = self.client.containers.create(**opts)
        self.sock = self.container.attach_socket(
            params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1})
        self.raw = getattr(self.sock, "_sock", self.sock)
        self.container.start()

    def write(self, data: bytes) -> None:
        self.raw.sendall(data)

    def read_stdout(self, deliver: Callable[[bytes], None]) -> None:
        """Demultiplex Docker's attach stream: 8-byte headers, stream 1 is stdout."""
        while True:
            head = _recv_exactly(self.raw, 8)
            if head is None:
                return
            kind, size = head[0], struct.unpack(">I", head[4:])[0]
            body = _recv_exactly(self.raw, size)
            if body is None:
                return
            if kind == 1:
                deliver(body)
            else:
                log.debug("plugin host stderr: %s", body[:2000].decode(errors="replace"))

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.container.remove(force=True)
        with contextlib.suppress(Exception):
            self.sock.close()


def send_frame(conn: socket.socket, message: dict) -> None:
    """A whole frame or an error: sendall never leaves half of one on the wire."""
    import json

    body = json.dumps(message, separators=(",", ":")).encode()
    conn.sendall(struct.pack(">I", len(body)) + body)


def _recv_exactly(sock, n: int) -> bytes | None:
    chunks, got = [], 0
    while got < n:
        chunk = sock.recv(n - got)
        if not chunk:
            return None
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        conn: socket.socket = self.request
        server: BrokerServer = self.server  # type: ignore[assignment]
        stream = conn.makefile("rb", buffering=0)  # reads only; frames go out by sendall
        conn.settimeout(AUTH_TIMEOUT_S)
        nonce = secrets.token_hex(16)
        try:
            send_frame(conn, {"challenge": nonce})
            hello = wire.read_frame(stream)
        except (OSError, wire.WireError):
            return
        if not isinstance(hello, dict) or not hmac.compare_digest(
                str(hello.get("auth", "")), sign(nonce, server.key)):
            log.warning("plugin broker: refused a connection from %s: bad auth",
                        self.client_address[0])
            with contextlib.suppress(OSError):
                send_frame(conn, {"broker": "refused", "why": "authentication failed"})
            return
        spec, why = check_spec(hello.get("spec"), server.root)
        if spec is None:
            with contextlib.suppress(OSError):
                send_frame(conn, {"broker": "refused", "why": why})
            return
        try:
            host = server.launch(spec)
        except Exception as exc:
            log.warning("plugin broker: could not start a host: %s", exc)
            with contextlib.suppress(OSError):
                send_frame(conn, {"broker": "refused",
                                  "why": f"the host could not start: {exc}"[:500]})
            return
        conn.settimeout(None)
        try:
            send_frame(conn, {"broker": "ok"})
            out = threading.Thread(target=host.read_stdout, args=(conn.sendall,), daemon=True)
            out.start()
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                host.write(data)
        except OSError:
            pass
        finally:
            host.close()


class BrokerServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], *, key: bytes | None = None,
                 root: Path | None = None, launch: Callable[[dict], Any] | None = None):
        self.key = key if key is not None else secret()
        self.root = (root or _plugins_root()).resolve()
        self.launch = launch or _DockerHost
        super().__init__(address, _Handler)


def serve_forever() -> None:  # pragma: no cover - the runner's thread
    from yaaf.core.config import settings

    s = settings()
    server = BrokerServer((s.plugin_broker_bind, s.plugin_broker_port))
    log.info("plugin broker listening on %s:%s", s.plugin_broker_bind, s.plugin_broker_port)
    server.serve_forever()


def start_in_background() -> threading.Thread | None:
    """Called by the runner at start in docker mode. A broker that cannot start
    (no master key, port taken) is logged, and plugin calls then fail closed."""
    try:
        secret()
    except RuntimeError as exc:
        log.warning("plugin broker not started: %s", exc)
        return None
    thread = threading.Thread(target=serve_forever, name="plugin-broker", daemon=True)
    thread.start()
    return thread
