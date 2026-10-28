"""Which credential pays which provider, for one project.

A project's billing credential (`Project.billing_secret_id`) pays whichever
provider it is for, and sets the project's meter. A project whose seats run on
a second provider holds a credential for it beside the first
(`ProjectLlmCredential`, docs/RUNTIMES.md M3). Every reader — the seat doors,
the lease, the health checklist, the credential screens — asks here, so "who
pays for this seat" has one answer.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import Project, ProjectLlmCredential, Secret
from yaaf.runtimes.providers import DEFAULT_PROVIDER


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
                   fallback: bool | None = None) -> Secret | None:
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


def seat_credential(session: Session, project: Project, provider: str, *,
                    fallback: bool | None = None) -> tuple[str, str] | None:
    """(provider, mode) of what pays a seat on `provider`, as the seat rules
    read it. The billing credential's mode is the project's billing mode."""
    secret = credential_for(session, project, provider, fallback=fallback)
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
    for row in session.scalars(select(ProjectLlmCredential)):
        project = session.get(Project, row.project_id)
        if project is not None:
            out.setdefault(row.secret_id, {})[project.id] = project
    return {secret_id: sorted(projects.values(), key=lambda p: p.name)
            for secret_id, projects in out.items()}
