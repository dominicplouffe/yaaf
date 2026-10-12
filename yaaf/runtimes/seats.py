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


def _meters_usd(found, provider: str, credential) -> bool:
    """Whether this seat's spend is kept in dollars: by its credential when
    one is known, else by whether the harness could take anything else."""
    if credential is not None:
        return providers.provider(credential[0]).kind(credential[1]).meter == "usd"
    kinds = [k for k in providers.provider(provider).kinds
             if f"{provider}:{k.mode}" in found.accepts]
    return not kinds or all(k.meter == "usd" for k in kinds)


def refusal(harness: str | None, model: str, tool_permissions: dict | None,
            credential: tuple[str | None, str | None] | None = None,
            priced: bool | None = None, effort: str | None = None) -> str | None:
    """Why a seat on `harness` running `model` under these grants cannot run,
    paying with `credential` — (provider, mode), when one is known — or None
    when it can. `priced` is whether the install can meter `model` in dollars
    (yaaf.core.prices.covered, which reads the install's own prices); a
    caller with no database reads the shipped catalog. Every rule here reads
    the registries and none names a harness (docs/RUNTIMES.md, "The rules this
    adds")."""
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
    # R2, the other way round — a grant the harness cannot honour. On yaaf's
    # loop the web is two tools, and search is the provider's: one that runs
    # none cannot give a role that grants `web` what it was hired with.
    # Refused rather than quietly halved (docs/RUNTIMES.md, open question 6).
    seller = providers.provider(provider)
    if (found.family == "loop" and (tool_permissions or {}).get("web")
            and not seller.search):
        return (f"{found.label} cannot search the web on {seller.label}, and this role "
                f"grants web — run this seat on a model whose provider searches, or "
                f"leave web off the role")
    # R3 — a model the harness would have to price, with no price to price it
    # by. A run nobody can price is a run the budget cannot stop. Only under a
    # dollar meter: a credential metered in tokens is stopped in tokens.
    if priced is None:
        priced = catalog.price(model) is not None or seller.reports_cost
    if found.cost == "priced" and not priced and _meters_usd(found, provider, credential):
        return (f"{found.label} prices every run, and {model} has no price on this "
                f"install — set one in Settings → Model prices, or run this seat on a "
                f"harness that reports its own cost")
    # R6 — an effort the harness cannot express, or the model does not take.
    # Never mapped silently, in either direction: "max" sent as "high" is a
    # seat run cheaper than it was hired at, and the other way round dearer.
    if effort and found.efforts:
        if effort not in found.efforts:
            return (f"{found.label} has no effort '{effort}' — it takes "
                    f"{', '.join(found.efforts)}")
        row = catalog.model(model)
        if row is not None and row.efforts is not None and effort not in row.efforts:
            return (f"{model} takes the efforts {', '.join(row.efforts)} — not '{effort}'; "
                    f"pick one of those for this seat")
    # R1 — a credential the harness cannot authenticate with.
    if credential is not None and found.accepts:
        cred_provider, mode = credential
        kind = providers.provider(cred_provider).kind(mode)
        if f"{cred_provider or providers.DEFAULT_PROVIDER}:{kind.mode}" not in found.accepts:
            return (f"{found.label} cannot pay with a {kind.bills.split(',')[0]} — "
                    f"assign a credential it accepts, or run this seat on another harness")
    return None
