"""The one resolution of what a seat runs on.

The runner, the resident chat and the spend report each used to carry their
own copy of "override, then role version, then the platform default" — and a
copy is a place for the next field to be added to two of three. Here once.

A seat names a model reference (yaaf.runtimes.catalog): a model, or a class of
one. A class resolves on the project's primary provider through its lineup,
so the same role is a Claude seat on a project Anthropic pays and a GPT seat on
one OpenAI pays (docs/PROVIDERS.md §1). This module knows no database: the
caller hands it a `ModelContext` (yaaf.core.model_policy builds one per
project) saying which provider is primary, what the install's lineups say, how
each provider is paid, and whether the install has a search backend.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from yaaf.runtimes import catalog, providers
from yaaf.runtimes.harnesses import HARNESSES, LEGACY_RUNTIME, canonical


@dataclass(frozen=True)
class ModelContext:
    """What resolving a seat needs to know about the project and the install."""
    #: The provider a bare class resolves on: the billing credential's, else
    #: the install's default provider.
    provider: str = providers.DEFAULT_PROVIDER
    #: The install's lineup overrides, {provider: {class: model}}.
    lineups: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    #: How the project pays each provider it pays: {provider: mode}. A seat's
    #: default harness is its credential kind's.
    modes: Mapping[str, str] = field(default_factory=dict)
    #: Whether the install has a web search backend (R2's honour half).
    platform_search: bool = False

    def resolve(self, ref: str | None) -> str | None:
        return catalog.resolve(ref, self.provider, self.lineups)


#: The shipped lineups on the default provider, for a caller with no project.
SHIPPED = ModelContext()


@dataclass(frozen=True)
class Seat:
    #: The concrete model it runs on — or, when its class has no model on its
    #: provider, that class named with the provider (`openai/@fast`), which
    #: `refusal` turns into R8's sentence and nothing ever sends to a model.
    model: str
    #: The effort it was hired at, in yaaf's words: what `runs.effort` keeps.
    effort: str
    provider: str
    harness: str
    #: The reference as written on the seat or its role.
    ref: str = ""
    #: The rung it actually runs at (R6): the effort itself on a pinned model,
    #: the model's lowest rung at or above it on a class seat, None where the
    #: model has no dial.
    rung: str | None = None

    @property
    def resolved(self) -> bool:
        return not catalog.is_class(self.model)

    @property
    def pinned(self) -> bool:
        return not catalog.is_class(self.ref or self.model)


def effective_harness(seat_harness: str | None, runtime: str | None) -> str:
    """What will actually execute a seat. The project-wide `runtime` is an
    override and nothing else: "mock" puts every seat on the scripted backend
    (CI, the goldens, the fire drill), any other explicit value is honoured as
    a harness name as it always was, and the legacy default defers to the
    seat. The runner picks its backend by this and pins it on the run by this,
    so the row cannot name a harness the run never touched."""
    if runtime and runtime != LEGACY_RUNTIME:
        return canonical(runtime) or runtime
    return canonical(seat_harness) or providers.provider(None).default_harness()


def seat_for(agent, role_version, runtime: str | None = None, *,
             context: ModelContext = SHIPPED) -> Seat:
    """`agent` is an Agent row; `role_version` the RoleVersion it is pinned to,
    or None; `runtime` the project's `integrations.runtime`, when the caller is
    about to execute; `context` the project's (yaaf.core.model_policy).
    Pinned onto the Run at lease time, never at finalize, so a reaped or killed
    run stays attributed to what actually ran.

    The harness resolves like the model beside it: the seat's own override,
    then the role's, then the default of the credential kind that pays the
    seat's provider on this project."""
    ref = agent.model_override or (role_version.model if role_version
                                   else catalog.DEFAULT_MODEL)
    effort = agent.effort_override or (role_version.effort if role_version
                                       else catalog.DEFAULT_EFFORT)
    named, cls = catalog.parse(ref)
    if cls is not None:
        resolved = context.resolve(ref)
        provider = catalog.provider_of(resolved) if resolved else (named or context.provider)
        model = resolved or catalog.class_ref(cls, provider)
    else:
        model = ref
        provider = catalog.provider_of(model)
    chosen = (getattr(agent, "harness_override", None)
              or (getattr(role_version, "harness", None) if role_version else None)
              or providers.provider(provider).default_harness(context.modes.get(provider)))
    harness = effective_harness(chosen, runtime)
    found = HARNESSES.get(harness)
    run_at: str | None = effort
    if cls is not None and not catalog.is_class(model):
        run_at = catalog.rung(effort, catalog.rungs(model, found.efforts if found else None))
    return Seat(model=model, effort=effort, provider=provider, harness=harness, ref=ref,
                rung=run_at)


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


def unmodelled(model: str) -> str:
    """R8's sentence: a class the seat's provider has no model for."""
    named, cls = catalog.parse(model)
    seller = providers.provider(named)
    return (f"{seller.label} has no {catalog.CLASS_LABELS.get(cls or '', cls)} model set — "
            f"name one for it in Settings → Models (its lineup), or put this seat on a "
            f"model of its own")


def refusal(harness: str | None, model: str, tool_permissions: dict | None,
            credential: tuple[str | None, str | None] | None = None,
            priced: bool | None = None, effort: str | None = None,
            converses: bool = False, *, pinned: bool = True,
            platform_search: bool = False) -> str | None:
    """See `_refusal`. With neither a harness nor a credential named — a role
    door, before any project pays it — the seat runs on whichever harness its
    payer's kind defaults to, so it is refused only if no way of paying its
    provider could run it: an unpriced GPT role is fine for a ChatGPT plan
    (Codex, in tokens) and refused only once an API key is what pays it."""
    if harness or credential is not None or catalog.is_class(model):
        return _refusal(harness, model, tool_permissions, credential, priced, effort,
                        converses, pinned=pinned, platform_search=platform_search)
    provider = catalog.provider_of(model)
    problems = [_refusal(None, model, tool_permissions, (provider, kind.mode), priced, effort,
                         converses, pinned=pinned, platform_search=platform_search)
                for kind in providers.provider(provider).kinds]
    return None if any(p is None for p in problems) else problems[0]


def _refusal(harness: str | None, model: str, tool_permissions: dict | None,
             credential: tuple[str | None, str | None] | None = None,
             priced: bool | None = None, effort: str | None = None,
             converses: bool = False, *, pinned: bool = True,
             platform_search: bool = False) -> str | None:
    """Why a seat on `harness` running `model` under these grants cannot run,
    paying with `credential` — (provider, mode), when one is known — or None
    when it can. `model` is concrete, or a class named with its provider when
    the lineup has no model for it (R8). `priced` is whether the install can
    meter `model` in dollars (yaaf.core.prices.covered, which reads the
    install's own prices); a caller with no database reads the shipped
    catalog. `converses` is whether the seat holds the boardroom conversation
    — the CEO's, where the chat is on. `pinned` is whether the seat names
    this model rather than a class: only a pinned model's efforts are held
    strictly (R6). Every rule here reads the registries and none names a
    harness (docs/RUNTIMES.md, "The rules this adds"; docs/PROVIDERS.md)."""
    if catalog.is_class(model):
        # R8 — a class with no model on its provider is refused, never filled
        # in with some other model.
        return unmodelled(model)
    provider = catalog.provider_of(model)
    seller = providers.provider(provider)
    name = canonical(harness) or seller.default_harness(credential[1] if credential else None)
    found = HARNESSES.get(name)
    if found is None or found.family == "mock":
        options = ", ".join(h.name for h in choosable())
        return f"unknown harness '{name}' — a seat runs on one of: {options}"
    if found.providers and provider not in found.providers:
        return (f"{found.label} cannot run {model}: it serves "
                f"{', '.join(found.providers)} models, and {model} is {provider}'s")
    # R7 — the chairman waits on the CEO's seat in real time, turn after
    # turn; a harness that cannot resume a session cannot answer him.
    if converses and not found.conversation:
        return (f"{found.label} cannot hold the boardroom conversation, and this seat "
                f"is the CEO's — run the CEO on a harness that can, or turn the "
                f"boardroom chat off for this project")
    # R2 — a grant the harness cannot take away. Widening a role because the
    # transport is clumsy is exactly what a pinned role version exists to stop.
    unwithholdable = sorted(_withheld(tool_permissions) - set(found.withholds))
    if unwithholdable:
        grant = unwithholdable[0]
        return (f"{found.label} cannot run without {grant}, and this role does not "
                f"grant it — grant {grant} on the role, or run this seat on another harness")
    # R2, the other way round — a grant the harness cannot honour. The web is
    # fetch and search, and search is the harness's own, the provider's wire
    # (yaaf's loop), the platform's search backend, or nobody's: a seat with
    # none cannot give a role that grants `web` what it was hired with.
    # Refused rather than quietly halved (docs/RUNTIMES.md, open question 6).
    if (tool_permissions or {}).get("web"):
        searches = (found.search == "own"
                    or (found.search == "loop" and (seller.search or platform_search))
                    or (found.search == "platform" and platform_search))
        if not searches:
            where = f" on {seller.label}" if found.search == "loop" else ""
            return (f"{found.label} cannot search the web{where}, and this role grants web — "
                    f"configure a web search backend (Settings → Web search), run this seat "
                    f"on a harness or a provider that searches, or leave web off the role")
    # R6 — an effort outside yaaf's ladder is a typo wherever it is. On a
    # pinned model one the harness cannot express, or the model does not
    # take, is refused: never mapped silently, in either direction — "max"
    # sent as "high" is a seat run cheaper than it was hired at, and the other
    # way round dearer. A class seat was written for no model in particular,
    # so it runs at its model's published rung instead (`catalog.rung`).
    if effort and effort not in catalog.EFFORTS:
        return f"no effort '{effort}' — an effort is one of {', '.join(catalog.EFFORTS)}"
    if effort and pinned and found.efforts and effort not in found.efforts:
        return (f"{found.label} has no effort '{effort}' — it takes "
                f"{', '.join(found.efforts)}")
    if effort and pinned:
        row = catalog.model(model)
        if row is not None and row.efforts is not None and effort not in row.efforts:
            return (f"{model} takes the efforts {', '.join(row.efforts)} — not '{effort}'; "
                    f"pick one of those for this seat")
    # R3 — a model the harness would have to price, with no price to price it
    # by. A run nobody can price is a run the budget cannot stop. Only under a
    # dollar meter: a credential metered in tokens is stopped in tokens.
    if priced is None:
        priced = catalog.price(model) is not None or seller.reports_cost
    if found.cost == "priced" and not priced and _meters_usd(found, provider, credential):
        return (f"{found.label} prices every run, and {model} has no price on this "
                f"install — import or set one in Settings → Model prices, or run this "
                f"seat on a harness that reports its own cost")
    # R1 — a credential the harness cannot authenticate with.
    if credential is not None and found.accepts:
        cred_provider, mode = credential
        kind = providers.provider(cred_provider).kind(mode)
        if f"{cred_provider or providers.DEFAULT_PROVIDER}:{kind.mode}" not in found.accepts:
            return (f"{found.label} cannot pay with a {kind.bills.split(',')[0]} — "
                    f"assign a credential it accepts, or run this seat on another harness")
    return None


def class_role_refusal(harness: str | None, tool_permissions: dict | None,
                       effort: str | None = None, converses: bool = False) -> str | None:
    """Why a role naming a class cannot be a role at all, or None. A class
    role is bound to no model until a project resolves it, so what is checked
    here is only what holds whatever provider it lands on — the effort's
    vocabulary, and the harness it names, if it names one: that it can hold
    the boardroom, withhold what the role withholds, and search at all. Price,
    payment and the model's own rungs are the seat doors' (R3, R5, R6, R8)."""
    if effort and effort not in catalog.EFFORTS:
        return f"no effort '{effort}' — an effort is one of {', '.join(catalog.EFFORTS)}"
    if not harness:
        return None
    found = HARNESSES.get(canonical(harness) or "")
    if found is None or found.family == "mock":
        options = ", ".join(h.name for h in choosable())
        return f"unknown harness '{harness}' — a seat runs on one of: {options}"
    if converses and not found.conversation:
        return (f"{found.label} cannot hold the boardroom conversation, and this is the "
                f"CEO's role — name a harness that can, or none")
    unwithholdable = sorted(_withheld(tool_permissions) - set(found.withholds))
    if unwithholdable:
        grant = unwithholdable[0]
        return (f"{found.label} cannot run without {grant}, and this role does not "
                f"grant it — grant {grant} on the role, or name another harness")
    if (tool_permissions or {}).get("web") and found.search == "none":
        return (f"{found.label} cannot search the web, and this role grants web — name "
                f"another harness, or leave web off the role")
    return None
