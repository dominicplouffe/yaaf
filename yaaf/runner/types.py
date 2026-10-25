"""Shared types for the runner: a leased job and its outcome."""

from __future__ import annotations

from dataclasses import dataclass, field

from yaaf.runtimes.catalog import DEFAULT_EFFORT, DEFAULT_MODEL


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
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    # What serves the model and what drives it (docs/RUNTIMES.md). Resolved
    # with the model, by yaaf.runtimes.seats, and pinned on the run row.
    provider: str = ""
    harness: str = ""
    # The model's catalog row, for the sandbox (which cannot import the
    # catalog): its price, so every run is priced whoever bills it, and what
    # the API accepts for it, for a harness that builds requests itself.
    # None / {} for a model the catalog does not list.
    price: dict | None = None
    model_flags: dict = field(default_factory=dict)
    # The provider, for a harness that calls its API itself: its wire, base
    # URL, how its key is presented and under which variable, whether it
    # states costs or searches (yaaf.runtimes.providers.spec).
    provider_spec: dict = field(default_factory=dict)
    # A credential that is a key file the control plane mints short-lived
    # tokens from (Vertex): minted just before the container starts, never
    # handed to it whole, and refreshed by the run through the agent API.
    mint_secret_id: str | None = None
    tool_permissions: dict = field(default_factory=dict)
    # Which tool SCHEMAS this run is worth sending. Every agent used to receive
    # all of them — for a plain employee that was 7,700 tokens of schema on
    # every API call, half of it verbs the server answers with a 403, and more
    # than the whole context pack beside it. Never enforcement: the 403s are
    # unchanged and are still the only thing that decides who may do what.
    agent_kind: str = "ceo"
    timeout_s: int = 1800
    # Hard per-run ceiling handed to the agent SDK. 0 = unbounded.
    max_usd: float = 0.0
    # Constitution run_token_budget: the model is told its token budget so it
    # paces itself and wraps up before the line. 0 = off.
    task_budget_tokens: int = 0
    # Injected credentials (never logged):
    api_base_url: str = ""
    api_token: str = ""            # per-run yaaf token (plaintext)
    billing_env: dict = field(default_factory=dict)  # the credential kind's one variable
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
    #: The same run priced from yaaf's catalog. For a harness that prices its
    #: own runs it *is* cost_usd; for one that reports its bill it is the check
    #: on the catalog (docs/RUNTIMES.md, the gap check). None when unpriced.
    priced_usd: float | None = None
    trace_path: str | None = None
    #: The plan meter the SDK volunteered during this run, if it did:
    #: {"status": str, "windows": [{"key", "utilization", "resets_at"}]}.
    #: Belongs to the billing credential, not to the run.
    plan: dict | None = None
    #: How a failed run failed, as the harness classified it: "auth" (the
    #: credential is dead — the daemon pauses on three in a row), "transient",
    #: or "fatal". None when the run did not fail or the harness could not say.
    error_kind: str | None = None
    # Success checks only:
    check_exit_code: int | None = None
    check_output: str = ""
