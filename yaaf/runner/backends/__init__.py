"""Runner backends.

A backend executes one leased job and returns a result — one per harness
(yaaf.runtimes.harnesses; docs/RUNTIMES.md):

- ``mock``: scripted behaviors that drive the *real* HTTP API — the framework's
  orchestration is CI-testable end to end at $0 in model tokens
- ``claude_agent_sdk``: the Claude Agent SDK inside a Docker sandbox — the real thing
- ``claude_cli``: the Claude Code CLI (``claude -p``) in the same sandbox, driven
  directly — the reference every other CLI harness is built from
- ``anthropic_api``: yaaf's own agent loop on the Messages API, in the same
  sandbox — the reference every provider reached by plain API is built from
- ``openai_compatible``: the same loop on the OpenAI-compatible chat API —
  DeepSeek, OpenRouter, a custom server; one client, a registry row each

The backend never touches the database. Everything it needs rides on the
:class:`~yaaf.runner.types.RunJob`; everything it produces comes back on the
:class:`~yaaf.runner.types.RunResult`. That seam is what will let worker
processes move to other machines without an architecture change.
"""

from __future__ import annotations

from typing import Protocol

from yaaf.runner.types import RunJob, RunResult


class RunnerBackend(Protocol):
    def execute(self, job: RunJob) -> RunResult: ...


def get_backend(name: str) -> RunnerBackend:
    """The backend that executes a harness (yaaf.runtimes.harnesses). Accepts
    the harness name, and "agent_sdk" — what `integrations.runtime` held before
    seats had harnesses of their own — as the SDK's old name."""
    if name == "mock":
        from yaaf.runner.backends.mock import MockBackend

        return MockBackend()
    if name in ("claude_agent_sdk", "agent_sdk"):
        from yaaf.runner.backends.agent_sdk import SandboxBackend

        return SandboxBackend("sandbox_entry.py")
    if name == "claude_cli":
        from yaaf.runner.backends.agent_sdk import SandboxBackend

        return SandboxBackend("claude_cli_entry.py")
    if name == "anthropic_api":
        from yaaf.runner.backends.agent_sdk import SandboxBackend

        return SandboxBackend("anthropic_api_entry.py")
    if name == "openai_compatible":
        from yaaf.runner.backends.agent_sdk import SandboxBackend

        return SandboxBackend("openai_compatible_entry.py")
    raise ValueError(f"unknown runner backend {name!r}")
