"""Runner backends.

A backend executes one leased job and returns a result — one per harness
(yaaf.runtimes.harnesses; docs/RUNTIMES.md):

- ``mock``: scripted behaviors that drive the *real* HTTP API — the framework's
  orchestration is CI-testable end to end at $0 in model tokens
- ``claude_agent_sdk``: the Claude Agent SDK inside a Docker sandbox — the real thing
- ``claude_cli``: the Claude Code CLI (``claude -p``) in the same sandbox, driven
  directly — the reference every other CLI harness is built from
- ``yaaf_loop``: yaaf's own agent loop, in the same sandbox, on whichever
  wire the seat's provider speaks (yaaf.runtimes.wire) — Anthropic's, OpenAI's
  and Azure's, Gemini's and Vertex's, Bedrock's, and the OpenAI chat shape of
  every other provider; a registry row each
- ``codex_cli``: OpenAI's Codex CLI (``codex exec --json``), the first CLI
  harness that is not Claude's
- ``gemini_cli``: Google's Gemini CLI (``gemini --output-format stream-json``)
- ``opencode``: the OpenCode CLI (``opencode run --format json``), one CLI for
  every provider on the OpenAI-compatible chat API

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
    the harness name, and any name a harness used to go by (harnesses.ALIASES)
    — "agent_sdk", what `integrations.runtime` held before seats had harnesses
    of their own, and the two loop harnesses before the wire became the
    provider's."""
    from yaaf.runtimes.harnesses import canonical

    name = canonical(name) or name
    if name == "mock":
        from yaaf.runner.backends.mock import MockBackend

        return MockBackend()
    if name == "claude_agent_sdk":
        from yaaf.runner.backends.agent_sdk import SandboxBackend

        return SandboxBackend("sandbox_entry.py")
    if name == "claude_cli":
        from yaaf.runner.backends.agent_sdk import SandboxBackend

        return SandboxBackend("claude_cli_entry.py")
    if name == "yaaf_loop":
        from yaaf.runner.backends.agent_sdk import SandboxBackend

        return SandboxBackend("loop_entry.py")
    if name == "codex_cli":
        from yaaf.runner.backends.agent_sdk import SandboxBackend

        return SandboxBackend("codex_cli_entry.py")
    if name == "gemini_cli":
        from yaaf.runner.backends.agent_sdk import SandboxBackend

        return SandboxBackend("gemini_cli_entry.py")
    if name == "opencode":
        from yaaf.runner.backends.agent_sdk import SandboxBackend

        return SandboxBackend("opencode_cli_entry.py")
    raise ValueError(f"unknown runner backend {name!r}")
