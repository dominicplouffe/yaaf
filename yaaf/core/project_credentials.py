"""Which credential pays which provider, for one project.

A project's billing credential (`Project.billing_secret_id`) pays whichever
provider it is for, and sets the project's meter. A project whose seats run on
a second provider holds a credential for it beside the first
(`ProjectLlmCredential`). Every reader — the seat doors,
the lease, the health checklist, the credential screens — asks here, so "who
pays for this seat" has one answer.

A Code Factory holds one more, beside all of those: its coding credential
(`Project.coding_secret_id`). It pays the seats that hold the repo and nobody
else, and it is asked for by seat rather than by provider — it may well be a
second credential for the provider the billing one already pays (a Claude API
key for the talking, a Claude subscription for the coding), which "one
provider, one credential" could never say.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import Agent, AgentKind, Project, ProjectLlmCredential, Secret
from yaaf.core.shape import is_factory
from yaaf.runtimes.providers import DEFAULT_PROVIDER, PROVIDERS

#: The grant that makes a seat a coding seat: its role is handed the repo.
REPO_SCOPE = "github"


def coding_providers() -> tuple[str, ...]:
    """The providers that ship a coding CLI of their own — Claude Code, Codex,
    the Gemini CLI — which are the ones that also sell a login for it. Read
    from the registry, so a fourth is a registry row."""
    return tuple(name for name, seller in PROVIDERS.items()
                 if any(kind.mode == "subscription" for kind in seller.kinds))


def coding_harness(provider: str) -> str | None:
    """The coding CLI a coding seat on `provider` is driven by, whichever kind
    of key pays it: the harness that provider's login runs on."""
    seller = PROVIDERS.get(provider)
    if seller is None or provider not in coding_providers():
        return None
    return seller.default_harness("subscription")


def coding_credential(session: Session, project: Project | None) -> Secret | None:
    """What this project's repo seats code with, or None — always None outside
    a Code Factory, whatever the column holds."""
    if project is None or not project.coding_secret_id or not is_factory(project):
        return None
    return session.get(Secret, project.coding_secret_id)


def holds_repo(session: Session, agent: Agent | None) -> bool:
    """Whether this seat is one that writes to the repo: an employee whose role
    is granted it. Never the lead — it plans, staffs and answers the chairman,
    and that is the talking the coding credential exists to stay out of."""
    if agent is None or agent.kind == AgentKind.CEO:
        return False
    from yaaf.server.contextpack import role_version_for

    role_version = role_version_for(session, agent)
    return role_version is not None and REPO_SCOPE in (role_version.secret_scopes or [])


def codes(session: Session, project: Project | None, agent: Agent | None) -> Secret | None:
    """The coding credential, when it is what pays this seat."""
    secret = coding_credential(session, project)
    if secret is None or not holds_repo(session, agent):
        return None
    return secret


def held(session: Session, project: Project, *,
         fallback: bool | None = None) -> dict[str, Secret]:
    """Every credential the project pays with, by the provider it pays.

    The fallback pays only while class seats are moved onto it
    (yaaf.core.model_policy.failover) — `fallback=None`, the default. Held
    always, it would also pay any seat pinned to its provider, at any hour,
    which nobody named it for. `True` asks as if it were in use: the fallback
    door, checking the seats would run there."""
    out: dict[str, Secret] = {}
    for row in session.scalars(select(ProjectLlmCredential)
                               .where(ProjectLlmCredential.project_id == project.id)):
        secret = session.get(Secret, row.secret_id)
        if secret is not None:
            out[row.provider] = secret
    if project.fallback_secret_id and fallback is not False:
        from yaaf.core.model_policy import failover

        moved = session.get(Secret, project.fallback_secret_id) if fallback \
            else failover(session, project)
        if moved is not None:
            out.setdefault(moved.provider or DEFAULT_PROVIDER, moved)
    # The billing credential last, so it wins: a row naming the provider it
    # already pays is a stale row, not a second answer.
    if project.billing_secret_id:
        billing = session.get(Secret, project.billing_secret_id)
        if billing is not None:
            out[billing.provider or DEFAULT_PROVIDER] = billing
    return out


def credential_for(session: Session, project: Project, provider: str, *,
                   fallback: bool | None = None, agent: Agent | None = None) -> Secret | None:
    """What pays a run on `provider` — `agent`'s run, when one is named. A
    coding seat on the coding credential's provider is paid by it and by
    nothing else; every other question is answered by provider."""
    coding = codes(session, project, agent) if agent is not None else None
    if coding is not None and (coding.provider or DEFAULT_PROVIDER) == provider:
        return coding
    return held(session, project, fallback=fallback).get(provider) \
        or _install_payer(session, provider)


def _install_payer(session: Session, provider: str) -> Secret | None:
    """The install's own key, when it pays `provider` (PLAN-09): what pays a
    seat whose provider no project credential pays. The platform's own key
    only — a borrowed one pays the Founder, never a project's seats."""
    from yaaf.core import platform

    row = platform.install_key(session)
    if row is not None and (row.provider or None) == provider:
        return row
    return None


def mode_of(project: Project, secret: Secret) -> str:
    """How `secret` bills on this project. The billing credential's mode is
    the project's billing mode."""
    if secret.id == project.billing_secret_id:
        return project.billing_mode
    return secret.llm_mode or "api_key"


def seat_credential(session: Session, project: Project, provider: str, *,
                    fallback: bool | None = None,
                    agent: Agent | None = None) -> tuple[str, str] | None:
    """(provider, mode) of what pays a seat on `provider`, as the seat rules
    read it. The billing credential's mode is the project's billing mode."""
    secret = credential_for(session, project, provider, fallback=fallback, agent=agent)
    if secret is None:
        return None
    if secret.id == project.billing_secret_id:
        return (secret.provider or DEFAULT_PROVIDER, project.billing_mode)
    return (secret.provider or DEFAULT_PROVIDER, secret.llm_mode or "api_key")


def projects_using(session: Session, secret_id: str) -> list[Project]:
    """Every project paying anything with this credential."""
    ids = set(session.scalars(select(ProjectLlmCredential.project_id)
                              .where(ProjectLlmCredential.secret_id == secret_id)))
    ids |= set(session.scalars(select(Project.id).where(Project.billing_secret_id == secret_id)))
    ids |= set(session.scalars(select(Project.id).where(Project.fallback_secret_id == secret_id)))
    ids |= set(session.scalars(select(Project.id).where(Project.coding_secret_id == secret_id)))
    if not ids:
        return []
    return list(session.scalars(select(Project).where(Project.id.in_(ids))
                                .order_by(Project.name)))


def usage(session: Session) -> dict[str, list[Project]]:
    """Every credential that pays for anything, with the projects it pays for —
    the billing credential and the per-provider ones alike, so a credential a
    project pays its second provider with is as "in use" as its first."""
    out: dict[str, dict[str, Project]] = {}
    for project in session.scalars(select(Project).where(Project.billing_secret_id.is_not(None))):
        out.setdefault(project.billing_secret_id, {})[project.id] = project
    for project in session.scalars(select(Project).where(Project.fallback_secret_id.is_not(None))):
        out.setdefault(project.fallback_secret_id, {})[project.id] = project
    for project in session.scalars(select(Project).where(Project.coding_secret_id.is_not(None))):
        out.setdefault(project.coding_secret_id, {})[project.id] = project
    for row in session.scalars(select(ProjectLlmCredential)):
        project = session.get(Project, row.project_id)
        if project is not None:
            out.setdefault(row.secret_id, {})[project.id] = project
    return {secret_id: sorted(projects.values(), key=lambda p: p.name)
            for secret_id, projects in out.items()}


def coding_problem(secret: Secret) -> str | None:
    """Why `secret` cannot be a coding credential, or None. A coding credential
    is a key for a provider with a coding CLI of its own — the API key or the
    login, whichever that CLI is signed in with."""
    name = secret.provider or DEFAULT_PROVIDER
    if name not in coding_providers():
        sellers = ", ".join(PROVIDERS[p].label for p in coding_providers())
        label = PROVIDERS[name].label if name in PROVIDERS else name
        return (f"'{secret.name}' pays {label}, which has no coding CLI of its own — a "
                f"coding key is one for {sellers}")
    return None


def coding_refusal(session: Session, project: Project) -> str | None:
    """Why this Code Factory may not start, or None: it names no coding
    credential, or the one it names can no longer code. Nothing to say about
    any other kind of project, about one on the scripted backend, which reads
    no credential at all — or about one nothing pays for yet: it cannot run a
    model on any key, so there is no bill for the coding key to keep apart,
    and the billing door asks again the moment something does pay."""
    if not is_factory(project) or (project.integrations or {}).get("runtime") == "mock":
        return None
    if not project.billing_secret_id:
        from yaaf.core import platform

        if platform.install_key(session) is None:
            return None
    secret = session.get(Secret, project.coding_secret_id) if project.coding_secret_id else None
    if secret is not None:
        if secret.hidden_at is not None:
            return (f"this Code Factory's coding key '{secret.name}' is hidden — un-hide it "
                    "under Settings → Providers, or name another")
        return coding_problem(secret)
    from yaaf.core.llm_credentials import offerable

    sellers = ", ".join(PROVIDERS[p].label for p in coding_providers())
    names = [row.name for row in session.scalars(offerable().order_by(Secret.name))
             if coding_problem(row) is None]
    if not names:
        return ("a Code Factory codes with a key of its own, and this install holds none: "
                f"add a credential for {sellers} under Settings → Providers, then name it "
                "as this project's coding key")
    return ("a Code Factory does not start without a coding key — the credential its repo "
            "seats code with, apart from the one that pays for the talking. Name one in "
            f"the project's Settings → Money: {', '.join(names)}")
