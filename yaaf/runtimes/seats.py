"""The one resolution of what a seat runs on.

The runner, the resident chat and the spend report each used to carry their
own copy of "override, then role version, then the platform default" — and a
copy is a place for the next field to be added to two of three. Here once.
"""

from __future__ import annotations

from dataclasses import dataclass

from yaaf.runtimes import catalog, providers
from yaaf.runtimes.harnesses import HARNESSES, LEGACY_RUNTIME


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
    a reaped or killed run stays attributed to what actually ran.

    The harness resolves like the model beside it: the seat's own override,
    then the role's, then whatever the model's provider runs on by default."""
    model = agent.model_override or (role_version.model if role_version
                                     else catalog.DEFAULT_MODEL)
    effort = agent.effort_override or (role_version.effort if role_version
                                       else catalog.DEFAULT_EFFORT)
    provider = catalog.provider_of(model)
    chosen = (getattr(agent, "harness_override", None)
              or (getattr(role_version, "harness", None) if role_version else None)
              or providers.provider(provider).default_harness)
    return Seat(model=model, effort=effort, provider=provider,
                harness=effective_harness(chosen, runtime))


def choosable() -> list:
    """The harnesses a seat may be put on. The scripted backend is a
    project-wide test override, never a seat's choice."""
    return [h for h in HARNESSES.values() if h.family != "mock"]


def _withheld(tool_permissions: dict | None) -> set[str]:
    """The grants a role leaves off, read the way the sandbox reads them
    (sandbox_entry._builtin_tools): files unless refused, a shell for `bash`
    or `data_processing`, the web only when granted."""
    perms = tool_permissions or {}
    withheld = set()
    if not perms.get("files", True):
        withheld.add("files")
    if not (perms.get("bash", False) or perms.get("data_processing", False)):
        withheld.add("bash")
    if not perms.get("web", False):
        withheld.add("web")
    return withheld


def refusal(harness: str | None, model: str, tool_permissions: dict | None,
            credential: tuple[str | None, str | None] | None = None) -> str | None:
    """Why a seat on `harness` running `model` under these grants cannot run,
    paying with `credential` — (provider, mode), when one is known — or None
    when it can. Every rule here reads the registries and none names a
    harness (docs/RUNTIMES.md, "The rules this adds")."""
    provider = catalog.provider_of(model)
    name = harness or providers.provider(provider).default_harness
    found = HARNESSES.get(name)
    if found is None or found.family == "mock":
        options = ", ".join(h.name for h in choosable())
        return f"unknown harness '{name}' — a seat runs on one of: {options}"
    if found.providers and provider not in found.providers:
        return (f"{found.label} cannot run {model}: it serves "
                f"{', '.join(found.providers)} models, and {model} is {provider}'s")
    # R2 — a grant the harness cannot take away. Widening a role because the
    # transport is clumsy is exactly what a pinned role version exists to stop.
    unwithholdable = sorted(_withheld(tool_permissions) - set(found.withholds))
    if unwithholdable:
        grant = unwithholdable[0]
        return (f"{found.label} cannot run without {grant}, and this role does not "
                f"grant it — grant {grant} on the role, or run this seat on another harness")
    # R1 — a credential the harness cannot authenticate with.
    if credential is not None and found.accepts:
        cred_provider, mode = credential
        kind = providers.provider(cred_provider).kind(mode)
        if f"{cred_provider or providers.DEFAULT_PROVIDER}:{kind.mode}" not in found.accepts:
            return (f"{found.label} cannot pay with a {kind.bills.split(',')[0]} — "
                    f"assign a credential it accepts, or run this seat on another harness")
    return None
