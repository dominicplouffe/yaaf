"""Sandbox execution: Docker containers behind the egress proxy.

Every agent run gets a fresh container: no privileges, resource caps, the
project workspace mounted, and ALL egress forced through the allowlist proxy
(untrusted content + secrets + open network is the standard lethal
combination — the proxy ships with the sandbox, not in some later milestone).

``sandbox_mode = "subprocess"`` exists for dev machines and CI without Docker.
It provides NO isolation and is refused unless explicitly configured.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess

from yaaf.core.config import settings

log = logging.getLogger("yaaf.sandbox")

CHECK_TIMEOUT_DEFAULT = 300


def run_check_command(command: str, workspace_dir: str, timeout_s: int) -> tuple[int, str]:
    """Run a task's success-test command; return (exit_code, output)."""
    if not command.strip():
        return 1, "empty success-test command"
    mode = getattr(settings(), "sandbox_mode", "docker")
    if mode == "subprocess":
        return _subprocess_check(command, workspace_dir, timeout_s)
    return _docker_check(command, workspace_dir, timeout_s)


def _subprocess_check(command: str, workspace_dir: str, timeout_s: int) -> tuple[int, str]:
    try:
        if workspace_dir:
            from pathlib import Path

            Path(workspace_dir).mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["bash", "-lc", command],
            cwd=workspace_dir or None,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return proc.returncode, (proc.stdout + proc.stderr)[-8000:]
    except subprocess.TimeoutExpired:
        return 124, f"success test timed out after {timeout_s}s"
    except Exception as exc:
        return 125, f"could not run success test: {exc!r}"


def _docker_check(command: str, workspace_dir: str, timeout_s: int) -> tuple[int, str]:
    import docker

    client = docker.from_env()
    container = client.containers.run(
        settings().sandbox_image,
        command=["bash", "-lc", command],
        environment=_proxy_env(),
        volumes={workspace_dir: {"bind": "/workspace", "mode": "rw"}},
        working_dir="/workspace",
        network=settings().sandbox_network,
        mem_limit="1g",
        nano_cpus=1_000_000_000,
        security_opt=["no-new-privileges"],
        detach=True,
    )
    try:
        result = container.wait(timeout=timeout_s)
        exit_code = int(result.get("StatusCode", 1))
        output = container.logs(stdout=True, stderr=True, tail=400).decode(errors="replace")
        return exit_code, output[-8000:]
    except Exception as exc:
        with contextlib.suppress(Exception):
            container.kill()
        return 124, f"success test killed: {exc!r}"
    finally:
        with contextlib.suppress(Exception):
            container.remove(force=True)


def run_agent_container(
    *,
    workspace_dir: str,
    env: dict[str, str],
    timeout_s: int,
    memory: str = "2g",
    stop_event=None,
    proxy_url: str | None = None,
) -> tuple[int, str]:
    """Launch the sandbox entrypoint for an agent run; return (exit_code, logs).
    The entry script reads its inputs from /workspace/.yaaf/ and writes
    result.json + a trace there. If ``stop_event`` (a threading.Event) is set —
    the run token was revoked by a kill switch, pause, or budget trip — the
    container itself is stopped: a revoked run must stop burning tokens, not
    just lose its API access."""
    import time as _time

    import docker

    client = docker.from_env()
    container = client.containers.run(
        settings().sandbox_image,
        command=["python3", "/opt/yaaf/sandbox_entry.py"],
        environment={**_proxy_env(proxy_url), **env},
        volumes={workspace_dir: {"bind": "/workspace", "mode": "rw"}},
        working_dir="/workspace",
        network=settings().sandbox_network,
        mem_limit=memory,
        nano_cpus=2_000_000_000,
        security_opt=["no-new-privileges"],
        detach=True,
    )
    deadline = _time.monotonic() + timeout_s
    try:
        while True:
            try:
                result = container.wait(timeout=5)
                exit_code = int(result.get("StatusCode", 1))
                break
            except Exception:  # wait timed out (or transient API hiccup): reassess
                if stop_event is not None and stop_event.is_set():
                    with contextlib.suppress(Exception):
                        container.stop(timeout=10)
                    logs = _container_logs(container)
                    return 137, f"agent run stopped: token revoked\n{logs}"
                if _time.monotonic() >= deadline:
                    with contextlib.suppress(Exception):
                        container.kill()
                    logs = _container_logs(container)
                    return 124, f"agent run killed at {timeout_s}s timeout\n{logs}"
        return exit_code, _container_logs(container)
    finally:
        with contextlib.suppress(Exception):
            container.remove(force=True)


# ── the chat lane ───────────────────────────────────────────────────────────────

# Chat containers are found by label, not by handle: the supervisor must
# re-adopt a session that outlived a runner restart. The run-id label binds a
# container to ITS run row — a running container whose run died is a zombie
# (polling with a dead token forever), and adopting one silently suppresses
# the fallback wake path. Seen live; never again.
CHAT_LABEL = "yaaf.chat.project"
CHAT_RUN_LABEL = "yaaf.chat.run"


def start_chat_container(*, project_id: str, run_id: str, name: str,
                         workspace_dir: str, env: dict[str, str],
                         proxy_url: str | None = None, memory: str = "2g") -> str:
    """Launch the resident chat entrypoint, detached; returns the container id.
    Same image, mounts, network, and limits as an agent run — the talk lane
    gets no extra privileges, only a different entrypoint and no timeout."""
    import docker

    client = docker.from_env()
    for stale in client.containers.list(all=True, filters={"name": name}):
        with contextlib.suppress(Exception):
            stale.remove(force=True)
    container = client.containers.run(
        settings().sandbox_image,
        command=["python3", "/opt/yaaf/chat_entry.py"],
        name=name,
        labels={CHAT_LABEL: project_id, CHAT_RUN_LABEL: run_id},
        environment={**_proxy_env(proxy_url), **env},
        volumes={workspace_dir: {"bind": "/workspace", "mode": "rw"}},
        working_dir="/workspace",
        network=settings().sandbox_network,
        mem_limit=memory,
        nano_cpus=2_000_000_000,
        security_opt=["no-new-privileges"],
        detach=True,
    )
    return str(container.id)


def chat_container_run_ids(project_id: str) -> set[str]:
    """Run ids of the RUNNING chat containers for a project. More than one, or
    one that doesn't match the live run row, means zombies to clear."""
    import docker

    client = docker.from_env()
    return {
        (c.labels or {}).get(CHAT_RUN_LABEL, "")
        for c in client.containers.list(filters={"label": f"{CHAT_LABEL}={project_id}"})
    }


def stop_chat_container(project_id: str) -> str:
    """Stop and remove the project's chat container(s); returns the last logs
    for the session run's summary."""
    import docker

    client = docker.from_env()
    logs = ""
    for container in client.containers.list(
        all=True, filters={"label": f"{CHAT_LABEL}={project_id}"}
    ):
        logs = _container_logs(container)
        with contextlib.suppress(Exception):
            container.stop(timeout=5)
        with contextlib.suppress(Exception):
            container.remove(force=True)
    return logs


def _container_logs(container) -> str:
    with contextlib.suppress(Exception):
        return container.logs(stdout=True, stderr=True, tail=1000).decode(errors="replace")[-16000:]
    return ""


def _proxy_env(proxy_url: str | None = None) -> dict[str, str]:
    proxy = proxy_url or settings().egress_proxy_url
    if not proxy:
        return {}
    return {
        "HTTP_PROXY": proxy,
        "HTTPS_PROXY": proxy,
        "http_proxy": proxy,
        "https_proxy": proxy,
        # The platform API and the proxy itself are reached directly.
        "NO_PROXY": "api,localhost,127.0.0.1,proxy",
        "no_proxy": "api,localhost,127.0.0.1,proxy",
    }
