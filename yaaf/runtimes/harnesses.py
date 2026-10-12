"""What runs the agent loop inside the sandbox, as data.

**Imports nothing from `yaaf`.** The sandbox image carries single files, not
the package, and the harness that runs there will need the same facts the
control plane holds about it. One copy, readable from both sides, is the point;
`tests/selftest_invariants/test_runtime_registries.py` holds the import rule.

A harness is transport. It decides how a model is driven, never what the model
may do — nothing in an entry here is enforcement (docs/RUNTIMES.md).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Harness:
    name: str
    label: str
    #: "sdk" | "cli" | "loop" | "mock"
    family: str
    #: Providers whose models this harness can serve. Empty means any — only
    #: the scripted backend, which serves no model at all.
    providers: tuple[str, ...]
    #: Credential kinds it can authenticate with, as "provider:mode". Empty
    #: means any, for the same reason.
    accepts: tuple[str, ...]
    #: The role grants it can actually take away — "files", "bash", "web". A
    #: harness that cannot run without a shell cannot honour a role that
    #: grants none, and is refused for it rather than quietly widening it.
    withholds: tuple[str, ...] = ("files", "bash", "web")
    #: "reported" — the harness bills each run itself (the Agent SDK, Claude
    #: Code); "priced" — yaaf prices it from the catalog, so a model with no
    #: price cannot run on it (R3).
    cost: str = "reported"
    #: The efforts it can express (R6). None: it hands the seat's effort to
    #: the model as yaaf names it (the Claude harnesses, whose names are
    #: yaaf's). Empty: it has no effort dial at all, and the seat's is unused.
    efforts: tuple[str, ...] | None = None
    #: Where a `web` grant's search comes from (R2): "own" — the harness
    #: searches; "provider" — the provider's API does, where it does (yaaf's
    #: loop); "none" — nothing does, so a role granting `web` is refused.
    search: str = "own"
    #: Whether it can hold the boardroom conversation (R7): a session it can
    #: resume turn after turn. Off unless shown — the chat lane is the one
    #: place the chairman waits on a seat in real time.
    conversation: bool = False


HARNESSES: dict[str, Harness] = {h.name: h for h in (
    Harness(
        name="claude_agent_sdk",
        label="Claude Agent SDK",
        family="sdk",
        providers=("anthropic",),
        accepts=("anthropic:api_key", "anthropic:subscription"),
        conversation=True,
    ),
    # The same CLI the SDK drives, driven directly: same model, same
    # credentials, same tools under the same names. The reference every other
    # CLI harness is built from, which is why it changes nothing else.
    Harness(
        name="claude_cli",
        label="Claude Code CLI (claude -p)",
        family="cli",
        providers=("anthropic",),
        accepts=("anthropic:api_key", "anthropic:subscription"),
        conversation=True,
    ),
    # yaaf's own agent loop on the Messages API: no CLI underneath, the
    # built-in tools yaaf's own, every call priced from the catalog. API keys
    # only — a subscription token is a CLI credential, not a Messages API one.
    # The reference every provider reached by plain API is built from.
    Harness(
        name="anthropic_api",
        label="Anthropic API (yaaf's own loop)",
        family="loop",
        providers=("anthropic",),
        accepts=("anthropic:api_key",),
        cost="priced",
        search="provider",
        conversation=True,
    ),
    # The same loop on the OpenAI-compatible chat API: one client, and every
    # provider that speaks that shape is a row in the provider registry. The
    # loop's own tools; search only where the provider runs one (R2 refuses a
    # `web` grant it cannot honour).
    Harness(
        name="openai_compatible",
        label="OpenAI-compatible API (yaaf's own loop)",
        family="loop",
        providers=("deepseek", "openrouter", "custom", "moonshot", "qwen", "mistral", "xai"),
        accepts=("deepseek:api_key", "openrouter:api_key", "custom:api_key",
                 "moonshot:api_key", "qwen:api_key", "mistral:api_key", "xai:api_key"),
        cost="priced",
        # The chat-completions shape has no common word for effort.
        efforts=(),
        search="provider",
        conversation=True,
    ),
    # The Codex CLI (`codex exec --json`), driven like the Claude CLI: the
    # first CLI that is not Claude's. It reports tokens but no dollars, so
    # yaaf prices it; it can take away its shell and its search, and a role's
    # file grant without a shell is served by yaaf's own file tools over MCP.
    Harness(
        name="codex_cli",
        label="Codex CLI",
        family="cli",
        providers=("openai",),
        accepts=("openai:api_key", "openai:subscription"),
        # Its shell and its search go when a role leaves them off; its file
        # editor does not — the models it knows best are handed `apply_patch`
        # by Codex's own presets, shell or no shell — so a role that withholds
        # files is refused for it (R2) rather than quietly handed one.
        withholds=("bash", "web"),
        cost="priced",
        efforts=("low", "medium", "high", "xhigh", "max"),
        conversation=True,
    ),
    # The OpenCode CLI (`opencode run --format json`): one CLI for every
    # provider on the OpenAI-compatible chat API, its own file tools and
    # shell, yaaf's tools over MCP. Its search is its own hosted service's,
    # so it searches for nobody here. OpenRouter is not among its providers:
    # the cost OpenRouter states per call never reaches OpenCode's stream,
    # and an OpenRouter model needs no price to be hired.
    Harness(
        name="opencode",
        label="OpenCode CLI",
        family="cli",
        providers=("openai", "deepseek", "custom", "moonshot", "qwen", "mistral", "xai"),
        accepts=("openai:api_key", "deepseek:api_key", "custom:api_key", "moonshot:api_key",
                 "qwen:api_key", "mistral:api_key", "xai:api_key"),
        cost="priced",
        efforts=(),
        search="none",
        conversation=True,
    ),
    # The scripted backend that drives the real API at $0: CI, the goldens and
    # the fire drill. It reads no credential and calls no model.
    Harness(
        name="mock",
        label="Scripted agents (tests and the fire drill)",
        family="mock",
        providers=(),
        accepts=(),
    ),
)}

#: What `integrations.runtime` said before seats had harnesses of their own.
#: It still means "run each seat on its own harness"; only "mock" overrides.
LEGACY_RUNTIME = "agent_sdk"
