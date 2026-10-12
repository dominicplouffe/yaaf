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


HARNESSES: dict[str, Harness] = {h.name: h for h in (
    Harness(
        name="claude_agent_sdk",
        label="Claude Agent SDK",
        family="sdk",
        providers=("anthropic",),
        accepts=("anthropic:api_key", "anthropic:subscription"),
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
