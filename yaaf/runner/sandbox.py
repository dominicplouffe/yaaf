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
) -> tuple[int, str]:
    """Launch the sandbox entrypoint for an agent run; return (exit_code, logs).
    The entry script reads its inputs from /workspace/.yaaf/ and writes
    result.json + a trace there."""
    import docker

    client = docker.from_env()
    container = client.containers.run(
        settings().sandbox_image,
        command=["python3", "/opt/yaaf/sandbox_entry.py"],
        environment={**_proxy_env(), **env},
        volumes={workspace_dir: {"bind": "/workspace", "mode": "rw"}},
        working_dir="/workspace",
        network=settings().sandbox_network,
        mem_limit=memory,
        nano_cpus=2_000_000_000,
        security_opt=["no-new-privileges"],
        detach=True,
    )
    try:
        result = container.wait(timeout=timeout_s)
        exit_code = int(result.get("StatusCode", 1))
        logs = container.logs(stdout=True, stderr=True, tail=1000).decode(errors="replace")
        return exit_code, logs[-16000:]
    except Exception as exc:
        with contextlib.suppress(Exception):
            container.kill()
        return 124, f"agent run killed (timeout or error): {exc!r}"
    finally:
        with contextlib.suppress(Exception):
            container.remove(force=True)


def _proxy_env() -> dict[str, str]:
    proxy = settings().egress_proxy_url
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
