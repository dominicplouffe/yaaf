"""The one resolution of what a seat runs on.

The runner, the resident chat and the spend report each used to carry their
own copy of "override, then role version, then the platform default" — and a
copy is a place for the next field to be added to two of three. Here once.
"""

from __future__ import annotations

from dataclasses import dataclass

from yaaf.runtimes import catalog, providers
from yaaf.runtimes.harnesses import LEGACY_RUNTIME


@dataclass(frozen=True)
class Seat:
    model: str
    effort: str
    provider: str
    harness: str


def effective_harness(seat_harness: str | None, runtime: str | None) -> str:
    """What will actually execute a seat. The project-wide `runtime` is an
    override and nothing else: "mock" puts every seat on the scripted backend
    (CI, the goldens, the fire drill), any other explicit value is honoured as
    a harness name as it always was, and the legacy default defers to the
    seat. The runner picks its backend by this and pins it on the run by this,
    so the row cannot name a harness the run never touched."""
    if runtime and runtime != LEGACY_RUNTIME:
        return runtime
    return seat_harness or providers.provider(None).default_harness


def seat_for(agent, role_version, runtime: str | None = None) -> Seat:
    """`agent` is an Agent row; `role_version` the RoleVersion it is pinned to,
    or None; `runtime` the project's `integrations.runtime`, when the caller is
    about to execute. Pinned onto the Run at lease time, never at finalize, so
    a reaped or killed run stays attributed to what actually ran."""
    model = agent.model_override or (role_version.model if role_version
                                     else catalog.DEFAULT_MODEL)
    effort = agent.effort_override or (role_version.effort if role_version
                                       else catalog.DEFAULT_EFFORT)
    provider = catalog.provider_of(model)
    harness = effective_harness(providers.provider(provider).default_harness, runtime)
    return Seat(model=model, effort=effort, provider=provider, harness=harness)
