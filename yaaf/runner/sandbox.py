"""Sandbox execution: fresh Docker containers per run.

Every agent run gets a fresh container: no privileges, resource caps, the
project workspace mounted, and the open network — the chairman's decision
(docs/AUTONOMY.md): the per-role egress allowlist solved one problem at the
price of making every capability a firewall ticket. Containment is the fresh
container, role-scoped secrets, budgets, and the audit trail.

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

# Stamped on every container this stack launches. Sandboxes are sibling
# containers with Docker-random names and no other handle, so without this a
# second stack on the same box has no way to tell its own containers from the
# live one's — and the teardown sweep is box-wide by necessity.
STACK_LABEL = "yaaf.stack"


def _stack_labels(extra: dict | None = None) -> dict:
    return {STACK_LABEL: settings().stack, **(extra or {})}


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
        labels=_stack_labels(),
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
        environment=env,
        labels=_stack_labels(),
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


def chat_container_name(slug: str) -> str:
    """The chat container's name. Unchanged on the default stack, so a running
    session keeps its name across this upgrade and is still adopted; suffixed
    elsewhere, because Docker names are unique per daemon and two stacks
    running the same project slug would otherwise fight over one name."""
    stack = settings().stack
    return f"yaaf-chat-{slug}" if stack == "deploy" else f"yaaf-chat-{stack}-{slug}"


def _chat_filters(project_id: str) -> dict:
    """Find this stack's chat containers for a project, and only this stack's.

    The stack label is load-bearing rather than tidy: a test stack restored
    from a dump of the live database carries the SAME project ids, so a filter
    on project id alone matches the live stack's session. Its run id will not
    match, the supervisor will read it as a zombie, and stop_chat_container
    will remove a live session out from under the box next door."""
    return {"label": [f"{CHAT_LABEL}={project_id}", f"{STACK_LABEL}={settings().stack}"]}


def start_chat_container(*, project_id: str, run_id: str, name: str,
                         workspace_dir: str, env: dict[str, str],
                         company_dir: str | None = None, memory: str = "2g") -> str:
    """Launch the resident chat entrypoint, detached; returns the container id.
    Same image, network and limits as an agent run — the talk lane gets no extra
    privileges, only a different entrypoint and no timeout.

    Mounts differ in exactly one way, and it matters: the session's own scratch
    is read-write at /workspace, and the *project's* workspace is mounted
    read-only at /company. Before that second mount the boardroom CEO could see
    nothing the project had produced — it ran `ls /workspace`, found its own
    empty scratch, and told the chairman the project had lost its data while
    the file sat on disk two directories away. Read-only because a resident
    session writing into the working tree is the race the split was built to
    avoid; the CEO reads the project's output, it does not edit it.
    """
    import docker

    client = docker.from_env()
    # Names are stack-scoped upstream, so this sweep cannot reach another
    # stack's session — but check the label anyway rather than trusting a
    # naming convention to be a boundary.
    for stale in client.containers.list(all=True, filters={"name": name}):
        if (stale.labels or {}).get(STACK_LABEL, settings().stack) != settings().stack:
            continue
        with contextlib.suppress(Exception):
            stale.remove(force=True)
    volumes = {workspace_dir: {"bind": "/workspace", "mode": "rw"}}
    if company_dir:
        volumes[company_dir] = {"bind": "/company", "mode": "ro"}
    container = client.containers.run(
        settings().sandbox_image,
        command=["python3", "/opt/yaaf/chat_entry.py"],
        name=name,
        labels=_stack_labels({CHAT_LABEL: project_id, CHAT_RUN_LABEL: run_id}),
        environment=env,
        volumes=volumes,
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
        for c in client.containers.list(filters=_chat_filters(project_id))
    }


def stop_chat_container(project_id: str) -> str:
    """Stop and remove the project's chat container(s); returns forensics +
    logs for every container found — the corpse is removed here, so this is
    the only chance to record why it died."""
    import docker

    client = docker.from_env()
    sections = []
    for container in client.containers.list(all=True, filters=_chat_filters(project_id)):
        sections.append(_chat_forensics(container))
        with contextlib.suppress(Exception):
            container.stop(timeout=5)
        with contextlib.suppress(Exception):
            container.remove(force=True)
    return "\n---\n".join(sections)


def _chat_forensics(container) -> str:
    """One header line of death-cause facts (exit code, OOM, uptime) + the logs.
    Distinguishes a 401 boot-loop (exit 1, seconds of uptime) from a clean
    poll-loop exit (0), a failed turn (1, long uptime), and an OOM kill
    (137, OOMKilled=True) — previously all identical 'lost' deaths."""
    header = ""
    with contextlib.suppress(Exception):
        container.reload()
        state = container.attrs.get("State", {})
        header = ("exit={exit} oom={oom} error={err!r} started={start} finished={fin}".format(
            exit=state.get("ExitCode"), oom=state.get("OOMKilled"),
            err=state.get("Error") or "", start=state.get("StartedAt"),
            fin=state.get("FinishedAt")))
    logs = _container_logs(container)
    return (header + "\n" + logs).strip() or "(no container state or logs available)"


def _container_logs(container) -> str:
    with contextlib.suppress(Exception):
        return container.logs(stdout=True, stderr=True, tail=1000).decode(errors="replace")[-16000:]
    return ""

