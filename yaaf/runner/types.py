"""Shared types for the runner: a leased job and its outcome."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunJob:
    run_id: str
    project_id: str
    agent_id: str | None
    kind: str  # RunKind
    trigger: dict
    task_id: str | None
    requirement_id: str | None = None
    attempt: int = 1
    # Assembled by the runner from the DB before execution:
    system_prompt: str = ""
    context_pack: dict = field(default_factory=dict)
    model: str = "claude-sonnet-5"
    effort: str = "high"
    tool_permissions: dict = field(default_factory=dict)
    timeout_s: int = 1800
    # Hard per-run ceiling handed to the agent SDK. 0 = unbounded.
    max_usd: float = 0.0
    # Constitution run_token_budget: the model is told its token budget so it
    # paces itself and wraps up before the line. 0 = off.
    task_budget_tokens: int = 0
    # Injected credentials (never logged):
    api_base_url: str = ""
    api_token: str = ""            # per-run yaaf token (plaintext)
    billing_env: dict = field(default_factory=dict)  # ANTHROPIC_API_KEY / OAuth token
    workspace_dir: str = ""
    # Success checks only:
    check_command: str | None = None


@dataclass
class RunResult:
    ok: bool
    summary: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float | None = None
    trace_path: str | None = None
    #: The plan meter the SDK volunteered during this run, if it did:
    #: {"status": str, "windows": [{"key", "utilization", "resets_at"}]}.
    #: Belongs to the billing credential, not to the run.
    plan: dict | None = None
    # Success checks only:
    check_exit_code: int | None = None
    check_output: str = ""
