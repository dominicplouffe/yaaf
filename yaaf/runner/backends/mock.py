"""The MockModel backend: scripted agents that exercise the real API.

A behavior is a plain function ``(api, job) -> summary`` registered for an
agent name (or ``"*"``). It receives an :class:`AgentApi` bound to the run's
own token and the job's context pack, and does whatever a model would do —
create tasks, claim, post messages, add evidence — through the same HTTP
surface, subject to the same refusals.

This is how CI proves the orchestration (planning -> decomposition -> claim ->
complete -> verify -> governance trips) without burning a model token, and how
the invariant selftests attack the rules with a real principal.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from yaaf.runner.types import RunJob, RunResult


class AgentApi:
    """Thin client over the agent-facing API, bound to one run token."""

    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None):
        self._client = client or httpx.Client(base_url=base_url, timeout=30)
        self._own_client = client is None
        self._headers = {"Authorization": f"Bearer {token}"}
        self.refusals: list[dict] = []

    def close(self) -> None:
        if self._own_client:
            self._client.close()

    def call(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = self._client.request(method, path, headers=self._headers, **kwargs)
        if response.status_code in (403, 409, 422) and "error" in _safe_json(response):
            self.refusals.append({"path": path, **_safe_json(response)})
        return response

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.call("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self.call("POST", path, **kwargs)


def _safe_json(response: httpx.Response) -> dict:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


Behavior = Callable[[AgentApi, RunJob], str]

_REGISTRY: dict[str, Behavior] = {}


def register(agent_name: str, behavior: Behavior) -> None:
    _REGISTRY[agent_name] = behavior


def clear_registry() -> None:
    _REGISTRY.clear()


def _default_behavior(api: AgentApi, job: RunJob) -> str:
    # A well-behaved idle agent: read context, find nothing scripted, exit.
    api.get("/agent/context")
    return "mock: read context and exited (no behavior registered)"


class MockBackend:
    """Executes registered behaviors. An injectable client lets tests drive
    this against an in-process FastAPI TestClient."""

    def __init__(self, client: httpx.Client | None = None):
        self._client = client

    def execute(self, job: RunJob, stop_event=None) -> RunResult:
        behavior = (
            _REGISTRY.get(job.context_pack.get("you", {}).get("name", ""))
            or _REGISTRY.get("*")
            or _default_behavior
        )
        api = AgentApi(job.api_base_url, job.api_token, client=self._client)
        try:
            summary = behavior(api, job)
            return RunResult(ok=True, summary=summary, tokens_in=0, tokens_out=0)
        except Exception as exc:
            return RunResult(ok=False, summary=f"mock behavior raised: {exc!r}")
        finally:
            api.close()
