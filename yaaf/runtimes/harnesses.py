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


HARNESSES: dict[str, Harness] = {h.name: h for h in (
    Harness(
        name="claude_agent_sdk",
        label="Claude Agent SDK",
        family="sdk",
        providers=("anthropic",),
        accepts=("anthropic:api_key", "anthropic:subscription"),
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
    ),
    # The same loop on the OpenAI-compatible chat API: one client, and every
    # provider that speaks that shape is a row in the provider registry. The
    # loop's own tools; search only where the provider runs one (R2 refuses a
    # `web` grant it cannot honour).
    Harness(
        name="openai_compatible",
        label="OpenAI-compatible API (yaaf's own loop)",
        family="loop",
        providers=("deepseek", "openrouter", "custom"),
        accepts=("deepseek:api_key", "openrouter:api_key", "custom:api_key"),
        cost="priced",
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
