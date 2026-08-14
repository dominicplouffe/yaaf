"""Runner backends.

A backend executes one leased job and returns a result. Two exist in M1:

- ``mock``: scripted behaviors that drive the *real* HTTP API — the framework's
  orchestration is CI-testable end to end at $0 in model tokens
- ``agent_sdk``: the Claude Agent SDK inside a Docker sandbox behind the egress
  proxy — the real thing

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
    if name == "mock":
        from yaaf.runner.backends.mock import MockBackend

        return MockBackend()
    if name == "agent_sdk":
        from yaaf.runner.backends.agent_sdk import AgentSDKBackend

        return AgentSDKBackend()
    raise ValueError(f"unknown runner backend {name!r}")
