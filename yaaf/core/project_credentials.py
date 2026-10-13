"""Which credential pays which provider, for one project.

A project's billing credential (`Project.billing_secret_id`) pays whichever
provider it is for, sets the project's meter, and decides what its seats run
on: the provider it pays is the project's provider, and that provider's lineup
— or the project's own (`Project.seat_models`) — answers each role's tier. A
project with a seat on a second provider holds a credential for it beside the
first (`ProjectLlmCredential`, docs/RUNTIMES.md M3). Every reader — the seat
doors, the lease, the health checklist, the credential screens — asks here, so
"what does this seat run on, and who pays for it" has one answer.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import Project, ProjectLlmCredential, Secret
from yaaf.runtimes import catalog
from yaaf.runtimes.providers import DEFAULT_PROVIDER


def held(session: Session, project: Project) -> dict[str, Secret]:
    """Every credential the project holds, by the provider it pays."""
    out: dict[str, Secret] = {}
    for row in session.scalars(select(ProjectLlmCredential)
                               .where(ProjectLlmCredential.project_id == project.id)):
        secret = session.get(Secret, row.secret_id)
        if secret is not None:
            out[row.provider] = secret
    # The billing credential last, so it wins: a row naming the provider it
    # already pays is a stale row, not a second answer.
    if project.billing_secret_id:
        billing = session.get(Secret, project.billing_secret_id)
        if billing is not None:
            out[billing.provider or DEFAULT_PROVIDER] = billing
    return out


def provider_of(session: Session, project: Project) -> str:
    """The project's provider: the one its billing credential pays. A draft
    with no credential yet reads as the default provider's, which is what it
    will run on if the credential it is given pays that one."""
    billing = session.get(Secret, project.billing_secret_id) if project.billing_secret_id else None
    return (billing.provider if billing is not None else None) or DEFAULT_PROVIDER


def project_lineup(session: Session, project: Project) -> dict[str, str | None]:
    """The model each role tier runs on in this project — the one place a
    caller gets the `lineup` yaaf.runtimes.seats.seat_for resolves against."""
    return catalog.lineup(provider_of(session, project), project.seat_models)


def credential_for(session: Session, project: Project, provider: str) -> Secret | None:
    return held(session, project).get(provider)


def seat_credential(session: Session, project: Project,
                    provider: str) -> tuple[str, str] | None:
    """(provider, mode) of what pays a seat on `provider`, as the seat rules
    read it. The billing credential's mode is the project's billing mode."""
    secret = credential_for(session, project, provider)
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
    for row in session.scalars(select(ProjectLlmCredential)):
        project = session.get(Project, row.project_id)
        if project is not None:
            out.setdefault(row.secret_id, {})[project.id] = project
    return {secret_id: sorted(projects.values(), key=lambda p: p.name)
            for secret_id, projects in out.items()}
