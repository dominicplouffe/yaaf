"""Asking the runner's broker to run a plugin's build or tests (docker mode).

The same connection and challenge as a plugin host (`host.BrokerLauncher`),
with a `job` in place of a `spec`. The broker decides the command and the
container's flags (`broker.job_options`); this names only the op and the paths.
"""

from __future__ import annotations

import socket
from pathlib import Path

from yaaf_plugin_api import wire


def run_job(op: str, root: Path, site: Path | None, *, network: bool, timeout_s: int,
            address: tuple[str, int] | None = None, key: bytes | None = None
            ) -> tuple[int, str]:
    from yaaf.plugins import broker
    from yaaf.plugins.host import BrokerLauncher

    launcher = BrokerLauncher(address, key)
    why = launcher.unavailable()
    if why:
        return 126, why
    key = key if key is not None else broker.secret()
    try:
        conn = socket.create_connection(launcher.address(), timeout=30)
    except OSError as exc:
        return 126, f"the runner's plugin broker is not reachable ({exc}); is the runner up?"
    try:
        reader = conn.makefile("rb", buffering=0)
        nonce = str((wire.read_frame(reader) or {}).get("challenge", ""))
        broker.send_frame(conn, {"auth": broker.sign(nonce, key), "job": {
            "op": op, "root": str(root), "site": str(site) if site else None,
            "timeout_s": timeout_s}})
        conn.settimeout(timeout_s + 60)
        answer = wire.read_frame(reader) or {}
    except (OSError, wire.WireError) as exc:
        return 126, f"the broker did not answer: {exc}"
    finally:
        conn.close()
    if answer.get("broker") != "done":
        return 126, f"the broker refused: {answer.get('why', 'no reason')}"
    return int(answer.get("code", 1)), str(answer.get("log", ""))
